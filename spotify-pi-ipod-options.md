# Spotify on Raspberry Pi Controlled by an iPod nano 7

## Purpose

The goal is to use a Raspberry Pi 4 as a headless Spotify player, send its audio to a Bluetooth speaker, and use an iPod nano 7 (A1446) running NanoApps as the screen and controller. The iPod remains connected to the Pi by a data-capable Lightning cable.

The best overall design is:

```text
iPod nano NanoApp
        |
        | Lightning/USB: small command and status messages
        v
Pi controller bridge
        |                         |
        | local REST/WebSocket    | BlueZ / PipeWire control
        v                         v
go-librespot                 Bluetooth speaker
        |
        v
Spotify service
```

## Recommendation

Use these components:

1. **go-librespot** as the Spotify Connect player.
2. **PipeWire and WirePlumber** for audio routing to the Bluetooth speaker.
3. **BlueZ** for pairing, trusting, connecting and reconnecting the speaker.
4. A small **Python bridge service** on the Pi for commands, state and recovery.
5. A custom **NanoApps LVGL app** for the iPod interface.
6. A small, purpose-built **USB/SCSI mailbox protocol** between the NanoApp and the bridge.

This combination is the best fit because go-librespot exposes a local REST API and WebSocket events. The bridge can issue play, pause, next, previous, seek and volume commands, while receiving track metadata and state changes without repeatedly querying Spotify's public Web API. It also supports Spotify Connect, so a phone or computer remains available as a setup and fallback controller.

The custom USB exchange is the experimental part. Prove one command from the iPod to the Pi and one response back before building the complete interface.

## Playback engine options

| Option | Control interface | Setup effort | Fit for the iPod project | Recommendation |
|---|---|---:|---|---|
| **go-librespot** | REST API, WebSocket events and MPRIS | Medium | Excellent | **Use this** |
| **spotifyd** | MPRIS over D-Bus | Medium | Good | Strong alternative |
| **Raspotify/librespot** | Spotify Connect; limited direct local control | Low | Fair | Good for playback-only prototypes |
| Direct librespot integration | Custom Rust code or process integration | High | Potentially excellent | Only if go-librespot becomes limiting |
| Spotify Web API controlling a Connect device | HTTPS/OAuth | High | Fair | Avoid as the primary control path |
| moOde or Volumio | Distribution-specific UI and plugins | Low initially | Fair to poor for custom USB control | Appliance-oriented alternative |
| Browser-based Spotify player | Browser automation or web playback APIs | High | Poor | Do not use for this project |

### Option 1: go-librespot

**Best overall choice.** It runs as a lightweight Spotify Connect receiver and exposes the local interfaces needed by a custom controller:

- REST commands for playback, seeking, queue actions and volume.
- A `/status` endpoint with current state and track metadata.
- A WebSocket `/events` stream for immediate metadata, playback and volume changes.
- Optional playlist and context metadata support.
- ALSA, PulseAudio/PipeWire and pipe audio outputs.
- Zeroconf, interactive and device-code authentication modes.

The bridge can bind the go-librespot API to `127.0.0.1`, keeping it private to the Pi. The NanoApp talks only to the bridge and never handles Spotify credentials.

**Trade-offs:** It is an unofficial Spotify client, requires Spotify Premium, and could need updates if Spotify changes its private protocols.

### Option 2: spotifyd

spotifyd is a mature daemon built around librespot. It can expose standard Linux media controls through MPRIS/D-Bus, allowing the bridge to use tools such as `playerctl` or D-Bus directly.

Choose spotifyd if:

- You prefer MPRIS as the abstraction.
- You want the same bridge to control other MPRIS players later.
- You are comfortable managing a user D-Bus session on a headless Pi.

**Trade-offs:** Metadata and commands pass through D-Bus rather than a simple local HTTP API. Headless user-session configuration adds complexity, and browsing playlists still needs another data source.

### Option 3: Raspotify

Raspotify packages librespot as a Debian service and is designed to make a Pi appear as a Spotify Connect speaker with minimal setup.

Choose Raspotify if the first milestone is simply:

1. Select the Pi from Spotify on a phone or computer.
2. Hear music through the Pi's speaker output.

**Trade-offs:** Raspotify is convenient for receiving Spotify Connect playback, but it does not provide the rich, purpose-built local control and event API that the iPod interface needs. A custom controller would require an additional control layer, MPRIS support, or Spotify Web API calls. At that point, go-librespot or spotifyd is cleaner.

### Option 4: integrate librespot directly

The bridge could be written in Rust and incorporate librespot as a library, giving complete control over playback state and custom messaging.

This is appropriate only if the ready-made daemons cannot provide a required feature. It substantially increases development and maintenance effort and couples the project directly to librespot internals.

### Option 5: Spotify Web API

The bridge could register a Spotify developer application, complete OAuth, and use the public Web API to control a Spotify Connect player.

This is useful for features outside the player's local interface, but it should not be the main transport-control mechanism here because:

- It introduces token refresh and OAuth storage.
- Commands travel through Spotify's cloud rather than remaining local.
- Development Mode has account and user limits.
- Spotify changed Development Mode access and supported endpoints in 2026.
- Availability and latency depend on the public API as well as the player.

Use it later only for a specific feature that the chosen player cannot expose.

### Option 6: moOde or Volumio

These distributions provide a polished music-appliance experience and web interfaces. They are attractive if the priority changes to "working music player quickly."

They are less suitable for this project because the custom NanoApps bridge must integrate with distribution-specific services and plugins. They also replace the normal Raspberry Pi OS setup that is already working.

## iPod-to-Pi control options

| Transport | Works on this iPod? | Responsiveness | Development effort | Assessment |
|---|---:|---:|---:|---|
| **NanoApps plus custom SCSI RAM mailbox** | Expected, proof required | Good | High | **Best match** |
| Files on the iPod storage volume | Technically possible | Poor | Medium | Unsuitable for live controls |
| Apple iAP over Lightning | Uncertain | Potentially good | Very high | Avoid |
| Wi-Fi/HTTP directly from the iPod | No | — | — | iPod nano 7 has no Wi-Fi |
| Bluetooth commands directly from NanoApps | No ready NanoApps API | — | Very high | Avoid |
| Replace iPod with phone/Pi touchscreen | Yes, different hardware | Excellent | Low | Reliability fallback |

### Recommended transport: SCSI RAM mailbox

The patched iPod firmware supplies custom SCSI operations that can read and write memory and execute code over USB. NanoApps already uses this mechanism for deployment, debug traces and memory inspection.

The proposed extension is a small shared-memory mailbox owned by the NanoApps resident or controller app. A Pi process polls or exchanges bounded messages through that mailbox.

A minimal protocol should include:

- Protocol version and fixed magic value.
- Monotonically increasing sequence number.
- Command type and bounded payload length.
- Acknowledgement and error value.
- Playback snapshot version.
- Checksums or another corruption check.
- Timeouts and reconnect recovery.

Initial commands should be limited to:

- `PLAY_PAUSE`
- `NEXT`
- `PREVIOUS`
- `SET_VOLUME`
- `PLAY_PRESET`
- `SPEAKER_CONNECT`
- `SPEAKER_DISCONNECT`
- `GET_STATUS`

Initial status should contain:

- Playing, paused, buffering or stopped.
- Track and artist text.
- Position and duration.
- Volume.
- Bluetooth speaker name and connection state.
- Network/player error state.

Start with a low polling rate such as 5 Hz. Transfer album art only when the track changes, after resizing it on the Pi to a small format suitable for the iPod.

### Why not use files for commands?

The Pi and iPod cannot safely treat the same FAT filesystem as a live, simultaneously writable message bus. Switching between normal and disk modes is slow and increases the risk of filesystem corruption after an interrupted write or disconnect.

### Why not use Apple accessory protocols?

Implementing iAP over Lightning would require reverse engineering proprietary accessory behavior and possibly authentication hardware. NanoApps' existing SCSI channel is already available and better aligned with the project.

## Bluetooth audio options

### PipeWire and WirePlumber — recommended

Use PipeWire's PulseAudio-compatible service as the player output and WirePlumber to route audio to the connected Bluetooth A2DP sink. Use BlueZ for pairing and connection management.

Advantages:

- Modern default Linux audio stack.
- Bluetooth support through the PipeWire BlueZ plugin.
- `wpctl` can inspect sinks, choose the default and change output volume.
- The player can use the `pulseaudio` backend while PipeWire handles the real audio graph.
- The bridge can reconnect the speaker and then rediscover its current sink ID.

For a headless service, disable WirePlumber's Bluetooth seat monitoring for the dedicated audio user and enable that user's systemd lingering. Never save a temporary numeric sink ID; rediscover the speaker after each reconnect.

### BlueALSA

BlueALSA is a lighter alternative that exposes Bluetooth devices through ALSA. It can work well on a minimal appliance, but configuration is more manual and examples written for older Raspberry Pi OS versions frequently conflict with current PipeWire setups.

Choose it only if PipeWire proves unreliable on the final system or an ALSA-only design is required.

### Wired output or USB DAC

A wired speaker or USB DAC is the reliability fallback. It removes pairing, reconnection and Bluetooth latency. The iPod controller architecture remains the same; only the Pi's audio sink changes.

## Pi bridge responsibilities

Keep the bridge independent of the NanoApps user interface. It should:

1. Translate iPod mailbox messages into local player API calls.
2. Subscribe to go-librespot events and maintain a cached state snapshot.
3. Send only changed state back to the iPod.
4. Pair, connect and disconnect the saved Bluetooth speaker through BlueZ.
5. Select the corresponding PipeWire sink after reconnection.
6. Expose one consistent volume policy.
7. Detect player, network, speaker and iPod disconnects.
8. Start at boot and restart after failure.
9. Reinject the NanoApps resident loader after an iPod reboot.
10. Log events without storing Spotify credentials in logs or on the iPod.

Bind all bridge and player APIs to localhost. Only the USB protocol should reach the iPod.

## Volume policy

There may be three distinct volume controls:

1. Spotify player volume.
2. PipeWire software-output volume.
3. Bluetooth speaker hardware volume.

Controlling all three independently causes jumps and confusing synchronization. For the first version:

- Use go-librespot's player volume as the main slider.
- Set PipeWire output to a safe fixed level.
- Leave speaker hardware volume at a sensible level.

Hardware-volume synchronization can be added after confirming that the specific speaker handles it reliably.

## Recommended development sequence

### Phase 1: stable audio

1. Pair and trust the Bluetooth speaker with BlueZ.
2. Confirm that it appears as a PipeWire sink.
3. Make it the default output and play a local test sound.
4. Power-cycle the speaker and prove reconnection.

### Phase 2: stable Spotify player

1. Install go-librespot for ARM64.
2. Enable Spotify Connect and persistent credentials.
3. Bind its control API to `127.0.0.1`.
4. Play through the Bluetooth speaker.
5. Test status, play/pause, next, previous and volume from the Pi command line.

### Phase 3: bridge without the iPod

1. Implement the player and Bluetooth modules in Python.
2. Exercise them through a small command-line test client.
3. Add a systemd user service and reconnect handling.
4. Confirm operation after SSH logout and Pi reboot.

### Phase 4: prove USB messaging

1. Create a NanoApps test app with one button and one counter.
2. Send a button event to the Pi.
3. Return an incremented counter from the Pi.
4. Test hundreds of exchanges.
5. Test cable removal, iPod sleep, full reboot and reconnection.

Do not proceed to the full interface until this round trip is reliable.

### Phase 5: NanoApps controller

Build the interface in this order:

1. Connection/player state.
2. Track and artist text.
3. Play/pause, previous and next.
4. Volume.
5. Saved playlist presets.
6. Bluetooth speaker state and reconnect.
7. Progress bar and seek.
8. Small album art.
9. Playlist browsing and search, if still desirable.

### Phase 6: recovery and polish

1. Automatically re-execute the NanoApps resident after an iPod reboot.
2. Recover from a Spotify session drop.
3. Reconnect the speaker with bounded backoff.
4. Show actionable errors on the iPod.
5. Add watchdogs without creating reboot or reconnect loops.

## Important constraints

- Spotify playback through librespot-based clients requires **Spotify Premium**.
- go-librespot, spotifyd, librespot and Raspotify are unofficial clients and can require maintenance after Spotify service changes.
- NanoApps is an early homebrew project. A malformed payload or incorrect firmware modification can crash or brick an iPod.
- The installed NanoApps files persist, but the resident loader currently needs re-execution after a full iPod reboot.
- The USB mailbox is a proposed extension, not a documented ready-made NanoApps messaging API.
- The Pi should use a reliable 5.1 V, 3 A supply, especially while charging the iPod and compiling software.
- Bluetooth speakers vary in codec, reconnection, battery-reporting and hardware-volume behavior.

## Final choice

Build around **go-librespot + PipeWire/WirePlumber + BlueZ + a Python bridge + a NanoApps LVGL controller over the existing SCSI channel**.

Use spotifyd if MPRIS is preferred over HTTP. Use Raspotify only for a quick playback proof. Treat the Spotify Web API as an optional supplement, and keep a wired or USB audio output as the fallback if Bluetooth recovery becomes unreliable.

## References

- [NanoApps](https://github.com/nfzerox/NanoApps)
- [ipod_sun_untethered](https://github.com/nfzerox/ipod_sun_untethered)
- [go-librespot](https://github.com/devgianlu/go-librespot)
- [go-librespot API specification](https://github.com/devgianlu/go-librespot/blob/master/api-spec.yml)
- [librespot](https://github.com/librespot-org/librespot)
- [Raspotify](https://github.com/dtcooper/raspotify)
- [spotifyd MPRIS/D-Bus control](https://docs.spotifyd.rs/advanced/dbus.html)
- [WirePlumber Bluetooth configuration](https://pipewire.pages.freedesktop.org/wireplumber/daemon/configuration/bluetooth.html)
- [Spotify February 2026 Web API migration guide](https://developer.spotify.com/documentation/web-api/tutorials/february-2026-migration-guide)

Information checked 30 September 2026.
