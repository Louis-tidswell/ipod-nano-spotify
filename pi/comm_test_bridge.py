#!/usr/bin/env python3
"""Pi side of the NanoApps bidirectional SCSI mailbox proof."""

from __future__ import annotations

import argparse
import os
import signal
import struct
import subprocess
import tempfile
import time
from pathlib import Path

PAGE_SIZE = 512
NANO_TO_PI_ADDR = 0x09122000
PI_TO_NANO_ADDR = 0x09122200
NANO_TO_PI_MAGIC = 0x4932504E  # bytes b"NP2I"
PI_TO_NANO_MAGIC = 0x4E324950  # bytes b"PI2N"
PROTOCOL_VERSION = 1
EVENT_HELLO = 0
EVENT_BUTTON = 1
STATUS_ONLINE = 1
CHECKSUM_SALT = 0xA5A55A5A
HEADER = struct.Struct("<7I")


def checksum(words: tuple[int, ...]) -> int:
    value = CHECKSUM_SALT
    for word in words:
        value ^= word
    return value & 0xFFFFFFFF


def address_bytes(address: int) -> list[str]:
    return [f"{(address >> shift) & 0xff:02x}" for shift in (24, 16, 8, 0)]


def find_ipod() -> str:
    result = subprocess.run(
        ["lsblk", "-dno", "NAME,MODEL"],
        check=True,
        text=True,
        capture_output=True,
    )
    for line in result.stdout.splitlines():
        if "ipod" in line.lower():
            return f"/dev/{line.split()[0]}"
    raise RuntimeError("No iPod block device found")


class ScsiMailbox:
    def __init__(self, device: str):
        self.device = device
        self._read_file = tempfile.NamedTemporaryFile(prefix="ipod-read-", delete=False)
        self._write_file = tempfile.NamedTemporaryFile(prefix="ipod-write-", delete=False)
        self._read_path = Path(self._read_file.name)
        self._write_path = Path(self._write_file.name)
        self._read_file.close()
        self._write_file.close()

    def close(self) -> None:
        self._read_path.unlink(missing_ok=True)
        self._write_path.unlink(missing_ok=True)

    def read_page(self, address: int) -> bytes:
        subprocess.run(
            [
                "sg_raw",
                "-o",
                str(self._read_path),
                "-r",
                str(PAGE_SIZE),
                self.device,
                "c6",
                "96",
                "02",
                *address_bytes(address),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        data = self._read_path.read_bytes()
        if len(data) != PAGE_SIZE:
            raise RuntimeError(f"SCSI read returned {len(data)} bytes")
        return data

    def write_page(self, address: int, data: bytes) -> None:
        if len(data) != PAGE_SIZE:
            raise ValueError("mailbox writes must be exactly 512 bytes")
        self._write_path.write_bytes(data)
        subprocess.run(
            [
                "sg_raw",
                "-s",
                str(PAGE_SIZE),
                "-i",
                str(self._write_path),
                self.device,
                "c6",
                "96",
                "01",
                *address_bytes(address),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )


def decode_request(data: bytes) -> tuple[int, int, int] | None:
    magic, version, seq, event, value, nonce, received_sum = HEADER.unpack_from(data)
    words = (magic, version, seq, event, value, nonce)
    if (
        magic != NANO_TO_PI_MAGIC
        or version != PROTOCOL_VERSION
        or received_sum != checksum(words)
    ):
        return None
    return seq, event, nonce


def encode_response(generation: int, ack: int, counter: int, nonce: int) -> bytes:
    words = (PI_TO_NANO_MAGIC, PROTOCOL_VERSION, generation, ack, counter, nonce)
    page = bytearray(PAGE_SIZE)
    HEADER.pack_into(page, 0, *words, checksum(words))
    return bytes(page)


def run(configured_device: str | None, interval: float) -> None:
    mailbox: ScsiMailbox | None = None
    stopping = False
    last_nonce: int | None = None
    last_seq: int | None = None
    ack = 0
    counter = 0
    generation = 0
    announced_waiting = False

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
                    device = configured_device or find_ipod()
                    mailbox = ScsiMailbox(device)
                    print(f"Pi Link Test bridge using {device}", flush=True)
                request = decode_request(mailbox.read_page(NANO_TO_PI_ADDR))
                if request is None:
                    if not announced_waiting:
                        print("Waiting for the Pi Link Test app to open on the Nano...", flush=True)
                        announced_waiting = True
                else:
                    announced_waiting = False
                    seq, event, nonce = request
                    if nonce != last_nonce:
                        last_nonce = nonce
                        last_seq = None
                        ack = 0
                        counter = 0
                        print(f"Nano session {nonce:08x} connected", flush=True)
                    if seq != last_seq:
                        last_seq = seq
                        ack = seq
                        if event == EVENT_BUTTON:
                            counter += 1
                            print(f"Button {seq} received; returning counter {counter}", flush=True)
                        elif event != EVENT_HELLO:
                            print(f"Ignoring unknown event {event} at sequence {seq}", flush=True)

                    generation = (generation + 1) & 0xFFFFFFFF
                    if generation == 0:
                        generation = 1
                    mailbox.write_page(
                        PI_TO_NANO_ADDR,
                        encode_response(generation, ack, counter, nonce),
                    )
            except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
                print(f"SCSI link unavailable: {error}", flush=True)
                if mailbox is not None:
                    mailbox.close()
                    mailbox = None
                time.sleep(1.0)

            elapsed = time.monotonic() - started
            time.sleep(max(0.0, interval - elapsed))
    finally:
        if mailbox is not None:
            mailbox.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", help="iPod device, auto-detected when omitted")
    parser.add_argument("--interval", type=float, default=0.2, help="poll interval in seconds")
    args = parser.parse_args()
    run(args.device, args.interval)


if __name__ == "__main__":
    main()
