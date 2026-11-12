"""Журнал змін налаштувань (settings_log) для людей: ролі, режим бухгалтера, доступ до розділів, чий бюджет людина
бачить і які статті бюджету може вибирати. Ключ запису — user:<id користувача>:<поле>, як dept:<код>:<поле> у відділів.

Пишуть самі сховища (user_roles, budget_access, user_budget) — тож у журнал потрапляє і картка користувача, і
автоматичні зміни з 1С («1С (автоматично)»). Записується лише справжня зміна (було ≠ стало).
"""
FIELDS = {"roles": "ролі", "acc_mode": "бухгалтер — що робить", "sections": "доступ до розділів",
          "budget_access": "бюджет — чий бачить", "budget_items": "статті бюджету"}
PREFIX = "user:"


def log_key(uid, field):
    return f"{PREFIX}{uid}:{field}"


def record(uid, field, old, new, who):
    """Записати зміну, якщо значення справді змінилося. Помилки журналу не заважають зберегти налаштування."""
    if not uid or old == new:
        return
    import settings_log
    settings_log.record(log_key(uid, field), old, new, who)


def _split(key):
    """user:<id>:<поле> -> (id, поле) або None. Id може містити «:» — поле завжди останнє."""
    if not key.startswith(PREFIX):
        return None
    uid, _, field = key[len(PREFIX):].rpartition(":")
    return (uid, field) if uid and field in FIELDS else None


def log_label(key):
    """«Олена Іваненко (olena@…): ролі» або None — не наш ключ."""
    parts = _split(key)
    if not parts:
        return None
    uid, field = parts
    return f"{person_label(uid)}: {FIELDS[field]}"


def person_label(uid):
    """«Олена Іваненко (olena@…)»; людини вже немає серед користувачів — її id."""
    import user_roles
    person = user_roles.get(uid) or {}
    name, email = person.get("name") or "", person.get("email") or ""
    return f"{name} ({email})" if name and email and name != email else (name or email or uid)


def _departments(codes):
    import departments
    return ", ".join(departments.get_name(c) for c in codes or []) or "—"


def log_value(key, value):
    """Значення запису журналу — по-людськи."""
    parts = _split(key)
    field = parts[1] if parts else ""
    if field == "roles":
        import workflow as wf
        return ", ".join(wf.ROLES.get(r, r) for r in value or []) or "без ролей"
    if field == "acc_mode":
        import user_roles
        return user_roles.ACC_MODES.get(value or "both", value)
    if field == "sections":
        import sections
        return sections.describe(value) or "усе за роллю"
    if field == "budget_access":
        v = value or {}
        if v.get("mode") == "all":
            return "усі відділи"
        if v.get("mode") == "list":
            return "свої й додані відділи: " + _departments(v.get("departments"))
        return "лише відділи, які очолює"
    if field == "budget_items":
        v = value or {}
        parts = ["прямі"] if v.get("direct") else []
        if v.get("all_departments"):
            parts.append("з розподілом — усі відділи")
        elif v.get("departments"):
            parts.append("з розподілом — " + ", ".join(v["departments"]))
        return "; ".join(parts) or "немає статей"
    return "" if value is None else str(value)
