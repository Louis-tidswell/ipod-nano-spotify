"""Adapter-scoped Bluetooth audio control, off the USB mailbox thread."""
from __future__ import annotations

import json
import queue
import re
import subprocess
import threading
import time
from pathlib import Path

import dbus

SINK_UUID = "0000110b-0000-1000-8000-00805f9b34fb"
ADDRESS = re.compile(r"[0-9A-F]{2}(?::[0-9A-F]{2}){5}\Z")
CONFIG = Path.home() / ".config/nanoapps/bluetooth.json"


def run(*args: str, timeout: int = 25) -> str:
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "Command failed")
    return result.stdout


def objects() -> dict:
    bus = dbus.SystemBus(private=True)
    try:
        manager = dbus.Interface(bus.get_object("org.bluez", "/"), "org.freedesktop.DBus.ObjectManager")
        return manager.GetManagedObjects(timeout=3)
    finally:
        bus.close()


def call(path: str, interface: str, method: str, *args: str) -> None:
    run("busctl", "--system", "--timeout=20", "call", "org.bluez", path, interface, method, *args)


def set_property(path: str, interface: str, name: str, value: bool) -> None:
    call(path, "org.freedesktop.DBus.Properties", "Set", "ssv", interface, name, "b", str(value).lower())


def audio_nodes() -> list[dict]:
    return json.loads(run("pw-dump", timeout=3))


def sink_for(nodes: list[dict], address: str) -> dict | None:
    for node in nodes:
        props = node.get("info", {}).get("props", {})
        if props.get("media.class") == "Audio/Sink" and (
            props.get("api.bluez5.address") == address
            or address.replace(":", "_") in props.get("node.name", "")
        ):
            return node
    return None


def route_audio(address: str) -> None:
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        nodes = audio_nodes()
        sink = sink_for(nodes, address)
        if sink:
            run("wpctl", "set-default", str(sink["id"]), timeout=3)
            name = sink["info"]["props"]["node.name"]
            for node in nodes:
                props = node.get("info", {}).get("props", {})
                if props.get("media.class") == "Stream/Output/Audio":
                    run("pw-metadata", "-n", "default", str(node["id"]), "target.object", "Spa:String", name, timeout=3)
            return
        time.sleep(0.3)
    raise RuntimeError("Connected, but no audio output")


class BluetoothControl:
    def __init__(self, config: Path = CONFIG):
        self.config = config
        try:
            self.preferences = json.loads(config.read_text())
        except (OSError, ValueError):
            self.preferences = {}
        self.adapter = ""
        self.adapter_address = ""
        self.devices: list[dict] = []
        self.page = 0
        self.flags = 0
        self.message = "Checking Bluetooth..."
        self.lock = threading.Lock()
        self.jobs: queue.Queue = queue.Queue(maxsize=1)
        self.stop = threading.Event()
        self.scanner: subprocess.Popen | None = None
        self.scan_until = 0.0
        self.last_seen = time.monotonic()
        self.retry_after = 0.0
        self.retry_delay = 5
        self.thread = threading.Thread(target=self.worker, daemon=True)

    def save(self) -> None:
        self.config.parent.mkdir(parents=True, exist_ok=True)
        temp = self.config.with_suffix(".tmp")
        temp.write_text(json.dumps(self.preferences) + "\n")
        temp.replace(self.config)

    def refresh(self) -> None:
        tree = objects()
        adapters = [(path, entry["org.bluez.Adapter1"]) for path, entry in tree.items()
                    if "org.bluez.Adapter1" in entry]
        chosen = next(((p, a) for p, a in adapters if a["Address"] == self.preferences.get("adapter")), None)
        if chosen is None and not self.preferences.get("adapter"):
            chosen = next(((p, a) for p, a in adapters if "/usb" in str(
                (Path("/sys/class/bluetooth") / p.rsplit("/", 1)[-1]).resolve())), None)
        if chosen is None:
            self.adapter = ""
            with self.lock:
                self.flags &= ~1
                self.devices = []
            raise RuntimeError("USB Bluetooth adapter unavailable")
        self.adapter, props = chosen
        self.adapter_address = props["Address"]
        if not self.preferences.get("adapter"):
            self.preferences["adapter"] = self.adapter_address
            self.save()
        nodes = audio_nodes()
        devices = []
        for path, entry in tree.items():
            device = entry.get("org.bluez.Device1", {})
            if device.get("Adapter") != self.adapter or device.get("Blocked"):
                continue
            audio = SINK_UUID in device.get("UUIDs", []) or device.get("Icon", "").startswith("audio-")
            # During discovery, class/service information may arrive after the name.
            if not audio and (device.get("Paired") or "RSSI" not in device):
                continue
            address = device["Address"]
            paired = device.get("Paired") and device.get("Bonded")
            connected = device.get("Connected", False)
            output = connected and sink_for(nodes, address) is not None
            devices.append(dict(address=address, name=device.get("Alias", address), path=path,
                                flags=int(bool(paired)) | (2 if connected else 0) | (4 if output else 0)))
        devices.sort(key=lambda d: (not bool(d["flags"] & 2), not bool(d["flags"] & 1), d["name"].casefold(), d["address"]))
        with self.lock:
            self.devices = devices
            self.page = min(self.page, max(0, (len(devices) - 1) // 3))
            self.flags = (self.flags & ~17) | int(bool(props.get("Powered"))) | (16 if any(d["flags"] & 4 for d in devices) else 0)
        if "preferred" not in self.preferences:
            current = next((d for d in devices if d["flags"] & 4), None)
            if current:
                self.preferences.update(preferred=current["address"], reconnect=True)
                self.save()

    def snapshot(self) -> tuple[int, int, int, str, list[dict]]:
        with self.lock:
            return self.flags, len(self.devices), self.page, self.message, list(self.devices[self.page * 3:self.page * 3 + 3])

    def status(self, message: str, error: bool = False) -> None:
        with self.lock:
            self.message = message
            self.flags = (self.flags & ~8) | (8 if error else 0)

    def submit(self, command: int, value: int, address: str) -> None:
        self.last_seen = time.monotonic()
        if command == 10:
            with self.lock:
                self.page = max(0, min(value, max(0, (len(self.devices) - 1) // 3)))
            return
        with self.lock:
            if self.flags & 4:
                return
            self.flags |= 4
            self.jobs.put_nowait((command, address))

    def stop_scan(self) -> None:
        if self.scanner:
            self.scanner.terminate()
            try:
                self.scanner.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.scanner.kill()
                self.scanner.wait()
            self.scanner.stdin.close()
            self.scanner = None
        with self.lock:
            self.flags &= ~2

    def session(self, *commands: str) -> subprocess.Popen:
        process = subprocess.Popen(["bluetoothctl", "--agent", "NoInputNoOutput"], stdin=subprocess.PIPE,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True)
        process.stdin.write("select " + self.adapter_address + "\n" + "\n".join(commands) + "\n")
        process.stdin.flush()
        return process

    def pair(self, device: dict) -> None:
        self.status("Pairing " + device["name"])
        process = self.session("pair " + device["address"])
        try:
            deadline = time.monotonic() + 35
            while not self.stop.is_set() and time.monotonic() < deadline:
                props = objects().get(device["path"], {}).get("org.bluez.Device1", {})
                if props.get("Paired") and props.get("Bonded"):
                    set_property(device["path"], "org.bluez.Device1", "Trusted", True)
                    return
                time.sleep(0.4)
            try:
                call(device["path"], "org.bluez.Device1", "CancelPairing")
            except RuntimeError:
                pass
            raise RuntimeError("Pair failed: use speaker pairing mode")
        finally:
            process.terminate()
            process.wait(timeout=2)
            process.stdin.close()

    def connect(self, device: dict) -> None:
        # Persist selection before disconnecting anything, so reconnect cannot fight it.
        self.preferences.update(preferred=device["address"], reconnect=True)
        self.save()
        for other in self.devices:
            if other["address"] != device["address"] and other["flags"] & 2:
                call(other["path"], "org.bluez.Device1", "Disconnect")
        if not device["flags"] & 1:
            self.pair(device)
        self.status("Connecting " + device["name"])
        call(device["path"], "org.bluez.Device1", "Connect")
        route_audio(device["address"])
        self.stop_scan()
        self.retry_delay = 5
        self.status("Audio ready: " + device["name"])

    def action(self, command: int, address: str) -> None:
        self.refresh()
        if command == 11:
            if self.scanner:
                self.stop_scan()
                self.status("Scan stopped")
            else:
                set_property(self.adapter, "org.bluez.Adapter1", "Powered", True)
                self.scanner = self.session("scan on")
                self.scan_until = time.monotonic() + 60
                with self.lock:
                    self.flags |= 2
                self.status("Scanning: use speaker pairing mode")
            return
        if not ADDRESS.fullmatch(address):
            raise RuntimeError("Invalid device address")
        device = next((d for d in self.devices if d["address"] == address), None)
        if not device:
            raise RuntimeError("Device gone: scan again")
        if command == 12:
            set_property(self.adapter, "org.bluez.Adapter1", "Powered", True)
            self.connect(device)
        elif command in (13, 14):
            if self.preferences.get("preferred") == address:
                self.preferences["reconnect"] = False
                self.save()
            if command == 13:
                call(device["path"], "org.bluez.Device1", "Disconnect")
                self.status("Disconnected " + device["name"])
            else:
                call(self.adapter, "org.bluez.Adapter1", "RemoveDevice", "o", device["path"])
                self.status("Forgot " + device["name"])
        else:
            raise RuntimeError("Unknown Bluetooth command")

    def worker(self) -> None:
        self.status("Choose a speaker or Scan")
        while not self.stop.is_set():
            job = None
            try:
                try:
                    job = self.jobs.get(timeout=1)
                except queue.Empty:
                    pass
                if job:
                    self.action(*job)
                if self.scanner and (time.monotonic() > self.scan_until or
                                     time.monotonic() - self.last_seen > 4 or self.scanner.poll() is not None):
                    self.stop_scan()
                    self.status("Scan finished")
                self.refresh()
                now = time.monotonic()
                if not job and not self.scanner and self.preferences.get("reconnect") and now >= self.retry_after:
                    device = next((d for d in self.devices if d["address"] == self.preferences.get("preferred")
                                   and d["flags"] & 1), None)
                    if device and not device["flags"] & 2:
                        with self.lock:
                            if self.flags & 4:
                                continue
                            self.flags |= 4
                        self.retry_after = now + self.retry_delay + 25
                        self.retry_delay = min(30, self.retry_delay * 2)
                        self.connect(device)
            except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError, dbus.DBusException) as error:
                self.status(str(error).splitlines()[-1][:80], error=True)
                self.retry_after = time.monotonic() + self.retry_delay
            finally:
                with self.lock:
                    if self.jobs.empty():
                        self.flags &= ~4
        self.stop_scan()

    def start(self) -> None:
        self.thread.start()

    def close(self) -> None:
        self.stop.set()
        self.thread.join(timeout=40)
