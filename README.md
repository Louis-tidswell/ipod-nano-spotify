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

## Current status

- The Pi runs 64-bit Debian 13 and is reachable as `ltidswell@ltpi`.
- This repository is cloned at `/home/ltidswell/ipod-nano-spotify` on the Pi.
- **Pi Link Test** proves bidirectional Nano-to-Pi SCSI communication: button
  events reach the Pi and acknowledgements/counters return to the Nano.
- The bridge runs as the `ipod-comm-test.service` user service.
- BlueZ detects the Pi's Bluetooth adapter, and its software block is cleared.
- `~/bt-menu` provides interactive Bluetooth scanning and device management.

## Next steps

1. **Prove Bluetooth audio.** Run `~/bt-menu`, scan for the Bose headphones,
   then pair, trust and connect them. Record their Bluetooth address for later
   automatic reconnection.
2. **Install the audio stack and select the sink.** This minimal Pi image does
   not include PipeWire, WirePlumber or a BlueZ audio-profile provider. Install
   the Raspberry Pi OS audio package, then find the Bose sink ID:

   ```sh
   sudo apt update
   sudo apt install -y pipewire-audio
   mkdir -p ~/.config/wireplumber/wireplumber.conf.d
   cp ~/ipod-nano-spotify/pi/wireplumber-headless-bluetooth.conf \
      ~/.config/wireplumber/wireplumber.conf.d/51-headless-bluetooth.conf
   systemctl --user enable --now pipewire pipewire-pulse wireplumber
   systemctl --user restart wireplumber
   wpctl status
   wpctl set-default <sink-id>
   wpctl set-volume @DEFAULT_AUDIO_SINK@ 0.25
   speaker-test -c 2 -t wav
   ```

3. **Test recovery.** Enable the bounded reconnect service, then power-cycle the
   headphones and confirm that `wpctl status` shows the sink again:

   ```sh
   mkdir -p ~/.config/systemd/user
   cp ~/ipod-nano-spotify/pi/bluetooth-autoconnect.service ~/.config/systemd/user/
   systemctl --user daemon-reload
   systemctl --user enable --now bluetooth-autoconnect.service
   journalctl --user-unit=bluetooth-autoconnect.service -f
   ```
4. **Install go-librespot.** Install its ARM64 release, configure PipeWire audio,
   enable Spotify Connect credentials, and bind its REST/WebSocket API to
   `127.0.0.1`. Verify play, pause, next, previous and volume from the Pi first.
5. **Connect playback to the Nano.** Replace the counter-only Pi bridge actions
   with go-librespot commands and return player state through the proven mailbox.
   Start with play/pause and track status before building the full Nano UI.

## Pi-Nano communication proof

The first Nano app and Pi bridge are in this repository. Their two-page SCSI
mailbox protocol is documented in [docs/comms-test.md](docs/comms-test.md).

The app is installed and its round trip has been verified. On a fresh NanoApps
installation, deploy it from a PowerShell terminal in this repository with:

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
Its Pair action confirms the headset request and verifies that BlueZ saved a
persistent bond before reporting success.
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

The PC checkout is the development copy. Commit and push there:

```powershell
git push
```

Update the Pi checkout afterward:

```sh
cd /home/ltidswell/ipod-nano-spotify
git pull --ff-only
```

Do not commit Spotify credentials, private keys or runtime authentication data.
