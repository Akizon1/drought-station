import requests


def _call(token, method, http_timeout=10, **params):
    """Вызов Telegram Bot API. Возвращает (успех, результат или текст ошибки)."""
    if not token:
        return False, "Не указан токен бота"
    url = f"https://api.telegram.org/bot{token}/{method}"
    try:
        response = requests.post(url, json=params, timeout=http_timeout)
    except requests.RequestException as err:
        return False, f"Нет связи с Telegram: {err}"
    try:
        data = response.json()
    except ValueError:
        return False, f"Telegram ответил {response.status_code}"
    if response.status_code == 200 and data.get("ok"):
        return True, data.get("result")
    return False, data.get("description", f"Telegram ответил {response.status_code}")


def check_token(token):
    """Проверяет токен. Возвращает (успех, имя бота или текст ошибки)."""
    ok, result = _call(token, "getMe")
    if ok:
        return True, "@" + result.get("username", "бот")
    return False, result


def send(token, chat_id, text):
    ok, result = _call(token, "sendMessage", chat_id=chat_id, text=text)
    return ok, ("Отправлено" if ok else result)


def get_updates(token, offset=None, timeout=20):
    params = {"timeout": timeout}
    if offset is not None:
        params["offset"] = offset
    ok, result = _call(token, "getUpdates", http_timeout=timeout + 10, **params)
    return (True, result) if ok else (False, [])
