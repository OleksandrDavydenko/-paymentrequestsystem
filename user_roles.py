"""Ролі користувачів у самій системі (коли вхід через Google: «Налаштування → Дані й система → Вхід у систему»).

{id користувача: {"email", "name", "roles": [...], "updated_by", "updated_at"}}. id — Entra object id для тих,
кого перенесено з груп Azure, або пошта (нижнім регістром) для доданих в адмінці; людину на вході
знаходимо за поштою, тож однаково працює вхід і через Google, і через Microsoft.

У базі даних (docstore), а без бази — JSON-файл (/home/data на Azure). Ролі керівника відділу й
«бачить заявки відділу» — як і раніше, з оргструктури (org_structure), а не тут.
"""
import os
import threading

import clock
import docstore
import user_directory

ROLES = ("admin", "initiator", "acc_cash", "acc_resident", "acc_nonresident", "cfo")

_lock = threading.Lock()
_data = None


def _path():
    base = "/home/data" if os.environ.get("WEBSITE_SITE_NAME") else os.path.join(os.path.dirname(__file__), "data")
    return os.environ.get("USER_ROLES_FILE") or os.path.join(base, "user_roles.json")


def _load():
    global _data
    if _data is None:
        try:
            _data = docstore.read("user_roles", _path())
        except (OSError, ValueError):
            _data = {}
    return _data


def reload():
    global _data
    with _lock:
        _data = None
        _load()


def _person(uid, u):
    """Запис для показу: пошта й ім'я, яких немає в записі (перенесені з Azure лише з ID), — з довідника
    користувачів (хто вже входив у систему)."""
    email, name = (u.get("email") or "").strip(), u.get("name") or ""
    if not email or not name:
        known = user_directory.lookup(uid) or {}
        email, name = email or known.get("email") or "", name or known.get("name") or ""
    return {"id": uid, "email": email, "name": name or email or uid, "roles": set(u.get("roles") or [])}


def all_users():
    """[{"id", "email", "name", "roles"}] — усі, кому в системі призначено ролі (зокрема з порожнім списком)."""
    with _lock:
        items = list(_load().items())
    return [_person(uid, u) for uid, u in items]


def get(uid):
    with _lock:
        u = _load().get(uid)
    return _person(uid, u) if u else None


def find_by_email(email):
    """Запис за поштою (без урахування регістру) або None."""
    email = (email or "").strip().lower()
    if not email:
        return None
    return next((p for p in all_users() if p["email"].lower() == email), None)


def roles_of(uid):
    u = get(uid)
    return set(u["roles"]) if u else set()


def members(role):
    """Люди з роллю: [{"id", "email", "name"}] — для розсилки погоджувачам."""
    return [{"id": u["id"], "email": u["email"], "name": u["name"]} for u in all_users()
            if role in u["roles"] and u["email"]]


def set_roles(uid, email, name, roles, updated_by):
    """Записати ролі людини (невідомі ролі відкидаються). Повертає збережені ролі."""
    roles = sorted(r for r in set(roles) if r in ROLES)
    with _lock:
        data = _load()
        prev = data.get(uid) or {}
        data[uid] = {"email": (email or prev.get("email") or "").strip(), "name": name or prev.get("name") or "",
                     "roles": roles, "updated_by": updated_by, "updated_at": clock.now().isoformat(timespec="seconds")}
        docstore.write("user_roles", _path(), data)
    return set(roles)


def import_people(people, updated_by):
    """Перенести ролі з груп Azure: people — [{"id", "email", "name", "roles"}]. Уже наявні записи не
    перезаписуються (їх могли змінити в системі). Повертає кількість доданих."""
    added = 0
    with _lock:
        data = _load()
        stamp = clock.now().isoformat(timespec="seconds")
        for p in people:
            if not p.get("id") or p["id"] in data:
                continue
            known = user_directory.lookup(p["id"]) or {}  # токен додатку дає лише ID учасників груп
            data[p["id"]] = {"email": p.get("email") or known.get("email") or "",
                             "name": p.get("name") or known.get("name") or "",
                             "roles": sorted(r for r in p.get("roles") or [] if r in ROLES),
                             "updated_by": updated_by, "updated_at": stamp}
            added += 1
        if added:
            docstore.write("user_roles", _path(), data)
    return added


# Інший процес сайту змінив документ — перечитати (docstore.sync)
docstore.register("user_roles", reload)
