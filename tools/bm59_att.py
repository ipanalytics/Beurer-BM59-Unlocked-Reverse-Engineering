#!/usr/bin/env python3
"""Прямой ATT-канал L2CAP для Python 3.11 (без 3-элементного кортежа).

connect() делается через libc с честной структурой sockaddr_l2 — Python 3.11
не умеет передавать bdaddr_type, поэтому обходим это ctypes-вызовом.

Порядок по снятому HCI-логу приложения:
  connect (LE random, CID 4 = ATT) -> 250 мс на SMP ->
  WRITE_REQ 0x0016 = 02 00 (CCCD, обязателен ответ) ->
  WRITE_CMD 0x0012 = время (0x2A2B) -> приём индикаций 0x1D + подтверждение 0x1E
"""
import ctypes
import json
import os
import socket
import struct
import time
from datetime import datetime
from zoneinfo import ZoneInfo

MAC = "AA:BB:CC:DD:EE:FF"
AF_BLUETOOTH = 31
ATT_CID = 4
BDADDR_LE_RANDOM = 2          # значение ядра Linux (в Python-кортежах оно же)
SOL_BLUETOOTH, BT_SECURITY, BT_SECURITY_MEDIUM = 274, 4, 2

ATT_WRITE_REQ, ATT_WRITE_RSP, ATT_WRITE_CMD = 0x12, 0x13, 0x52
ATT_IND, ATT_CNF = 0x1D, 0x1E

LOG = open("/var/log/bm59-l2cap.log", "a", buffering=1)
FOUND: list = []

libc = ctypes.CDLL("libc.so.6", use_errno=True)


class BdAddr(ctypes.Structure):
    _fields_ = [("b", ctypes.c_uint8 * 6)]


class SockAddrL2(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("l2_family", ctypes.c_uint16),
                ("l2_psm", ctypes.c_uint16),
                ("l2_bdaddr", BdAddr),
                ("l2_cid", ctypes.c_uint16),
                ("l2_bdaddr_type", ctypes.c_uint8)]


def log(m: str) -> None:
    line = datetime.now().strftime("%H:%M:%S ") + m
    print(line, flush=True)
    LOG.write(line + "\n")


def connect_l2cap(s: socket.socket) -> None:
    a = SockAddrL2()
    a.l2_family = AF_BLUETOOTH
    a.l2_psm = 0
    a.l2_cid = ATT_CID
    a.l2_bdaddr_type = BDADDR_LE_RANDOM
    for i, octet in enumerate(reversed([int(x, 16) for x in MAC.split(":")])):
        a.l2_bdaddr.b[i] = octet
    if libc.connect(s.fileno(), ctypes.byref(a), ctypes.sizeof(a)) != 0:
        err = ctypes.get_errno()
        raise OSError(err, os.strerror(err))


def time_pkt() -> bytes:
    n = datetime.now(ZoneInfo("Europe/Berlin"))
    return bytes([n.year & 0xFF, (n.year >> 8) & 0xFF, n.month, n.day,
                  n.hour, n.minute, n.second, n.isoweekday(), 0x00, 0x00])


def attempt() -> bool:
    s = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_SEQPACKET, socket.BTPROTO_L2CAP)
    try:
        s.setsockopt(SOL_BLUETOOTH, BT_SECURITY, struct.pack("BB", BT_SECURITY_MEDIUM, 0))
    except OSError as e:
        log(f"setsockopt: {e}")
    s.settimeout(6.0)
    try:
        connect_l2cap(s)
    except OSError as e:
        log(f"connect: {e}")
        s.close()
        return False
    log("L2CAP LE RANDOM подключён")
    time.sleep(0.25)                                  # ядру на SMP

    s.send(struct.pack("<BHH", ATT_WRITE_REQ, 0x0016, 0x0002))
    try:
        rsp = s.recv(64)
    except OSError as e:
        log(f"ответ CCCD: {e}")
        s.close()
        return False
    if rsp and rsp[0] == ATT_WRITE_RSP:
        log("CCCD 02 00 ПОДТВЕРЖДЁН прибором")
    elif rsp and rsp[0] == 0x01:
        log(f"ATT-ОШИБКА на CCCD: {rsp.hex()}")
        s.close()
        return False
    else:
        log(f"ответ на CCCD: {rsp.hex() if rsp else 'пусто'}")

    for i in range(4):
        s.send(struct.pack("<BH", ATT_WRITE_CMD, 0x0012) + time_pkt())
        log(f"время отправлено {i + 1}/4")
        time.sleep(1.2)

    s.settimeout(3.0)
    end = time.time() + 30
    while time.time() < end:
        try:
            d = s.recv(1024)
        except socket.timeout:
            continue
        except OSError as e:
            log(f"линк закрыт: {e}")
            break
        if not d:
            break
        if d[0] == ATT_IND and len(d) >= 4:
            h = struct.unpack("<H", d[1:3])[0]
            v = d[3:]
            log(f"ИНДИКАЦИЯ h=0x{h:04x} {v.hex()}")
            if len(v) >= 7 and v[0] == 0x16:
                sy = v[1] | (v[2] << 8); di = v[3] | (v[4] << 8); pu = v[5] | (v[6] << 8)
                FOUND.append([sy, di, pu, v.hex()])
                log(f"   -> ИЗМЕРЕНИЕ {sy}/{di}, пульс {pu}")
            try:
                s.send(bytes([ATT_CNF]))
            except OSError:
                pass
        else:
            log(f"ATT op={hex(d[0])} {d.hex()}")
    s.close()
    return bool(FOUND)


def main() -> int:
    log("=== ctypes L2CAP ATT: старт ===")
    for n in range(1, 21):
        if attempt():
            break
        time.sleep(3)
    json.dump(FOUND, open("/root/bm59_l2cap.json", "w"), ensure_ascii=False, indent=1)
    log(f"=== итог: измерений {len(FOUND)} -> /root/bm59_l2cap.json ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
