#!/usr/bin/env python3
"""Register installed Nano apps once during Pi startup, if USB is connected."""
from __future__ import annotations
import argparse
import fcntl
import os
import subprocess
import sys
import time
from pathlib import Path

from spotify_bridge import find_ipod

ROOT = Path(__file__).resolve().parents[1]
STATE = Path.home() / '.local/state/ipod-nano/boot-reload'
BOOT_ID = Path('/proc/sys/kernel/random/boot_id')
UPTIME = Path('/proc/uptime')
STARTUP_WINDOW = 120
USB_WAIT = 20


def run(state=STATE, boot_id_path=BOOT_ID, uptime_path=UPTIME,
        find_device=find_ipod, execute=subprocess.run, clock=time.monotonic, sleep=time.sleep,
        usb_wait=USB_WAIT):
    """The persistent boot-ID guard survives user-service restarts and logouts."""
    boot_id = boot_id_path.read_text().strip()
    state.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(state, os.O_RDWR | os.O_CREAT, 0o600)
    with os.fdopen(fd, 'r+') as record:
        fcntl.flock(record, fcntl.LOCK_EX)
        if record.read().strip() == boot_id:
            print('Nano boot reload already handled for this Pi boot', flush=True)
            return 0
        # Mark the attempt before doing USB work: no repeated reloads this boot,
        # including when the Nano is absent or the initial reload fails.
        record.seek(0)
        record.write(boot_id + '\n')
        record.truncate()
        record.flush()
        os.fsync(record.fileno())
        uptime = float(uptime_path.read_text().split()[0])
        if uptime > STARTUP_WINDOW:
            print('Outside Pi startup; Nano will reload on the next Pi boot', flush=True)
            return 0
        deadline = clock() + min(usb_wait, STARTUP_WINDOW - uptime)
        while True:
            try:
                device = find_device()
                break
            except (OSError, RuntimeError):
                remaining = deadline - clock()
                if remaining <= 0:
                    print('No Nano connected at startup; skipping app reload', flush=True)
                    return 0
                sleep(min(0.5, remaining))
        # The unit starts before the bridge and config page. Avoid a manual start
        # racing an already-running bridge or its Bluetooth/USB controls.
        bridge = execute(['systemctl', '--user', 'is-active', '--quiet', 'ipod-spotify-bridge.service'],
                         check=False, timeout=5)
        if bridge.returncode == 0:
            print('Bridge already running; skipping startup-only Nano reload', flush=True)
            return 0
        print(f'Nano connected ({device}); reloading its installed apps', flush=True)
        result = execute([sys.executable, '-u', str(ROOT / 'pi/install_nanoapps.py'), '--managed', '--reload-only'],
                         cwd=ROOT, check=False, timeout=60)
        if result.returncode:
            print('Startup reload failed; use Reload installed apps on the config page to retry', flush=True)
        return result.returncode


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    try:
        return run()
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f'Nano boot reload failed: {type(error).__name__}. Pi services will still start.', flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
