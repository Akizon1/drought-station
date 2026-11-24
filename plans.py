"""Тарифы программы: бесплатный, пробный период эксперта и подписка.

Логика без интерфейса, чтобы её было легко проверить и поменять. Оплата ещё не подключена:
подписку Pro может выдать только администратор (раздел «Все пользователи»), а эксперт может
один раз включить пробный период на TRIAL_DAYS дней.

Кто какой тариф получает:
* администратор — все функции, без подписки;
* эксперт — свой тариф (free / trial / pro);
* фермер — тариф пригласившего его эксперта, автоматически. Фермеру подписку не предлагаем.
"""
import time
from datetime import datetime

TRIAL_DAYS = 7
FREE_FARMER_LIMIT = 3        # сколько фермеров эксперт может пригласить бесплатно
FREE_HISTORY_DAYS = 7        # история датчиков на бесплатном тарифе
FREE_EXPORT_DAYS = 7         # экспорт в Excel на бесплатном тарифе
PRO_EXPORT_DAYS = 30         # экспорт в Excel в подписке
PRICE_TEXT = "цена будет указана после подключения оплаты"

TITLES = {"free": "Бесплатный", "trial": "Пробный период", "pro": "Подписка Pro", "admin": "Администратор"}

# Строки сравнения на вкладке «Подписка»: (что, бесплатно, в подписке)
COMPARISON = [
    ("Датчики в реальном времени, графики", "✓", "✓"),
    ("История датчиков", f"за последние {FREE_HISTORY_DAYS} дней", "за любой период (хранится 90 дней)"),
    ("Экспорт в Excel", f"за {FREE_EXPORT_DAYS} дней", f"за {PRO_EXPORT_DAYS} дней"),
    ("Импорт из Excel", "✓", "✓"),
    ("Фермеры", f"до {FREE_FARMER_LIMIT}", "без ограничений"),
    ("Подписка для ваших фермеров", "—", "✓ автоматически, бесплатно для них"),
    ("Уведомления в программе и Telegram", "✓", "✓"),
    ("Подключение к сайту по коду", "—", "✓"),
    ("Отправка данных по API и в JSON-файл", "—", "✓"),
    ("Аналитика сайта «ПочваМонитор X Drought»: погода на 7 дней, прогноз почвы, что посадить", "—", "✓"),
    ("Устройства аккаунта на сайте и их отключение", "—", "✓"),
]


def _get(user, key, default=None):
    try:
        value = user[key]
    except (KeyError, IndexError, TypeError):
        return default
    return default if value is None else value


def own_plan(user, now=None):
    """Действующий собственный тариф пользователя с учётом срока: free, trial или pro."""
    now = now or time.time()
    plan = _get(user, "plan", "free")
    until = float(_get(user, "plan_until", 0) or 0)
    if plan == "pro" and (until == 0 or until > now):
        return "pro", until
    if plan == "trial" and until > now:
        return "trial", until
    return "free", 0


def effective(user, lookup, now=None):
    """Тариф, по которому работает пользователь. lookup(id) возвращает пользователя по id.

    Возвращает словарь: code (free/trial/pro/admin), paid, until, days_left, source (own/expert/none),
    expert_name, title, trial_available."""
    now = now or time.time()
    role = _get(user, "role", "farmer")
    info = {"code": "free", "paid": False, "until": 0, "days_left": None, "source": "own",
            "expert_name": "", "trial_available": False}
    if role == "admin":
        info.update(code="admin", paid=True)
    elif role == "farmer":
        expert = lookup(_get(user, "invited_by")) if _get(user, "invited_by") else None
        if expert is None:
            info["source"] = "none"
        else:
            code, until = own_plan(expert, now)
            info.update(code=code, paid=code != "free", until=until, source="expert",
                        expert_name=f"{_get(expert, 'first_name', '')} {_get(expert, 'last_name', '')}".strip())
    else:
        code, until = own_plan(user, now)
        info.update(code=code, paid=code != "free", until=until,
                    trial_available=not _get(user, "trial_used", 0) and code == "free")
    if info["until"]:
        info["days_left"] = max(0, int((info["until"] - now + 86399) // 86400))
    info["title"] = TITLES[info["code"]]
    return info


def limits(info):
    """Ограничения тарифа. None — без ограничений."""
    paid = info["paid"]
    return {
        "history_days": None if paid else FREE_HISTORY_DAYS,
        "export_days": PRO_EXPORT_DAYS if paid else FREE_EXPORT_DAYS,
        "farmers": None if paid else FREE_FARMER_LIMIT,
        "integrations": paid,      # код подключения к сайту, отправка по API и в JSON-файл
        "analytics": paid,         # аналитика с сайта
    }


def status_line(info):
    """Короткая строка о тарифе для интерфейса."""
    if info["code"] == "admin":
        return "Администратор: доступны все функции"
    if info["source"] == "none":
        return "Тариф появится после приглашения экспертом"
    prefix = f"Тариф фирмы ({info['expert_name']}): " if info["source"] == "expert" else ""
    if info["code"] == "trial":
        end = datetime.fromtimestamp(info["until"]).strftime("%d.%m.%Y")
        return f"{prefix}пробный период, осталось {info['days_left']} дн. (до {end})"
    if info["code"] == "pro":
        if info["until"]:
            return f"{prefix}подписка Pro до {datetime.fromtimestamp(info['until']):%d.%m.%Y}"
        return f"{prefix}подписка Pro"
    return f"{prefix}бесплатный тариф"


def locked_text(info, what):
    """Почему функция недоступна. Фермеру подписку не предлагаем — только объясняем."""
    if _is_farmer(info):
        return f"{what}: недоступно на тарифе вашей фирмы."
    return f"{what}: доступно в подписке или в {TRIAL_DAYS}-дневном пробном периоде (раздел «Подписка»)."


def _is_farmer(info):
    return info["source"] in ("expert", "none")
