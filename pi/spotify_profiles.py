"""Saved go-librespot credentials; only public profile metadata reaches HTTP."""
from __future__ import annotations
import json
import os
import threading
import time
import uuid
from pathlib import Path


def private_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix('.tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(value, stream)
        stream.write('\n')
    temporary.chmod(0o600)
    temporary.replace(path)


class SpotifyProfiles:
    def __init__(self, directory: Path, player_directory: Path, service, api):
        self.directory = directory
        self.state_path = player_directory / 'state.json'
        self.legacy_path = player_directory / 'credentials.json'
        self.index_path = directory / 'profiles.json'
        self.service = service
        self.api = api
        self.lock = threading.RLock()
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        directory.chmod(0o700)
        try:
            self.index = json.loads(self.index_path.read_text())
        except FileNotFoundError:
            self.index = {'active': None, 'pending': None, 'profiles': []}
            self._import_current()

    def _state(self):
        try:
            state = json.loads(self.state_path.read_text())
        except FileNotFoundError:
            state = {}
        if not state.get('credentials', {}).get('data') and self.legacy_path.exists():
            state['credentials'] = json.loads(self.legacy_path.read_text())
        return state

    def _save(self):
        private_json(self.index_path, self.index)

    def _credential_path(self, identifier):
        # Identifier is looked up before use; never accept a browser-supplied path.
        return self.directory / (identifier + '.json')

    def _import_current(self):
        state = self._state()
        credentials = state.get('credentials', {})
        if credentials.get('username') and credentials.get('data'):
            identifier = uuid.uuid4().hex
            self.index['profiles'].append({'id': identifier, 'name': 'Current account',
                                           'username': credentials['username']})
            self.index['active'] = identifier
            private_json(self._credential_path(identifier), credentials)
        self._save()

    def _find(self, identifier):
        return next((p for p in self.index['profiles'] if p['id'] == identifier), None)

    def _capture_active(self):
        active = self._find(self.index['active'])
        credentials = self._state().get('credentials', {})
        if active and credentials.get('username') == active['username'] and credentials.get('data'):
            private_json(self._credential_path(active['id']), credentials)

    def _write_credentials(self, credentials):
        state = self._state()
        state['credentials'] = credentials
        # A playback session belongs to the account, unlike device ID and volume.
        state.pop('event_manager', None)
        private_json(self.state_path, state)
        self.legacy_path.unlink(missing_ok=True)

    def public(self):
        with self.lock:
            pending = self.index.get('pending')
            return {'active': self.index['active'], 'profiles': [dict(p) for p in self.index['profiles']],
                    'pending': bool(pending), 'pending_name': pending['name'] if pending else ''}

    def activate(self, identifier):
        with self.lock:
            profile = self._find(identifier)
            if profile is None:
                raise ValueError('Unknown Spotify profile')
            credentials = json.loads(self._credential_path(identifier).read_text())
            previous = self._state().get('credentials', {})
            old_index = dict(self.index)
            self.service('stop')
            try:
                if not self.index.get('pending'):
                    self._capture_active()
                self._write_credentials(credentials)
                self.service('start')
            except Exception:
                self._write_credentials(previous)
                self.index = old_index
                self.service('start')
                raise
            self.index.update(active=identifier, pending=None)
            self._save()

    def begin(self, name):
        name = str(name).strip()
        if not name or len(name) > 40:
            raise ValueError('Use a profile name between 1 and 40 characters')
        with self.lock:
            if self.index.get('pending'):
                raise ValueError('Finish or cancel the current login first')
            self.service('stop')
            self._capture_active()
            previous = self._state().get('credentials', {})
            try:
                self._write_credentials({})
                self.index['pending'] = {'name': name, 'started': time.time()}
                self._save()  # Survives a web-service restart during Spotify login.
                self.service('start')
            except Exception:
                self._write_credentials(previous)
                self.index['pending'] = None
                self._save()
                self.service('start')
                raise

    def finish_if_ready(self):
        with self.lock:
            pending = self.index.get('pending')
            if not pending:
                return
            status = self.api('/status')
            if not isinstance(status, dict) or not status.get('username'):
                return
            credentials = self._state().get('credentials', {})
            username = status['username']
            if credentials.get('username') != username or not credentials.get('data'):
                return
            profile = next((p for p in self.index['profiles'] if p['username'] == username), None)
            if not profile:
                profile = {'id': uuid.uuid4().hex, 'name': pending['name'], 'username': username}
                self.index['profiles'].append(profile)
            private_json(self._credential_path(profile['id']), credentials)
            self.index.update(active=profile['id'], pending=None)
            self._save()

    def cancel(self):
        with self.lock:
            if not self.index.get('pending'):
                return
            if self.index['active']:
                self.activate(self.index['active'])
            else:
                self.service('stop')
                self._write_credentials({})
                self.index['pending'] = None
                self._save()

    def delete(self, identifier):
        with self.lock:
            profile = self._find(identifier)
            if not profile:
                raise ValueError('Unknown Spotify profile')
            if self.index.get('pending'):
                raise ValueError('Finish or cancel the current login first')
            if self.index['active'] == identifier:
                self.service('stop')
                self._write_credentials({})
                self.index['active'] = None
            self.index['profiles'].remove(profile)
            self._save()
            self._credential_path(identifier).unlink(missing_ok=True)

    def rename(self, identifier, name):
        with self.lock:
            profile = self._find(identifier)
            if not profile or not isinstance(name, str) or not 1 <= len(name.strip()) <= 40:
                raise ValueError('Choose a profile and a name between 1 and 40 characters')
            profile['name'] = name.strip()
            self._save()
