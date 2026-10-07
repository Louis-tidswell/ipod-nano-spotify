# Nano Spotify mailbox protocol

Two 512-byte DRAM pages carry the USB/SCSI protocol:

| Direction | Address | Magic |
|---|---:|---|
| Nano to Pi | `0x09122000` | `NP2I` |
| Pi to Nano | `0x09122200` | `PI2N` |

Version **3** requires updating Nano and Pi together. All integers are
little-endian. Checksum: `0xA5A55A5A` XOR every 32-bit word in the entire page
except the checksum word, including the final magic. This covers device identity
and displayed text.

## Commands

Seven words: magic, version, sequence, command, signed value, launch nonce,
checksum. At byte 28: address[18], NUL-terminated. Remaining bytes are zero.
Nano publishes magic last after a barrier and waits for acknowledgement while
linked before sending another command.

| Command | Value | Address |
|---|---|---|
| 0 hello | 0 | empty |
| 1 play/pause | 0 | empty |
| 2 previous | 0 | empty |
| 3 next | 0 | empty |
| 4 volume delta | signed percent | empty |
| 10 device list page | zero-based page | empty |
| 11 toggle discovery | 0 | empty |
| 12 pair if needed, connect and route | 0 | device |
| 13 disconnect | 0 | device |
| 14 forget, after confirmation | 0 | device |

Addresses keep action targets stable when discovery reorders the list. The
bridge validates adapter membership. Disconnect/Forget suppress reconnection
for the preferred speaker. Acknowledgement means received, while Bluetooth
progress/completion/errors arrive asynchronously. Mutating actions received
while busy are ignored; Nano disables their buttons while busy.

## Status

Eleven words: magic, version, generation, acknowledged sequence, player flags,
position milliseconds, duration milliseconds, volume percent, player error,
echoed launch nonce, checksum. UTF-8 text: track at 44 (96 bytes), artist at
140 (96), player message at 236 (64).

At 300: four words for Bluetooth flags, total devices, zero-based page, row
count (0–3). At 316: Bluetooth message[40]. Three 52-byte rows at 356:
address[18], uint16 flags, name[32]. All strings are bounded and NUL-terminated.

Bluetooth flags: powered=1, scanning=2, busy=4, error=8, any audio output=16.
Device flags: paired and bonded=1, connected=2, audio output available=4.

Nano verifies page size at compile time, then checksum, version, nonce,
generation and row count before using status. Pi sends status at 5 Hz; Nano
shows offline after three seconds without a valid new generation. Bluetooth
operations run on a separate worker. Discovery lasts at most 60 seconds.

Checks: `python3 -m unittest discover -s tests -v`.
