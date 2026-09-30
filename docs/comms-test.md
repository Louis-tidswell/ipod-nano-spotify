# Pi–Nano communication test

This proof uses NanoApps' existing custom SCSI memory commands. It does not
write commands through the iPod filesystem.

## Mailbox layout

Two 512-byte, SCSI-aligned DRAM pages avoid read/write races:

| Direction | Address | Magic | Purpose |
|---|---:|---|---|
| Nano to Pi | `0x09122000` | `NP2I` | Hello and button events |
| Pi to Nano | `0x09122200` | `PI2N` | Heartbeat, acknowledgement and counter |

Each header contains seven little-endian 32-bit words: magic, protocol version,
sequence/generation, event/acknowledgement, value/counter, Nano session nonce,
and an XOR checksum salted with `0xa5a55a5a`. The rest of each page is reserved.

The pages sit after the NanoApps trace ring and before the resident service.
They do not overlap the file cache, trace data, resident image, framebuffer,
LVGL pool, app BSS or app executable described by the current NanoApps memory
map.

## Expected result

Open **Pi Link Test** on the iPod. It changes from `Waiting for Pi...` to
`Pi connected`. Each tap on **SEND TEST** sends a new sequence number to the Pi.
The Pi increments its counter, acknowledges that sequence and returns the new
counter. The iPod then displays `Round trip OK` and the new value.

The initial implementation polls at 5 Hz. A valid response must echo the Nano's
session nonce, and a three-second heartbeat gap changes the app status to
`Pi link timed out`.
