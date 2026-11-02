"""Види оплати з 1С і хто з бухгалтерів до яких має доступ.

Таблиці семантичної моделі Power BI:
- PaymentTypes: GUID, Code, Name, IsDeleted — довідник «Види оплати» (НАЛ, КАРТА, КАСА, NLB LITBIA…);
- UserPaymentTypes: UserGUID, UserName, PaymentTypeGUID, IsDeleted, EmployeeGUID — вкладка «Виды оплаты»
  в картці користувача 1С (до яких видів оплати в нього є доступ) і хто це зі співробітників (Employees).
Хто з користувачів 1С бухгалтер системи — лише з 1С (onec_link): пошта входу → співробітник (Employees.Email) →
його користувач 1С (EmployeeGUID). Вручну не задається: щоб зв'язок з'явився, у 1С заповнюють пошту співробітника
і співробітника в картці користувача 1С. Без Power BI — мокові дані в тому ж форматі.
"""
import pbi_cache

MOCK_TYPES = [
    {"GUID": "PT-NAL", "Code": "000000001", "Name": "НАЛ", "IsDeleted": False, "OrganizationsGUID": "ORG-G1"},
    {"GUID": "PT-CARD", "Code": "000000002", "Name": "КАРТА", "IsDeleted": False, "OrganizationsGUID": "ORG-G3"},
    {"GUID": "PT-KASA", "Code": "000000003", "Name": "КАСА", "IsDeleted": False, "OrganizationsGUID": "ORG-G2"},
    {"GUID": "PT-NLB", "Code": "000000004", "Name": "NLB LITBIA", "IsDeleted": False, "OrganizationsGUID": "ORG-G4"},
    {"GUID": "PT-PUMB", "Code": "000000005", "Name": "ПУМБ ФТП, ТОВ", "IsDeleted": False,
     "OrganizationsGUID": "00000000000000000000000000000000"},
]
MOCK_ACCESS = [
    {"UserGUID": "U-IRINA", "UserName": "Irina.Chagan", "PaymentTypeGUID": "PT-NAL", "IsDeleted": False},
    {"UserGUID": "U-IRINA", "UserName": "Irina.Chagan", "PaymentTypeGUID": "PT-CARD", "IsDeleted": False},
    {"UserGUID": "U-IRINA", "UserName": "Irina.Chagan", "PaymentTypeGUID": "PT-PUMB", "IsDeleted": False},
    {"UserGUID": "U-OLENA", "UserName": "Olena.Bondar", "PaymentTypeGUID": "PT-KASA", "IsDeleted": False,
     "EmployeeGUID": "EMP-OLENA"},
    {"UserGUID": "U-OLENA", "UserName": "Olena.Bondar", "PaymentTypeGUID": "PT-NLB", "IsDeleted": False,
     "EmployeeGUID": "EMP-OLENA"},
]


def _parse_types(rows):
    by_id = {}
    for r in rows:
        guid, name = str(r.get("GUID") or "").strip(), str(r.get("Name") or "").strip()
        if guid and name:
            org = str(r.get("OrganizationsGUID") or "").strip()
            by_id[guid] = {"id": guid, "code": str(r.get("Code") or "").strip(), "name": name,
                           "deleted": pbi_cache.truthy(r.get("IsDeleted")),
                           # до якої організації належить вид оплати (порожнє посилання 1С — нулі)
                           "org_guid": org if org.strip("0") else None}
    return sorted(by_id.values(), key=lambda t: t["name"].lower())


def _parse_access(rows):
    out = []
    for r in rows:
        user, ptype = str(r.get("UserGUID") or "").strip(), str(r.get("PaymentTypeGUID") or "").strip()
        if user and ptype:
            out.append({"user": user, "user_name": str(r.get("UserName") or "").strip(), "type": ptype,
                        "deleted": pbi_cache.truthy(r.get("IsDeleted")),
                        "employee": str(r.get("EmployeeGUID") or "").strip().upper() or None})
    return out


_types = pbi_cache.CachedTable("payment_types", "PBI_PAYMENT_TYPES_TABLE", "PaymentTypes", _parse_types,
                               lambda: _parse_types(MOCK_TYPES))
_access = pbi_cache.CachedTable("user_payment_types", "PBI_USER_PAYMENT_TYPES_TABLE", "UserPaymentTypes",
                                _parse_access, lambda: _parse_access(MOCK_ACCESS))


INACTIVE_PREFIX = "---"  # так у 1С позначають неактивні / вільні види оплати («---ЛЕНА», «---можнавикористовувати4»)


def is_active(t):
    return not t["deleted"] and not t["name"].startswith(INACTIVE_PREFIX)


def types(include_deleted=False):
    """Види оплати для вибору: без помічених на видалення і без неактивних («---» на початку назви)."""
    return [t for t in _types.items() if include_deleted or is_active(t)]


def get(type_id):
    return next((t for t in _types.items() if t["id"] == str(type_id or "")), None)


def name(type_id):
    t = get(type_id)
    return t["name"] if t else (f"{type_id} (немає в довіднику)" if type_id else "")


def onec_users():
    """Користувачі 1С, у яких є доступ хоча б до одного виду оплати: [{"guid", "name"}] за іменем."""
    seen = {}
    for a in _access.items():
        if not a["deleted"]:
            seen.setdefault(a["user"], a["user_name"] or a["user"])
    return sorted(({"guid": g, "name": n} for g, n in seen.items()), key=lambda u: u["name"].lower())


def onec_user_for_employee(employee_guid):
    """Користувач 1С співробітника (UserPaymentTypes.EmployeeGUID): {"guid", "name"} або None."""
    employee_guid = (employee_guid or "").strip().upper()
    if not employee_guid:
        return None
    a = next((a for a in _access.items() if a.get("employee") == employee_guid and not a["deleted"]), None)
    return {"guid": a["user"], "name": a["user_name"] or a["user"]} if a else None


def onec_user_for_email(email):
    """Користувач 1С за поштою співробітника (Employees → EmployeeGUID) або None."""
    return onec_link(email)["user"]


def onec_link(email):
    """Зв'язок користувача системи з користувачем 1С — лише з 1С, ланцюжком: пошта входу → співробітник 1С з цією
    поштою (Employees.Email) → користувач 1С цього співробітника з видами оплати (UserPaymentTypes.EmployeeGUID).
    {"user": {"guid", "name"} або None, "employee": співробітник або None,
     "reason": "" | "no_email" | "no_employee" | "dismissed" | "no_user" — на якому кроці ланцюжок обірвався}."""
    import employees
    if not (email or "").strip():
        return {"user": None, "employee": None, "reason": "no_email"}
    e = employees.by_email(email)
    if not e:
        return {"user": None, "employee": None, "reason": "no_employee"}
    if e["deleted"]:
        return {"user": None, "employee": e, "reason": "dismissed"}
    user = onec_user_for_employee(e["guid"])
    return {"user": user, "employee": e, "reason": "" if user else "no_user"}


def types_for_user(onec_guid):
    """Види оплати, до яких користувач 1С має доступ (без помічених на видалення)."""
    if not onec_guid:
        return []
    allowed = {a["type"] for a in _access.items() if a["user"] == onec_guid and not a["deleted"]}
    return [t for t in types() if t["id"] in allowed]


def refresh():
    ok1, ok2 = _types.refresh(), _access.refresh()
    return ok1 and ok2


def status():
    st = _types.status()
    acc = _access.status()
    st.update(users=len(onec_users()), access_error=acc["error"], access_source=acc["source"])
    if acc["error"] and not st["error"]:
        st["error"] = acc["error"]
    return st


def from_powerbi():
    """Чи взяті види оплати з Power BI (а не мокові)."""
    return _types.status()["source"] in ("powerbi", "copy")
