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

## Local config page

**Nano Config** runs at `http://<Pi-Wi-Fi-IP>:8080` on the Pi's current network.
The Spotify app's **System → Config** heading displays the complete address,
preferring Wi-Fi and falling back to Ethernet. It refreshes as networks change.
The mobile-friendly page uses the Nano's dark theme and green accents.

Install the service and restricted Pi-control helper once:

```sh
cd ~/ipod-nano-spotify
bash pi/setup_config.sh
```

Open the private, one-time setup link displayed by the script and set a shared
household password. Anyone on the network can view status; changes require that
password. After setup, use the ordinary Config address. Sessions expire after
eight hours; changing the password signs out other browsers. Before setup, retrieve
the link again with `cat ~/.config/ipod-nano-config/setup-link.txt`.

The page provides:

- **Overview:** Pi temperature, uptime, USB connection and Spotify playback controls.
- **Nano apps:** choose apps, save the selection and install it. **Reload installed
  apps** restores existing app icons after a restart/disconnection without copying
  every app again. Close custom Nano apps first. After reloading/installing, open
  Music or Settings and return Home. Existing `/Apps/Data` is preserved.
- **Spotify profiles:** choose who is listening, **Add account**, **Rename** or
  **Delete**. The current login is imported as **Current account**. Adding an account
  uses Spotify's browser/device-code login. Switching stops playback; the active
  profile signs in automatically after a Pi restart. Cancelling login restores the
  previous profile. Deleting the active profile stops Spotify and signs it out;
  choose another profile or add an account to continue.
- **Bluetooth:** scan, pair/connect, disconnect and forget speakers/headphones,
  using the bridge's existing USB Bluetooth adapter and worker.
- **Wi-Fi:** scan, connect to WPA personal/open networks, use saved networks or forget
  them. After switching, join the new network and read the new Config address on the
  Nano. Unlock the page and **Keep this connection** within 120 seconds; otherwise
  NetworkManager restores the previous connection. Enterprise Wi-Fi and captive
  portal sign-in are outside this basic page.
- **System:** restart Spotify, the USB bridge, PipeWire or WirePlumber; change the
  household password; restart or shut down the Pi. After shutdown, reconnect its
  physical power to start it again.

The existing `python3 pi/install_nanoapps.py` command and `nanoapps/apps.toml`
selection still work. After config setup, a quick CLI reload is also available:
`python3 pi/install_nanoapps.py --managed --reload-only`.

The page starts at boot as the normal Pi user via `ipod-nano-config.service` and
listens on all IPv4 interfaces, port 8080. This is local-network HTTP; do not forward
the port to the internet. The Spotify API remains on `127.0.0.1:3678`.
The root-owned `/usr/local/libexec/ipod-nano-config-helper` accepts only fixed Pi,
NetworkManager and iPod disk actions; it cannot run arbitrary shell commands.
The web page does not expose Spotify tokens or a general command runner.

Password hashes and saved Spotify credentials live outside Git under
`~/.config/ipod-nano-config/`, in a private directory with credential files mode
`0600`. Only profile names, usernames and IDs reach the browser. Spotify passwords
are entered on Spotify's own site. Account switching preserves the Pi's device ID
and volume. Deleting a profile removes its saved credentials from the Pi, without
revoking other Spotify sessions.

```sh
systemctl --user status ipod-nano-config.service
systemctl --user restart ipod-nano-config.service
journalctl --user -u ipod-nano-config.service -n 50 --no-pager
```

After changing `pi/config_helper.py`, rerun `bash pi/setup_config.sh`.

### Forgotten household password

Use SSH or a keyboard/terminal on the Pi, signed in as `ltidswell`. Run:

```sh
systemctl --user stop ipod-nano-config.service
rm -f ~/.config/ipod-nano-config/password.json
systemctl --user start ipod-nano-config.service
```

Wait a moment, then display the new private setup link:

```sh
cat ~/.config/ipod-nano-config/setup-link.txt
```

Open that link on the Pi's network and set a new household password. The old
password cannot be recovered because only a hash is saved. Restarting the web
service invalidates existing browser sessions. Saved Spotify accounts, the active
Spotify login, Wi-Fi settings, and installed Nano apps are preserved: delete only
`password.json`, not the entire config directory. This resets the web-page password,
not your Pi/Linux login password or a Spotify password. If the link file does not
appear, check `systemctl --user status ipod-nano-config.service`.

### Browser development with Codex (proposal)

A development page could provide a persistent browser terminal running Codex CLI
in `~/ipod-nano-spotify`. Codex can edit files and run build/test commands locally;
the AI model is accessed online. OpenAI documents CLI installation for Linux and
[ChatGPT or API-key sign-in](https://learn.chatgpt.com/docs/auth). For a Pi without
a desktop browser, `codex login --device-auth` supports browser login on another
device when device-code login is enabled for the account/workspace.
See the [official CLI guide](https://learn.chatgpt.com/docs/codex/cli).

The recommended first version would add an **owner-only Development** tab, a
persistent terminal session, and a separate developer login. This would allow:

1. Create a Git branch/checkpoint and ask Codex to change the Nano app or Pi code.
2. Review the diff and run the existing Python, browser and native Nano UI checks.
3. Use **Nano apps → Install selected apps** to deploy a reviewed Nano app change.
4. Restart the affected Pi service for Pi-code changes; rerun `setup_config.sh` if
   the installed privileged helper changed. Restarting services should wait until
   the development operation is complete.
5. Commit and push the reviewed changes.

A second option is a chat panel with streamed progress, tool approvals and diffs,
using [Codex App Server](https://learn.chatgpt.com/docs/app-server) through a private
backend `stdio` connection. Its documentation marks App Server/remote WebSocket
support as experimental, so it needs version pinning and integration tests. Neither
the standalone CLI/browser terminal nor a custom development chat panel is installed
by this project yet; these are implementation options for a future feature.

Developer access needs separate protection from the shared household controls:
a terminal can change code, run commands and access the Pi user's files. For access
away from home, use a private VPN or authenticated encrypted tunnel. Keep developer
credentials on the Pi and make Nano installation an explicit action after reviewing
the changes. Codex sign-in uses the account's applicable access/usage limits; API-key
usage is billed separately by OpenAI.

## Repository layout

- `nanoapps/spotify_remote/` — Nano LVGL interface.
- `nanoapps/apps.toml` — app selection; one true/false switch per app.
- `vendor/NanoApps/` — pinned `ipod-spotify` branch of the NanoApps fork.
- `pi/spotify_bridge.py` — USB mailbox to go-librespot API bridge.
- `pi/bluetooth_control.py` — USB adapter audio control and preferred-device reconnection.
- `pi/bt_menu.py` — terminal Bluetooth management.
- `pi/spotify_content.py` — playlists and album artwork on background workers.
- `pi/setup_spotify.sh` — go-librespot and user-service setup.
- `pi/config_server.py`, `pi/config_web/` — LAN page and assets.
- `pi/spotify_profiles.py` — saved Spotify accounts.
- `pi/config_helper.py`, `pi/setup_config.sh` — restricted Pi controls and setup.
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

For everyday login/account switching, use **Spotify profiles** on the local
config page. These commands remain available for initial setup and recovery.

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
the current selection is Chess, Minesweeper, Pong, Quiz and Spotify. For example,
this minimal selection installs only Spotify:

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

On the Nano, open **Spotify → System → Speakers / headphones**. Tap a saved device, then **Pair / Connect**
or **Disconnect**. **Forget...** opens a confirmation before removing its pairing.
Devices show `[saved]`, `[linked]`, or `[audio]` (audio output available).

For a new speaker, put it in pairing mode, tap **Scan**, choose its
name, and tap **Pair / Connect**. Use the arrows for more than three devices.
Discovery stops after 60 seconds or a successful connection. Pairing supports
NoInputNoOutput speakers/headphones; PIN/passkey devices need terminal pairing.

The bridge pins the USB Bluetooth adapter address in
`~/.config/nanoapps/bluetooth.json`. Devices saved on the built-in adapter must
be paired again on USB. The same file saves one preferred speaker and whether
to reconnect it. Selecting a speaker disconnects other audio devices on that
adapter and routes existing streams to its output. Disconnect and Forget disable
reconnection for the preferred device.

The bridge requires `python3-dbus` and Pillow (`python3-pil`), installed by
`pi/setup_spotify.sh`. Disable
the old service so it cannot compete with the new controller:

```sh
systemctl --user disable --now bluetooth-autoconnect.service
```

Terminal diagnostics remain available through `~/bt-menu`. PipeWire's headless
Bluetooth override is tracked in `pi/wireplumber-headless-bluetooth.conf`.

## Playlists and artwork

Open **Spotify → Library** on the Nano to browse playlists belonging to the
currently logged-in Spotify account, including followed playlists. Two playlists
appear per page; use the arrows to move through the full library, and tap a
playlist to start it. **Refresh playlists** fetches edits made elsewhere. The Pi
uses go-librespot **v0.10.3**'s `/library/playlists` API with the existing login;
no separate Spotify developer app or OAuth setup is needed.

**Now** displays the current track's 96×96 cover, downloaded by the Pi and sent
in checked 256-byte RGB565 chunks through the existing USB mailbox. Artwork
updates only when its pixels change. Downloads and playlist requests run off
the mailbox thread so playback and Bluetooth remain responsive. A missing cover
uses a text placeholder. Keep the Nano connected over USB.

## Verification and restore point

Run `python3 -m unittest discover -s tests -v` for protocol, Bluetooth and content
regressions. `python3 tests/run_nano_ui.py` compiles the actual UI against native
LVGL, checks all controls at 240×432, tests repeated navigation and command
identity, reconstructs art, and writes PNG previews under `/tmp/nano-ui-native`.
The first native build needs `cc`, `ar` and a few minutes to compile LVGL.

The pre-change source restore point is commit **772f4e8**, pushed to GitHub before
this work. The previous Spotify binary is saved on this Pi at
`~/.local/share/ipod-nano-backups/772f4e8/go-librespot`. To restore, check out that
commit, stop go-librespot, copy the backup binary to `~/.local/bin/go-librespot`,
restart it, and reinstall the configured Nano apps. Source and Nano protocol
versions must always be deployed together.

## Current state

- Bose Bluetooth audio, bonding, and bounded reconnection work.
- go-librespot runs at boot and exposes its API only on `127.0.0.1:3678`.
- The Nano Spotify app shows cover art, track/artist, progress, playback state and volume.
- Previous, play/pause, next, and relative volume commands work over USB.
- System provides Bluetooth scanning, pairing, connection and removal controls.
- System shows the Pi's current local Config page address.
- The Pi-hosted config page manages app installs/reloads, saved Spotify profiles,
  Bluetooth, Wi-Fi, service restarts and Pi power, with household password protection.
- Library lists and starts the current account's playlists.
- The NanoApps fork fixes selective packaging and is pinned as a submodule.

## Next steps

1. Add per-playlist track browsing and seeking.
2. Add network and player diagnostics to System.
3. Improve pairing flows for devices requiring a PIN or passkey.
4. Improve recovery after Nano reboot and USB reconnection.
5. Exercise playback, headphone power cycling and Pi reboot as one end-to-end
   reliability test.
6. Add an owner-only browser development terminal or Codex chat panel as described
   above, with persistent sessions, reviewable diffs and explicit Nano deployment.

## Checks

```sh
python3 -m unittest discover -s tests -v
python3 tests/run_nano_ui.py
node --check pi/config_web/app.js
python3 tests/run_config_browser.py
```

The native UI check builds the actual Nano source against LVGL and checks screen
bounds, navigation, playback controls, Bluetooth, playlists, artwork and the Config
URL. Browser tests use isolated temporary profiles and mocked Pi actions; they
exercise setup/password access, profile login cancellation, app selection/reload,
confirmation dialogs and all six layouts at phone width. The browser test requires
`chromium`, `chromium-driver` and `python3-selenium`; Node is only used for the syntax
check. The web server itself needs no JavaScript runtime or browser installed.
Live validation includes local-network HTTP access, helper permissions, USB app
installation, Spotify readiness, Bluetooth status and Wi-Fi scanning. Power and
network-switching actions are mocked in tests rather than interrupting the Pi.

## Version control

Commit and push from either the PC or Pi checkout. On the Pi:

```sh
cd ~/ipod-nano-spotify
git status
git add <files-you-changed>
git commit -m "Describe the change"
git push origin main
```

On the other checkout, pull the pushed changes:

```sh
cd ~/ipod-nano-spotify
git pull --ff-only
git submodule update --init --recursive
```

Do not commit Spotify state, private keys, tokens, or runtime credentials.
For recovery, prefer a new branch at a known-good commit and redeploy the matching
Pi/Nano versions together. Checkpoints are listed by `git log --oneline`.
