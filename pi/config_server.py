#!/usr/bin/env python3
"""LAN config page for the Pi and Nano. Uses only the Python standard library."""
from __future__ import annotations
import argparse
import base64
import hashlib
import hmac
import json
import os
import secrets
import socket
import subprocess
import threading
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from config_network import CONFIG_DIR, addresses, config_url
from managed_nano import helper
from spotify_profiles import SpotifyProfiles, private_json

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / 'pi/config_web'
MANIFEST = ROOT / 'nanoapps/apps.toml'
PLAYER = Path.home() / '.config/go-librespot'
SERVICES = ('go-librespot.service', 'ipod-spotify-bridge.service', 'pipewire.service', 'wireplumber.service')


def player_api(path, payload=None):
    request = urllib.request.Request('http://127.0.0.1:3678' + path,
        data=None if payload is None else json.dumps(payload).encode(),
        headers={'Content-Type': 'application/json'}, method='GET' if payload is None else 'POST')
    with urllib.request.urlopen(request, timeout=3) as response:
        raw = response.read()
        return json.loads(raw) if raw else None


def user_service(action, service='go-librespot.service'):
    if action not in ('stop', 'start', 'restart') or service not in SERVICES:
        raise ValueError('Unknown service action')
    subprocess.run(['systemctl', '--user', action, service], check=True, timeout=20,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class Authentication:
    def __init__(self, directory):
        self.directory = directory
        self.path = directory / 'password.json'
        self.lock = threading.Lock()
        self.sessions = {}
        self.failures = {}
        self.setup_token = ''
        if not self.path.exists():
            token_path = directory / 'setup-token'
            if token_path.exists():
                self.setup_token = token_path.read_text().strip()
            else:
                self.setup_token = secrets.token_urlsafe(32)
                fd = os.open(token_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, 'w') as stream:
                    stream.write(self.setup_token + '\n')

    @property
    def configured(self):
        return self.path.exists()

    def set_password(self, password):
        if not isinstance(password, str) or not 8 <= len(password) <= 256:
            raise ValueError('Use a household password between 8 and 256 characters')
        salt = secrets.token_bytes(16)
        digest = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1)
        private_json(self.path, dict(salt=base64.b64encode(salt).decode(), hash=base64.b64encode(digest).decode()))
        self.sessions.clear()
        self.setup_token = ''
        (self.directory / 'setup-token').unlink(missing_ok=True)
        (self.directory / 'setup-link.txt').unlink(missing_ok=True)

    def check(self, password):
        if not isinstance(password, str) or len(password) > 256 or not self.configured:
            return False
        saved = json.loads(self.path.read_text())
        digest = hashlib.scrypt(password.encode(), salt=base64.b64decode(saved['salt']), n=16384, r=8, p=1)
        return hmac.compare_digest(digest, base64.b64decode(saved['hash']))

    def login(self, password, address):
        with self.lock:
            now = time.monotonic()
            attempts = [t for t in self.failures.get(address, []) if now - t < 60]
            if len(attempts) >= 5:
                raise ValueError('Too many attempts. Wait one minute and try again.')
            if not self.check(password):
                self.failures[address] = attempts + [now]
                raise PermissionError('Incorrect household password')
            self.failures.pop(address, None)
            return self.new_session()

    def new_session(self):
        token = secrets.token_urlsafe(32)
        self.sessions = {k: v for k, v in self.sessions.items() if v > time.monotonic()}
        self.sessions[token] = time.monotonic() + 8 * 3600
        return token

    def authenticated(self, cookie):
        try:
            cookies = SimpleCookie(cookie or '')
            token = cookies['nano_session'].value
            with self.lock:
                return self.sessions.get(token, 0) > time.monotonic()
        except (KeyError, ValueError):
            return False

    def logout(self, cookie):
        try:
            token = SimpleCookie(cookie or '')['nano_session'].value
            with self.lock:
                self.sessions.pop(token, None)
        except (KeyError, ValueError):
            pass


class Jobs:
    def __init__(self):
        self.lock = threading.Lock()
        self.current = None

    def snapshot(self):
        with self.lock:
            return dict(self.current) if self.current else None

    def submit(self, label, function):
        with self.lock:
            if self.current and self.current['state'] == 'running':
                raise ValueError('Wait for the current operation to finish')
            job = {'id': secrets.token_hex(8), 'label': label, 'state': 'running', 'message': 'Working…', 'log': ''}
            self.current = job

        def execute():
            try:
                result = function()
                with self.lock:
                    job.update(state='done', message=result.get('message', 'Done') if isinstance(result, dict) else 'Done')
            except Exception as error:
                with self.lock:
                    # Error strings are application messages, never raw credentials or Wi-Fi subprocess output.
                    message = str(error) if isinstance(error, (ValueError, RuntimeError)) else 'Operation failed. Check Pi services and try again.'
                    job.update(state='error', message=message[:300])
        threading.Thread(target=execute, daemon=True).start()
        return {'job': job['id']}

    def installer(self, reload_only):
        command = ['python3', '-u', str(ROOT / 'pi/install_nanoapps.py'), '--managed']
        if reload_only:
            command.append('--reload-only')
        with subprocess.Popen(command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              text=True, stdin=subprocess.DEVNULL) as process:
            for line in process.stdout:
                with self.lock:
                    self.current['log'] = (self.current['log'] + line)[-6000:]
            if process.wait() != 0:
                raise RuntimeError('Nano installation failed. See the operation log; the bridge was restored.')
        return {'message': 'Apps reloaded. On the Nano, open Music or Settings, then return Home.'}


def app_selection():
    config = tomllib.loads(MANIFEST.read_text())
    available = {p.name for parent in (ROOT / 'vendor/NanoApps/apps', ROOT / 'nanoapps', ROOT / 'nanoapps/apps')
                 if parent.is_dir() for p in parent.iterdir() if p.is_dir() and (p / 'Makefile').exists()}
    available.discard('silver_resident')
    return [dict(id=name, name='Spotify' if name == 'spotify_remote' else name.replace('_', ' ').title(),
                 enabled=bool(config.get('all') or config.get('apps', {}).get(name))) for name in sorted(available)]


def save_selection(selected):
    available = {row['id'] for row in app_selection()}
    if not isinstance(selected, list) or not selected or not all(isinstance(x, str) and x in available for x in selected):
        raise ValueError('Select at least one available app')
    output = '# Apps installed from the Pi config page or pi/install_nanoapps.py.\nall = false\n\n[apps]\n'
    output += ''.join(f"{name} = {'true' if name in selected else 'false'}\n" for name in sorted(available))
    temporary = MANIFEST.with_suffix('.tmp')
    temporary.write_text(output)
    temporary.replace(MANIFEST)


def bluetooth_request(payload):
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    connection.settimeout(3)
    runtime = Path(os.environ.get('XDG_RUNTIME_DIR', '/run/user/' + str(os.getuid())))
    try:
        connection.connect(str(runtime / 'ipod-spotify-control.sock'))
        connection.sendall(json.dumps(payload).encode() + b'\n')
        output = b''
        while not output.endswith(b'\n') and len(output) < 65536:
            chunk = connection.recv(4096)
            if not chunk:
                break
            output += chunk
        response = json.loads(output)
        if 'error' in response:
            raise ValueError(response['error'])
        return response
    finally:
        connection.close()


class Application:
    def __init__(self, directory=CONFIG_DIR):
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        directory.chmod(0o700)
        self.directory = directory
        self.auth = Authentication(directory)
        self.jobs = Jobs()
        self.profiles = SpotifyProfiles(directory / 'spotify-profiles', PLAYER, user_service, player_api)
        self.cache_lock = threading.Lock()
        self.status = {}
        try:
            self.status['addresses'] = addresses()
        except (OSError, ValueError, subprocess.SubprocessError):
            self.status['addresses'] = []
        self.wifi = {'networks': [], 'saved': [], 'confirmation_pending': False}
        self.bluetooth = {'devices': [], 'message': 'Checking bridge…'}
        self.stopping = threading.Event()

    def poll(self):
        while not self.stopping.is_set():
            try:
                self.profiles.finish_if_ready()
            except (OSError, ValueError, urllib.error.URLError):
                pass
            try:
                root = player_api('/') or {}
                player = player_api('/status') or {}
                spotify = {'online': True, 'ready': bool(root.get('playback_ready')), 'username': player.get('username', ''),
                           'track': (player.get('track') or {}).get('name', ''), 'paused': player.get('paused', True)}
            except (OSError, ValueError, urllib.error.URLError):
                spotify = {'online': False, 'ready': False, 'username': ''}
            try:
                network = addresses()
            except (OSError, ValueError, subprocess.SubprocessError):
                network = []
            services = {}
            for name in SERVICES:
                result = subprocess.run(['systemctl', '--user', 'is-active', name], text=True, capture_output=True, timeout=3)
                services[name] = result.stdout.strip() or 'unknown'
            try:
                temperature = int(Path('/sys/class/thermal/thermal_zone0/temp').read_text()) / 1000
            except (OSError, ValueError):
                temperature = None
            try:
                nano = any('ipod' in (g / 'device/model').read_text().lower()
                           for g in Path('/sys/class/scsi_generic').glob('sg*'))
            except OSError:
                nano = False
            try:
                wifi = helper('wifi-status')
                controls = True
            except (OSError, RuntimeError, subprocess.SubprocessError):
                wifi = self.wifi
                controls = False
            try:
                bluetooth = bluetooth_request({'action': 'status'})
            except (OSError, ValueError):
                bluetooth = {'devices': [], 'message': 'Restart the bridge to enable web Bluetooth controls'}
            with self.cache_lock:
                self.status = dict(hostname=socket.gethostname(), addresses=network, url=config_url(), temperature=temperature,
                                   uptime=int(float(Path('/proc/uptime').read_text().split()[0])), services=services,
                                   nano_connected=nano, spotify=spotify, controls_ready=controls)
                self.wifi, self.bluetooth = wifi, bluetooth
            self.stopping.wait(4)

    def get_status(self):
        with self.cache_lock:
            return dict(self.status)

    def action(self, path, data):
        jobs, profiles = self.jobs, self.profiles
        if path == '/api/apps/save':
            return jobs.submit('Save app selection', lambda: save_selection(data.get('selected')))
        if path in ('/api/apps/install', '/api/apps/reload'):
            reload_only = path.endswith('/reload')
            def install():
                if not reload_only and 'selected' in data:
                    save_selection(data['selected'])
                return jobs.installer(reload_only)
            return jobs.submit('Reload Nano apps' if reload_only else 'Install selected apps', install)
        if path == '/api/profiles/add':
            return jobs.submit('Spotify login', lambda: profiles.begin(data.get('name', '')))
        if path == '/api/profiles/activate':
            return jobs.submit('Switch Spotify profile', lambda: profiles.activate(data.get('id')))
        if path == '/api/profiles/delete':
            return jobs.submit('Delete Spotify profile', lambda: profiles.delete(data.get('id')))
        if path == '/api/profiles/cancel':
            return jobs.submit('Cancel Spotify login', profiles.cancel)
        if path == '/api/profiles/rename':
            return jobs.submit('Rename Spotify profile', lambda: profiles.rename(data.get('id'), data.get('name')))
        if path == '/api/services/restart':
            service = data.get('service')
            if service not in SERVICES:
                raise ValueError('Unknown service')
            return jobs.submit('Restart ' + service, lambda: user_service('restart', service))
        if path in ('/api/pi/reboot', '/api/pi/poweroff'):
            action = path.rsplit('/', 1)[1]
            return jobs.submit('Pi ' + action, lambda: helper(action))
        if path.startswith('/api/wifi/'):
            action = path.rsplit('/', 1)[1]
            if action not in ('scan', 'connect', 'saved', 'forget', 'confirm'):
                raise ValueError('Unknown Wi-Fi action')
            values = {key: data[key] for key in ('interface', 'ssid', 'password', 'uuid') if key in data}
            return jobs.submit('Wi-Fi ' + action, lambda: helper('wifi-' + action, **values))
        if path == '/api/bluetooth/action':
            return bluetooth_request({'action': 'command', 'command': data.get('command'), 'address': data.get('address', '')})
        if path == '/api/player/control':
            command = data.get('command')
            if command not in ('playpause', 'prev', 'next'):
                raise ValueError('Unknown player command')
            return jobs.submit('Spotify ' + command, lambda: player_api('/player/' + command, {}))
        raise ValueError('Unknown action')


class Handler(BaseHTTPRequestHandler):
    server_version = 'NanoConfig/1'

    def log_message(self, format, *arguments):
        # Do not log URLs, query strings, password bodies or Spotify pairing codes.
        pass

    @property
    def app(self):
        return self.server.application

    def response(self, code, value, cookie=None):
        body = json.dumps(value).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.security_headers()
        if cookie is not None:
            self.send_header('Set-Cookie', 'nano_session=' + cookie + '; Path=/; HttpOnly; SameSite=Strict; Max-Age=' + ('28800' if cookie else '0'))
        self.end_headers()
        self.wfile.write(body)

    def security_headers(self):
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")

    def valid_host(self):
        host = self.headers.get('Host', '')
        name = socket.gethostname().lower()
        allowed = {'localhost', '127.0.0.1', name, name + '.local'}
        allowed.update(row['ip'] for row in self.app.get_status().get('addresses', []))
        return host.lower() in {x + ':' + str(self.server.server_port) for x in allowed}

    def logged_in(self):
        return self.app.auth.authenticated(self.headers.get('Cookie'))

    def do_GET(self):
        if not self.valid_host():
            return self.response(403, {'error': 'Use the Pi network address'})
        path = urllib.parse.urlsplit(self.path).path
        if path == '/api/status':
            return self.response(200, self.app.get_status())
        if path == '/api/session':
            return self.response(200, dict(authenticated=self.logged_in(), configured=self.app.auth.configured))
        if path.startswith('/api/'):
            if not self.logged_in():
                return self.response(401, {'error': 'Sign in with the household password'})
            if path == '/api/apps':
                return self.response(200, {'apps': app_selection()})
            if path == '/api/profiles':
                return self.response(200, self.app.profiles.public())
            if path == '/api/spotify/code':
                try:
                    code = player_api('/auth/code') if self.app.profiles.public()['pending'] else None
                except (OSError, ValueError, urllib.error.URLError):
                    code = None
                return self.response(200, {'login': code})
            if path == '/api/jobs':
                return self.response(200, {'job': self.app.jobs.snapshot()})
            if path == '/api/wifi':
                with self.app.cache_lock:
                    return self.response(200, dict(self.app.wifi))
            if path == '/api/bluetooth':
                with self.app.cache_lock:
                    return self.response(200, dict(self.app.bluetooth))
            return self.response(404, {'error': 'Page not found'})
        files = {'/': ('index.html', 'text/html'), '/app.js': ('app.js', 'text/javascript'), '/style.css': ('style.css', 'text/css')}
        if path not in files:
            return self.response(404, {'error': 'Page not found'})
        filename, mime = files[path]
        body = (STATIC / filename).read_bytes()
        self.send_response(200)
        self.send_header('Content-Type', mime + '; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.security_headers()
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        origin = self.headers.get('Origin')
        if not self.valid_host() or (origin is not None and origin != 'http://' + self.headers.get('Host', '')) or self.headers.get('X-Nano-Config') != '1':
            return self.response(403, {'error': 'Use this page to change Pi settings'})
        if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
            return self.response(415, {'error': 'Expected JSON'})
        try:
            length = int(self.headers.get('Content-Length', '-1'))
            if not 0 <= length <= 16384:
                return self.response(413, {'error': 'Request is too large'})
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError('Expected a JSON object')
            path = urllib.parse.urlsplit(self.path).path
            auth = self.app.auth
            if path == '/api/login':
                cookie = auth.login(data.get('password'), self.client_address[0])
                return self.response(200, {'message': 'Signed in'}, cookie)
            if path == '/api/setup':
                with auth.lock:
                    if auth.configured or not auth.setup_token or not hmac.compare_digest(str(data.get('token', '')), auth.setup_token):
                        return self.response(403, {'error': 'Use the private setup link displayed on the Pi'})
                    auth.set_password(data.get('password'))
                    cookie = auth.new_session()
                return self.response(200, {'message': 'Household password set'}, cookie)
            if not self.logged_in():
                return self.response(401, {'error': 'Sign in with the household password'})
            if path == '/api/logout':
                auth.logout(self.headers.get('Cookie'))
                return self.response(200, {'message': 'Signed out'}, '')
            if path == '/api/password':
                with auth.lock:
                    if not auth.check(data.get('current')):
                        raise PermissionError('Incorrect current password')
                    auth.set_password(data.get('password'))
                    cookie = auth.new_session()
                return self.response(200, {'message': 'Password updated. Other sessions signed out.'}, cookie)
            return self.response(202, self.app.action(path, data))
        except PermissionError as error:
            self.response(401, {'error': str(error)})
        except (ValueError, RuntimeError) as error:
            self.response(400, {'error': str(error)[:300]})
        except Exception:
            self.response(500, {'error': 'Pi action failed. Check services and try again.'})


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def get_request(self):
        request, address = super().get_request()
        request.settimeout(10)
        return request, address


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8080)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error('Choose a port between 1024 and 65535')
    application = Application()
    private_json(CONFIG_DIR / 'settings.json', {'port': args.port})
    server = Server(('0.0.0.0', args.port), Handler)
    server.application = application
    if application.auth.setup_token:
        # Capability stays out of journal logs and public status.
        link = config_url() + '/#setup=' + application.auth.setup_token
        path = CONFIG_DIR / 'setup-link.txt'
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w') as stream:
            stream.write(link + '\n')
        path.chmod(0o600)
    threading.Thread(target=application.poll, daemon=True).start()
    print(f'Nano Config listening on port {args.port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        application.stopping.set()
        server.server_close()


if __name__ == '__main__':
    main()
