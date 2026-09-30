# iPod Spotify controller

Raspberry Pi Spotify player controlled by an iPod nano 7. See
[the architecture and development phases](spotify-pi-ipod-options.md).

## Pi access

- Host: `ltpi`
- User: `ltidswell`
- Dedicated SSH key: `%USERPROFILE%\.ssh\id_ed25519_ltpi`
- Private key stays outside this repository and OneDrive.

The Pi is reachable over SSH using the dedicated key. To install that key on a
fresh Pi, run this once in an interactive PowerShell terminal and enter the Pi
user's password when prompted:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\Install-PiSshKey.ps1
```

The script preserves existing authorized keys and verifies key authentication.
Connect afterward with:

```powershell
.\scripts\Connect-Pi.ps1
```

## First implementation milestone

1. Inspect the Pi OS, CPU architecture, installed audio services and Bluetooth adapter.
2. Install/configure BlueZ, PipeWire and WirePlumber as appropriate for that OS.
3. Pair and trust the intended Bluetooth speaker.
4. Select its audio sink, play a test sound and verify reconnection after power cycling.
5. Install go-librespot and verify playback and its localhost control API.

## Pi–Nano communication proof

The first Nano app and Pi bridge are in this repository. Their two-page SCSI
mailbox protocol is documented in [docs/comms-test.md](docs/comms-test.md).

The app has been built successfully with the NanoApps toolchain on the Pi. Its
one-time installation needs the Pi user's sudo password because NanoApps must
access the iPod block/SCSI device. From a PowerShell terminal in this repository,
run:

```powershell
.\scripts\Install-CommTest.ps1
```

After installation, wait for the iPod to return to its Home Screen, open a
built-in app such as **Music** or **Settings**, and return Home. Swipe through
the Home Screen pages to the green link icon named **Pi Link Test** (it is
registered alphabetically between **Passwords** and **Pong**). Tap **SEND TEST**;
a working round trip changes the status to `Round trip OK` and increments the
counter.

Check the bridge service on the Pi with:

```sh
systemctl --user status ipod-comm-test.service
journalctl --user-unit=ipod-comm-test.service -f
```

## Bluetooth test menu

The Pi has a simple terminal Bluetooth control panel installed at `~/bt-menu`.
Run it from an interactive SSH session:

```sh
~/bt-menu
```

Use the arrow keys to select a device and Enter to open its actions. The main
screen can scan, refresh, and toggle adapter power. Device actions include pair,
connect, disconnect, trust, untrust, forget/unpair, block, unblock, and detailed
status. If Bluetooth is software-blocked, the power action guides you through a
one-time sudo unblock.

For a plain status report without opening the interface:

```sh
~/bt-menu --diagnose
```

## Version control

The local repository uses `main` and tracks the GitHub remote:

```powershell
git push
```

Do not commit Spotify credentials, private keys or runtime authentication data.
