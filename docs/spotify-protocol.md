# Nano Spotify mailbox protocol

Two 512-byte pages: Nano→Pi at `0x09122000` (magic `NP2I`), Pi→Nano at
`0x09122200` (magic `PI2N`). Version **4** requires deploying Nano and Pi together.
All integers are little-endian. Checksum: `0xA5A55A5A` XOR all 128 words except
the checksum word, including the final magic. Text is bounded UTF-8 with NUL.

## Command page

Seven words: magic, version, sequence, command, signed value, per-launch nonce,
checksum. Bytes 28–91: payload[64] (Bluetooth address or Spotify playlist URI).
Remaining bytes zero. Nano commits magic last after a barrier, queues up to eight
commands and publishes the next only after acknowledgement.

| Command | Value | Payload |
|---|---|---|
| 0 hello | 0 | empty |
| 1 play/pause | 0 | empty |
| 2 previous | 0 | empty |
| 3 next | 0 | empty |
| 4 volume delta | signed percent | empty |
| 10 Bluetooth page | zero-based page | empty |
| 11 toggle discovery | 0 | empty |
| 12 pair/connect/route | 0 | device address |
| 13 disconnect | 0 | device address |
| 14 forget after confirmation | 0 | device address |
| 20 Library page | zero-based page | empty |
| 21 play playlist | 0 | full playlist URI |
| 22 refresh Library | zero-based page | empty |
| 30 view | 0 Now, 1 Library, 2 System | empty |
| 31 art received | unsigned image ID bits | empty |

An acknowledgement means received; Bluetooth/Library progress arrives
asynchronously. Device addresses and full playlist URIs prevent list reordering
from changing an action target. Bluetooth addresses must belong to the pinned
USB adapter; playlist URIs must belong to the loaded page and active account.

## Status page

Eleven words: magic, version, generation, acknowledged sequence, player flags,
position ms, duration ms, volume percent, player error, echoed nonce, checksum.
Text: track[64] at 44, artist[64] at 108, player message[64] at 172.

At 236: five words `kind, flags, total, page, count`. A 256-byte union begins at
256. Every status variant carries the player state above it.

- Kind 1 Bluetooth: flags powered=1, scanning=2, busy=4, error=8, audio output=16.
  Total devices, zero-based page, count 0–3. Union: message[64] then three rows
  of address[18], uint16 flags, name[44]. Row flags bonded=1, linked=2, audio=4.
- Kind 2 Library: flags loading=1, ready=2, error=4. Total playlists, zero-based
  page, count 0–2. Union: message[64], two rows of URI[40], name[52], uint32 length.
- Kind 3 art: flags=image ID, total=18432, page=chunk index 0–71, count=256.
  Union: 256 bytes of a 96×96 RGB565 little-endian image. Total zero clears art.
  Image ID is CRC32 of all pixels (zero remapped to one). Nano only shows art
  after all 72 checked chunks arrive and acknowledges its image ID.
- Kind 4 Config: flags/total/page/count are zero. The union starts with a
  NUL-terminated URL[64], such as `http://192.168.1.120:8080`. On System, the bridge
  alternates Config and Bluetooth frames. The Nano caches each independently.
  This extends version 4; older version-4 apps ignore Config frames and continue
  receiving Bluetooth frames. Playback, library and artwork frames are unchanged.

Status normally runs at 5 Hz; unacknowledged art in Now runs at up to 20 Hz.
The transfer cycles chunks until acknowledged and restarts on a new Nano nonce.
There are no extra RAM scratch regions. Nano rejects bad checksums/versions,
stale nonces/generations and invalid row/chunk counts. Offline timeout is three
seconds. Discovery stops after 60 seconds. All network content operations and
Bluetooth actions run on background workers.

Checks: `python3 -m unittest discover -s tests -v` and `python3 tests/run_nano_ui.py`.
