"""Налаштування роботи системи, які змінює адміністратор (наприклад, режим погодження).

У базі даних (docstore), а без бази — JSON-файл (/home/data на Azure).
Кожна зміна запам'ятовує, хто і коли її зробив.
"""
import os
import threading

import clock
import docstore
import settings_schema

# Значення за замовчуванням, назви й перевірка — у реєстрі налаштувань (settings_schema.py)
DEFAULTS = settings_schema.defaults()

_lock = threading.Lock()
_data = None


def _path():
    base = "/home/data" if os.environ.get("WEBSITE_SITE_NAME") else os.path.join(os.path.dirname(__file__), "data")
    return os.environ.get("APP_SETTINGS_FILE") or os.path.join(base, "app_settings.json")


def _load():
    global _data
    if _data is None:
        try:
            _data = docstore.read("app_settings", _path())
        except (OSError, ValueError):
            _data = {}
        _migrate_test_mode_mail(_data)
    return _data


def _migrate_test_mode_mail(data):
    """Раніше в тестовому режимі розсилку вмикав окремий перемикач test_mode_mail. Тепер перехід у тестовий
    режим сам вимикає «Розсилка увімкнена». Якщо зараз тестовий режим і старий перемикач був вимкнений —
    вимкнути розсилку (листи не підуть несподівано) і запам'ятати її стан для повернення до робочої бази."""
    if "test_mode_mail" not in data:
        return
    old = data.pop("test_mode_mail") or {}
    stamp = {"updated_by": "система", "updated_at": clock.now().isoformat(timespec="seconds")}
    if ((data.get("data_mode") or {}).get("value") == "test" and not old.get("value")
            and (data.get("mail_enabled") or {}).get("value")):
        data["mail_enabled_work"] = {"value": True, **stamp}
        data["mail_enabled"] = {"value": False, **stamp}
    docstore.write("app_settings", _path(), data)


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
    """Зберегти значення; зміна потрапляє в журнал змін налаштувань (settings_log)."""
    with _lock:
        entry = _load().get(key)
        old = entry["value"] if entry else DEFAULTS.get(key)
        _load()[key] = {"value": value, "updated_by": updated_by,
                        "updated_at": clock.now().isoformat(timespec="seconds")}
        docstore.write("app_settings", _path(), _data)
    if old != value and key != "mail_enabled_work":
        import settings_log
        settings_log.record(key, old, value, updated_by)


# Інший процес сайту змінив документ — перечитати (docstore.sync)
docstore.register("app_settings", reload)
