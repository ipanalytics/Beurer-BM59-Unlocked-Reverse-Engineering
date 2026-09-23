# Beurer BM59 Unlocked - Reverse Engineering Project

*Formal byte-level specification (Russian): [docs/specification.ru.md](docs/specification.ru.md)*

**Reverse engineering the Beurer BM59 blood pressure monitor — pulling measurements straight into Linux, with no vendor app and no phone.**

*Русская версия: [README.ru.md](README.ru.md)*

The Beurer BM59 will only hand over your blood pressure history to its own phone app, over an encrypted BLE link with a closed protocol. That annoyed me: the monitor sits on my desk, the server sits next to it, and the data still has to make a detour through someone else's software.

So I took the protocol apart. Now the readings are collected by my own code on Linux — over the air, without the app, without accounts, without the cloud.

Everything below is not theory: the bytes came out of a real Bluetooth capture, were verified against a live device, and match down to the last bit.

---

## The protocol in short

The BM59 is built on a Nordic chip and tunnels **standard Bluetooth SIG GATT characteristics** through a Nordic UART service. Beurer invented nothing of their own — and that is the key to the whole thing.

- **Characteristic `...6e400002...` (write, handle `0x0012`)** — the inbound channel. The current-time packet goes here.
- **Characteristic `...6e400003...` (indicate, handle `0x0015`)** — the outbound channel. Measurements come out here.
- **CCCD descriptor, handle `0x0016`** — subscribing to the outbound channel.

The order is exactly what the phone app does:

1. Connect to the monitor.
2. Write `02 00` to the CCCD (`0x0016`) — **as a write request**, i.e. `ATT_WRITE_REQ` (`0x12`). A write without response (`0x52`) is silently dropped by the device: the Bluetooth spec explicitly forbids configuring a CCCD with an unacknowledged command.
3. Write the 10-byte current time to `0x0012` — this one as a command without response (`ATT_WRITE_CMD`).
4. Accept incoming indications (`0x1D`) from `0x0015` and confirm each one (`0x1E`), otherwise the device goes quiet.

### The time packet is the standard `Current Time` characteristic (0x2A2B)

```
E8 07 | 01 | 0F | 09 | 0C | 21 | 01 | 00 | 00
  |     |    |    |    |    |    |    └────┴── fractions of a second, adjust reason
  |     |    |    |    |    |    └── day of week (1 = Monday)
  |     |    |    |    |    └── seconds
  |     |    |    |    └── minutes
  |     |    |    └── hours
  |     |    └── day of month
  |     └── month
  └── year (little-endian: 0x07E8 = 2024)
```

### The reply is the standard `Blood Pressure Measurement` characteristic (0x2A35)

```
16 | 80 00 | 52 00 | 4A 00 | E8 07 01 0F 09 0C 21 | 57 00 | 00 00 00
 |     |       |       |            |                 |        └── measurement status
 |     |       |       |            |                 └── pulse status
 |     |       |       |            └── measurement timestamp: 2024-01-15 09:12:33
 |     |       |       └── pulse
 |     |       └── diastolic
 |     └── systolic
 └── flags (mmHg units, timestamp present, pulse present, status present)
```

The monitor starts streaming records **on its own**, as soon as indications are enabled and a time packet arrives. There is no "give me the history" request — just a stream.

---

## Why this was not trivial

Three things make the task non-obvious, and each one cost me an evening.

**1. A hardware watchdog of ~1500 ms.** If the device sees no meaningful GATT activity for about a second and a half, it drops the link. There is no "let's take our time and enumerate the services first".

**2. The standard GATT discovery does not fit in that window.** Before handing control back, BlueZ has to read the whole characteristic table (`ServicesResolved`). On a slow link — a USB dongle passed through into a virtual machine — that takes 2.5–3.5 seconds, and the device is gone long before. Android only made it because it already had the characteristic handles cached and hit them directly, with no discovery at all.

**3. The address type.** The device advertises a **Random Static** address. Connect "by the MAC string" without specifying the type and the stack knocks as if it were a public device — the monitor simply ignores it.

The combination that finally worked:

- connect directly, skipping full GATT discovery — straight to known handles (via `gatttool`, or your own ATT socket);
- address type: **random**;
- security level: **medium** (Just Works) — encryption comes up on the fly, no keys, no PIN prompt;
- after connecting, wait ~250–350 ms for the kernel to finish the key exchange, then send the first write;
- confirm every indication with `0x1E`.

---

## Reproducing it

You need Linux with a Bluetooth adapter and root. Put the monitor into transfer mode (hold its button for ~3 seconds until the Bluetooth icon blinks).

The quick path is interactive `gatttool`:

```bash
hciconfig hci0 reset

gatttool -t random -b <MAC> -l medium -I
# then, in the interactive shell, with no pause longer than a fraction of a second:
connect
char-write-req 0x0016 0200
char-write-cmd 0x0012 <10-byte time packet>
# and listen: Indication handle = 0x0015 value: 16 ...
```

Note the asymmetry: `char-write-req` (acknowledged) for the CCCD, `char-write-cmd` (unacknowledged) for the time. Swap them and the device silently does nothing.

The more robust path — and the one I ended up using — is your own client on a Linux kernel socket: `AF_BLUETOOTH` + `BTPROTO_L2CAP`, the fixed ATT channel (CID 4), a `sockaddr_l2` structure with address type `2` (LE random) and security level medium. Every timing detail is then under your control down to the millisecond, with no dependency on command-line utilities.

---

## How it was cracked

The protocol was not guessed and not brute-forced — it was read out of actual traffic:

1. Enabled the HCI snoop log on Android, so the system records every Bluetooth exchange to a file.
2. Synced the app with the monitor and pulled the capture.
3. Parsed the capture by hand: ATT packets sorted by opcode, handle and value.
4. Compared the bytes against the Bluetooth SIG specification — the payload inside the Nordic tunnel turned out to be exactly the standard `0x2A2B` and `0x2A35` structures.
5. Dropped the "Beurer has its own protocol" hypothesis for good: the structure matched field by field, down to the day-of-week and the timestamp.

One finding worth repeating: **syncing with the phone app marks records in the monitor's memory as sent**, and from then on they are never handed out over the air again. If the history matters — pull it yourself first, and only then let the app sync.

---

## Status

- Protocol cracked and verified on a live device — **done**.
- Direct kernel-socket channel, new measurements landing on the server — **working**.
- Pulling the monitor's stored history over the air — **in progress**.

---

## Disclaimer

This project is about my own device and my own data. Nothing was "hacked" in the sense of defeating a protection: it uses the open Bluetooth standard and the stock characteristics the device itself exposes. The monitor was not opened and its firmware was not modified. If you reproduce this, do it on your own hardware — and remember that vendor apps may wipe the history from the device's memory.

---

