#!/usr/bin/env python3
"""Reconnect trusted Bluetooth audio sinks when they become available."""

from __future__ import annotations

import re
import subprocess
import time


DEVICE_RE = re.compile(r"^Device\s+([0-9A-Fa-f:]{17})\s+(.+)$")
AUDIO_SINK_UUID = "0000110b-0000-1000-8000-00805f9b34fb"


def bluetoothctl(*args: str, timeout: int = 12) -> str:
    try:
        result = subprocess.run(
            ["bluetoothctl", *args],
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return result.stdout + result.stderr


def properties(address: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in bluetoothctl("info", address).splitlines():
        line = raw.strip()
        if ":" in line:
            key, value = line.split(":", 1)
            values[key.strip()] = value.strip()
    return values


def trusted_audio_devices() -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for raw in bluetoothctl("devices", "Trusted").splitlines():
        match = DEVICE_RE.match(raw.strip())
        if not match:
            continue
        address, name = match.groups()
        if AUDIO_SINK_UUID in bluetoothctl("info", address).lower():
            found.append((address, name))
    return found


def main() -> int:
    last_state: dict[str, bool] = {}
    retry_after: dict[str, float] = {}
    delay: dict[str, int] = {}

    while True:
        now = time.monotonic()
        for address, name in trusted_audio_devices():
            connected = properties(address).get("Connected", "no").lower() == "yes"
            if last_state.get(address) != connected:
                print(f"{name}: {'connected' if connected else 'disconnected'}", flush=True)
                last_state[address] = connected
            if connected:
                delay[address] = 5
                continue
            if now < retry_after.get(address, 0):
                continue

            output = bluetoothctl("connect", address)
            connected = properties(address).get("Connected", "no").lower() == "yes"
            if connected:
                print(f"{name}: reconnected", flush=True)
                last_state[address] = True
                delay[address] = 5
            else:
                wait = delay.get(address, 5)
                retry_after[address] = time.monotonic() + wait
                delay[address] = min(wait * 2, 30)
                detail = next((line.strip() for line in reversed(output.splitlines()) if line.strip()), "failed")
                print(f"{name}: reconnect failed ({detail}); retrying in {wait}s", flush=True)
        time.sleep(2)


if __name__ == "__main__":
    raise SystemExit(main())
