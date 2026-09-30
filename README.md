# iPod Spotify controller

Raspberry Pi Spotify player controlled by an iPod nano 7. See
[the architecture and development phases](spotify-pi-ipod-options.md).

## Pi access

- Host: `ltpi`
- User: `ltidswell`
- Dedicated SSH key: `%USERPROFILE%\.ssh\id_ed25519_ltpi`
- Private key stays outside this repository and OneDrive.

The Pi is reachable over SSH. Initial key authentication failed because the
public key has not yet been authorized on the Pi. Run this once in an
interactive PowerShell terminal and enter the Pi user's password when prompted:

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

No packages or configuration have been changed on the Pi yet. The speaker's
name or Bluetooth address will be needed for pairing.

## Version control

The local repository uses `main`. Once a remote repository URL is available:

```powershell
git remote add origin <repository-url>
git push -u origin main
```

Do not commit Spotify credentials, private keys or runtime authentication data.
