# iPod nano Spotify controller

A Raspberry Pi 4 runs Spotify and sends audio to Bluetooth headphones. An iPod
nano 7 connected by Lightning acts as the screen and remote control.

## How it fits together

```text
Spotify phone/desktop
        |
        | Spotify cloud
        v
go-librespot on the Pi ---- REST API (localhost only) ---- Pi bridge
        |                                                   |
        | PipeWire / BlueZ                                  | USB/SCSI mailbox
        v                                                   v
Bose headphones                                      iPod nano Spotify app
```

The Pi appears in Spotify as **iPod Nano**. The physical Nano has no network
connection or Spotify credentials: it sends controls over USB and displays the
state returned by the Pi. go-librespot produces audio, PipeWire routes it, and
BlueZ maintains the bonded headphone connection.

## Repository layout

- `nanoapps/spotify_remote/` — Nano LVGL interface.
- `nanoapps/apps.toml` — app selection; one true/false switch per app.
- `vendor/NanoApps/` — pinned `ipod-spotify` branch of the NanoApps fork.
- `pi/spotify_bridge.py` — USB mailbox to go-librespot API bridge.
- `pi/bluetooth_control.py` — USB adapter audio control and preferred-device reconnection.
- `pi/bt_menu.py` — terminal Bluetooth management.
- `pi/setup_spotify.sh` — go-librespot and user-service setup.
- `scripts/` — PowerShell entry points for the PC.
- `docs/spotify-protocol.md` — bounded 512-byte mailbox protocol.

## Pi access

The project is cloned at `/home/ltidswell/ipod-nano-spotify` on `ltpi` and uses
the `ltidswell` account. Connect from PowerShell in this repository:

```powershell
.\scripts\Connect-Pi.ps1
```

On a fresh Pi, install the dedicated SSH key first:

```powershell
.\scripts\Install-PiSshKey.ps1
```

## Spotify setup and authorization

Install the pinned ARM64 go-librespot release and services:

```powershell
.\scripts\Install-Spotify.ps1
```

Then retrieve the device-authorization request on the Pi:

```sh
curl -s http://127.0.0.1:3678/auth/code
```

Open the returned URL, or enter its code at `https://spotify.com/pair`. The
resulting credentials remain in `~/.config/go-librespot/state.json` on the Pi
and must not be committed. Spotify Premium is required.

Useful checks:

```sh
systemctl --user status go-librespot.service ipod-spotify-bridge.service
journalctl --user-unit=go-librespot.service -f
curl -s http://127.0.0.1:3678/status
```

## Selecting and installing Nano apps

Edit `nanoapps/apps.toml`. Every available app has an explicit boolean switch;
only `spotify_remote` is enabled by default:

```toml
all = false

[apps]
calculator = false
notes = false
spotify_remote = true
```

Set individual apps to `true`, or set `all = true` to install everything.
Install the configured set from the PC with:

```powershell
.\scripts\Install-NanoApps.ps1
```

On the Pi, the equivalent command is:

```sh
cd ~/ipod-nano-spotify
python3 pi/install_nanoapps.py
```

The installer copies project apps into the pinned NanoApps build tree, packages
only the selected apps, pauses the SCSI bridge during installation, and restarts
it afterward. After installing, open a built-in Nano app and return Home to
refresh the Home Screen icons.

## Bluetooth headphones

On the Nano, open **Spotify → System**. Tap a saved device, then **Pair / Connect**
or **Disconnect**. **Forget...** opens a confirmation before removing its pairing.
Devices show `[saved]`, `[linked]`, or `[audio]` (audio output available).

For a new speaker, put it in pairing mode, tap **Scan for speakers**, choose its
name, and tap **Pair / Connect**. Use the arrows for more than three devices.
Discovery stops after 60 seconds or a successful connection. Pairing supports
NoInputNoOutput speakers/headphones; PIN/passkey devices need terminal pairing.

The bridge pins the USB Bluetooth adapter address in
`~/.config/nanoapps/bluetooth.json`. Devices saved on the built-in adapter must
be paired again on USB. The same file saves one preferred speaker and whether
to reconnect it. Selecting a speaker disconnects other audio devices on that
adapter and routes existing streams to its output. Disconnect and Forget disable
reconnection for the preferred device.

The bridge requires `python3-dbus`, installed by `pi/setup_spotify.sh`. Disable
the old service so it cannot compete with the new controller:

```sh
systemctl --user disable --now bluetooth-autoconnect.service
```

Terminal diagnostics remain available through `~/bt-menu`. PipeWire's headless
Bluetooth override is tracked in `pi/wireplumber-headless-bluetooth.conf`.

## Current state

- Bose Bluetooth audio, bonding, and bounded reconnection work.
- go-librespot runs at boot and exposes its API only on `127.0.0.1:3678`.
- The Nano Spotify app shows track/artist, progress, playback state and volume.
- Previous, play/pause, next, and relative volume commands work over USB.
- System provides Bluetooth scanning, pairing, connection and removal controls.
- Library remains a placeholder.
- The NanoApps fork fixes selective packaging and is pinned as a submodule.

## Next steps

1. Populate Library from go-librespot's playlist and context APIs.
2. Add network and player diagnostics to System.
3. Add seeking and small album art with transfer only on track changes.
4. Improve recovery after Nano reboot and USB reconnection.
5. Exercise playback, headphone power cycling and Pi reboot as one end-to-end
   reliability test.

## Version control

Commit and push from the PC checkout, then update the Pi:

```powershell
git push
```

```sh
cd ~/ipod-nano-spotify
git pull --ff-only
git submodule update --init --recursive
```

Do not commit Spotify state, private keys, tokens, or runtime credentials.
