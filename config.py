import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def _data_dir():
    """Папка для базы и настроек.

    Установленная программа (Drought.exe) хранит данные в %LOCALAPPDATA%\\Drought:
    так их не нужно искать в папке программы, они переживают обновление и не мешают удалению.
    При запуске из исходников (python main.py) всё лежит рядом с main.py, как раньше.
    """
    if getattr(sys, "frozen", False):
        root = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or str(Path.home())
        folder = Path(root) / "Drought"
    else:
        folder = BASE_DIR
    folder.mkdir(parents=True, exist_ok=True)
    return folder


DATA_DIR = _data_dir()
DB_PATH = DATA_DIR / "monitor.db"
ANALYTICS_CACHE = DATA_DIR / "analytics_cache.json"   # последняя аналитика сайта (для работы без сети)
APP_NAME = "Drought Monitor"                          # название без подключения к сайту
APP_VERSION = "1.5"
BAUDRATE = 9600
HISTORY_KEEP_DAYS = 90      # историю храним 90 дней (импортированные данные не удаляются раньше)
TICK_SECONDS = 2          # как часто сохраняются показания всех датчиков
OFFLINE_SECONDS = 300     # через сколько секунд без данных считать датчик отключённым
FRESH_SECONDS = 10        # показания старше этого не сохраняются

DEFAULT_SETTINGS = {
    "temp_warn": "30.0",
    "hum_warn": "40.0",
    "water_warn": "300",
    "send_mode": "off",                 # off | api | file
    "site_url": "http://127.0.0.1:5000",
    "send_url": "http://127.0.0.1:5000/api/sensors/readings",
    "api_key": "",
    "file_path": str(DATA_DIR / "latest.json"),
    "send_interval": "2",
    "test_mode": "0",                   # тестовый режим: включает и показывает тестовые датчики (только админ)
    "tg_token": "",
    "tg_enabled": "0",
    "notify_drought": "1",
    "notify_temp": "1",
    "notify_offline": "1",
    "notify_test": "1",
    "notify_cooldown": "600",
    "site_brand": "",                   # «ПочваМонитор X Drought» — приходит с сайта при подключении
    "site_owner": "",                   # id пользователя программы, который подключил её к сайту
    "site_owner_name": "",              # владелец аккаунта на сайте (логин)
    "popups": "1",                      # всплывающие уведомления в окне программы
    "popup_sound": "1",                 # звук уведомлений
}
