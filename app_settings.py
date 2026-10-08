"""Налаштування роботи системи, які змінює адміністратор (наприклад, режим погодження).

Поки бази даних немає — JSON-файл (/home/data на Azure переживає перезапуск і деплой).
Кожна зміна запам'ятовує, хто і коли її зробив.
"""
import json
import os
import threading
from datetime import datetime

DEFAULTS = {
    "approval_mode": "sequential",
}

_lock = threading.Lock()
_data = None


def _path():
    base = "/home/data" if os.environ.get("WEBSITE_SITE_NAME") else os.path.join(os.path.dirname(__file__), "data")
    return os.environ.get("APP_SETTINGS_FILE") or os.path.join(base, "app_settings.json")


def _load():
    global _data
    if _data is None:
        try:
            with open(_path(), encoding="utf-8") as f:
                _data = json.load(f)
        except (OSError, ValueError):
            _data = {}
    return _data


def reload():
    global _data
    with _lock:
        _data = None
        _load()


def get(key):
    with _lock:
        entry = _load().get(key)
    return entry["value"] if entry else DEFAULTS.get(key)


def info(key):
    """Значення разом із тим, хто і коли його змінив."""
    with _lock:
        entry = dict(_load().get(key) or {})
    entry.setdefault("value", DEFAULTS.get(key))
    return entry


def set(key, value, updated_by):
    with _lock:
        _load()[key] = {"value": value, "updated_by": updated_by,
                        "updated_at": datetime.now().isoformat(timespec="seconds")}
        path = _path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
