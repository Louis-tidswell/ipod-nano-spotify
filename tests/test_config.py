"""Config authentication, profile lifecycle and privileged-input boundaries."""
import base64
import http.client
import io
import json
import socket
import sys
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'pi'))
from config_server import Authentication, Handler, Jobs, Server
from config_helper import split_fields, validate_archive, dispatch
from config_network import addresses
from spotify_profiles import SpotifyProfiles, private_json
from spotify_bridge import Snapshot, encode_status, page_checksum, get_snapshot, PlayerApi, FLAG_AUTHENTICATED, FLAG_PLAYING


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.player = self.root / 'player'
        self.state = self.player / 'state.json'
        private_json(self.state, {'device_id': 'same-device', 'last_volume': 25, 'event_manager': {'old': True},
                                 'credentials': {'username': 'alice', 'data': 'alice-secret'}})
        self.service = Mock()
        self.api = Mock(return_value=None)
        self.profiles = SpotifyProfiles(self.root / 'profiles', self.player, self.service, self.api)
        self.alice = self.profiles.public()['active']

    def tearDown(self):
        self.temp.cleanup()

    def login_bob(self):
        self.profiles.begin('Bob')
        state = json.loads(self.state.read_text())
        state['credentials'] = {'username': 'bob', 'data': 'bob-secret'}
        private_json(self.state, state)
        self.api.return_value = {'username': 'bob'}
        self.profiles.finish_if_ready()
        return self.profiles.public()['active']

    def test_import_and_switch_keep_secrets_private_and_device_stable(self):
        self.assertNotIn('alice-secret', json.dumps(self.profiles.public()))
        bob = self.login_bob()
        self.assertNotEqual(bob, self.alice)
        self.profiles.activate(self.alice)
        state = json.loads(self.state.read_text())
        self.assertEqual(state['credentials']['data'], 'alice-secret')
        self.assertEqual(state['device_id'], 'same-device')
        self.assertEqual(state['last_volume'], 25)
        self.assertNotIn('event_manager', state)
        for file in (self.root / 'profiles').glob('*.json'):
            self.assertEqual(file.stat().st_mode & 0o777, 0o600)
        self.assertNotIn('secret', json.dumps(self.profiles.public()))

    def test_cancel_restores_previous_account_even_after_web_restart(self):
        self.profiles.begin('Bob')
        restarted = SpotifyProfiles(self.root / 'profiles', self.player, self.service, self.api)
        self.assertTrue(restarted.public()['pending'])
        restarted.cancel()
        self.assertEqual(json.loads(self.state.read_text())['credentials']['username'], 'alice')
        self.assertFalse(restarted.public()['pending'])

    def test_failed_activation_restores_previous_credentials(self):
        bob = self.login_bob()
        self.service.side_effect = [None, RuntimeError('failed start'), None]
        with self.assertRaises(RuntimeError):
            self.profiles.activate(self.alice)
        self.assertEqual(self.profiles.public()['active'], bob)
        self.assertEqual(json.loads(self.state.read_text())['credentials']['username'], 'bob')

    def test_delete_active_logs_out_and_removes_credentials(self):
        bob = self.login_bob()
        self.profiles.delete(bob)
        self.assertIsNone(self.profiles.public()['active'])
        self.assertEqual(json.loads(self.state.read_text())['credentials'], {})
        self.assertFalse((self.root / 'profiles' / (bob + '.json')).exists())
        self.service.assert_called_with('stop')
        self.assertEqual(len(self.profiles.public()['profiles']), 1)

    def test_legacy_credentials_do_not_reappear_after_account_switch(self):
        private_json(self.player / 'credentials.json', {'username': 'alice', 'data': 'old-secret'})
        self.profiles.begin('Bob')
        self.assertFalse((self.player / 'credentials.json').exists())
        self.assertEqual(json.loads(self.state.read_text())['credentials'], {})

    def test_unknown_profile_cannot_read_arbitrary_files_or_stop_player(self):
        with self.assertRaises(ValueError):
            self.profiles.activate('../state')
        self.service.assert_not_called()


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.auth = Authentication(Path(self.temp.name))
        self.app = Mock()
        self.app.auth = self.auth
        self.app.get_status.return_value = {'addresses': [], 'temperature': 42}
        self.app.action.return_value = {'message': 'accepted'}
        self.server = Server(('127.0.0.1', 0), Handler)
        self.server.application = self.app
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.host = '127.0.0.1:' + str(self.server.server_port)
        self.cookie = ''

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.temp.cleanup()

    def request(self, path, data=None, headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port)
        fields = {'Host': self.host, 'Cookie': self.cookie}
        if data is not None:
            fields.update({'Content-Type': 'application/json', 'X-Nano-Config': '1', 'Origin': 'http://' + self.host})
        fields.update(headers or {})
        connection.request('GET' if data is None else 'POST', path,
                           body=None if data is None else json.dumps(data), headers=fields)
        response = connection.getresponse()
        body = response.read()
        if response.getheader('Set-Cookie'):
            self.cookie = response.getheader('Set-Cookie').split(';')[0]
        result = (response.status, json.loads(body) if 'application/json' in response.getheader('Content-Type', '') else body)
        connection.close()
        return result

    def login(self):
        self.auth.set_password('test-household')
        self.assertEqual(self.request('/api/login', {'password': 'test-household'})[0], 200)

    def test_public_status_but_actions_and_profiles_require_password(self):
        self.assertEqual(self.request('/api/status')[0], 200)
        self.assertEqual(self.request('/api/pi/reboot', {})[0], 401)
        self.assertEqual(self.request('/api/profiles')[0], 401)
        self.app.action.assert_not_called()

    def test_one_time_setup_requires_private_token(self):
        token = self.auth.setup_token
        self.assertEqual(self.request('/api/setup', {'token': 'wrong', 'password': 'test-household'})[0], 403)
        self.assertEqual(self.request('/api/setup', {'token': token, 'password': 'test-household'})[0], 200)
        self.assertTrue(self.auth.authenticated(self.cookie))
        self.assertEqual(self.request('/api/setup', {'token': token, 'password': 'new-password'})[0], 403)
        self.assertFalse((Path(self.temp.name) / 'setup-token').exists())

    def test_cross_site_and_rebinding_hosts_cannot_mutate(self):
        self.login()
        self.assertEqual(self.request('/api/pi/reboot', {}, {'Origin': 'http://evil.example'})[0], 403)
        self.assertEqual(self.request('/api/pi/reboot', {}, {'Host': 'evil.example:' + str(self.server.server_port)})[0], 403)
        self.assertEqual(self.request('/api/pi/reboot', {}, {'X-Nano-Config': ''})[0], 403)
        self.app.action.assert_not_called()

    def test_logout_and_password_change_invalidate_sessions(self):
        self.login()
        old = self.cookie
        self.assertEqual(self.request('/api/password', {'current': 'test-household', 'password': 'new-household'})[0], 200)
        self.assertFalse(self.auth.authenticated(old))
        self.assertEqual(self.request('/api/apps/save', {'selected': ['spotify_remote']})[0], 202)
        self.assertEqual(self.request('/api/logout', {})[0], 200)
        self.assertFalse(self.auth.authenticated(self.cookie))

    def test_get_does_not_run_actions_and_static_paths_are_allowlisted(self):
        self.login()
        self.assertEqual(self.request('/api/pi/poweroff')[0], 404)
        self.assertEqual(self.request('/../pi/go-librespot.yml')[0], 404)
        self.assertEqual(self.request('/')[0], 200)
        self.app.action.assert_not_called()


class BoundaryTests(unittest.TestCase):
    def test_logged_out_spotify_is_not_reported_as_playing(self):
        api = Mock()
        api.request.side_effect = [{'playback_ready': False}, {}]
        snapshot = get_snapshot(api)
        self.assertFalse(snapshot.flags & (FLAG_AUTHENTICATED | FLAG_PLAYING))
        self.assertEqual(snapshot.message, 'Waiting for Spotify login')

    def test_no_content_response_has_no_authenticated_session(self):
        response = Mock(status=204)
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        with patch('spotify_bridge.urllib.request.urlopen', return_value=response):
            self.assertIsNone(PlayerApi('http://localhost').request('/status'))

    def archive(self, names):
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w') as archive:
            for name in names:
                archive.writestr(name, b'app')
        return base64.b64encode(output.getvalue()).decode()

    def test_helper_rejects_paths_outside_app_payload_and_unknown_actions(self):
        for name in ['../../etc/passwd', 'Apps/Data/save.json', 'Apps/Executables/../../escape.bin', 'Apps/Executables/.hidden.bin']:
            with self.assertRaises(ValueError):
                validate_archive(self.archive(['Apps/AllApps.pack', name]))
        archive = validate_archive(self.archive(['Apps/AllApps.pack', 'Apps/Executables/Spotify.hbapp', 'Apps/Icons/Spotify.bin']))
        archive.close()
        with self.assertRaises(ValueError):
            dispatch({'action': 'shell', 'command': 'whoami'})

    def test_nmcli_escaped_names_remain_intact(self):
        self.assertEqual(split_fields(r'House\:WiFi:100:WPA2:*:wlan0'), ['House:WiFi', '100', 'WPA2', '*', 'wlan0'])

    def test_address_prefers_wifi_over_ethernet_and_excludes_virtual_links(self):
        data = [{'ifname': n, 'addr_info': [{'scope': 'global', 'local': a}]} for n, a in
                [('eth0', '10.0.0.2'), ('wlan0', '192.168.1.9'), ('docker0', '172.17.0.1')]]
        with patch('config_network.subprocess.run', return_value=Mock(stdout=json.dumps(data))):
            self.assertEqual([r['ip'] for r in addresses()], ['192.168.1.9', '10.0.0.2'])

    def test_config_status_is_checksummed_and_fits_mailbox(self):
        page = encode_status(3, 2, 1, Snapshot(), config='http://192.168.1.120:8080')
        import struct
        self.assertEqual(len(page), 512)
        self.assertEqual(struct.unpack_from('<I', page, 236)[0], 4)
        self.assertEqual(struct.unpack_from('<I', page, 40)[0], page_checksum(page, 10))
        self.assertEqual(page[256:320].split(b'\0')[0], b'http://192.168.1.120:8080')

    def test_mutating_jobs_are_serialized(self):
        jobs, release = Jobs(), threading.Event()
        jobs.submit('first', lambda: release.wait(2))
        with self.assertRaises(ValueError):
            jobs.submit('second', lambda: None)
        release.set()

    def test_install_keeps_app_data_and_replaces_only_validated_payload(self):
        from config_helper import nano_install
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory) / 'runtime'
            apps = runtime / 'mount/Apps'
            (apps / 'Data/Quiz').mkdir(parents=True)
            (apps / 'Data/Quiz/save').write_text('saved-game')
            (apps / 'AllApps.pack').write_text('old-pack')
            (apps / 'Executables').mkdir()
            (apps / 'Executables/Old.hbapp').write_text('old-app')
            device = str(Path(directory) / 'drive')
            Path(device + '1').touch()
            with patch('config_helper.RUNTIME', runtime), patch('config_helper.ipod_device', return_value=device), \
                    patch('config_helper.run', side_effect=lambda args, **kwargs: 'vfat' if 'blkid' in args[0] else ''), \
                    patch('config_helper.subprocess.run', return_value=Mock(returncode=0)), patch('config_helper.os.sync'):
                nano_install({'archive': self.archive(['Apps/AllApps.pack', 'Apps/Executables/Spotify.hbapp', 'Apps/Icons/Spotify.bin'])})
            self.assertEqual((apps / 'Data/Quiz/save').read_text(), 'saved-game')
            self.assertEqual((apps / 'AllApps.pack').read_bytes(), b'app')
            self.assertTrue((apps / 'Executables/Spotify.hbapp').exists())
            self.assertFalse((apps / 'Executables/Old.hbapp').exists())

    def test_wifi_failure_rolls_back_checkpoint(self):
        from config_helper import wifi_change
        manager = Mock()
        manager.CheckpointCreate.return_value = '/checkpoint/1'
        with tempfile.TemporaryDirectory() as directory, patch('config_helper.wifi_device', return_value='wlan0'), \
                patch('config_helper.nm_bus', return_value=(Mock(), manager)), patch('config_helper.CHECKPOINT', Path(directory) / 'checkpoint'):
            with self.assertRaises(ValueError):
                wifi_change({'action': 'wifi-saved', 'uuid': 'invalid'})
        manager.CheckpointRollback.assert_called_once_with('/checkpoint/1')
        manager.CheckpointDestroy.assert_called_once_with('/checkpoint/1')
        self.assertEqual(int(manager.CheckpointCreate.call_args.args[1]), 120)


if __name__ == '__main__':
    unittest.main()
