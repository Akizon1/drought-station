import threading
import time

import serial
from serial.tools import list_ports

from config import BAUDRATE

KEYWORDS = ("arduino", "ch340", "ch341", "wch", "usb serial", "usb-serial", "usb serial device", "последовательное usb")

WATER_MAX = 1000  # 0 — сухо, 1000 — максимум датчика воды


def water_percent(water):
    return max(0, min(WATER_MAX, water)) / WATER_MAX * 100


def find_arduino_port():
    ports = list(list_ports.comports())
    for p in ports:
        text = f"{p.description} {p.manufacturer or ''}".lower()
        if any(k in text for k in KEYWORDS):
            return p.device
    # Не угадываем: если Arduino не найден по названию, порт не выбирается (раньше тут брался COM1).
    return ""


def available_ports():
    return [p.device for p in list_ports.comports()]


EXTRA_ORDER = ("salinity", "ph", "n", "p", "k")   # необязательные поля после воды
EMPTY = ("", "-", "nan", "none", "null")


def parse_line(line):
    """Строка с Arduino: температура,влажность,вода[,засолённость,pH,N,P,K].

    Первые три поля обязательны (как раньше: «27.6,81.0,500»). Остальные — если подключены
    дополнительные датчики; пустое поле или «-» означает, что такого датчика нет:
    «27.6,81.0,500,,7.9» — есть pH, нет засолённости и N, P, K."""
    parts = [p.strip() for p in line.strip().split(",")]
    if not 3 <= len(parts) <= 3 + len(EXTRA_ORDER):
        return None
    try:
        data = {
            "temperature": float(parts[0]),
            "humidity": float(parts[1]),
            "water": int(float(parts[2])),
        }
        for key, raw in zip(EXTRA_ORDER, parts[3:]):
            if raw.lower() not in EMPTY:
                data[key] = float(raw)
    except ValueError:
        return None
    return data


def evaluate_status(temp, hum, water, s):
    """0 признаков — ok, 1 — warn, 2 и больше — danger."""
    score = (
        int(temp >= float(s["temp_warn"]))
        + int(hum <= float(s["hum_warn"]))
        + int(water <= float(s["water_warn"]))
    )
    return ("ok", "warn", "danger")[min(score, 2)]


class SerialReader(threading.Thread):
    def __init__(self, port, on_data, on_status):
        super().__init__(daemon=True)
        self.port = port
        self.on_data = on_data
        self.on_status = on_status
        self._stop = threading.Event()

    def stop(self):
        self._stop.set()

    def run(self):
        if not self.port:
            self.on_status("Порт не выбран", False)
            return
        while not self._stop.is_set():
            try:
                with serial.Serial(self.port, BAUDRATE, timeout=2) as ser:
                    self.on_status(f"Подключено: {self.port}", True)
                    while not self._stop.is_set():
                        raw = ser.readline()
                        if not raw:
                            continue
                        data = parse_line(raw.decode("utf-8", errors="ignore"))
                        if data:
                            data["ts"] = time.time()
                            self.on_data(data)
            except (serial.SerialException, OSError):
                self.on_status(f"Нет связи с {self.port}", False)
                time.sleep(3)
