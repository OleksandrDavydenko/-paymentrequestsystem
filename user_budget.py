"""Статті бюджету користувачів: які статті витрат користувач може вибирати в заявці.

Налаштування задає адміністратор:
  • direct          — прямі статті (без відділу);
  • all_departments — усі статті з розподілом;
  • departments     — статті з розподілом лише цих відділів.
Немає налаштувань — жодної статті.

Налаштування зберігаються в базі даних (docstore), а без бази — у JSON-файлі (/home/data на Azure). Ключ — Entra object id (oid) користувача.
"""
import os
import threading

import clock
import docstore

_lock = threading.Lock()
_data = None


def _path():
    base = "/home/data" if os.environ.get("WEBSITE_SITE_NAME") else os.path.join(os.path.dirname(__file__), "data")
    return os.environ.get("USER_BUDGET_FILE") or os.path.join(base, "user_budget.json")


def _load():
    global _data
    if _data is None:
        try:
            _data = docstore.read("user_budget", _path())
        except (OSError, ValueError):
            _data = {}
    return _data


def _save():
    docstore.write("user_budget", _path(), _data)


def reload():
    """Перечитати файл (для тестів / після ручного редагування)."""
    global _data
    with _lock:
        _data = None
        _load()


def get(oid):
    if not oid:
        return None
    with _lock:
        return _load().get(oid)


def all_settings():
    with _lock:
        return dict(_load())


def save_settings(oid, email, name, direct, all_departments, departments, updated_by):
    with _lock:
        _load()[oid] = {
            "email": email,
            "name": name,
            "direct": bool(direct),
            "all_departments": bool(all_departments),
            "departments": [] if all_departments else sorted({d for d in departments if d}, key=str.lower),
            "updated_at": clock.now().isoformat(timespec="seconds"),
            "updated_by": updated_by,
        }
        _save()
        return _data[oid]


def is_configured(settings):
    return bool(settings and (settings.get("direct") or settings.get("all_departments") or settings.get("departments")))


def allows(settings, item):
    """Чи може користувач з цими налаштуваннями вибрати статтю."""
    if not settings:
        return False
    if not item["departments"]:
        return bool(settings.get("direct"))
    if settings.get("all_departments"):
        return True
    return bool(set(settings.get("departments") or []) & set(item["departments"]))


def allowed_items(settings, items):
    return [i for i in items if allows(settings, i)]


def summary(settings):
    """Короткий опис для таблиці в адмінці: [(текст, тип)]."""
    if not is_configured(settings):
        return []
    parts = []
    if settings.get("direct"):
        parts.append(("Прямі", "direct"))
    if settings.get("all_departments"):
        parts.append(("Усі відділи", "all"))
    else:
        parts += [(d, "dep") for d in settings.get("departments") or []]
    return parts
