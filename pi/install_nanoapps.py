#!/usr/bin/env python3
"""Install the NanoApps selection declared in nanoapps/apps.toml."""

from __future__ import annotations

import shutil
import argparse
import subprocess
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "nanoapps" / "apps.toml"
NANOAPPS = ROOT / "vendor" / "NanoApps"


def project_apps() -> dict[str, Path]:
    found: dict[str, Path] = {}
    for parent in (ROOT / "nanoapps" / "apps", ROOT / "nanoapps"):
        if not parent.is_dir():
            continue
        for candidate in parent.iterdir():
            if candidate.is_dir() and (candidate / "Makefile").is_file():
                found[candidate.name] = candidate
    return found


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--managed', action='store_true', help='Use the installed config-page helper')
    parser.add_argument('--reload-only', action='store_true', help='Register apps already on disk without rebuilding them')
    args = parser.parse_args()
    config = tomllib.loads(MANIFEST.read_text())
    install_all = bool(config.get("all", False))
    switches = config.get("apps", {})
    if not isinstance(switches, dict) or not all(isinstance(value, bool) for value in switches.values()):
        raise SystemExit("nanoapps/apps.toml [apps] values must be true or false")
    available_project = project_apps()
    selected = list(switches) if install_all else [name for name, enabled in switches.items() if enabled]
    project = [name for name in selected if name in available_project]
    upstream = [name for name in selected if name not in available_project]

    if not (NANOAPPS / "start").is_file():
        raise SystemExit("NanoApps submodule is missing; run git submodule update --init --recursive")

    for name in project:
        source = available_project[name]
        destination = NANOAPPS / "apps" / name
        shutil.copytree(source, destination, dirs_exist_ok=True)
        print(f"Synced project app: {name}")

    for name in upstream:
        if not (NANOAPPS / "apps" / name / "Makefile").is_file():
            raise SystemExit(f"Unknown upstream NanoApps app: {name}")

    if install_all:
        for name, source in available_project.items():
            destination = NANOAPPS / "apps" / name
            shutil.copytree(source, destination, dirs_exist_ok=True)
            if name not in project:
                print(f"Synced project app: {name}")
        targets: list[str] = []
    else:
        targets = selected
    if not install_all and not targets:
        raise SystemExit("No apps selected; enable an app in nanoapps/apps.toml or set all = true")
    print("Installing " + ("all apps" if install_all else ", ".join(targets)))
    bridge = "ipod-spotify-bridge.service"
    bridge_was_active = subprocess.run(
        ["systemctl", "--user", "is-active", "--quiet", bridge], check=False
    ).returncode == 0
    if bridge_was_active:
        subprocess.run(["systemctl", "--user", "stop", bridge], check=True)
    try:
        if args.managed:
            from managed_nano import install
            install(NANOAPPS, targets, args.reload_only)
        else:
            command = [str(NANOAPPS / 'start'), 'run', 'silver_resident'] if args.reload_only else [str(NANOAPPS / 'start'), 'install', *targets]
            subprocess.run(command, cwd=NANOAPPS, check=True)
    finally:
        if bridge_was_active:
            subprocess.run(["systemctl", "--user", "start", bridge], check=True)


if __name__ == "__main__":
    main()
