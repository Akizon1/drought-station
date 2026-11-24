"""Уведомления в Telegram. Получатели — чаты, подписанные командой /start.
Каждое правило имеет паузу (cooldown), чтобы не засыпать сообщениями."""
import time
from datetime import datetime

import database as db
import telegram
from config import OFFLINE_SECONDS
from soil_profiles import PROFILES

STATUS_TEXT = {"ok": "норма", "warn": "внимание", "danger": "засуха"}


def _basis(sensor):
    if sensor["kind"] == "test" and sensor["profile"] in PROFILES:
        return f" *\n{PROFILES[sensor['profile']]['basis']}"
    return ""


def status_summary():
    names = {s["id"]: s["name"] for s in db.all_sensors()}
    lines = []
    for r in db.latest_by_sensor():
        if r["sensor_id"] in names:
            lines.append(f"{names[r['sensor_id']]}: {STATUS_TEXT.get(r['status'], '—')}, "
                         f"вода {r['water'] / 10:.0f} %, {r['temperature']:.1f} °C, {r['humidity']:.0f} %")
    return "\n".join(lines) or "Данных пока нет."


class Notifier:
    def __init__(self):
        self.last = {}
        self.prev = {}
        self.offline = set()

    @staticmethod
    def _enabled(s):
        return s["tg_enabled"] == "1" and bool(s["tg_token"])

    def _broadcast(self, s, key, text, force=False):
        now = time.time()
        if not force and now - self.last.get(key, 0) < float(s["notify_cooldown"]):
            return
        self.last[key] = now
        delivered = False
        for sub in db.list_subscribers():
            ok, _ = telegram.send(s["tg_token"], sub["chat_id"], text)
            delivered = delivered or ok
        db.log_notification(now, text, delivered)

    def on_reading(self, s, sensor, values, status):
        prev = self.prev.get(sensor["id"])
        self.prev[sensor["id"]] = status
        if not self._enabled(s):
            return
        name = sensor["name"]
        sid = sensor["id"]
        is_test = sensor["kind"] == "test"
        if s["notify_drought"] == "1" and prev is not None and status != prev:
            if status == "danger":
                self._broadcast(s, f"danger:{sid}",
                                f"🚨 Датчик {name} находится в засухе.\n"
                                f"Влажность почвы {values['water'] / 10:.0f} %, температура {values['temperature']:.1f} °C, "
                                f"влажность воздуха {values['humidity']:.0f} %.{_basis(sensor) if is_test else ''}")
            elif status == "warn":
                self._broadcast(s, f"warn:{sid}", f"⚠️ Датчик {name}: признаки засухи, состояние «внимание».")
            else:
                self._broadcast(s, f"ok:{sid}", f"✅ Датчик {name} снова в норме.")
        if s["notify_temp"] == "1" and values["temperature"] >= float(s["temp_warn"]) + 5:
            self._broadcast(s, f"temp:{sid}", f"🌡 Датчик {name}: очень жарко, {values['temperature']:.1f} °C.")
        if is_test and s["notify_test"] == "1":
            salinity = values.get("salinity")
            if salinity is not None and salinity >= 4:
                self._broadcast(s, f"salt:{sid}",
                                f"🧂 Датчик {name}: засолённость {salinity} dS/m.{_basis(sensor)}")
            ph = values.get("ph")
            if ph is not None and not 6.0 <= ph <= 8.5:
                self._broadcast(s, f"ph:{sid}", f"🧪 Датчик {name}: pH {ph} вне диапазона 6.0–8.5.{_basis(sensor)}")

    def check_offline(self, s, sensors, now):
        if not self._enabled(s) or s["notify_offline"] != "1":
            return
        for row in sensors:
            if not row["last_seen"]:
                continue
            silent = now - row["last_seen"]
            if silent > OFFLINE_SECONDS and row["id"] not in self.offline:
                self.offline.add(row["id"])
                self._broadcast(s, f"offline:{row['id']}",
                                f"📡 Датчик {row['name']} не передаёт данные уже {int(silent // 60)} мин.", force=True)
            elif silent <= OFFLINE_SECONDS and row["id"] in self.offline:
                self.offline.discard(row["id"])
                self._broadcast(s, f"online:{row['id']}", f"🟢 Датчик {row['name']} снова на связи.", force=True)

    def test_mode_changed(self, s, enabled):
        if self._enabled(s):
            text = ("🧪 Тестовый режим включён. Тестовые датчики помечены *."
                    if enabled else "🧪 Тестовый режим выключен.")
            self._broadcast(s, "testmode", text, force=True)
