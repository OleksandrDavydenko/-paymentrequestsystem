"""Довідник користувачів: ID в Entra (oid) -> email та ім'я.

Дозвіл додатку GroupMember.Read.All повертає учасників груп лише з ID — без імені й пошти.
Ще довідник — це список «користувачів системи» (active: є роль) для вибору учасників заявки.
Тому запам'ятовуємо людей, коли вони входять у систему або видні адміну в «Адмініструванні»
(там групи читаються з правами адміна і пошта є), і за цим довідником знаходимо адреси
погоджувачів для листів. JSON-файл (/home/data на Azure переживає перезапуск і деплой).
"""
import json
import os
import threading

_lock = threading.Lock()
_data = None


def _path():
    base = "/home/data" if os.environ.get("WEBSITE_SITE_NAME") else os.path.join(os.path.dirname(__file__), "data")
    return os.environ.get("USER_DIRECTORY_FILE") or os.path.join(base, "user_directory.json")


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


def lookup(oid):
    """{"email", "name"} або None."""
    with _lock:
        return _load().get(oid or "")


def remember(users, active=True):
    """Запам'ятати [{"id", "email", "name"}]. active — чи має людина доступ до системи (є роль).
    Файл перезаписується, лише якщо щось змінилося."""
    with _lock:
        data, changed = _load(), False
        for u in users:
            oid, email = u.get("id"), (u.get("email") or "").strip()
            if not oid or not email:
                continue
            entry = {"email": email, "name": u.get("name") or email, "active": bool(active)}
            if data.get(oid) != entry:
                data[oid], changed = entry, True
        if not changed:
            return
        path = _path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)


def active_users():
    """Користувачі системи (з роллю): [{"id", "email", "name"}] за іменем."""
    with _lock:
        rows = [{"id": oid, "email": e["email"], "name": e.get("name") or e["email"]}
                for oid, e in _load().items() if e.get("active")]
    return sorted(rows, key=lambda u: u["name"].lower())


def find_active(email):
    """Активний користувач за поштою або None."""
    email = (email or "").strip().lower()
    return next((u for u in active_users() if u["email"].lower() == email), None) if email else None


def search(query, limit=10):
    """Пошук серед користувачів системи за іменем або поштою (для підказок)."""
    q = (query or "").strip().lower()
    if len(q) < 2:
        return []
    return [u for u in active_users() if q in u["name"].lower() or q in u["email"].lower()][:limit]
