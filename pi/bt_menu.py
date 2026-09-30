#!/usr/bin/env python3
"""Simple curses Bluetooth control panel for Raspberry Pi testing."""

from __future__ import annotations

import argparse
import curses
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path


ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
DEVICE_RE = re.compile(r"^Device\s+([0-9A-Fa-f:]{17})\s*(.*)$")


@dataclass
class Result:
    ok: bool
    output: str


@dataclass
class Device:
    address: str
    name: str
    properties: dict[str, str] = field(default_factory=dict)

    def yes(self, key: str) -> bool:
        return self.properties.get(key, "no").lower() == "yes"


def clean(text: str) -> str:
    return ANSI_RE.sub("", text).replace("\r", "").strip()


def ctl(*args: str, timeout: int = 1, agent: bool = False) -> Result:
    command = ["bluetoothctl"]
    if agent:
        command += ["--agent", "NoInputNoOutput"]
    command += ["--timeout", str(timeout), *args]
    try:
        completed = subprocess.run(
            command,
            text=True,
            capture_output=True,
            timeout=timeout + 3,
        )
    except subprocess.TimeoutExpired:
        return Result(False, f"Command timed out: {' '.join(args)}")
    except OSError as error:
        return Result(False, str(error))

    output = clean("\n".join(part for part in (completed.stdout, completed.stderr) if part))
    failed_words = ("failed", "not available", "not ready", "no default controller")
    ok = completed.returncode == 0 and not any(word in output.lower() for word in failed_words)
    return Result(ok, output or ("Done" if ok else "Command failed"))


def controller_info() -> dict[str, str]:
    result = ctl("show")
    values: dict[str, str] = {}
    for raw in result.output.splitlines():
        line = raw.strip()
        if line.startswith("Controller "):
            parts = line.split()
            if len(parts) >= 2:
                values["Address"] = parts[1]
        elif ":" in line:
            key, value = line.split(":", 1)
            values[key.strip()] = value.strip()
    if not result.ok:
        values["Error"] = result.output
    return values


def device_info(address: str) -> dict[str, str]:
    result = ctl("info", address)
    values: dict[str, str] = {}
    for raw in result.output.splitlines():
        line = raw.strip()
        if line.startswith("Device "):
            parts = line.split(maxsplit=2)
            if len(parts) == 3:
                values.setdefault("Name", parts[2])
        elif ":" in line:
            key, value = line.split(":", 1)
            values[key.strip()] = value.strip()
    if not result.ok:
        values["Error"] = result.output
    return values


def devices() -> list[Device]:
    result = ctl("devices")
    found: list[Device] = []
    for raw in result.output.splitlines():
        match = DEVICE_RE.match(raw.strip())
        if not match:
            continue
        address, listed_name = match.groups()
        found.append(Device(address.upper(), listed_name or "Unknown device"))

    # Asking BlueZ for filtered device sets is much faster than running `info`
    # once per discovered device, especially in a busy radio environment.
    by_address = {device.address: device for device in found}
    for property_name in ("Connected", "Paired", "Trusted", "Blocked"):
        filtered = ctl("devices", property_name, timeout=1)
        for raw in filtered.output.splitlines():
            match = DEVICE_RE.match(raw.strip())
            if match and match.group(1).upper() in by_address:
                by_address[match.group(1).upper()].properties[property_name] = "yes"
    return sorted(
        found,
        key=lambda item: (
            not item.yes("Connected"),
            not item.yes("Paired"),
            item.name.casefold(),
        ),
    )


def bluetooth_rfkill_paths() -> list[Path]:
    paths: list[Path] = []
    for entry in Path("/sys/class/rfkill").glob("rfkill*"):
        try:
            if (entry / "type").read_text().strip() == "bluetooth":
                paths.append(entry)
        except OSError:
            continue
    return paths


def bluetooth_soft_blocked() -> bool:
    for entry in bluetooth_rfkill_paths():
        try:
            if (entry / "soft").read_text().strip() == "1":
                return True
        except OSError:
            pass
    return False


def clip(text: str, width: int) -> str:
    if width <= 0:
        return ""
    return text if len(text) <= width else text[: max(0, width - 1)] + "…"


class BluetoothMenu:
    def __init__(self, screen: curses.window):
        self.screen = screen
        self.items: list[Device] = []
        self.selected = 0
        self.message = "Ready"
        self.controller: dict[str, str] = {}

    def refresh_data(self) -> None:
        self.controller = controller_info()
        self.items = devices()
        self.selected = min(self.selected, max(0, len(self.items) - 1))

    def add(self, row: int, col: int, text: str, attr: int = 0) -> None:
        height, width = self.screen.getmaxyx()
        if 0 <= row < height and col < width:
            try:
                self.screen.addstr(row, col, clip(text, width - col - 1), attr)
            except curses.error:
                pass

    def draw(self) -> None:
        self.screen.erase()
        height, width = self.screen.getmaxyx()
        power = self.controller.get("Powered", "unknown")
        blocked = "BLOCKED" if bluetooth_soft_blocked() else ""
        adapter = self.controller.get("Alias", self.controller.get("Name", "No adapter"))

        self.add(0, 0, " Bluetooth Test Menu ", curses.A_REVERSE | curses.A_BOLD)
        self.add(1, 0, f"Adapter: {adapter}   Power: {power.upper()} {blocked}")
        self.add(2, 0, "Up/Down: select   Enter: device menu   S: scan   P: power   R: refresh   Q: quit")
        self.add(3, 0, "Flags: C=connected  P=paired  T=trusted  B=blocked")
        self.add(4, 0, "-" * max(1, width - 1))

        available_rows = max(0, height - 8)
        start = 0
        if self.selected >= available_rows and available_rows:
            start = self.selected - available_rows + 1
        visible = self.items[start : start + available_rows]
        if not visible:
            self.add(6, 2, "No devices known. Press S to scan.", curses.A_DIM)
        for offset, device in enumerate(visible):
            index = start + offset
            flags = "".join(
                (
                    "C" if device.yes("Connected") else "-",
                    "P" if device.yes("Paired") else "-",
                    "T" if device.yes("Trusted") else "-",
                    "B" if device.yes("Blocked") else "-",
                )
            )
            rssi = device.properties.get("RSSI", "")
            suffix = f"  RSSI {rssi}" if rssi else ""
            text = f"[{flags}] {device.name}  {device.address}{suffix}"
            attr = curses.A_REVERSE if index == self.selected else 0
            self.add(5 + offset, 0, text, attr)

        self.add(height - 2, 0, "-" * max(1, width - 1))
        self.add(height - 1, 0, self.message, curses.A_BOLD)
        self.screen.refresh()

    def set_result(self, action: str, result: Result) -> None:
        lines = [line.strip() for line in result.output.splitlines() if line.strip()]
        detail = lines[-1] if lines else ("succeeded" if result.ok else "failed")
        self.message = f"{action}: {detail}"

    def pause_for_sudo(self) -> None:
        curses.def_prog_mode()
        curses.endwin()
        print("\nBluetooth is software-blocked. Administrator access is needed to unblock it.")
        for entry in bluetooth_rfkill_paths():
            subprocess.run(
                ["sudo", "tee", str(entry / "state")],
                input="1\n",
                text=True,
                stdout=subprocess.DEVNULL,
                check=False,
            )
        subprocess.run(["sudo", "systemctl", "restart", "bluetooth"], check=False)
        input("Press Enter to return to the Bluetooth menu...")
        curses.reset_prog_mode()
        self.screen.refresh()

    def toggle_power(self) -> None:
        if bluetooth_soft_blocked():
            self.pause_for_sudo()
        self.controller = controller_info()
        turn_on = self.controller.get("Powered", "no").lower() != "yes"
        self.set_result("Power", ctl("power", "on" if turn_on else "off"))
        self.refresh_data()

    def scan(self) -> None:
        if self.controller.get("Powered", "no").lower() != "yes":
            self.message = "Turn the adapter on first (press P)."
            return
        self.message = "Scanning for 10 seconds... keep the device in pairing mode."
        self.draw()
        result = ctl("scan", "on", timeout=10)
        ctl("scan", "off", timeout=5)
        self.set_result("Scan", result)
        self.refresh_data()

    def confirm(self, prompt: str) -> bool:
        self.message = f"{prompt} [y/N]"
        self.draw()
        return self.screen.getch() in (ord("y"), ord("Y"))

    def show_info(self, device: Device) -> None:
        while True:
            self.screen.erase()
            self.add(0, 0, f" {device.name} ", curses.A_REVERSE | curses.A_BOLD)
            lines = [f"Address: {device.address}"]
            lines.extend(f"{key}: {value}" for key, value in device.properties.items())
            for row, line in enumerate(lines, start=2):
                self.add(row, 0, line)
            self.add(self.screen.getmaxyx()[0] - 1, 0, "Press any key to return")
            self.screen.refresh()
            self.screen.getch()
            return

    def device_menu(self) -> None:
        if not self.items:
            self.message = "No device selected. Scan first."
            return
        device = self.items[self.selected]
        while True:
            device.properties = device_info(device.address)
            options = [
                ("1", "Pair", ("pair", device.address)),
                ("2", "Connect", ("connect", device.address)),
                ("3", "Disconnect", ("disconnect", device.address)),
                ("4", "Trust", ("trust", device.address)),
                ("5", "Untrust", ("untrust", device.address)),
                ("6", "Forget / unpair", ("remove", device.address)),
                ("7", "Block", ("block", device.address)),
                ("8", "Unblock", ("unblock", device.address)),
            ]
            self.screen.erase()
            self.add(0, 0, f" {device.name} ", curses.A_REVERSE | curses.A_BOLD)
            self.add(1, 0, device.address)
            flags = []
            for key in ("Connected", "Paired", "Trusted", "Blocked"):
                flags.append(f"{key}: {device.properties.get(key, 'unknown')}")
            self.add(2, 0, "   ".join(flags))
            for row, (key, label, _) in enumerate(options, start=4):
                self.add(row, 2, f"{key}. {label}")
            self.add(12, 2, "I. Full device information")
            self.add(14, 2, "Esc or Q. Back")
            self.add(self.screen.getmaxyx()[0] - 1, 0, self.message, curses.A_BOLD)
            self.screen.refresh()
            key = self.screen.getch()
            if key in (27, ord("q"), ord("Q")):
                self.refresh_data()
                return
            if key in (ord("i"), ord("I")):
                self.show_info(device)
                continue
            chosen = next((option for option in options if ord(option[0]) == key), None)
            if not chosen:
                continue
            _, label, command = chosen
            if command[0] == "remove" and not self.confirm(f"Forget {device.name}?"):
                self.message = "Forget cancelled"
                continue
            self.message = f"Running {label.lower()}..."
            self.draw()
            result = ctl(*command, timeout=30, agent=command[0] == "pair")
            self.set_result(label, result)
            if command[0] == "remove" and result.ok:
                self.refresh_data()
                return

    def run(self) -> None:
        try:
            curses.curs_set(0)
        except curses.error:
            pass
        self.screen.keypad(True)
        self.refresh_data()
        while True:
            self.draw()
            key = self.screen.getch()
            if key in (ord("q"), ord("Q")):
                return
            if key in (curses.KEY_UP, ord("k")) and self.items:
                self.selected = max(0, self.selected - 1)
            elif key in (curses.KEY_DOWN, ord("j")) and self.items:
                self.selected = min(len(self.items) - 1, self.selected + 1)
            elif key in (10, 13, curses.KEY_ENTER):
                self.device_menu()
            elif key in (ord("s"), ord("S")):
                self.scan()
            elif key in (ord("p"), ord("P")):
                self.toggle_power()
            elif key in (ord("r"), ord("R")):
                self.message = "Refreshed"
                self.refresh_data()


def diagnose() -> int:
    info = controller_info()
    print("Controller")
    for key in ("Address", "Name", "Alias", "Powered", "PowerState", "Discovering"):
        if key in info:
            print(f"  {key}: {info[key]}")
    print(f"  Software blocked: {'yes' if bluetooth_soft_blocked() else 'no'}")
    print("Devices")
    known = devices()
    if not known:
        print("  None")
    for device in known:
        flags = ", ".join(
            key.lower() for key in ("Connected", "Paired", "Trusted", "Blocked") if device.yes(key)
        )
        print(f"  {device.name} [{device.address}] {flags or 'seen'}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--diagnose", action="store_true", help="print status without opening the UI")
    args = parser.parse_args()
    if args.diagnose:
        return diagnose()
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        parser.error("the menu needs an interactive terminal; use --diagnose for plain output")
    curses.wrapper(lambda screen: BluetoothMenu(screen).run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
