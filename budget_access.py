"""Які відділи людина бачить на вкладці «Бюджет».

Саму вкладку відкриває розділ «budget» (sections.py; картка користувача → «Доступ до розділів»): типово — керівникам
відділів, фіндиректору й адміністратору. Тут — лише чий бюджет показувати:
  • role — типово: адміністратор і фіндиректор — усі відділи, керівник відділу — відділи, які він очолює
    (разом із тими, де він заступник, що погоджує, — RoleSet.head_departments);
  • all  — усі відділи (напр. бухгалтер чи аналітик, якому відкрили вкладку);
  • list — вибрані відділи (до відділів, які людина очолює, додаються).
Налаштування — у картці користувача («Бюджет: які відділи»), ключ — id користувача (як у user_roles).
У базі даних (docstore), а без бази — JSON-файл (/home/data на Azure).
"""
import os
import threading

import clock
import docstore

MODES = {"role": "Як керівник — свої відділи", "all": "Усі відділи", "list": "Вибрані відділи"}
ALL_ROLES = frozenset({"admin", "cfo"})  # бачать бюджет усіх відділів без налаштувань

_lock = threading.Lock()
_data = None


def _path():
    base = "/home/data" if os.environ.get("WEBSITE_SITE_NAME") else os.path.join(os.path.dirname(__file__), "data")
    return os.environ.get("BUDGET_ACCESS_FILE") or os.path.join(base, "budget_access.json")


def _load():
    global _data
    if _data is None:
        try:
            _data = docstore.read("budget_access", _path())
        except (OSError, ValueError):
            _data = {}
    return _data


def reload():
    global _data
    with _lock:
        _data = None
        _load()


def get(uid):
    """{"mode", "departments"} — збережене або типове ("role")."""
    with _lock:
        rec = _load().get(uid) if uid else None
    mode = (rec or {}).get("mode")
    return {"mode": mode if mode in MODES else "role", "departments": list((rec or {}).get("departments") or [])}


def save(uid, email, mode, departments, updated_by):
    """Зберегти; повертає True, якщо щось змінилося. «role» без відділів — запис прибирається."""
    mode = mode if mode in MODES else "role"
    departments = sorted({d for d in (departments or []) if d}) if mode == "list" else []
    with _lock:
        data = _load()
        old = data.get(uid) or {}
        if (old.get("mode") or "role") == mode and sorted(old.get("departments") or []) == departments:
            return False
        if mode == "role":
            data.pop(uid, None)
        else:
            data[uid] = {"email": email, "mode": mode, "departments": departments,
                         "updated_at": clock.now().isoformat(timespec="seconds"), "updated_by": updated_by}
        docstore.write("budget_access", _path(), data)
        return True


def departments_for(uid, roles):
    """Відділи, бюджет яких людина бачить: None — усі, інакше множина кодів (може бути порожньою)."""
    if set(roles) & ALL_ROLES:
        return None
    cfg = get(uid)
    if cfg["mode"] == "all":
        return None
    own = set(getattr(roles, "head_departments", ()) or ())
    if cfg["mode"] == "list":
        own |= set(cfg["departments"])
    return frozenset(own)


def describe(uid):
    """Коротко для журналу картки: «усі відділи», «вибрані: LOG, SAL» або ""."""
    cfg = get(uid)
    if cfg["mode"] == "all":
        return "усі відділи"
    if cfg["mode"] == "list":
        return "вибрані: " + (", ".join(cfg["departments"]) or "—")
    return ""
