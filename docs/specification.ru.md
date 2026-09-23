# Reverse Engineering: Beurer BM59 Blood Pressure Monitor — BLE protocol specification

*Английская версия: [specification.md](specification.md)*

Техническая спецификация протокола обмена данными по Bluetooth Low Energy (BLE) для тонометра Beurer BM59 (и совместимых моделей линейки Sanitas/Beurer на чипах Nordic Semiconductor).

Документ описывает процедуру низкоуровневой синхронизации истории измерений, обход аппаратного сторожевого таймера (Watchdog) и формат инкапсулированных пакетов.

> Источник: разбор выполнен по реальному Bluetooth-дампу (HCI snoop log с Android) и проверен на живом приборе.

---

## 1. Режимы работы устройства

Тонометр функционирует в двух принципиально разных режимах вещания:

| Параметр | Режим 1: Live Measurement (Реальное время) | Режим 2: Sync / Memory (Выгрузка архива) |
| :--- | :--- | :--- |
| Активация | Автоматически сразу после окончания накачки манжеты | Зажатие кнопки включения/памяти на выключенном приборе (~3–5 сек) до мигания символа Bluetooth |
| Имя устройства (Adv) | Beurer BM59 | Серийный номер устройства (напр. `01GZE00A1B2C3456`) |
| Окно вещания | 10–15 секунд | 60–90 секунд |
| Профиль GATT | Стандартный Bluetooth SIG Blood Pressure Profile (`0x1810`) | Проприетарный туннель поверх Nordic UART Service (NUS) |

---

## 2. Параметры физического и канального уровня (Link Layer)

* Тип MAC-адреса: BDADDR_LE_RANDOM (Static Random Address, старшие биты 11..., напр. `DE:3E:06:33:5A:F7` — здесь приведён адрес с заменёнными октетами `AA:BB:CC:DD:EE:FF`).
* Модель безопасности: LE Security Mode 1, Level 2 (**Just Works** unauthenticated pairing with encryption). Ввод PIN-кода не требуется.
* Connection Watchdog: в прошивке активен жёсткий аппаратный тайм-аут: ровно 1500 мс. Если в течение 1.5 секунд после установки соединения не произведена запись в CCCD и не отправлен пакет времени, тонометр принудительно вызывает `sd_ble_gap_disconnect()`.
* Требуемые параметры соединения: для укладывания в окно тайм-аута интервалы соединения должны быть минимальными:
  * `conn_min_interval`: 6 (7.5 мс)
  * `conn_max_interval`: 12 (15.0 мс)
  * `conn_latency`: 0

---

## 3. Таблица атрибутов GATT (Nordic UART Service)

Весь обмен в режиме синхронизации памяти идёт через сервис Nordic UART:

* Service UUID: `6e400001-b5a3-f393-e0a9-e50e24dcca9e`

| Handle | UUID | Назначение | Свойства | Правило записи |
| :--- | :--- | :--- | :--- | :--- |
| `0x0012` | 6e400002-... | RX (вход в тонометр) | Write Without Response | Отправка пакетов времени через ATT_OP_WRITE_CMD (`0x52`) |
| `0x0015` | 6e400003-... | TX (выход из тонометра) | Indicate | Поток индикаций ATT_OP_HANDLE_IND (`0x1D`) с записями |
| `0x0016` | 00002902-... | CCCD (дескриптор конфигурации) | Read, Write | Включение индикаций значением `0x0002`. Строго через `ATT_OP_WRITE_REQ` (`0x12`)! |

> ⚠️ **Критическое замечание по CCCD (`0x0016`):**
> Дескриптор CCCD не принимает Write Command (`0x52`). При попытке записи без ответа прошивка SoftDevice игнорирует команду, индикации не активируются. Запись обязана производиться через Write Request (`0x12`) с ожиданием подтверждения Write Response (`0x13`).

---

## 4. Диаграмма последовательности (Protocol Flow)

```
Client (Host)                                         Beurer BM59 (Peripheral)
     |                                                           |
     |---- 1. LE Create Connection (BDADDR_LE_RANDOM) ---------->|
     |<--- 2. LE Connection Complete ----------------------------|
     |                                                           |
     | [ Пауза 200–300 мс для завершения SMP Just Works ядром ]  |
     |                                                           |
     |---- 3. ATT_OP_WRITE_REQ (Handle 0x0016, Data: 02 00) ---->|  (Включение индикаций)
     |<--- 4. ATT_OP_WRITE_RSP (Handle 0x0016) ------------------|
     |                                                           |
     |---- 5. ATT_OP_WRITE_CMD (Handle 0x0012, Data: Time10B) -->|  (Синхронизация времени)
     |                                                           |
     |                                [ Сброс Watchdog таймера ]  |
     |<--- 6. ATT_OP_HANDLE_IND (Handle 0x0015, Data: BP18B) ----|  (Кадр замера 1)
     |---- 7. ATT_OP_HANDLE_CNF -------------------------------->|  (Подтверждение кадра)
     |                                                           |
     |<--- 8. ATT_OP_HANDLE_IND (Handle 0x0015, Data: BP18B) ----|  (Кадр замера 2)
     |---- 9. ATT_OP_HANDLE_CNF -------------------------------->|
     |                                                           |
     |                      ... (до исчерпания очереди буфера)   |
     |<--- 10. LL_TERMINATE_IND (Сессия завершена) --------------|
     |                                                           |
```

---

## 5. Форматы пакетов данных

Архитектурная особенность протокола Beurer: разработчики не создавали кастомный бинарный протокол, а инкапсулировали стандартные структуры Bluetooth SIG поверх туннеля Nordic UART.

### 5.1. Пакет синхронизации времени (Host → Device)

* Куда: Handle `0x0012` (`6e400002`)
* Метод: ATT_OP_WRITE_CMD (`0x52`)
* Длина: ровно 10 байт
* Структура: Bluetooth SIG Current Time Service (`0x2A2B`)

```
[Year_L] [Year_H] [Month] [Day] [Hours] [Minutes] [Seconds] [DayOfWeek] [Fractions256] [AdjustReason]
```

#### Пример сырого пакета:

```
e8 07 01 0f 09 0c 21 01 00 00
```

| Байт(ы) | Значение (HEX) | Расшифровка |
| :--- | :--- | :--- |
| 0..1 | E8 07 | Год: 0x07E8 = 2024 (Little-Endian) |
| 2 | 01 | Месяц: Январь |
| 3 | 0F | Число: 0x0F = 15 |
| 4 | 09 | Час: 0x09 = 9 |
| 5 | 0C | Минуты: 0x0C = 12 |
| 6 | 21 | Секунды: 0x21 = 33 |
| 7 | 01 | День недели: 1 (Понедельник: 1 = Пн, 7 = Вс) |
| 8 | 00 | Доли секунды (Fractions256): 0 |
| 9 | 00 | Причина корректировки (Adjust Reason): 0 |

---

### 5.2. Пакет записи измерения (Device → Host)

* Откуда: Handle `0x0015` (`6e400003`)
* Тип: ATT_OP_HANDLE_IND (`0x1D`)
* Длина: ровно 18 байт полезной нагрузки
* Структура: Bluetooth SIG Blood Pressure Measurement (`0x2A35`)

#### Пример сырого пакета:

```
16 80 00 52 00 4a 00 e8 07 01 0f 09 0c 21 57 00 00 00
```

| Байт(ы) | Значение (HEX) | Поле структуры 0x2A35 | Расшифровка |
| :--- | :--- | :--- | :--- |
| 0 | 16 | Flags | 0b00010110: мм рт. ст., штамп времени присутствует, пульс присутствует, статус присутствует |
| 1..2 | 80 00 | Systolic | 0x0080 = 128 мм рт. ст. |
| 3..4 | 52 00 | Diastolic | 0x0052 = 82 мм рт. ст. |
| 5..6 | 4A 00 | Mean Arterial Pressure / Pulse | 0x004A = 74 (уд/мин) — в наблюдаемых пакетах прибор кладёт сюда пульс |
| 7..13 | E8 07 01 0F 09 0C 21 | Timestamp | 2024-01-15 09:12:33 |
| 14..15 | 57 00 | Measurement Status | Флаги состояния прибора во время накачки |
| 16..17 | 00 00 | Reserved / User ID | Индекс пользователя |

> ⚠️ **Критически важно:** на каждый принятый пакет `0x1D` хост обязан немедленно ответить ATT_OP_HANDLE_CNF (`0x1E`). Если подтверждение не отправлено, тонометр замораживает передачу следующих записей и отваливается по тайм-ауту.

---

## 6. Логика управления памятью (EEPROM Ring Buffer)

В энергонезависимой памяти тонометра каждая запись имеет флаг синхронизации (`is_sent`):

1. **Сохранение:** прибор хранит измерения в кольцевом буфере EEPROM. Пользователь всегда может просмотреть их на встроенном LCD-экране.
2. **Очередь BLE-передачи:** в эфир выставляются только те записи, у которых `is_sent == 0`.
3. **Очистка флага:** как только хост подтверждает приём индикации (`ATT Confirmation`), прошивка выставляет `is_sent = 1`.
4. **Сброс при переподключении:** при подключении нового управляющего устройства (смене хоста) и перезаписи времени часы и указатели сдвигаются. Ранее подтверждённые замеры повторно по воздуху не передаются.

---

## 7. Тонкости реализации под Linux (BlueZ / kernel ctypes)

Стандартные библиотеки верхнего уровня (такие как Bleak через D-Bus) стабильно терпят сбой при попытке подключиться к BM59 из-за медленного Service Discovery, который не укладывается в 1500 мс.

Для стабильного захвата данных рекомендуется использовать прямой сокет ядра `AF_BLUETOOTH` / `BTPROTO_L2CAP` на CID 4 (ATT).

### Размерность структуры `sockaddr_l2` в Linux

В ядре Linux структура `sockaddr_l2` для LE-адресов имеет длину ровно 14 байт:

```c
struct sockaddr_l2 {
    sa_family_t      l2_family;      // 2 байта (AF_BLUETOOTH = 31)
    unsigned short   l2_psm;         // 2 байта (0 для фиксированных каналов)
    bdaddr_t         l2_bdaddr;      // 6 байт (MAC в обратном порядке)
    unsigned short   l2_cid;         // 2 байта (CID 4 = ATT)
    uint8_t          l2_bdaddr_type; // 1 байт (BDADDR_LE_RANDOM = 2)
    uint8_t          l2_padding;     // 1 байт (выравнивание структуры до 14 байт)
};
```

> Если передать в системный вызов `connect()` структуру без байта выравнивания (13 байт), ядро вернёт ошибку `EINVAL (22) Invalid argument`.

### Эталонная реализация на Python 3 (через `ctypes`)

```python
import socket
import struct
import ctypes
import os
import time
from datetime import datetime

libc = ctypes.CDLL("libc.so.6", use_errno=True)

class BdAddr(ctypes.Structure):
    _fields_ = [("b", ctypes.c_uint8 * 6)]

class SockAddrL2(ctypes.Structure):
    _fields_ = [
        ("l2_family", ctypes.c_uint16),
        ("l2_psm", ctypes.c_uint16),
        ("l2_bdaddr", BdAddr),
        ("l2_cid", ctypes.c_uint16),
        ("l2_bdaddr_type", ctypes.c_uint8),
        ("l2_padding", ctypes.c_uint8),
    ]

def connect_bm59(mac_str: str):
    s = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_SEQPACKET, socket.BTPROTO_L2CAP)

    # 1. Уровень безопасности MEDIUM (Just Works шифрование)
    s.setsockopt(274, 4, struct.pack("BB", 2, 0))

    addr = SockAddrL2()
    addr.l2_family = 31
    addr.l2_psm = 0
    addr.l2_cid = 4          # ATT CID
    addr.l2_bdaddr_type = 2  # BDADDR_LE_RANDOM

    mac_bytes = [int(x, 16) for x in reversed(mac_str.split(":"))]
    for i in range(6):
        addr.l2_bdaddr.b[i] = mac_bytes[i]

    # Системный вызов libc connect (обход ограничений socket в Python < 3.12)
    res = libc.connect(s.fileno(), ctypes.byref(addr), ctypes.sizeof(addr))
    if res != 0:
        err = ctypes.get_errno()
        raise OSError(err, f"connect failed: {os.strerror(err)}")

    # 2. Пауза 250 мс для завершения SMP Just Works ядром
    time.sleep(0.25)

    # 3. Включение индикаций (CCCD Handle 0x0016 <- 0x0002) через ATT_OP_WRITE_REQ
    s.send(struct.pack("<BHH", 0x12, 0x0016, 0x0002))
    rsp = s.recv(1024)
    assert rsp[0] == 0x13, "CCCD Write Request rejected"

    # 4. Отправка времени (Current Time Service 0x2A2B) в Handle 0x0012
    now = datetime.now()
    time_payload = bytes([
        now.year & 0xFF, (now.year >> 8) & 0xFF,
        now.month, now.day, now.hour, now.minute, now.second,
        now.isoweekday(), 0x00, 0x00
    ])
    s.send(struct.pack("<BH", 0x52, 0x0012) + time_payload)

    # 5. Цикл приёма измерений
    s.settimeout(2.0)
    while True:
        try:
            data = s.recv(1024)
            if data and data[0] == 0x1D:  # ATT_OP_HANDLE_IND
                payload = data[3:]
                sys_p = payload[1] | (payload[2] << 8)
                dia_p = payload[3] | (payload[4] << 8)
                pulse = payload[5] | (payload[6] << 8)
                print(f"Замер: {sys_p}/{dia_p}, Пульс: {pulse}")

                # Подтверждение индикации
                s.send(bytes([0x1E]))  # ATT_OP_HANDLE_CNF
        except socket.timeout:
            break

    s.close()
```
