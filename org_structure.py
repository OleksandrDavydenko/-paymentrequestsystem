"""Оргструктура: відділ кожного співробітника і відділи, які очолюють керівники.

Ключ — ID користувача в Entra (oid). Заявка запам'ятовує відділ автора на момент
створення, тож зміна відділу співробітника не переносить його старі заявки.
Поки бази даних немає — JSON-файл (/home/data на Azure переживає перезапуск і деплой).
"""
import json
import os
import threading

import clock

_lock = threading.Lock()
_data = None


def _path():
    base = "/home/data" if os.environ.get("WEBSITE_SITE_NAME") else os.path.join(os.path.dirname(__file__), "data")
    return os.environ.get("ORG_STRUCTURE_FILE") or os.path.join(base, "org_structure.json")


def _load():
    global _data
    if _data is None:
        try:
            with open(_path(), encoding="utf-8") as f:
                _data = json.load(f)
        except (OSError, ValueError):
            _data = {}
        _data.setdefault("users", {})
        _data.setdefault("heads", {})
    return _data


def _write():
    path = _path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(_data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def reload():
    global _data
    with _lock:
        _data = None
        _load()


def department_of(oid):
    """Код відділу співробітника або None."""
    with _lock:
        return (_load()["users"].get(oid or "") or {}).get("department")


def set_department(oid, email, name, department, updated_by):
    with _lock:
        users = _load()["users"]
        if department:
            users[oid] = {"email": email, "name": name, "department": department,
                          "updated_by": updated_by, "updated_at": clock.now().isoformat(timespec="seconds")}
        else:
            users.pop(oid, None)
        _write()


def head_departments(oid):
    """Коди відділів, які очолює керівник (порожній список — не призначено)."""
    with _lock:
        return list((_load()["heads"].get(oid or "") or {}).get("departments") or [])


def set_head_departments(oid, email, name, departments, updated_by):
    with _lock:
        heads = _load()["heads"]
        if departments:
            heads[oid] = {"email": email, "name": name, "departments": sorted(set(departments)),
                          "updated_by": updated_by, "updated_at": clock.now().isoformat(timespec="seconds")}
        else:
            heads.pop(oid, None)
        _write()


def heads_of(department):
    """Керівники відділу: [{"id", "email", "name"}]."""
    if not department:
        return []
    with _lock:
        return [{"id": oid, "email": h.get("email", ""), "name": h.get("name", "")}
                for oid, h in _load()["heads"].items() if department in (h.get("departments") or [])]


def is_head_of(email, department):
    """Чи очолює людина з цією поштою відділ (для «автор — керівник відділу»)."""
    email = (email or "").lower()
    return any(h["email"].lower() == email for h in heads_of(department))
