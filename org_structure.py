"""Оргструктура: відділ кожного співробітника; керівник відділу і хто ще бачить заявки відділу.

- users: {oid: {"email", "name", "department"}} — до якого відділу належить співробітник;
- departments: {код: {"head": {"id","email","name"} | None, "viewers": [{"id","email","name"}]}}
  — у відділу ОДИН керівник (одна людина може очолювати кілька відділів) і будь-скільки людей,
  що лише бачать заявки відділу (заступники, менеджери).

Заявка запам'ятовує відділ автора на момент створення, тож зміна відділу співробітника
не переносить його старі заявки. А от зміна керівника діє одразу: заявки, що чекають
погодження керівника, переходять до нового.
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


def _person(oid, entry):
    return {"id": oid, "email": entry.get("email", ""), "name": entry.get("name", "")}


def _migrate(data):
    """Старий формат «heads: {oid: {departments: [...]}}» -> departments[код]["head"]."""
    heads = data.pop("heads", None)
    if not heads:
        return False
    deps = data["departments"]
    for oid, h in heads.items():
        for code in h.get("departments") or []:
            entry = deps.setdefault(code, {"head": None, "viewers": []})
            if not entry.get("head"):  # у відділі лишається перший керівник
                entry["head"] = _person(oid, h)
    return True


def _load():
    global _data
    if _data is None:
        try:
            with open(_path(), encoding="utf-8") as f:
                _data = json.load(f)
        except (OSError, ValueError):
            _data = {}
        _data.setdefault("users", {})
        _data.setdefault("departments", {})
        if _migrate(_data):
            _write()
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


# ---------------------------------------------------------------- відділ співробітника

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


def members_of(department):
    """Співробітники відділу: [{"id", "email", "name"}]."""
    with _lock:
        return [_person(oid, u) for oid, u in _load()["users"].items() if u.get("department") == department]


# ---------------------------------------------------------------- керівник і хто бачить

def staff(department):
    """{"head": людина або None, "viewers": [...]} відділу."""
    with _lock:
        entry = _load()["departments"].get(department or "") or {}
        return {"head": entry.get("head"), "viewers": list(entry.get("viewers") or [])}


def head_of(department):
    return staff(department)["head"] if department else None


def heads_of(department):
    """Керівник відділу списком (0 або 1 людина) — для маршруту і листів."""
    head = head_of(department)
    return [head] if head else []


def watchers_of(department):
    """Усі, хто бачить заявки відділу: керівник + «бачать заявки»."""
    s = staff(department) if department else {"head": None, "viewers": []}
    return ([s["head"]] if s["head"] else []) + s["viewers"]


def head_departments(oid):
    """Коди відділів, які очолює людина."""
    with _lock:
        return sorted(code for code, d in _load()["departments"].items()
                      if oid and (d.get("head") or {}).get("id") == oid)


def viewer_departments(oid):
    """Коди відділів, заявки яких людина бачить як заступник / менеджер."""
    with _lock:
        return sorted(code for code, d in _load()["departments"].items()
                      if oid and any(v.get("id") == oid for v in d.get("viewers") or []))


def set_department_staff(department, head, viewers, updated_by):
    """Призначити керівника (людина або None) і тих, хто бачить заявки відділу.
    Керівник автоматично прибирається зі списку тих, хто бачить."""
    head_id = (head or {}).get("id")
    seen, clean = set(), []
    for v in viewers:
        if v.get("id") and v["id"] != head_id and v["id"] not in seen:
            seen.add(v["id"])
            clean.append({"id": v["id"], "email": v.get("email", ""), "name": v.get("name", "")})
    with _lock:
        deps = _load()["departments"]
        if not head and not clean:
            deps.pop(department, None)
        else:
            deps[department] = {
                "head": {"id": head["id"], "email": head.get("email", ""), "name": head.get("name", "")} if head else None,
                "viewers": clean,
                "updated_by": updated_by,
                "updated_at": clock.now().isoformat(timespec="seconds"),
            }
        _write()


def set_person_assignments(person, head_deps, view_deps, updated_by):
    """Картка користувача: які відділи людина очолює і заявки яких бачить.
    Очолити відділ = стати його єдиним керівником (попередній замінюється).
    Повертає [(відділ, попередній керівник)] — кого замінено."""
    pid = person["id"]
    head_deps, view_deps = set(head_deps), set(view_deps) - set(head_deps)
    replaced = []
    with _lock:
        deps = _load()["departments"]
        codes = set(deps) | head_deps | view_deps
        changed = False
        for code in codes:
            entry = deps.get(code) or {"head": None, "viewers": []}
            head = entry.get("head")
            viewers = [v for v in entry.get("viewers") or [] if v.get("id") != pid]
            if code in head_deps:
                if head and head.get("id") != pid:
                    replaced.append((code, head))
                head = {"id": pid, "email": person.get("email", ""), "name": person.get("name", "")}
            elif head and head.get("id") == pid:
                head = None
            if code in view_deps:
                viewers.append({"id": pid, "email": person.get("email", ""), "name": person.get("name", "")})
            new = {"head": head, "viewers": viewers}
            if new["head"] == entry.get("head") and new["viewers"] == list(entry.get("viewers") or []):
                continue
            changed = True
            if not head and not viewers:
                deps.pop(code, None)
            else:
                deps[code] = {**new, "updated_by": updated_by, "updated_at": clock.now().isoformat(timespec="seconds")}
        if changed:
            _write()
    return replaced


def is_head_of(email, department):
    """Чи очолює людина з цією поштою відділ (для «автор — керівник відділу»)."""
    head = head_of(department)
    return bool(head) and head.get("email", "").lower() == (email or "").lower()
