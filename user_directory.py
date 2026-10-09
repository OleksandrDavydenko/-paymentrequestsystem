"""Довідник користувачів: ID в Entra (oid) -> email та ім'я.

Дозвіл додатку GroupMember.Read.All повертає учасників груп лише з ID — без імені й пошти.
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


def remember(users):
    """Запам'ятати [{"id", "email", "name"}]. Файл перезаписується, лише якщо щось змінилося."""
    with _lock:
        data, changed = _load(), False
        for u in users:
            oid, email = u.get("id"), (u.get("email") or "").strip()
            if not oid or not email:
                continue
            entry = {"email": email, "name": u.get("name") or email}
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
