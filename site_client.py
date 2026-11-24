"""Связь программы с сайтом «ПочваМонитор»: подключение по коду, аналитика, устройства аккаунта.

Все функции возвращают простые значения и не трогают интерфейс — их вызывают в отдельном потоке.
Ответ 401 означает, что ключ этого компьютера отозван на сайте (устройство деактивировано).
"""
from urllib.parse import quote

import requests

TIMEOUT = 10


class Revoked(Exception):
    """Сайт не принимает ключ: устройство отключено от аккаунта."""


def _url(site_url, path):
    return site_url.rstrip("/") + path


def _error(response):
    try:
        return response.json().get("error", response.text[:120])
    except ValueError:
        return response.text[:120]


def pair(site_url, code, name):
    """Обменивает одноразовый код с сайта на ключ API. Возвращает (ключ или None, текст ошибки или None)."""
    key, _, error = pair_full(site_url, code, name)
    return key, error


def pair_full(site_url, code, name):
    """Как pair(), но ещё возвращает название, под которым программа работает с сайтом."""
    try:
        response = requests.post(_url(site_url, "/api/pair"), json={"code": code, "name": name}, timeout=8)
    except requests.RequestException as err:
        return None, "", f"Нет связи с сайтом: {err}"
    if response.status_code == 201:
        body = response.json()
        return body["key"], body.get("brand", ""), None
    return None, "", f"{response.status_code}: {_error(response)}"


def _call(method, site_url, key, path, **params):
    try:
        response = requests.request(method, _url(site_url, path), headers={"X-API-Key": key},
                                    params=params or None, timeout=TIMEOUT)
    except requests.RequestException as err:
        raise ConnectionError(f"Нет связи с сайтом: {err}") from err
    if response.status_code == 401:
        raise Revoked("Устройство отключено от аккаунта на сайте")
    if response.status_code >= 400:
        raise ConnectionError(f"Сайт ответил {response.status_code}: {_error(response)}")
    try:
        return response.json()
    except ValueError as err:
        raise ConnectionError("Сайт вернул не JSON") from err


def info(site_url, key):
    return _call("GET", site_url, key, "/api/station/info")


def overview(site_url, key, period="24h", lat=None, lon=None, lang="ru"):
    params = {"period": period, "lang": lang}
    if lat is not None and lon is not None:
        params.update(lat=lat, lon=lon)
    return _call("GET", site_url, key, "/api/station/overview", **params)


def plot(site_url, key, plot_id, lang="ru"):
    return _call("GET", site_url, key, f"/api/station/plot/{int(plot_id)}", lang=lang)


def sensors(site_url, key, include_test=False):
    """Датчики аккаунта, по которым сайт получил показания за неделю."""
    return _call("GET", site_url, key, "/api/station/sensors", include_test="1" if include_test else "0")["sensors"]


def sensor(site_url, key, sensor_id, lang="ru"):
    """Аналитика сайта по показаниям одного датчика (погода, прогноз, культуры, полнота данных)."""
    return _call("GET", site_url, key, f"/api/station/sensor/{quote(str(sensor_id), safe='')}", lang=lang)


def devices(site_url, key):
    return _call("GET", site_url, key, "/api/station/devices")["devices"]


def revoke(site_url, key, device_id):
    return _call("POST", site_url, key, f"/api/station/devices/{int(device_id)}/revoke")
