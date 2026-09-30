#!/usr/bin/env python3
"""Install the NanoApps selection declared in nanoapps/apps.toml."""

from __future__ import annotations

import shutil
import subprocess
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "nanoapps" / "apps.toml"
NANOAPPS = ROOT / "vendor" / "NanoApps"


def project_source(name: str) -> Path:
    for candidate in (ROOT / "nanoapps" / "apps" / name, ROOT / "nanoapps" / name):
        if (candidate / "Makefile").is_file():
            return candidate
    raise SystemExit(f"Project app {name!r} is missing its Makefile")


def main() -> None:
    config = tomllib.loads(MANIFEST.read_text())
    project = list(config.get("project", []))
    upstream = list(config.get("upstream", []))
    install_all = bool(config.get("all", False))

    if not (NANOAPPS / "start").is_file():
        raise SystemExit("NanoApps submodule is missing; run git submodule update --init --recursive")

    for name in project:
        source = project_source(name)
        destination = NANOAPPS / "apps" / name
        shutil.copytree(source, destination, dirs_exist_ok=True)
        print(f"Synced project app: {name}")

    for name in upstream:
        if not (NANOAPPS / "apps" / name / "Makefile").is_file():
            raise SystemExit(f"Unknown upstream NanoApps app: {name}")

    targets = [] if install_all else project + upstream
    if not install_all and not targets:
        raise SystemExit("No apps selected; add names to nanoapps/apps.toml or set all = true")
    print("Installing " + ("all apps" if install_all else ", ".join(targets)))
    subprocess.run([str(NANOAPPS / "start"), "install", *targets], cwd=NANOAPPS, check=True)


if __name__ == "__main__":
    main()
