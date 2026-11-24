import json
from datetime import datetime
from pathlib import Path

import requests


def build_payload(values, sensor):
    payload = {
        "device": "drought-station-1",
        "sensor_id": sensor["id"],
        "sensor_name": sensor["name"],
        "is_test": sensor["kind"] == "test",
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "temperature": values["temperature"],
        "humidity": values["humidity"],
        "water": values["water"],
        "status": values.get("status", ""),
    }
    if sensor["lat"] is not None and sensor["lon"] is not None:
        payload["lat"] = sensor["lat"]
        payload["lon"] = sensor["lon"]
    for key in ("salinity", "ph", "n", "p", "k"):
        if values.get(key) is not None:
            payload[key] = values[key]
    return payload


def send_api(url, api_key, payload):
    response = requests.post(url, json=payload, headers={"X-API-Key": api_key}, timeout=5)
    return response.status_code, response.text[:200]


def write_json_file(path, payload):
    target = Path(path)
    name = f"{target.stem}_{payload['sensor_id']}{target.suffix or '.json'}"
    with open(target.with_name(name), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
