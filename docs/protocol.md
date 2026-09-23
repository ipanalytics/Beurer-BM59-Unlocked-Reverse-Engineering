# BM59 protocol — byte-level reference

*Русская версия: [protocol.ru.md](protocol.ru.md)*

Everything below comes from a real Bluetooth exchange between the Beurer app and the monitor (Android HCI snoop log), confirmed by experiments on a live device.

## GATT map

A Nordic UART service — the same layout thousands of Nordic-based devices use — with two custom characteristics and one descriptor:

- `6e400001-...` — the service itself;
- `6e400002-...` — Write / Write Without Response, **handle 0x0012** — the channel *into* the device;
- `6e400003-...` — Indicate, **handle 0x0015** — the channel *out of* the device;
- the CCCD of `6e400003-...` — **handle 0x0016** — the indication subscription.

The payload carried inside that tunnel is **standard Bluetooth SIG GATT characteristics**:

- `0x2A2B` Current Time — 10 bytes, into the device;
- `0x2A35` Blood Pressure Measurement — 18 bytes, out of the device.

## Sequence

```
central                                  device
  |                                          |
  |  connect (LE random, sec-level medium)   |
  |----------------------------------------->|
  |  <---- Just Works key exchange ----------|   ~150–350 ms
  |                                          |
  |  ATT WRITE_REQ  handle 0x0016 = 02 00    |
  |----------------------------------------->|   enable indications
  |  <---- ATT WRITE_RSP (0x13) -------------|
  |                                          |
  |  ATT WRITE_CMD  handle 0x0012 = <time>   |
  |----------------------------------------->|   triggers the data
  |                                          |
  |  <---- ATT INDICATE 0x0015 = <record> ---|   stream of records
  |  ---- ATT CONFIRMATION (0x1E) ---------->|
  |  <---- ATT INDICATE 0x0015 = <record> ---|
  |  ---- ATT CONFIRMATION (0x1E) ---------->|
```

Key points:

- **The CCCD must be configured with `ATT_WRITE_REQ` (0x12)**, waiting for the `0x13` response. A write without response (`0x52`) to a CCCD descriptor is forbidden by the spec: the device ignores it and indications never start.
- **The time goes out without a response** (`ATT_WRITE_CMD`, 0x52) — exactly as the phone app does it.
- **Every indication must be confirmed** (`ATT_HANDLE_VALUE_CONFIRMATION`, 0x1E). Indications, unlike notifications, are delivered with guarantee: the device waits for the confirmation and will not send the next record without it, then drops the connection.
- **The order matters**: subscribe first, then send the time. Send the time without a subscription and it goes into the void.

## Time packet: Current Time (0x2A2B), 10 bytes

| Offset | Size | Field | Format |
|---|---|---|---|
| 0 | 2 | year | uint16 little-endian |
| 2 | 1 | month | uint8 (1–12) |
| 3 | 1 | day | uint8 (1–31) |
| 4 | 1 | hours | uint8 |
| 5 | 1 | minutes | uint8 |
| 6 | 1 | seconds | uint8 |
| 7 | 1 | day of week | uint8 (1 = Monday, 7 = Sunday) |
| 8 | 1 | fractions256 | uint8 (1/256th of a second) |
| 9 | 1 | adjust reason | uint8 (bitmask) |

Example — 15 January 2024, 09:12:33, Monday:

```
E8 07 01 0F 09 0C 21 01 00 00
```

Check: `0x07E8` = 2024; `0x01` = January; `0x0F` = 15; `0x09` = 9 hours; `0x0C` = 12 minutes; `0x21` = 33 seconds; `01` = Monday.

The app refreshed this packet **once a minute** during a session — visible in the log as a series of packets with changing minutes and seconds. So the time packet doubles as a keep-alive: the device expects activity.

## Measurement packet: Blood Pressure Measurement (0x2A35), 18 bytes

| Offset | Size | Field (as observed) | Notes |
|---|---|---|---|
| 0 | 1 | flags | `0x16` on the BM59 = mmHg + timestamp + pulse + status |
| 1 | 2 | systolic | uint16, mmHg |
| 3 | 2 | diastolic | uint16, mmHg |
| 5 | 2 | pulse | uint16 - the slot the specification reserves for mean arterial pressure |
| 7 | 7 | timestamp | year(2) month day hour min sec |
| 14 | 2 | measurement status | uint16 |
| 16 | 2 | reserved / user id | observed as zeros |

The flags byte says which units the values use (mmHg or kPa), whether a timestamp is present, whether a pulse is present, and whether a measurement status is present.

A real packet from the capture:

```
16 80 00 52 00 4A 00 E8 07 01 0F 09 0C 21 57 00 00 00
```

Parsed: `0x0080` = 128 systolic, `0x0052` = 82 diastolic, `0x004A` = 74 pulse, timestamp `2024-01-15 09:12:33`, status `0x0057`.

Note something important: in the observed packets the device does not follow the specification's field layout - the pulse arrives in the 5-6 slot, the position the spec reserves for mean arterial pressure, while bytes 14-15 carry the measurement status. Decode strictly "per the spec" and you will read the pulse from the wrong place.

## Link parameters the device expects

- **Address type**: Random Static. In a Linux `sockaddr_l2` structure that is type `2` (`BDADDR_LE_RANDOM`).
- **Security level**: Just Works, no MITM. IO capabilities are `NoInputNoOutput` on both sides, no PIN is requested, and no stored keys are needed for a reconnect.
- **Connection interval**: the device is happy with 7.5–15 ms (on Linux: `conn_min_interval 6`, `conn_max_interval 12`, `conn_latency 0` in the HCI settings).
- **Hardware watchdog of ~1500 ms**: without meaningful GATT activity the device drops the link. A full characteristic-table discovery does not fit in that window — you have to work from known handles immediately.

## What happens to the device's memory

Syncing with the phone app marks the records in the monitor's memory as sent. After that they are never handed out over the air — the device only produces new measurements. Verified: in the Bluetooth capture of an app session exactly one record from the whole history is visible.

The practical conclusion: if the history matters, download it with your own client first, and only then let the app sync. And always take a backup before any sync.
