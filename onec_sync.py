"""Співробітники з 1С → користувачі системи («Адміністрування → Користувачі й ролі → Співробітники з 1С»).

Адміністратор бачить, кого з працюючих співробітників (Employees з поштою) ще немає в системі, і додає вибраних
за правилом (налаштування new_user_roles / new_user_budget_own):
  • ролі з правила (за замовчуванням «Ініціатор»);
  • основний відділ — з 1С (DepartmentGUID), а з позначкою DepartmentHead — ще й керівник цього відділу
    (у відділу один керівник: попередній замінюється);
  • статті бюджету: з розподілом свого відділу (відділи статей за DepartmentGUID), а співробітникам
    профітцентрів (Departments.Profitcenter) — ще й прямі;
  • бухгалтеру — користувач 1С для «Видів оплати» (UserPaymentTypes.EmployeeGUID).
Далі все це можна змінити в картці користувача. Тих, хто вже є, сторінка показує з розбіжностями з 1С (відділ,
керівник відділу, користувач 1С), а звільнених у 1С, які ще мають ролі, — окремо, щоб закрити їм доступ.
Що людина очолює інші відділи (не з 1С), сторінка не чіпає.
"""
import app_settings
import departments
import employees
import expense_items
import org_structure
import payment_types
import user_budget
import user_directory
import user_onec
import user_roles
import workflow as wf


def rule():
    return {"roles": list(app_settings.get("new_user_roles") or []),
            "budget_own": bool(app_settings.get("new_user_budget_own"))}


def rule_budget(department):
    """Статті бюджету за правилом для основного відділу: {"direct", "all_departments", "departments"}."""
    own = expense_items.departments_for(department) if department and rule()["budget_own"] else []
    return {"direct": departments.is_profit_center(department), "all_departments": False, "departments": own}


def _people_by_email():
    return {u["email"].lower(): u for u in user_roles.all_users() if u["email"]}


def _employees_by_email():
    """Один запис на пошту: працюючий має перевагу над звільненим."""
    out = {}
    for e in employees.all_employees():
        if e["email"] and (e["email"] not in out or out[e["email"]]["deleted"] and not e["deleted"]):
            out[e["email"]] = e
    return out


def overview():
    """{"new": [...], "existing": [...], "dismissed": [...], "not_in_onec": кількість}."""
    people, staff = _people_by_email(), _employees_by_email()
    new, existing, dismissed = [], [], []
    for email, e in sorted(staff.items(), key=lambda kv: kv[1]["name"].lower()):
        u = people.get(email)
        onec = payment_types.onec_user_for_employee(e["guid"])
        if e["deleted"]:
            if u and u["roles"]:
                dismissed.append({**e, "id": u["id"], "user_name": u["name"], "roles": u["roles"]})
            continue
        if not u:
            budget = rule_budget(e["department"])
            new.append({**e, "onec": onec, "budget": budget})
            continue
        dept_now = org_structure.department_of(u["id"])
        is_acc = bool(u["roles"] & wf.ACCOUNTANT_ROLES)
        link = user_onec.get(u["id"])
        issues = []
        if e["department"] and dept_now != e["department"]:
            issues.append("dept")
        if e["head"] and e["department"] and e["department"] not in org_structure.head_departments(u["id"]):
            issues.append("head")
        if is_acc and onec and (not link or link["guid"] != onec["guid"]):
            issues.append("onec")
        existing.append({**e, "id": u["id"], "user_name": u["name"], "roles": u["roles"], "dept_now": dept_now,
                         "onec": onec, "link": link, "is_accountant": is_acc, "issues": issues})
    existing.sort(key=lambda p: (not p["issues"], p["name"].lower()))
    return {"new": new, "existing": existing, "dismissed": dismissed,
            "not_in_onec": sum(1 for email in people if email not in staff)}


def _make_head(uid, email, name, department, who):
    """Призначити керівником відділу з 1С, лишивши інші відділи, які людина очолює чи бачить."""
    heads = set(org_structure.head_departments(uid)) | {department}
    org_structure.set_person_assignments({"id": uid, "email": email, "name": name}, heads,
                                         org_structure.viewer_departments(uid), who)


def _uid_for(email):
    """id нової людини: той, під яким вона вже входила (Entra oid у довіднику), інакше пошта."""
    known = user_directory.find_active(email)
    return known["id"] if known else email


def add(emails, who):
    """Додати співробітників за правилом. Повертає кількість доданих."""
    people, staff, r = _people_by_email(), _employees_by_email(), rule()
    added = 0
    for email in emails:
        email = (email or "").strip().lower()
        e = staff.get(email)
        if not e or e["deleted"] or email in people:
            continue
        uid = _uid_for(email)
        user_roles.set_roles(uid, email, e["name"], r["roles"], who)
        if e["department"]:
            org_structure.set_department(uid, email, e["name"], e["department"], who)
            if e["head"]:
                _make_head(uid, email, e["name"], e["department"], who)
        budget = rule_budget(e["department"])
        if budget["direct"] or budget["departments"]:
            user_budget.save_settings(uid, email, e["name"], updated_by=who, **budget)
        onec = payment_types.onec_user_for_employee(e["guid"])
        if onec and set(r["roles"]) & wf.ACCOUNTANT_ROLES:
            user_onec.set_link(uid, email, onec["guid"], onec["name"], who)
        added += 1
    return added


def _existing(ids):
    by_id = {p["id"]: p for p in overview()["existing"]}
    return [by_id[i] for i in ids if i in by_id]


def fix_from_onec(ids, who):
    """Відділ, керівника відділу і (бухгалтерам) користувача 1С — як у 1С. Повертає кількість змінених людей."""
    changed = 0
    for p in _existing(ids):
        if not p["issues"]:
            continue
        if "dept" in p["issues"]:
            org_structure.set_department(p["id"], p["email"], p["user_name"], p["department"], who)
        if "head" in p["issues"]:
            _make_head(p["id"], p["email"], p["user_name"], p["department"], who)
        if "onec" in p["issues"]:
            user_onec.set_link(p["id"], p["email"], p["onec"]["guid"], p["onec"]["name"], who)
        changed += 1
    return changed


def apply_rule(ids, who):
    """Правило до тих, хто вже є: лише додати ролі й статті (прямі — профітцентрам), нічого не знімаючи."""
    r, changed = rule(), 0
    for p in _existing(ids):
        department = org_structure.department_of(p["id"]) or p["department"]
        roles = set(p["roles"]) | set(r["roles"])
        touched = False
        if roles != set(p["roles"]):
            user_roles.set_roles(p["id"], p["email"], p["user_name"], roles, who)
            touched = True
        prev = user_budget.get(p["id"]) or {}
        want = rule_budget(department)
        direct = bool(prev.get("direct")) or want["direct"]
        deps = sorted(set(prev.get("departments") or []) | set(want["departments"]), key=str.lower)
        if not prev.get("all_departments") and (direct != bool(prev.get("direct"))
                                                or deps != sorted(prev.get("departments") or [], key=str.lower)):
            user_budget.save_settings(p["id"], p["email"], p["user_name"], direct, False, deps, updated_by=who)
            touched = True
        changed += touched
    return changed


def close_access(ids, who):
    """Звільненим у 1С — зняти всі ролі (історія заявок лишається). Повертає кількість."""
    by_id = {p["id"]: p for p in overview()["dismissed"]}
    closed = 0
    for i in ids:
        p = by_id.get(i)
        if p:
            user_roles.set_roles(p["id"], p["email"], p["user_name"], [], who)
            closed += 1
    return closed
