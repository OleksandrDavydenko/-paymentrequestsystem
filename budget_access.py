"""Хто й чий бюджет бачить на вкладці «Бюджет» — картка користувача, пункт «Бюджет».

Вкладку (розділ «budget», sections.py) бачать: адміністратор і фіндиректор — усі відділи; керівник відділу — відділи,
які він очолює (разом із тими, де він заступник, що погоджує, — RoleSet.head_departments); а також кожен, кому в
картці додано відділи на перегляд, — окремо «дозволяти» вкладку не треба (opens_tab). Заборона вкладки — виняток
розділу «budget» у user_roles (sections: {"budget": false}).
Що зберігається тут ({id користувача: {"mode", "departments"}}):
  • role — нічого не додано: лише свої відділи (запису немає);
  • all  — усі відділи (напр. аналітик);
  • list — вибрані відділи (до відділів, які людина очолює, додаються).
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


def opens_tab(uid):
    """Чи відкривають налаштування людини вкладку «Бюджет» самі собою: їй додано відділи або «усі відділи»."""
    cfg = get(uid)
    return cfg["mode"] == "all" or (cfg["mode"] == "list" and bool(cfg["departments"]))


def head_departments(uid):
    """Відділи, бюджет яких людина бачить як керівник: які очолює і де вона заступник, що погоджує, — так само, як
    на вході (web/auth.requires → RoleSet.head_departments)."""
    import dept_settings
    import org_structure
    heads = set(org_structure.head_departments(uid))
    return sorted(heads | {d for d in org_structure.viewer_departments(uid) if dept_settings.deputies_approve(d)})


def summary(uid, roles, override=None):
    """Що людина бачить на вкладці «Бюджет» — для картки користувача й зведення в налаштуваннях.
    roles — її ролі (з адміністратором за ADMIN_EMAILS), override — виняток розділу «Бюджет» (False — заборонено).
    {"sees", "denied", "all", "all_by_role", "own": [коди своїх відділів], "extra": [додані коди]}."""
    cfg = get(uid)
    own = head_departments(uid)
    all_by_role = bool(set(roles) & ALL_ROLES)
    everything = all_by_role or cfg["mode"] == "all"
    extra = sorted(set(cfg["departments"]) - set(own)) if cfg["mode"] == "list" and not everything else []
    denied = override is False
    return {"sees": not denied and (everything or bool(own) or bool(extra) or override is True), "denied": denied,
            "all": everything, "all_by_role": all_by_role, "own": own, "extra": extra}


def overview(people, admin_emails=()):
    """Хто бачить бюджет (і кому вкладку заборонено, хоча мав би бачити) — для «Налаштування → Бюджет».
    people — [{"id", "email", "name", "roles", "sections"}]; рядки — ті самі записи + summary(), за іменем."""
    admins = {e.lower() for e in admin_emails}
    out = []
    for p in people:
        roles = set(p.get("roles") or ())
        if (p.get("email") or "").lower() in admins:
            roles.add("admin")
        s = summary(p["id"], roles, (p.get("sections") or {}).get("budget"))
        if s["sees"] or (s["denied"] and (s["all"] or s["own"] or s["extra"])):
            out.append({"id": p["id"], "name": p.get("name") or p.get("email") or p["id"], "email": p.get("email") or "",
                        "roles": roles, **s})
    return sorted(out, key=lambda r: r["name"].lower())


def describe(uid):
    """Коротко для журналу картки: «усі відділи», «додатково: Логістика, Продажі» або ""."""
    import departments
    cfg = get(uid)
    if cfg["mode"] == "all":
        return "усі відділи"
    if cfg["mode"] == "list":
        return "додатково: " + (", ".join(departments.get_name(d) for d in cfg["departments"]) or "—")
    return ""
