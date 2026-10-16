"""Зв'язок користувача системи з користувачем 1С (пошта не збігається з логіном 1С).

Адмін вибирає користувача 1С у картці користувача; від цього залежить, які «Види оплати»
бухгалтер бачить у заявці (його доступи з 1С). Ключ — oid користувача в Entra.
У базі даних (docstore), а без бази — JSON-файл (/home/data на Azure).
"""
import os
import threading

import clock
import docstore

_lock = threading.Lock()
_data = None


def _path():
    base = "/home/data" if os.environ.get("WEBSITE_SITE_NAME") else os.path.join(os.path.dirname(__file__), "data")
    return os.environ.get("USER_ONEC_FILE") or os.path.join(base, "user_onec.json")


def _load():
    global _data
    if _data is None:
        try:
            _data = docstore.read("user_onec", _path())
        except (OSError, ValueError):
            _data = {}
    return _data


def reload():
    global _data
    with _lock:
        _data = None
        _load()


def get(oid):
    """{"guid", "name", "email", ...} або None."""
    with _lock:
        return (_load().get(oid or "") or None)


def guid_for(oid):
    link = get(oid)
    return link["guid"] if link else None


def all_links():
    with _lock:
        return dict(_load())


def set_link(oid, email, guid, onec_name, updated_by):
    with _lock:
        d = _load()
        if guid:
            d[oid] = {"guid": guid, "name": onec_name, "email": email, "updated_by": updated_by,
                      "updated_at": clock.now().isoformat(timespec="seconds")}
        else:
            d.pop(oid, None)
        docstore.write("user_onec", _path(), d)


# Інший процес сайту змінив документ — перечитати (docstore.sync)
docstore.register("user_onec", reload)
