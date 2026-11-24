"""Бот станции: принимает команды в Telegram. Работает в отдельном потоке (long polling)."""
import threading
import time

import database as db
import telegram
from notifier import status_summary

HELP = ("Команды:\n"
        "/start — подписаться на уведомления\n"
        "/turn_notifications — включить или выключить уведомления (можно добавить on или off)\n"
        "/status — текущее состояние датчиков\n"
        "/help — список команд")


class StationBot(threading.Thread):
    def __init__(self, token):
        super().__init__(daemon=True)
        self.token = token
        self._stop = threading.Event()
        self.offset = None

    def stop(self):
        self._stop.set()

    def run(self):
        while not self._stop.is_set():
            try:
                ok, updates = telegram.get_updates(self.token, self.offset, timeout=20)
                if not ok:
                    time.sleep(5)
                    continue
                for update in updates:
                    self.offset = update["update_id"] + 1
                    message = update.get("message") or {}
                    if message.get("text"):
                        self.handle(message)
            except Exception:
                # одна ошибка не должна останавливать бота
                time.sleep(5)

    def reply(self, chat_id, text):
        telegram.send(self.token, chat_id, text)

    def handle(self, message):
        chat = message["chat"]
        chat_id = chat["id"]
        title = chat.get("title") or chat.get("first_name") or str(chat_id)
        parts = message["text"].strip().split()
        command = parts[0].split("@")[0].lower()
        arg = parts[1].lower() if len(parts) > 1 else None

        if command == "/start":
            db.set_subscriber(chat_id, title, 1)
            self.reply(chat_id, "Вы подписаны на уведомления станции.\n\n" + HELP)
        elif command == "/turn_notifications":
            if arg in ("on", "off"):
                flag = 1 if arg == "on" else 0
            else:
                current = db.subscriber_flag(chat_id)
                flag = 0 if current else 1
            db.set_subscriber(chat_id, title, flag)
            self.reply(chat_id, "Уведомления включены." if flag else "Уведомления выключены.")
        elif command == "/status":
            self.reply(chat_id, status_summary())
        elif command == "/help":
            self.reply(chat_id, HELP)
        else:
            self.reply(chat_id, "Не знаю такой команды. Напишите /help.")
