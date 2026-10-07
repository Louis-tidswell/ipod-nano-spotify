#!/usr/bin/env python3
"""Bridge the Nano Spotify mailbox to go-librespot's localhost API."""

from __future__ import annotations

import argparse
import json
import signal
import struct
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from bluetooth_control import BluetoothControl
from spotify_content import SpotifyContent
from config_network import config_url
from bridge_control import BridgeControl

PAGE_SIZE = 512
NANO_TO_PI_ADDR = 0x09122000
PI_TO_NANO_ADDR = 0x09122200
NANO_TO_PI_MAGIC = 0x4932504E
PI_TO_NANO_MAGIC = 0x4E324950
PROTOCOL_VERSION = 4
CHECKSUM_SALT = 0xA5A55A5A
COMMAND_HEADER = struct.Struct("<7I")
STATUS_HEADER = struct.Struct("<11I")

CMD_HELLO = 0
CMD_PLAY_PAUSE = 1
CMD_PREVIOUS = 2
CMD_NEXT = 3
CMD_VOLUME_DELTA = 4

FLAG_PLAYER_READY = 1 << 0
FLAG_PLAYING = 1 << 1
FLAG_PAUSED = 1 << 2
FLAG_BUFFERING = 1 << 3
FLAG_SPEAKER = 1 << 4
FLAG_AUTHENTICATED = 1 << 5


def checksum(words: tuple[int, ...]) -> int:
    result = CHECKSUM_SALT
    for word in words:
        result ^= word
    return result & 0xFFFFFFFF


def address_bytes(address: int) -> list[str]:
    return [f"{(address >> shift) & 0xff:02x}" for shift in (24, 16, 8, 0)]


def find_ipod() -> str:
    for generic in sorted(Path("/sys/class/scsi_generic").glob("sg*")):
        try:
            if "ipod" in (generic / "device/model").read_text().lower():
                return f"/dev/{generic.name}"
        except OSError:
            pass
    raise RuntimeError("No iPod SCSI device found")


class ScsiMailbox:
    def __init__(self, device: str):
        self.device = device
        read_file = tempfile.NamedTemporaryFile(prefix="spotify-read-", delete=False)
        write_file = tempfile.NamedTemporaryFile(prefix="spotify-write-", delete=False)
        self.read_path, self.write_path = Path(read_file.name), Path(write_file.name)
        read_file.close()
        write_file.close()

    def close(self) -> None:
        self.read_path.unlink(missing_ok=True)
        self.write_path.unlink(missing_ok=True)

    def read(self, address: int) -> bytes:
        subprocess.run(
            ["sg_raw", "-o", str(self.read_path), "-r", str(PAGE_SIZE), self.device,
             "c6", "96", "02", *address_bytes(address)],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )
        data = self.read_path.read_bytes()
        if len(data) != PAGE_SIZE:
            raise RuntimeError(f"SCSI read returned {len(data)} bytes")
        return data

    def write(self, address: int, data: bytes) -> None:
        self.write_path.write_bytes(data)
        subprocess.run(
            ["sg_raw", "-s", str(PAGE_SIZE), "-i", str(self.write_path), self.device,
             "c6", "96", "01", *address_bytes(address)],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )


class PlayerApi:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    def request(self, path: str, payload: dict[str, object] | None = None, timeout: float = 2) -> object | None:
        data = None if payload is None else json.dumps(payload).encode()
        request = urllib.request.Request(
            self.base_url + path,
            data=data,
            headers={"Content-Type": "application/json"},
            method="GET" if payload is None else "POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                if response.status == 204:
                    return None
                body = response.read()
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as error:
            if error.code == 204:
                return None
            raise

    def command(self, command: int, value: int) -> None:
        if command == CMD_PLAY_PAUSE:
            self.request("/player/playpause", {})
        elif command == CMD_PREVIOUS:
            self.request("/player/prev", {})
        elif command == CMD_NEXT:
            self.request("/player/next", {})
        elif command == CMD_VOLUME_DELTA:
            self.request("/player/volume", {"volume": value, "relative": True})


@dataclass
class Snapshot:
    flags: int = 0
    position_ms: int = 0
    duration_ms: int = 0
    volume_percent: int = 0
    error_code: int = 0
    track: str = ""
    artist: str = ""
    message: str = "Waiting for Spotify login"
    username: str = ""
    cover_url: str = ""


def bounded(text: str, size: int) -> bytes:
    raw = text.encode("utf-8", errors="replace")[: size - 1].decode("utf-8", errors="ignore").encode("utf-8")
    return raw + bytes(size - len(raw))


def page_checksum(data: bytes, skip: int) -> int:
    words = struct.unpack("<128I", data)
    return checksum(tuple(word for index, word in enumerate(words) if index != skip))


def decode_command(data: bytes) -> tuple[int, int, int, int, str] | None:
    if len(data) != PAGE_SIZE:
        return None
    magic, version, seq, command, value, nonce, received = COMMAND_HEADER.unpack_from(data)
    if magic != NANO_TO_PI_MAGIC or version != PROTOCOL_VERSION or page_checksum(data, 6) != received:
        return None
    signed_value = value if value < 0x80000000 else value - 0x100000000
    return seq, command, signed_value, nonce, data[28:92].split(b"\0", 1)[0].decode("ascii", errors="replace")


def encode_status(generation: int, ack: int, nonce: int, status: Snapshot,
                  bluetooth: tuple = (0, 0, 0, "", []), library: tuple | None = None,
                  art: tuple[int, bytes, int] | None = None, config: str | None = None) -> bytes:
    words = (PI_TO_NANO_MAGIC, PROTOCOL_VERSION, generation, ack, status.flags,
             status.position_ms, status.duration_ms, status.volume_percent,
             status.error_code, nonce)
    page = bytearray(PAGE_SIZE)
    STATUS_HEADER.pack_into(page, 0, *words, 0)
    page[44:108] = bounded(status.track, 64)
    page[108:172] = bounded(status.artist, 64)
    page[172:236] = bounded(status.message, 64)
    if config is not None:
        struct.pack_into('<5I', page, 236, 4, 0, 0, 0, 0)
        page[256:320] = bounded(config, 64)
    elif art is not None:
        identifier, pixels, index = art
        chunk = pixels[index * 256:(index + 1) * 256]
        struct.pack_into('<5I', page, 236, 3, identifier, len(pixels), index, len(chunk))
        page[256:256 + len(chunk)] = chunk
    else:
        flags, total, index, message, rows = library if library is not None else bluetooth
        struct.pack_into('<5I', page, 236, 2 if library is not None else 1, flags, total, index, len(rows))
        page[256:320] = bounded(message, 64)
        for row, item in enumerate(rows):
            if library is not None:
                struct.pack_into('<40s52sI', page, 320 + row * 96, bounded(item['uri'], 40),
                                 bounded(item['name'], 52), max(0, int(item.get('length', 0))))
            else:
                struct.pack_into('<18sH44s', page, 320 + row * 64, bounded(item['address'], 18),
                                 item['flags'], bounded(item['name'], 44))
    struct.pack_into('<I', page, 40, page_checksum(bytes(page), 10))
    return bytes(page)


def get_snapshot(api: PlayerApi) -> Snapshot:
    snapshot = Snapshot()
    try:
        root = api.request("/")
        if isinstance(root, dict) and root.get("playback_ready"):
            snapshot.flags |= FLAG_PLAYER_READY
        status = api.request("/status")
        if isinstance(status, dict) and status.get('username'):
            snapshot.flags |= FLAG_AUTHENTICATED
            snapshot.username = str(status.get("username") or "")
            if status.get("buffering"):
                snapshot.flags |= FLAG_BUFFERING
            elif status.get("paused"):
                snapshot.flags |= FLAG_PAUSED
            elif not status.get("stopped"):
                snapshot.flags |= FLAG_PLAYING
            steps = int(status.get("volume_steps") or 0)
            snapshot.volume_percent = round(int(status.get("volume") or 0) * 100 / steps) if steps else 0
            track = status.get("track")
            if isinstance(track, dict):
                snapshot.cover_url = str(track.get("album_cover_url") or "")
                snapshot.track = str(track.get("name") or "")
                artists = track.get("artist_names") or []
                snapshot.artist = ", ".join(str(artist) for artist in artists)
                snapshot.position_ms = max(0, int(track.get("position") or 0))
                snapshot.duration_ms = max(0, int(track.get("duration") or 0))
            snapshot.message = "Playing" if snapshot.flags & FLAG_PLAYING else "Spotify ready"
        elif snapshot.flags & FLAG_PLAYER_READY:
            snapshot.message = "Select iPod Nano in Spotify"
    except (OSError, ValueError, urllib.error.URLError):
        snapshot.message = "go-librespot offline"
        snapshot.error_code = 1
    return snapshot


def run(device: str | None, api_url: str, interval: float) -> None:
    stopping = False
    mailbox: ScsiMailbox | None = None
    api = PlayerApi(api_url)
    bluetooth = BluetoothControl()
    bluetooth.start()
    control = BridgeControl(bluetooth)
    control.start()
    content = SpotifyContent(api)
    content.start()
    view = 0
    art_index = 0
    art_ack = 0
    art_previous = 0
    last_seq: int | None = None
    last_nonce: int | None = None
    ack = 0
    generation = 0
    snapshot = Snapshot()
    next_refresh = 0.0
    network_url = 'Checking Pi network...'
    next_network_refresh = 0.0

    def stop(_signum: int, _frame: object) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    try:
        while not stopping:
            started = time.monotonic()
            if started >= next_network_refresh:
                network_url = config_url()
                next_network_refresh = started + 5
            try:
                if mailbox is None:
                    mailbox = ScsiMailbox(device or find_ipod())
                    print(f"Spotify bridge using {mailbox.device}", flush=True)
                request = decode_command(mailbox.read(NANO_TO_PI_ADDR))
                if request:
                    seq, command, value, nonce, address = request
                    bluetooth.last_seen = time.monotonic()
                    if nonce != last_nonce:
                        last_nonce, last_seq, ack = nonce, None, 0
                        view, art_ack, art_index = 0, 0, 0
                        print(f"Nano Spotify session {nonce:08x} connected", flush=True)
                    if seq != last_seq:
                        last_seq, ack = seq, seq
                        if command != CMD_HELLO:
                            try:
                                if command == 30:
                                    view = max(0, min(2, value))
                                elif command == 31:
                                    art_ack = value & 0xffffffff
                                elif 20 <= command <= 22:
                                    content.submit(command, value, address)
                                elif 10 <= command <= 14:
                                    bluetooth.submit(command, value, address)
                                else:
                                    api.command(command, value)
                                print(f"command={command} value={value}", flush=True)
                            except (OSError, ValueError, urllib.error.URLError) as error:
                                print(f"player command failed: {error}", flush=True)
                        next_refresh = 0.0
                    now = time.monotonic()
                    if now >= next_refresh:
                        snapshot = get_snapshot(api)
                        content.observe(snapshot.username, snapshot.cover_url)
                        next_refresh = now + 0.75
                    generation = (generation + 1) & 0xFFFFFFFF or 1
                    bt = bluetooth.snapshot()
                    snapshot.flags &= ~FLAG_SPEAKER
                    if bt[0] & 16:
                        snapshot.flags |= FLAG_SPEAKER
                    art_id, pixels = content.cover()
                    if art_id != art_previous:
                        art_index, art_previous = 0, art_id
                    if view == 1:
                        page = encode_status(generation, ack, nonce, snapshot, bt, library=content.snapshot())
                    elif view == 0:
                        page = encode_status(generation, ack, nonce, snapshot, bt, art=(art_id, pixels, art_index))
                        if pixels and art_ack != art_id:
                            art_index = (art_index + 1) % ((len(pixels) + 255) // 256)
                    elif generation % 2:
                        page = encode_status(generation, ack, nonce, snapshot, bt, config=network_url)
                    else:
                        page = encode_status(generation, ack, nonce, snapshot, bt)
                    mailbox.write(PI_TO_NANO_ADDR, page)
            except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
                print(f"Nano link unavailable: {error}", flush=True)
                if mailbox:
                    mailbox.close()
                    mailbox = None
                time.sleep(1)
            delay = 0.05 if view == 0 and content.cover()[0] != art_ack else interval
            time.sleep(max(0.0, delay - (time.monotonic() - started)))
    finally:
        control.close()
        bluetooth.close()
        content.close()
        if mailbox:
            mailbox.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device")
    parser.add_argument("--api-url", default="http://127.0.0.1:3678")
    parser.add_argument("--interval", type=float, default=0.2)
    args = parser.parse_args()
    run(args.device, args.api_url, args.interval)


if __name__ == "__main__":
    main()
