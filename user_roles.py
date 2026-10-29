"""Ролі користувачів — у самій системі (адмінка → картка користувача); групи Azure не використовуються.

{id користувача: {"email", "name", "roles": [...], "acc_mode", "updated_by", "updated_at"}}. acc_mode — що робить
бухгалтер: "both" (погоджує і оплачує, за замовчуванням) або "pay" (лише оплачує). id — Entra object id для тих,
кого колись перенесено з груп Azure, або пошта (нижнім регістром) для доданих в адмінці; людину на вході
знаходимо за поштою, тож однаково працює вхід і через Google, і через Microsoft. Зовнішні користувачі
(пошта не з домену компанії) входять лише тоді, коли тут є запис хоча б з однією роллю.

У базі даних (docstore), а без бази — JSON-файл (/home/data на Azure). Ролі керівника відділу й
«бачить заявки відділу» — з оргструктури (org_structure), а не тут.
"""
import os
import threading

import clock
import docstore
import user_directory

ROLES = ("admin", "initiator", "acc_cash", "acc_resident", "acc_nonresident", "cfo")
ACCOUNTANT_ROLES = ("acc_cash", "acc_resident", "acc_nonresident")
# Що робить бухгалтер (картка користувача): «Лише оплачує» — без «На погодження», заявок на погодженні й сповіщень про них
ACC_MODES = {"both": "Погоджує і оплачує", "pay": "Лише оплачує"}

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


def display_name(email, fallback=""):
    """Ім'я людини для показу: з 1С (співробітник з цією поштою, «Прізвище Ім'я»), інакше fallback."""
    import employees  # довідник з 1С; тут, а не вгорі — щоб модуль ролей не тягнув Power BI під час імпорту
    e = employees.by_email(email) if email else None
    return (e or {}).get("name") or fallback


def _person(uid, u):
    """Запис для показу. Ім'я — з 1С, якщо людину там знайдено за поштою; пошта й ім'я, яких немає в записі
    (перенесені з Azure лише з ID), — з довідника користувачів (хто вже входив у систему)."""
    email, name = (u.get("email") or "").strip(), u.get("name") or ""
    if not email or not name:  # колись перенесені з Azure лише за ID
        known = user_directory.lookup(uid) or {}
        email, name = email or known.get("email") or "", name or known.get("name") or ""
    return {"id": uid, "email": email, "name": display_name(email, name) or email or uid,
            "roles": set(u.get("roles") or []), "acc_mode": u.get("acc_mode") if u.get("acc_mode") in ACC_MODES else "both"}


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
    """Люди з роллю: [{"id", "email", "name", "pays_only"}] — для розсилки погоджувачам. pays_only — бухгалтер
    «Лише оплачує»: сповіщення про оплату так, про погодження — ні."""
    return [{"id": u["id"], "email": u["email"], "name": u["name"],
             "pays_only": role in ACCOUNTANT_ROLES and u["acc_mode"] == "pay"} for u in all_users()
            if role in u["roles"] and u["email"]]


def acc_mode(uid):
    with _lock:
        u = _load().get(uid) or {}
    return u.get("acc_mode") if u.get("acc_mode") in ACC_MODES else "both"


def set_roles(uid, email, name, roles, updated_by):
    """Записати ролі людини (невідомі ролі відкидаються; режим бухгалтера лишається). Повертає збережені ролі."""
    roles = sorted(r for r in set(roles) if r in ROLES)
    with _lock:
        data = _load()
        prev = data.get(uid) or {}
        data[uid] = {"email": (email or prev.get("email") or "").strip(), "name": name or prev.get("name") or "",
                     "roles": roles, "updated_by": updated_by, "updated_at": clock.now().isoformat(timespec="seconds")}
        if prev.get("acc_mode") in ACC_MODES:
            data[uid]["acc_mode"] = prev["acc_mode"]
        docstore.write("user_roles", _path(), data)
    return set(roles)


def set_acc_mode(uid, mode, updated_by):
    """Що робить бухгалтер: "both" — погоджує і оплачує, "pay" — лише оплачує. Запис ролей має вже бути."""
    if mode not in ACC_MODES:
        raise ValueError(f"Невідомий режим бухгалтера {mode}")
    with _lock:
        data = _load()
        if uid not in data:
            return
        data[uid] = {**data[uid], "acc_mode": mode, "updated_by": updated_by,
                     "updated_at": clock.now().isoformat(timespec="seconds")}
        docstore.write("user_roles", _path(), data)


def accountants_without_approver():
    """Види бухгалтера, де є люди, але всі «Лише оплачує»: заявки цього виду нікому погодити."""
    people = all_users()
    return [r for r in ACCOUNTANT_ROLES
            if any(r in u["roles"] for u in people) and all(u["acc_mode"] == "pay" for u in people if r in u["roles"])]


def set_name(uid, name):
    """Ім'я з акаунта на вході — для людей, яких адміністратор додав лише за поштою."""
    with _lock:
        data = _load()
        if uid in data and name:
            data[uid] = {**data[uid], "name": name}
            docstore.write("user_roles", _path(), data)


# Інший процес сайту змінив документ — перечитати (docstore.sync)
docstore.register("user_roles", reload)
