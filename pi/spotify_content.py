"""Playlist browsing and album-art download without blocking the Nano link."""
from __future__ import annotations

import io
import queue
import re
import struct
import threading
import urllib.parse
import urllib.request
import zlib
from PIL import Image, ImageOps

ART_SIDE = 96
ART_BYTES = ART_SIDE * ART_SIDE * 2
PLAYLIST_URI = re.compile(r'spotify:playlist:[A-Za-z0-9]{22}\Z')


def image_rgb565(raw: bytes) -> bytes:
    with Image.open(io.BytesIO(raw)) as image:
        image = ImageOps.fit(image.convert('RGB'), (ART_SIDE, ART_SIDE), method=Image.Resampling.LANCZOS)
        return b''.join(struct.pack('<H', ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)) for r, g, b in image.getdata())


class SpotifyContent:
    def __init__(self, api):
        self.api = api
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.jobs = queue.Queue(maxsize=1)
        self.art_jobs = queue.Queue(maxsize=1)
        self.account = ''
        self.epoch = 0
        self.request_id = 0
        self.page = 0
        self.total = 0
        self.items = []
        self.flags = 0
        self.message = 'Open Library to load playlists'
        self.cover_url = None
        self.art_id = 0
        self.art = b''
        self.threads = [threading.Thread(target=self.library_worker, daemon=True),
                        threading.Thread(target=self.art_worker, daemon=True)]

    def start(self):
        for thread in self.threads:
            thread.start()

    def close(self):
        self.stop.set()
        for thread in self.threads:
            thread.join(timeout=35)

    @staticmethod
    def latest(jobs, value):
        try:
            jobs.get_nowait()
        except queue.Empty:
            pass
        jobs.put_nowait(value)

    def observe(self, account: str, url: str):
        with self.lock:
            if account != self.account:
                self.account = account
                self.epoch += 1
                self.items, self.total, self.page, self.flags = [], 0, 0, 0
                self.message = 'Open Library to load playlists'
            if url != self.cover_url:
                self.cover_url = url
                self.art_id, self.art = 0, b''
                self.latest(self.art_jobs, url)

    def submit(self, command: int, value: int, uri: str = ''):
        with self.lock:
            if command == 21:
                if not PLAYLIST_URI.fullmatch(uri) or not any(item['uri'] == uri for item in self.items):
                    self.message, self.flags = 'Playlist changed; refresh Library', 4
                    return
            else:
                self.page = max(0, value)
                self.items = []
            self.request_id += 1
            self.flags = 1
            self.message = 'Starting playlist...' if command == 21 else 'Loading your playlists...'
            self.latest(self.jobs, (command, self.page, uri, self.epoch, self.request_id))

    def snapshot(self):
        with self.lock:
            return self.flags, self.total, self.page, self.message, list(self.items)

    def cover(self):
        with self.lock:
            return self.art_id, self.art

    def library_worker(self):
        while not self.stop.is_set():
            try:
                command, page, uri, epoch, request_id = self.jobs.get(timeout=.5)
            except queue.Empty:
                continue
            try:
                if command == 21:
                    self.api.request('/player/play', {'uri': uri}, timeout=25)
                    result = None
                else:
                    result = self.api.request(f'/library/playlists?offset={page * 2}&limit=2', timeout=30)
                    if not isinstance(result, dict):
                        raise RuntimeError('Spotify login unavailable')
                    # Clamp a stale final page when playlists have been removed.
                    total = max(0, int(result['total']))
                    last = max(0, (total - 1) // 2)
                    if page > last:
                        page = last
                        result = self.api.request(f'/library/playlists?offset={page * 2}&limit=2', timeout=30)
                with self.lock:
                    if epoch != self.epoch or request_id != self.request_id:
                        continue
                    if command == 21:
                        self.flags, self.message = 2, 'Playlist started; open Now'
                    else:
                        self.total, self.page = int(result['total']), page
                        self.items = [item for item in result['items'][:2] if PLAYLIST_URI.fullmatch(item.get('uri', ''))]
                        self.flags = 2
                        self.message = 'Choose a playlist' if self.total else 'No saved playlists in this account'
            except Exception as error:
                with self.lock:
                    if epoch == self.epoch and request_id == self.request_id:
                        self.flags, self.message = 4, 'Library: ' + str(error)

    def art_worker(self):
        while not self.stop.is_set():
            try:
                url = self.art_jobs.get(timeout=.5)
            except queue.Empty:
                continue
            if not url:
                continue
            try:
                parsed = urllib.parse.urlparse(url)
                if parsed.scheme != 'https' or not (parsed.hostname or '').endswith('.scdn.co'):
                    raise ValueError('Unexpected cover URL')
                with urllib.request.urlopen(url, timeout=10) as response:
                    raw = response.read(2_000_001)
                if len(raw) > 2_000_000:
                    raise ValueError('Cover image too large')
                pixels = image_rgb565(raw)
                with self.lock:
                    if url == self.cover_url:
                        self.art_id = zlib.crc32(pixels) or 1
                        self.art = pixels
            except Exception as error:
                print('Cover art unavailable:', type(error).__name__, flush=True)
