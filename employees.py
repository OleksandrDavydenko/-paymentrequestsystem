"""Співробітники з 1С (таблиця Employees семантичної моделі Power BI): EmployeeGUID, Code, Name, Email,
IsDeleted, DepartmentGUID, DepartmentHead (1 — керівник свого відділу).

З неї адмінка пропонує додати співробітників у систему (сторінка «Співробітники з 1С»), бере основний відділ
(за DepartmentGUID — departments.code_by_guid) і знаходить бухгалтеру його користувача 1С (UserPaymentTypes.EmployeeGUID
— payment_types.onec_user_for_employee). Звільнені — IsDeleted (у 1С їхні імена ще й починаються з «XXX»).
Без Power BI — мокові співробітники.
"""
import re

import departments
import pbi_cache

MOCK = [
    {"EmployeeGUID": "EMP-OD", "Code": "00000001", "Name": "Давиденко Олександр", "Email": "od@ftpua.com",
     "IsDeleted": False, "DepartmentGUID": "DEP-BA", "DepartmentHead": 1},
    {"EmployeeGUID": "EMP-IP", "Code": "00000002", "Name": "Петренко Ірина", "Email": "i.petrenko@ftpua.com",
     "IsDeleted": False, "DepartmentGUID": "DEP-LOG"},
    {"EmployeeGUID": "EMP-OLENA", "Code": "00000003", "Name": "Бондар Олена", "Email": "o.bondar@ftpua.com",
     "IsDeleted": False, "DepartmentGUID": "DEP-ACC"},
    {"EmployeeGUID": "EMP-IRINA", "Code": "00000004", "Name": "Чаган Ірина", "Email": "i.chagan@ftpua.com",
     "IsDeleted": False, "DepartmentGUID": "DEP-ACC"},
    {"EmployeeGUID": "EMP-AM", "Code": "00000005", "Name": "Мельник Андрій", "Email": "a.melnyk@ftpua.com",
     "IsDeleted": False, "DepartmentGUID": "DEP-FIN"},
    {"EmployeeGUID": "EMP-SAL", "Code": "00000006", "Name": "Коваль Марія", "Email": "m.koval@ftpua.com",
     "IsDeleted": False, "DepartmentGUID": "DEP-SAL", "DepartmentHead": 1},
    {"EmployeeGUID": "EMP-SN", "Code": "00000007", "Name": "XXXНауменко Сергій", "Email": "sn@ftpua.com",
     "IsDeleted": True, "DepartmentGUID": "DEP-LOG"},
    {"EmployeeGUID": "EMP-NOMAIL", "Code": "00000008", "Name": "Без Пошти", "Email": "",
     "IsDeleted": False, "DepartmentGUID": "DEP-LOG"},
]

_DISMISSED_PREFIX = re.compile(r"^\s*X{2,}\s*")


def _parse(rows):
    by_guid = {}
    for r in rows:
        guid = str(r.get("EmployeeGUID") or "").strip().upper()
        name = _DISMISSED_PREFIX.sub("", str(r.get("Name") or "")).strip()
        if guid and name:
            by_guid[guid] = {"guid": guid, "code": str(r.get("Code") or "").strip(), "name": name,
                             "email": str(r.get("Email") or "").strip().lower(),
                             "deleted": pbi_cache.truthy(r.get("IsDeleted")),
                             "department_guid": str(r.get("DepartmentGUID") or "").strip().upper() or None,
                             "head": pbi_cache.truthy(r.get("DepartmentHead"))}
    return sorted(by_guid.values(), key=lambda e: e["name"].lower())


_table = pbi_cache.CachedTable("employees", "PBI_EMPLOYEES_TABLE", "Employees", _parse, lambda: _parse(MOCK))


def _with_department(e):
    return {**e, "department": departments.code_by_guid(e.get("department_guid"))}


def all_employees():
    """[{"guid", "code", "name", "email", "deleted", "department", "head"}] — department: код відділу з довідника,
    head — керівник цього відділу."""
    return [_with_department(e) for e in _table.items()]


_by_email = {"items": None, "map": {}}


def by_email(email):
    """Співробітник за поштою: працюючий має перевагу над звільненим. None — немає в 1С."""
    email = (email or "").strip().lower()
    if not email:
        return None
    items = _table.items()
    if _by_email["items"] is not items:  # довідник оновився — перебудувати покажчик
        index = {}
        for e in items:
            if e["email"] and (e["email"] not in index or index[e["email"]]["deleted"] and not e["deleted"]):
                index[e["email"]] = e
        _by_email.update(items=items, map=index)
    found = _by_email["map"].get(email)
    return _with_department(found) if found else None


def refresh():
    return _table.refresh()


def status():
    return _table.status()
