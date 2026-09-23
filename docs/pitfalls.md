# Pitfalls I walked into

*Русская версия: [pitfalls.ru.md](pitfalls.ru.md)*

This is everything that cost me the most time. If you are reproducing this, read this file first.

## 1. Configuring the CCCD with an unacknowledged write — a silent dead end

The most expensive mistake. Writing `02 00` to the `0x0016` descriptor with `ATT_WRITE_CMD` (opcode `0x52`, "no response") looks like a success: no error, the tool says nothing. But the device **drops** the packet — the Bluetooth spec explicitly forbids configuring a CCCD with an unacknowledged command. Indications never start, and you sit there waiting for data that will never come.

Do it right: `ATT_WRITE_REQ` (`0x12`), waiting for `ATT_WRITE_RSP` (`0x13`). One opcode byte of difference.

## 2. Indications without confirmation — the stream freezes

Indications (`0x1D`), unlike notifications (`0x1B`), are delivered with a guarantee: the device sends a record and **waits** for `ATT_HANDLE_VALUE_CONFIRMATION` (`0x1E`). No confirmation — no next record, and a disconnect a couple of seconds later.

Many examples online skip this because they got lucky: some stacks confirm automatically. If you write your own client, confirm explicitly.

## 3. The 1500 ms hardware watchdog versus full GATT discovery

The device drops the link if there is no meaningful activity for one and a half seconds. The standard path through a high-level library (`Bleak` and anything else layered over BlueZ) waits for BlueZ to read the entire characteristic table — on a slow link that is 2.5–3.5 seconds. The result is `failed to discover services, device disconnected`, with the device dropping out **before** any useful work happens.

The conclusion: discovery has to be bypassed. Either work directly from known handles (`gatttool` does exactly that — it performs no service discovery at all), or write your own client on a raw socket.

## 4. `EINVAL` on an L2CAP socket: 13 versus 14 bytes

If you build your own ATT client over `AF_BLUETOOTH` / `BTPROTO_L2CAP`, the kernel answers `EINVAL` to `connect()` — and it has nothing to do with permissions or the adapter. The kernel compares the length you pass against `sizeof(struct sockaddr_l2)` and requires **14 bytes** (2 + 2 + 6 + 2 + 1 + 1 of trailing alignment). A tightly packed 13-byte structure is rejected.

In Python this looks like a missing `_pack_` (or an extra one) in a `ctypes` structure definition. The bug is nasty because every field looks correct.

## 5. Python 3.11 cannot specify the address type for L2CAP

The three-element tuple (`address`, `psm/cid`, `address type`) for `BTPROTO_L2CAP` only appeared in Python 3.12. On 3.11 `connect()` answers `wrong format`. The workaround is to call `connect()` directly through `libc` with a hand-built `sockaddr_l2` structure (`ctypes`), then use the very same socket normally with `send`/`recv`.

## 6. `Device or resource busy (16)`

This comes back when a client dies abnormally and an unclosed L2CAP socket to the same address stays hanging in the kernel. Waiting does not help — the connection is held in kernel structures.

The cure is a radio reset: `hciconfig hci0 down && hciconfig hci0 up` (or `btmgmt --index 0 power off/on`). The abnormal exit is usually provoked by `gatttool`, which has a known memory bug (`malloc_consolidate(): unaligned fastbin chunk detected`) triggered when a disconnect arrives while it is reading the socket.

## 7. The BlueZ cache lies

Once a device has been seen, `bluetoothctl` may keep showing it "in range" even when it is switched off and sitting in a drawer. Check the signal level and the freshness of the data — it is easy to spend half an hour "connecting" to a target that is not there.

And separately: `bluetoothctl remove <MAC>` **wipes the characteristic cache**. After that every connection performs a full discovery again — so the command that "tidies things up" reliably makes the slow-link situation worse.

## 8. `AuthenticationFailed` with not a single SMP packet

If the device drops the link before the stack manages to start the key exchange, BlueZ returns `AuthenticationFailed` — a stock stub. Not a single SMP packet appears on air. This is not "the key was rejected" and not "the device wants a password": it simply means the connection died too early. The cure is the same — fit into the timing window and do not demand pairing up front.

## 9. The address type is mandatory

`DE:3E:...` — the top two bits are `11`, which means a random static address. Connect with a public type (for instance, a bare string with no explicit type) and the device ignores the attempt. It has to be random.

## 10. Where the Bluetooth log lives on Android

Not in the root and not in the obvious places. On MIUI/Xiaomi the working paths are `/MIUI/debug_log/` (HCI dumps live there) and `/data/misc/bluetooth/logs/` (needs root, or a bug report from the developer menu). The files are in btsnoop format and can be read by any HCI parser; without `tshark` they can be decoded by your own 100-line script.

## 11. Syncing with the app wipes the history from the device queue

A separate and the most painful trap. The app collects the records and marks them in the device's memory as sent — after that they are never handed out over the air again. So if you sync the monitor with the phone first and only then decide to pull the history yourself, you get nothing.

**Back up first, sync later.** That is how I lost 69 records, and there is no way to get them back over the air.
