# Nano Spotify mailbox protocol

The Spotify controller uses the two proven 512-byte DRAM mailbox pages:

| Direction | Address | Magic |
|---|---:|---|
| Nano to Pi | `0x09122000` | `NP2I` |
| Pi to Nano | `0x09122200` | `PI2N` |

Protocol version 2 keeps commands sequence-numbered and bounded. Commands are
hello, play/pause, previous, next, and signed relative volume adjustment. The
Pi acknowledges the latest sequence number.

Each Pi status page contains player flags, position, duration, volume percent,
error code, and fixed-size UTF-8 fields for track, artist, and status text. The
flags describe player readiness, authentication, playing/paused/buffering, and
Bluetooth speaker connection.

Both directions use the magic value as a commit marker and validate an XOR
checksum over their integer header. A per-launch Nano nonce prevents a stale
response from an earlier app session being accepted. The Pi writes a fresh
generation at 5 Hz, and the Nano shows the link offline after three seconds
without a valid generation.
