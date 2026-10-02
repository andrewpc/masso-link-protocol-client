# MASSO Link UDP Protocol Specification

**⚠️ WORK IN PROGRESS - INCOMPLETE DOCUMENTATION**

**Note**: This protocol specification is based on reverse engineering, packet analysis, and testing on a real controller. Many packet fields and status bytes are not fully understood or documented. This document represents the current state of knowledge and may contain errors or omissions.

Sources: MASSO Link captures on a lathe running v5.09 (Nov 2025) and v5.13 (Apr 2026), and live tests of this client on a v5.13 lathe (Sep 2026). Where a statement has only been checked on one firmware version, it says so.

---

This document describes the MASSO controller UDP protocol as implemented in `masso_udp_client.py`.

## Overview

- **Transport**: UDP
- **Controller IP**: User-specified (required via --host argument)
- **Controller Port**: 65535 (the controller sends its replies from this port)
- **Client Port**: The client binds the first free port in 11000-11050 and sends from it
- **Reply Destination**: The controller sends replies to port 11000, and sometimes also to the port the request came from. MASSO Link sends from an ephemeral port and still gets its replies on 11000.
- **Additional plasma-controller finding**: On a 5-axis MASSO Touch v5.13 / Core 2.05, repeated uploads became unreliable when reusing the same TX socket. Recreating only the ephemeral TX/upload socket before each upload restored repeat-upload reliability while leaving the bound status RX socket running. This behavior is used by Send-to-MASSO Manager; it has not been confirmed as necessary on the v5.13 lathe.
- **Packet Structure**: `[CRC16-CCITT 2 bytes][Magic 0x03 0x00 2 bytes][Type 1 byte][Payload...]`

## Packet Types

### Discovery/Version (Type 0x02)
- **Request**: 10 bytes total (8 bytes after the CRC)
  - Magic: `0x03 0x00`
  - Type: `0x02`
  - Payload: `0xf8 0x2a 0x00 0x00 <month>`
  - The last byte was the current month in MASSO Link captures (`0x0b` in November), but it is not checked: `0x00`, `0x09` and `0x0b` all get a reply on v5.13.
- **Response**: 46 bytes
  - Contains version string starting at byte 12

### Configuration Request (Type 0x03)
- **Request**: 14 bytes total
  - Magic: `0x03 0x00`
  - Type: `0x03`
  - Clock: 6 bytes `[hour, minute, second, day, month, year - 2000]`
  - Unknown: 3 bytes, always `0x00` in captures
- **Effect**: The controller sets its clock from these bytes. Sending all zeros resets the controller clock to 12:00 AM (confirmed on v5.13). The client sends the PC's time at connect.
- **Response**: 10 bytes
  - Magic: `0x03 0x00`
  - Type: `0x03`
  - Serial Number: 2 bytes (little-endian)

Example from a MASSO Link capture at 13:20:58 on 28 Nov 2025:
```
03 00 03 0d 14 3a 1c 0b 19 00 00 00
         hh mm ss dd mo yy
```

### Keepalive/Status Request (Type 0x01)
- **Request**: 10 bytes total
  - Magic: `0x03 0x00`
  - Type: `0x01`
  - 5 bytes: `[hour, minute, second, day, month]` of the connect time
- These 5 bytes are not a live clock. MASSO Link sends the connect time and leaves it unchanged; in captures the bytes are later overwritten by the argument of other commands (the last tool index after a tool download, the chunk counter during an upload).
- Sent about once per second while connected.
- **Response**: 270 bytes (status packet)

### Tool Data Request (Type 0x08)
- **Request**: 10 bytes total
  - Magic: `0x03 0x00`
  - Type: `0x08`
  - Tool Index: 1 byte (1-255)
  - 4 bytes: `[minute, second, day, month]` of the connect time
  - Older versions of this document listed these bytes as a constant `0x22 0x2c 0x1c 0x0b`; that was the connect time (34:44 on 28 Nov) of the capture it came from.
- **Response**: 38 bytes
  - Tool index at byte 5
  - Tool name starts at byte 6, null-terminated

#### Tool-table scope caveat

The observed type-`0x08` response only confirms tool index plus null-terminated tool name. MASSO's full tool table contains additional fields in the UI, but offsets/diameter/wear/slot fields have not been decoded from this packet exchange in the Touch/G3 work. Implementations should keep tool-table access read-only until the complete binary format is verified.

### File Upload - Start Upload (Type 0x0A)
- **Request**: Variable length
  - Magic: `0x03 0x00`
  - Type: `0x0A`
  - File Size: 4 bytes (little-endian)
  - Unknown: 3 bytes `0x00 0x00 0x01`
  - Backslash + Null: `0x5c 0x00`
  - Remote name: ASCII string, null-terminated. May include a folder, separated with `\`.
  - Padding: zeros so the packet after the CRC is a multiple of 4 bytes, minimum 28 (a 30-byte packet)
  - Length after the CRC is therefore `max(28, 16 + ceil4(len(name) + 1))`. This reproduces both captured start packets: 30 bytes for `adaptive.nc` (11 characters) and 34 bytes for `1st-test-al.nc` (14 characters).
- **Response**: 10 bytes
  - Byte 4: `0x0A`
  - Byte 5: `0x00` when the upload is accepted
  - Bytes 6 onward: the previous upload's final chunk counter, not a status code. After a 282-chunk upload the next start ACK was `0a 00 1a 01 00 00` (`0x011a` = 282). Do not require these bytes to be zero.
  - If the name is invalid (folder missing, `/` in the name, over 255 characters), the controller sends no reply at all. Over 255 characters can also freeze it (see Filename Restrictions).

#### Alternate folder-aware start packet observed on plasma controllers

Additional captures from a 5-axis MASSO G3 v5.13 / Core 2.00 showed an alternate folder-aware start packet in which folder and filename are carried separately. Total UDP payload lengths of 38 and 50 bytes were observed in the Touch/G3 capture set; the 50-byte form was captured on the G3.

Observed layout after the CRC:

```text
03 00 0A
[file size 4 LE]
00 00
[folder length 1]
[folder ASCII]
00
[filename ASCII]
00
[padding to 4-byte boundary]
```

A known-good G3 target used folder `\5178-24_44-IDUC\`. This is a controller-specific observation and should not replace the simpler combined-name format verified on the v5.13 lathe.

The same G3 test set also produced a start-upload reject with byte 5 = `0xF7` (`f7 00` at bytes 5-6). This reject code has not been reproduced on the v5.13 lathe.

### File Upload - Data Chunk (Type 0x0B)
- **Request**: Variable length
  - Magic: `0x03 0x00`
  - Type: `0x0B`
  - Chunk Index: 4 bytes (little-endian, starts at 0)
  - Chunk Length: 4 bytes (little-endian), the real number of data bytes in this chunk
  - Data
  - Trailer: zero bytes
- **Full chunk**: 1422 data bytes and a 3-byte trailer, 1438 bytes in total
- **Short final chunk**: sent compact with its real length. The packet after the CRC must be a multiple of 4 bytes, so the trailer is `(-(11 + length)) % 4`, with 0 becoming 4 (1 to 4 bytes).
  - Examples: 863 bytes → 2-byte trailer (878-byte packet, from a v5.09 capture); 1000 → 1; 1001 → 4; 1002 → 3; 1003 → 2 (all accepted on v5.13).
  - A wrong trailer makes the controller ignore the chunk: a 1003-byte chunk with a 4-byte trailer got no ACK on v5.13.
- **Fallback**: pad the final chunk's data area to 1422 bytes with a 3-byte trailer, keeping the real length in the length field. The client uses it only if the compact chunk gets no ACK. Historical testing on a 5-axis MASSO G3 v5.13 / Core 2.00 showed this fallback recovering 411-byte and 180,499-byte uploads; however, those compact packets were generated with an older, incorrect odd/even trailer rule. Both final lengths were congruent to 3 mod 4 and should have used a 2-byte trailer. Therefore those tests prove the fallback is useful, but do **not** prove the G3 inherently requires full-size final packets. Re-testing with the corrected compact rule is still needed.
  - Versions of this client before v0.0.3 padded the final chunk to 1422 bytes and also set the length field to 1422. v5.13 accepted that and the files had the right number of lines, but whether the padding zeros ended up in the file was not checked.
- **Response**: 10 bytes
  - Byte 4: `0x0B`
  - Bytes 6-7: the next expected chunk index, little-endian
  - Example: after chunk 255 the ACK is `0b 00 00 01 00 00`, meaning 256. Reading bytes 5-6 as big-endian gives 0, which is wrong past chunk 255.

## Controller Identification

The MASSO controller provides identification information through specific packet responses:

### Serial Number Discovery

**Location**: Configuration Response (Type 0x03, 10 bytes)
- **Bytes 5-6**: Serial number stored in little-endian format
- **Range**: 0-65535 (16-bit unsigned integer)
- **Example**: Serial "G3-12345" → numeric part "12345" (`0x3039`) stored as `0x39 0x30`

**Packet Structure**:
```
Bytes 0-1: CRC16 (little-endian)
Bytes 2-3: Magic 0x03 0x00
Byte 4:    Packet type (0x03)
Bytes 5-6: Serial number (little-endian)
Bytes 7-9: Reserved (typically 0x00)
```

**Implementation Notes**:
- Serial number is only available in the configuration response
- Must be extracted during initial connection sequence
- Numeric serial range suggests 16-bit unsigned integer format
- The "G3-" prefix (or similar) is not stored in the packet - only numeric portion

**Usage Example**:
```python
# Extract serial from configuration response
serial = int.from_bytes(data[5:7], 'little')  # 12345
print(f"Controller Serial: {serial}")
```

### Version Information

**Location**: Discovery/Version Response (Type 0x02, 46 bytes)
- **Bytes 12+**: Version string (ASCII, null-terminated)
- **Examples**: `@Lathe v5.09`, `@Lathe v5.13`

## Status Packet Structure (270 bytes)

> [!NOTE]
> The following field mappings have been reverse-engineered. Recent tests on newer firmware (v5.10) reveal the true purpose of several bytes.

Key fields:
- Byte 5: Job Progress Percentage
  - Decimal value from `0` to `100` (e.g., `0x64` = `100%`)
  - Increments steadily during execution
  - Remains at the last value if execution is interrupted
- Byte 6: Execution Active Flag
  - `0x00`: Not Running (Idle, Feed Hold, or E-Stop)
  - `0x02`: Actively Running
  - **Note**: Internal operations like Homing or Probing often appear as "Running" and may execute internal macros.
- Byte 7: `0xFF` in every packet in our captures, including during an E-Stop. Others have reported it changing (`0x15` during a plasma torch breakaway), so it may be a fault code; not verified here.
  - On a 5-axis MASSO Touch v5.13 / Core 2.05, a torch-breakaway test changed byte 7 `0xFF -> 0x15 -> 0xFF`. The `0x15` state lasted about 8.1 seconds in that capture. This confirms byte 7 is not constant across controller types/configurations.
- Bytes 8-11: Job count (little-endian)
- Byte 12: User Prompt Waiting Flag (Tool Change, M0, M1, etc.)
  - `0x01`: Normal operation
  - `0x00`: Machine paused, waiting for user input (e.g., manual tool change) and Cycle Start
- Byte 13: Line number (0–255, single byte)
  - **Note**: During Homing, this typically increments as MASSO runs its internal homing macro.
- Bytes 14–16: Always `0x00` — reserved/unused in observed captures
  - Controller-specific difference: on a 5-axis MASSO Touch v5.13 / Core 2.05 and a 5-axis MASSO G3 v5.13 / Core 2.00, bytes 13-16 behaved as a little-endian elapsed-seconds counter rather than byte 13 being an independent line byte. Examples observed on the plasma controllers include `d1 00 00 00` at 3:29 elapsed (209 s), `ff 00 00 00` at 255 s, `00 01 00 00` at 256 s, and `1d 01 00 00` at 285 s. This differs from the lathe captures above and should be treated as controller/firmware-specific until more captures explain the difference.
- Bytes 17-80: Filename of the loaded file (null-terminated, up to 63 bytes)
- Bytes 81-269: Unused/Padded with `0x00` during normal operation

## Feed Hold Detection
Because Feed Hold and E-Stop states simply set Byte 6 to `0x00` while Byte 5 freezes at its current progress percentage, these bytes alone cannot distinguish the exact stop reason. The client infers a feed hold state when:
1. The Execution Active Flag is Running (`0x02`)
2. The Line Number hasn't changed for 1.5 seconds or more
3. The Line Number is greater than 0

## Checksum Calculation

CRC16-CCITT algorithm:
- Polynomial: 0x1021
- Initial value: 0x0000
- Input data: Magic + Type + Payload (all bytes after checksum)
- Output: Little-endian 2 bytes

## File Upload Process

1. Send Start Upload packet with remote name and file size
2. Wait for ACK (Type 0x0A, 10 bytes) with byte 5 = `0x00`
3. Send the file in chunks of 1422 bytes each
4. Each chunk includes the chunk index (not byte offset) and its real length
5. Wait for ACK after each chunk (Type 0x0B, 10 bytes) and check that bytes 6-7 (little-endian) equal the chunk index + 1
6. Send a short last chunk compact, with the 1 to 4 byte trailer described above; if it gets no ACK, resend it padded to full size with the real length field

## Filename Restrictions

- Maximum length: 254 characters for the whole remote name, folder included (v5.13):
  - A 255-character file name uploads, but the controller keeps only a short 8.3 alias (e.g. `T0929_~9.NC`)
  - A 256-character name in the root, and `MASSO\` plus a 254-character name (260 in total), got no reply and froze the controller until it was power cycled
  - In a folder, totals of 254 and 255 (`MASSO\` plus 248 or 249 characters) were stored intact
- ASCII encoding only
- Subdirectories: Use backslash `\` separator
  - Example: `MASSO\file.nc`
  - A name containing forward slash `/` gets no reply on v5.13; the client converts `/` to `\`
  - A leading backslash (`\MASSO\file.nc`) is untested; the packet already has one before the name
- Directories must exist on MASSO (not created automatically); an upload into a missing folder gets no reply on v5.13

### Plasma-controller filename/folder observations

The following were observed in the Touch/G3 plasma test set (5-axis MASSO Touch v5.13 / Core 2.05 and 5-axis MASSO G3 v5.13 / Core 2.00):

- `part#12.tap` uploaded successfully, so `#` is accepted in at least these environments.
- A non-ASCII filename such as `café.tap` failed; plain ASCII is the safe choice.
- `.nc`, `.cnc`, `.tap`, `.eia`, and `.txt` were all accepted during testing.
- Nested backslash-delimited paths worked.
- Missing folders appeared to be created automatically in the plasma-controller tests, and existing files were overwritten.

The missing-folder behavior directly differs from the v5.13 lathe result above, where the folder had to exist first. This should be treated as a controller/core-specific difference rather than a universal protocol rule.

## Error Handling

Additional open questions from Touch/G3 plasma testing:

- Why bytes 13-16 behave as elapsed seconds on the v5.13 plasma controllers while the v5.13 lathe capture treats byte 13 as a line value.
- Whether the alternate folder-aware start packet is tied to controller type, core version, or MASSO Link behavior.
- Whether any controller with the corrected compact trailer rule still genuinely requires the full-size final-packet fallback.
- Full mapping of byte-7 fault/alarm values beyond the confirmed Touch torch-breakaway value `0x15`.

- Upload packets are retried up to 3 times (4 attempts)
- Timeout for ACK response: 2.0 seconds
- Filename validation refuses names over 254 characters and non-ASCII names before sending

## Implementation Notes

- Client binds to first available port in range 11000-11050
- Listen thread runs in background to receive replies
- Keepalive packets sent every 1.0 second when connected
- All packets use little-endian byte order for multi-byte fields
- The time bytes in the discovery, configuration, keepalive and tool request packets all come from one snapshot taken at connect

---
