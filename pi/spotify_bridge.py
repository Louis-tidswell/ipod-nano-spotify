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

PAGE_SIZE = 512
NANO_TO_PI_ADDR = 0x09122000
PI_TO_NANO_ADDR = 0x09122200
NANO_TO_PI_MAGIC = 0x4932504E
PI_TO_NANO_MAGIC = 0x4E324950
PROTOCOL_VERSION = 3
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

    def request(self, path: str, payload: dict[str, object] | None = None) -> object | None:
        data = None if payload is None else json.dumps(payload).encode()
        request = urllib.request.Request(
            self.base_url + path,
            data=data,
            headers={"Content-Type": "application/json"},
            method="GET" if payload is None else "POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=2) as response:
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
    return seq, command, signed_value, nonce, data[28:46].split(b"\0", 1)[0].decode("ascii", errors="replace")


def encode_status(generation: int, ack: int, nonce: int, status: Snapshot, bluetooth: tuple = (0, 0, 0, "", [])) -> bytes:
    words = (
        PI_TO_NANO_MAGIC, PROTOCOL_VERSION, generation, ack, status.flags,
        status.position_ms, status.duration_ms, status.volume_percent,
        status.error_code, nonce,
    )
    page = bytearray(PAGE_SIZE)
    STATUS_HEADER.pack_into(page, 0, *words, checksum(words))
    page[44:140] = bounded(status.track, 96)
    page[140:236] = bounded(status.artist, 96)
    page[236:300] = bounded(status.message, 64)
    bt_flags, total, index, message, devices = bluetooth
    struct.pack_into("<4I", page, 300, bt_flags, total, index, len(devices))
    page[316:356] = bounded(message, 40)
    for row, device in enumerate(devices):
        struct.pack_into("<18sH32s", page, 356 + row * 52,
                         bounded(device["address"], 18), device["flags"], bounded(device["name"], 32))
    struct.pack_into("<I", page, 40, page_checksum(bytes(page), 10))
    return bytes(page)


def get_snapshot(api: PlayerApi) -> Snapshot:
    snapshot = Snapshot()
    try:
        root = api.request("/")
        if isinstance(root, dict) and root.get("playback_ready"):
            snapshot.flags |= FLAG_PLAYER_READY
        status = api.request("/status")
        if isinstance(status, dict):
            snapshot.flags |= FLAG_AUTHENTICATED
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
    last_seq: int | None = None
    last_nonce: int | None = None
    ack = 0
    generation = 0
    snapshot = Snapshot()
    next_refresh = 0.0

    def stop(_signum: int, _frame: object) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    try:
        while not stopping:
            started = time.monotonic()
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
                        print(f"Nano Spotify session {nonce:08x} connected", flush=True)
                    if seq != last_seq:
                        last_seq, ack = seq, seq
                        if command != CMD_HELLO:
                            try:
                                if 10 <= command <= 14:
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
                        next_refresh = now + 0.75
                    generation = (generation + 1) & 0xFFFFFFFF or 1
                    bt = bluetooth.snapshot()
                    snapshot.flags &= ~FLAG_SPEAKER
                    if bt[0] & 16:
                        snapshot.flags |= FLAG_SPEAKER
                    mailbox.write(PI_TO_NANO_ADDR, encode_status(generation, ack, nonce, snapshot, bt))
            except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
                print(f"Nano link unavailable: {error}", flush=True)
                if mailbox:
                    mailbox.close()
                    mailbox = None
                time.sleep(1)
            time.sleep(max(0.0, interval - (time.monotonic() - started)))
    finally:
        bluetooth.close()
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
