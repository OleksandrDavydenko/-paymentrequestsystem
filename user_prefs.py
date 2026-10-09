"""Особисті налаштування email-сповіщень користувача.

Ключ — email у нижньому регістрі (за ним визначаємо одержувача листа).
Поки бази даних немає — JSON-файл (/home/data на Azure переживає перезапуск і деплой).
"""
import json
import os
import threading

import clock

# код події -> (назва для користувача, для кого: author / approver, увімкнено за замовчуванням)
EVENTS = {
    "my_approved": ("Мою заявку погоджено / передано далі", "author", True),
    "my_rework": ("Мою заявку повернуто на доопрацювання", "author", True),
    "my_rejected": ("Мою заявку відхилено", "author", True),
    "my_paid": ("Мою заявку оплачено", "author", True),
    "my_admin_status": ("Статус моєї заявки змінив адміністратор", "author", True),
    "my_comment": ("Новий коментар до моєї заявки", "author", True),
    "task_new": ("Нова заявка чекає мого рішення або оплати", "approver", True),
    "comment_participant": ("Коментар до заявки, яку я погоджував", "approver", False),
}

_lock = threading.Lock()
_data = None


def _path():
    base = "/home/data" if os.environ.get("WEBSITE_SITE_NAME") else os.path.join(os.path.dirname(__file__), "data")
    return os.environ.get("USER_PREFS_FILE") or os.path.join(base, "user_prefs.json")


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


def get(email):
    """{"enabled": bool, "events": {код: bool}} — з урахуванням значень за замовчуванням."""
    with _lock:
        saved = _load().get((email or "").lower()) or {}
    events = {code: default for code, (_, _, default) in EVENTS.items()}
    events.update({k: bool(v) for k, v in (saved.get("events") or {}).items() if k in EVENTS})
    return {"enabled": saved.get("enabled", True), "events": events, "updated_at": saved.get("updated_at")}


def wants(email, event):
    prefs = get(email)
    return prefs["enabled"] and prefs["events"].get(event, False)


def save(email, enabled, events):
    with _lock:
        _load()[(email or "").lower()] = {
            "enabled": bool(enabled),
            "events": {code: bool(events.get(code)) for code in EVENTS},
            "updated_at": clock.now().isoformat(timespec="seconds"),
        }
        path = _path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
