"""Особисті налаштування email-сповіщень користувача.

Ключ — email у нижньому регістрі (за ним визначаємо одержувача листа).
Поки бази даних немає — JSON-файл (/home/data на Azure переживає перезапуск і деплой).
"""
import hashlib
import json
import os
import secrets
import threading
from datetime import datetime, timedelta

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

CONFIRM_TTL = timedelta(hours=48)   # скільки діє посилання підтвердження іншої пошти
RESEND_PAUSE = timedelta(seconds=60)  # не частіше одного листа підтвердження за хвилину

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
    return {"enabled": saved.get("enabled", True), "events": events, "updated_at": saved.get("updated_at"),
            "alt_email": saved.get("alt_email") or "", "alt_verified": bool(saved.get("alt_verified")),
            "alt_sent_at": saved.get("alt_sent_at")}


def wants(email, event):
    prefs = get(email)
    return prefs["enabled"] and prefs["events"].get(event, False)


def save(email, enabled, events):
    with _lock:
        entry = _load().setdefault((email or "").lower(), {})
        entry.update({
            "enabled": bool(enabled),
            "events": {code: bool(events.get(code)) for code in EVENTS},
            "updated_at": clock.now().isoformat(timespec="seconds"),
        })
        _write()


def _write():
    path = _path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(_data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


# --- Інша пошта для сповіщень (замінює робочу після підтвердження посиланням) ---

def delivery_address(email, allow):
    """Куди насправді надсилати листи користувачу з робочою поштою email."""
    if allow:
        with _lock:
            saved = _load().get((email or "").lower()) or {}
        if saved.get("alt_email") and saved.get("alt_verified"):
            return saved["alt_email"]
    return email


def confirmed_addresses():
    """{робоча пошта: підтверджена інша} — для таблиці користувачів в адмінці."""
    with _lock:
        return {k: v["alt_email"] for k, v in _load().items() if v.get("alt_email") and v.get("alt_verified")}


def resend_wait(email):
    """Скільки секунд ще чекати до наступного листа підтвердження (0 — можна)."""
    sent = get(email)["alt_sent_at"]
    if not sent:
        return 0
    left = datetime.fromisoformat(sent) + RESEND_PAUSE - clock.now()
    return max(0, int(left.total_seconds()) + (1 if left.microseconds else 0))


def set_alt(email, alt_email):
    """Задати (або повторно надіслати) іншу адресу. Повертає токен для посилання підтвердження."""
    token = secrets.token_urlsafe(32)
    with _lock:
        entry = _load().setdefault((email or "").lower(), {})
        if (entry.get("alt_email") or "").lower() != alt_email.lower():
            entry["alt_verified"] = False  # нова адреса — нове підтвердження
        entry.update({"alt_email": alt_email, "alt_token_hash": _hash(token),
                      "alt_sent_at": clock.now().isoformat(timespec="seconds")})
        _write()
    return token


def confirm_alt(email, token):
    """Підтвердити іншу адресу токеном з листа. Повертає адресу або None (чужий / протермінований)."""
    with _lock:
        entry = _load().get((email or "").lower()) or {}
        stored, sent = entry.get("alt_token_hash"), entry.get("alt_sent_at")
        if not (stored and sent and entry.get("alt_email")) or not secrets.compare_digest(stored, _hash(token)):
            return None
        if clock.now() - datetime.fromisoformat(sent) > CONFIRM_TTL:
            return None
        entry.update({"alt_verified": True, "alt_token_hash": None})
        _write()
        return entry["alt_email"]


def clear_alt(email):
    with _lock:
        entry = _load().get((email or "").lower())
        if not entry:
            return
        for k in ("alt_email", "alt_verified", "alt_token_hash", "alt_sent_at"):
            entry.pop(k, None)
        _write()
