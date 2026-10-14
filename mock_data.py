"""Сховище заявок: list_requests / get_request / save_request / next_number… для маршрутів app.py.

З базою даних (db_sql, Azure SQL) заявки зберігаються в ній: при першому зверненні завантажуються
в пам'ять, кожне збереження одразу записується в базу. Без бази (локально, у тестах) — демо-заявки
в пам'яті процесу, які зникають після перезапуску.
"""
import logging
import threading
from copy import deepcopy
from datetime import date, datetime
from decimal import Decimal

import db_sql
import payment_accounts
import workflow

# ---------------------------------------------------------------- довідники

# Організації — у organizations.py (з 1С через Power BI); у заявці — код організації (рядок)

# Контрагенти — у counterparties.py (з 1С + тимчасові, внесені в заявках)

# Статті витрат — з Power BI (див. expense_items.py). Для мокових заявок — коди мокового довідника.
_EXP = {1: "01.001", 2: "02.001", 3: "03.001", 4: "04.001", 5: "01.004", 6: "05.001", 7: "02.004"}

CURRENCIES = ["UAH", "USD", "EUR"]

PAYMENT_FORMS = workflow.PAYMENT_FORMS

STATUSES = workflow.STATUSES

# Рахунки контрагентів (у майбутньому — підбір з 1С)
INVOICES = [
    {"id": 1, "counterparty_id": "1", "number": "НП-104512", "date": "2026-09-02", "amount": "12450.00", "currency": "UAH", "deal": "Договір №15/2025 від 10.01.2025"},
    {"id": 2, "counterparty_id": "1", "number": "НП-104788", "date": "2026-09-15", "amount": "8300.50", "currency": "UAH", "deal": "Договір №15/2025 від 10.01.2025"},
    {"id": 3, "counterparty_id": "2", "number": "КС-2026-0912", "date": "2026-09-01", "amount": "4820.00", "currency": "UAH", "deal": "Договір про надання послуг зв'язку №77"},
    {"id": 4, "counterparty_id": "3", "number": "ЕП-55123", "date": "2026-09-10", "amount": "23999.99", "currency": "UAH", "deal": "Рахунок-оферта"},
    {"id": 5, "counterparty_id": "4", "number": "UC-0915", "date": "2026-09-15", "amount": "1250.00", "currency": "USD", "deal": "Договір інспекції №UC-12"},
    {"id": 6, "counterparty_id": "4", "number": "UC-0920", "date": "2026-09-20", "amount": "980.00", "currency": "USD", "deal": "Договір інспекції №UC-12"},
    {"id": 7, "counterparty_id": "5", "number": "MSK-7781203", "date": "2026-09-05", "amount": "3400.00", "currency": "EUR", "deal": "Booking 245887123"},
    {"id": 8, "counterparty_id": "5", "number": "MSK-7781455", "date": "2026-09-18", "amount": "5150.00", "currency": "EUR", "deal": "Booking 245889010"},
    {"id": 9, "counterparty_id": "6", "number": "ОС-3321", "date": "2026-09-12", "amount": "3150.00", "currency": "UAH", "deal": "Видаткова накладна №3321"},
]


# ---------------------------------------------------------------- користувачі (мок)

_ME = ("od@ftpua.com", "Oleksandr Davydenko")
_OTHER = ("i.petrenko@ftpua.com", "Ірина Петренко")
_ACC = ("o.bondar@ftpua.com", "Олена Бондар")
_CFO = ("a.melnyk@ftpua.com", "Андрій Мельник")
# Відділ автора мокових заявок (коди з departments.MOCK_DEPARTMENTS)
_AUTHOR_DEPARTMENT = {_ME[0]: "BA", _OTHER[0]: "LOG"}

# ---------------------------------------------------------------- заявки

# Типові кроки процесу для мокової історії: (дата-час, користувач, роль, дія, коментар)
def _route(day, *steps):
    return [(f"2026-09-{day + i:02d}T{9 + i:02d}:15:00", *step) for i, step in enumerate(steps)]


_SUBMIT = (_ME, "initiator", "submit", "")
_ACC_OK = (_ACC, "accountant", "approve", "Документи в порядку")
_CFO_OK = (_CFO, "cfo", "approve", "")


def _req(id_, author, pay_date, cp, org, exp, form, cur, amount, steps, note="", lines=()):
    created = steps[0][0] if steps else "2026-09-24T10:00:00"
    req = {
        "id": id_,
        "number": f"{id_:09d}",
        "created_at": datetime.fromisoformat(created),
        "author_email": author[0],
        "author_name": author[1],
        "department": _AUTHOR_DEPARTMENT.get(author[0]),
        "pay_date": date.fromisoformat(pay_date),
        "counterparty_id": str(cp),
        "organization_id": str(org),
        "expense_code": _EXP[exp],
        "payment_form": form,
        "currency": cur,
        "amount": Decimal(amount),
        "status": "draft",
        "note": note,
        "lines": [
            {"invoice": n, "amount": Decimal(a), "currency": c, "deal": d}
            for n, a, c, d in lines
        ],
        "history": [],
        "attachments": [],
    }
    req["history"].append({
        "at": req["created_at"], "user_name": author[1], "user_email": author[0], "role": "initiator",
        "action": "create", "from_status": None, "to_status": "draft", "comment": "",
    })
    # Прогнати кроки по таблиці переходів, щоб історія й статус були узгоджені
    for at, user, role, action, comment in steps:
        if user is _ME:
            user = author
        if role == "accountant":
            role = workflow.accountant_role(req)
        old = req["status"]
        new = workflow.WORKFLOW[old][1][action]
        if action == "approve" and role in workflow.ACCOUNTANT_ROLES:
            # бухгалтер при погодженні вибирає «Вид оплати»
            req["payment_account_id"] = payment_accounts.mock_options_for(req)[0]["id"]
        req["history"].append({
            "at": datetime.fromisoformat(at), "user_name": user[1], "user_email": user[0], "role": role,
            "action": action, "from_status": old, "to_status": new, "comment": comment,
        })
        req["status"] = new
    return req


_REQUESTS = {
    r["id"]: r
    for r in [
        _req(1, _ME, "2026-09-05", 1, 1, 1, "bank", "UAH", "12450.00",
             _route(1, _SUBMIT, _ACC_OK, _CFO_OK, (_ACC, "accountant", "pay", "Платіжне доручення №1245")),
             "Доставка документів у серпні",
             [("НП-104512", "12450.00", "UAH", "Договір №15/2025 від 10.01.2025")]),
        _req(2, _OTHER, "2026-09-30", 2, 1, 2, "bank", "UAH", "4820.00",
             _route(22, _SUBMIT)),
        _req(3, _ME, "2026-09-26", 3, 2, 4, "bank", "UAH", "23999.99",
             _route(10, _SUBMIT, _ACC_OK, (_CFO, "cfo", "approve", "Оплатити до кінця місяця"))),
        _req(4, _ME, "2026-09-26", 4, 1, 1, "bank", "USD", "2230.00",
             _route(15, _SUBMIT), "",
             [("UC-0915", "1250.00", "USD", "Договір інспекції №UC-12"),
              ("UC-0920", "980.00", "USD", "Договір інспекції №UC-12")]),
        _req(5, _OTHER, "2026-09-29", 5, 4, 1, "bank", "EUR", "5150.00",
             _route(18, _SUBMIT, _ACC_OK),
             "", [("MSK-7781455", "5150.00", "EUR", "Booking 245889010")]),
        _req(6, _ME, "2026-09-22", 6, 3, 4, "cash", "UAH", "3150.00",
             _route(20, _SUBMIT, (_ACC, "accountant", "reject", "Закупівля не узгоджена, немає видаткової накладної"))),
        _req(7, _ME, "2026-09-30", 5, 4, 5, "bank", "EUR", "3400.00", [],
             "", [("MSK-7781203", "3400.00", "EUR", "Booking 245887123")]),
        _req(8, _OTHER, "2026-09-29", 3, 3, 6, "cash", "UAH", "1800.00", _route(24, _SUBMIT), "Аванс на ремонт складу"),
        _req(9, _ME, "2026-10-02", 1, 1, 1, "bank", "UAH", "8300.50",
             _route(16, _SUBMIT,
                    (_ACC, "accountant", "rework", "Додайте, будь ласка, договір з контрагентом та рахунок у PDF")),
             "Доставка вантажів, вересень",
             [("НП-104788", "8300.50", "UAH", "Договір №15/2025 від 10.01.2025")]),
        _req(10, _OTHER, "2026-10-01", 6, 2, 4, "bank", "UAH", "2640.00",
             _route(21, _SUBMIT), "Папір, картриджі"),
        _req(11, _OTHER, "2026-10-05", 4, 1, 1, "bank", "USD", "980.00",
             _route(23, _SUBMIT)),
        # Погоджені фіндиректором — чекають оплати
        _req(12, _OTHER, "2026-10-06", 4, 2, 1, "bank", "USD", "1250.00",
             _route(1, _SUBMIT, _ACC_OK, _CFO_OK), "",
             [("UC-0915", "1250.00", "USD", "Договір інспекції №UC-12")]),
        _req(13, _OTHER, "2026-10-09", 5, 4, 1, "bank", "EUR", "5150.00",
             _route(2, _SUBMIT, _ACC_OK, _CFO_OK), "Фрахт, жовтень",
             [("MSK-7781455", "5150.00", "EUR", "Booking 245889010")]),
        _req(14, _ME, "2026-10-08", 6, 3, 4, "cash", "UAH", "2400.00",
             _route(3, _SUBMIT, _ACC_OK, _CFO_OK), "Канцтовари для складу"),
        _req(15, _OTHER, "2026-10-12", 2, 1, 2, "bank", "UAH", "6180.00",
             _route(4, _SUBMIT, _ACC_OK, _CFO_OK), "Зв'язок, жовтень"),
        _req(16, _ME, "2026-10-14", 5, 4, 5, "bank", "USD", "3700.00",
             _route(5, _SUBMIT, _ACC_OK, _CFO_OK)),
    ]
}



def _parallel(req, day, approved_by=()):
    """Мокова заявка в паралельному режимі: відправлена, частина погоджувачів уже погодила."""
    author = (req["author_email"], req["author_name"])
    at = datetime.fromisoformat(f"2026-10-{day:02d}T09:30:00")
    req["route_mode"], req["approvals"], req["status"] = "parallel", {}, "approval"
    req["history"].append({"at": at, "user_name": author[1], "user_email": author[0], "role": "initiator",
                           "action": "submit", "from_status": "draft", "to_status": "approval", "comment": ""})
    for user, role in approved_by:
        role = workflow.accountant_role(req) if role == "accountant" else role
        req["approvals"][role] = {"at": at, "user_name": user[1]}
        if role in workflow.ACCOUNTANT_ROLES:
            req["payment_account_id"] = payment_accounts.mock_options_for(req)[0]["id"]
        req["history"].append({"at": at, "user_name": user[1], "user_email": user[0], "role": role,
                               "action": "approve", "from_status": "approval", "to_status": "approval",
                               "comment": ""})
    _REQUESTS[req["id"]] = req


_parallel(_req(17, _OTHER, "2026-10-16", 2, 1, 2, "bank", "UAH", "3920.00", [], "Зв'язок, листопад"), 6,
          approved_by=[(_ACC, "accountant")])
_parallel(_req(18, _ME, "2026-10-15", 6, 3, 4, "cash", "UAH", "1450.00", [], "Канцтовари"), 7)


def _at_head(req, day):
    """Мокова заявка, що чекає погодження керівника відділу (послідовний маршрут)."""
    at = datetime.fromisoformat(f"2026-10-{day:02d}T10:00:00")
    req["route_mode"], req["head_step"], req["approvals"], req["status"] = "sequential", True, {}, "dept_head"
    req["history"].append({"at": at, "user_name": req["author_name"], "user_email": req["author_email"],
                           "role": "initiator", "action": "submit", "from_status": "draft",
                           "to_status": "dept_head", "comment": ""})
    _REQUESTS[req["id"]] = req


_at_head(_req(19, _OTHER, "2026-10-20", 1, 1, 1, "bank", "UAH", "7600.00", [], "Перевезення, жовтень"), 8)

def _cash_paid(req, paid_on, confirmed_on=None, reminders=0):
    """Мокова оплачена готівкова заявка: автор отримав гроші в касі й передає підряднику."""
    paid_at = datetime.fromisoformat(f"{paid_on}T15:00:00")
    req["history"].append({"at": paid_at, "user_name": _ACC[1], "user_email": _ACC[0],
                           "role": workflow.accountant_role(req), "action": "pay", "from_status": "to_pay",
                           "to_status": "paid", "comment": "Видано з каси"})
    req["status"] = "paid"
    since = paid_at.date()
    req["handover"] = {"status": "pending", "since": since, "reminders": reminders,
                       "last_reminder": date.fromordinal(since.toordinal() + reminders) if reminders else None}
    if confirmed_on:
        when = date.fromisoformat(confirmed_on)
        req["handover"].update({"status": "done", "date": when, "by_name": req["author_name"],
                                "by_email": req["author_email"], "at": paid_at, "comment": "", "on_behalf": False})
        req["history"].append({"at": datetime.fromisoformat(f"{confirmed_on}T17:40:00"),
                               "user_name": req["author_name"], "user_email": req["author_email"],
                               "role": "initiator", "action": "handover", "from_status": "paid", "to_status": "paid",
                               "comment": f"Передано {when:%d.%m.%Y}"})
    _REQUESTS[req["id"]] = req


_cash_paid(_req(20, _ME, "2026-10-10", 6, 3, 4, "cash", "UAH", "5200.00",
                _route(6, _SUBMIT, _ACC_OK, _CFO_OK), "Монтаж стелажів на складі"), "2026-10-10", reminders=2)
_cash_paid(_req(21, _OTHER, "2026-10-07", 3, 1, 6, "cash", "UAH", "2750.00",
                _route(3, _SUBMIT, _ACC_OK, _CFO_OK), "Дрібний ремонт офісу"), "2026-10-07", confirmed_on="2026-10-07")


# Учасники (спостерігачі) для демонстрації
def _participant(req_id, person, added_by):
    _REQUESTS[req_id].setdefault("participants", []).append(
        {"email": person[0], "name": person[1], "added_by": added_by[1],
         "added_at": _REQUESTS[req_id]["created_at"]})


_participant(2, _ME, _OTHER)
_participant(4, _OTHER, _ME)


# ---------------------------------------------------------------- демо-заявки на справжніх довідниках

def _realize_demo():
    """Коли довідники з 1С підключені (Power BI), демо-заявки отримують справжні організації, контрагентів,
    статті витрат, відділи й види оплати — щоб на сайті все виглядало як у роботі. Без Power BI
    (локально, у тестах) — лишаються мокові. Вибір стабільний: однаковий після кожного перезапуску."""
    import random

    import counterparties
    import departments
    import expense_items
    import org_structure
    import organizations
    import payment_types

    if not expense_items._configured():
        return
    rng = random.Random(2026)
    clean = lambda name: name and not name.startswith(("-", "(", "*")) and "test" not in name.lower()         and "тест" not in name.lower()

    # Організації: мокові 1–3 — резиденти, 4 — нерезидент; ФОП Коваленко — по змозі справжній ФОП
    orgs = [o for o in organizations.get_all() if o["code"] in organizations.options() and clean(o["name"])]
    res = sorted((o for o in orgs if o["resident"]), key=lambda o: o["name"])
    nonres = sorted((o for o in orgs if not o["resident"]), key=lambda o: o["name"])
    fop = [o for o in res if "фоп" in o["name"].lower()] or res
    if not res or not nonres:
        return
    companies = [o for o in res if "фоп" not in o["name"].lower()] or res
    pair = rng.sample(companies, 2) if len(companies) > 1 else companies * 2
    org_map = {"1": pair[0]["code"], "2": pair[1]["code"], "3": rng.choice(fop)["code"], "4": rng.choice(nonres)["code"]}

    # Контрагенти: 5 — нерезидент, решта — українські юрособи з коректним ЄДРПОУ
    cps = [c for c in counterparties.onec_items() if clean(c["name"])]
    cp_res = sorted((c for c in cps if c["resident"] and len(c["edrpou"]) == 8), key=lambda c: c["id"])
    cp_non = sorted((c for c in cps if not c["resident"]), key=lambda c: c["id"])
    if len(cp_res) < 5 or not cp_non:
        return
    picked = rng.sample(cp_res, 5)
    cp_map = {"1": picked[0]["id"], "2": picked[1]["id"], "3": picked[2]["id"], "4": picked[3]["id"],
              "6": picked[4]["id"], "5": rng.choice(cp_non)["id"]}

    # Статті витрат, відділи, види оплати
    items = sorted(expense_items.get_items(), key=lambda i: i["code"])
    exp_codes = sorted(set(_EXP.values()))
    exp_map = dict(zip(exp_codes, (i["code"] for i in rng.sample(items, min(len(items), len(exp_codes))))))         if items else {}
    deps = [d["code"] for d in departments.get_departments()]
    my_dep = next((u.get("department") for u in org_structure._load()["users"].values()
                   if (u.get("email") or "").lower() == _ME[0] and u.get("department") in deps), None)
    dep_map = {"BA": my_dep or (rng.choice(deps) if deps else None), "LOG": rng.choice(deps) if deps else None}
    ptypes = [t["id"] for t in payment_types.types()]

    for r in _REQUESTS.values():
        r["organization_id"] = org_map.get(r["organization_id"], r["organization_id"])
        r["counterparty_id"] = cp_map.get(r["counterparty_id"], r["counterparty_id"])
        r["expense_code"] = exp_map.get(r["expense_code"], r["expense_code"])
        if r.get("department") in dep_map:
            r["department"] = dep_map[r["department"]]
        if r.get("payment_account_id"):
            r["payment_account_id"] = rng.choice(ptypes) if ptypes else None
    for inv in INVOICES:
        inv["counterparty_id"] = cp_map.get(inv["counterparty_id"], inv["counterparty_id"])


# ---------------------------------------------------------------- сховище: база даних або пам'ять (демо)

# З базою (Azure SQL) — лише справжні заявки з бази, демо-заявки туди не потрапляють.
# Без бази (локально, у тестах) — демо-заявки в пам'яті, зникають після перезапуску.
_DB = db_sql.configured()
_loaded = not _DB
_load_lock = threading.Lock()
if _DB:
    _REQUESTS = {}
else:
    try:
        _realize_demo()
    except Exception:  # довідники недоступні — лишаємо мокові демо-дані
        logging.getLogger(__name__).exception("Не вдалося підставити справжні довідники в демо-заявки")


def _reqs():
    """Усі заявки {id: заявка}; з базою — при першому зверненні завантажуються з неї (далі — з пам'яті)."""
    global _loaded
    if not _loaded:
        with _load_lock:
            if not _loaded:
                _REQUESTS.update(db_sql.load_requests())
                _loaded = True
    return _REQUESTS


def _persist(*reqs):
    if _DB and reqs:
        db_sql.save_requests(reqs)


def list_requests(author_email, status=None):
    rows = [
        r for r in _reqs().values()
        if r["author_email"].lower() == author_email.lower()
        and (not status or r["status"] == status)
    ]
    return sorted(rows, key=lambda r: r["created_at"], reverse=True)


def list_participating(email):
    """Заявки, до яких користувача додано учасником; нові — першими."""
    email = (email or "").lower()
    rows = [r for r in _reqs().values()
            if any(p["email"].lower() == email for p in r.get("participants") or [])]
    return sorted(rows, key=lambda r: r["created_at"], reverse=True)


def list_queue(roles):
    """Заявки, що чекають на рішення однієї з ролей (черга «На погодження»)."""
    rows = [r for r in _reqs().values() if workflow.in_queue(r, roles)]
    return sorted(rows, key=lambda r: r["history"][-1]["at"])


def list_all():
    """Усі заявки системи (для адміністратора), нові — першими."""
    return sorted(_reqs().values(), key=lambda r: r["created_at"], reverse=True)


def list_processed(roles):
    """Заявки, по яких ці ролі вже ухвалили рішення; найсвіжіше рішення — першим."""
    rows = [(workflow.processed_entry(r, roles), r) for r in _reqs().values()]
    return [r for e, r in sorted(((e, r) for e, r in rows if e), key=lambda x: x[0]["at"], reverse=True)]


def list_paid(roles):
    """Оплачені заявки, видимі цим ролям; найсвіжіша оплата — першою."""
    rows = [r for r in _reqs().values() if workflow.in_paid(r, roles)]
    return sorted(rows, key=lambda r: workflow.paid_entry(r)["at"] if workflow.paid_entry(r) else r["created_at"],
                  reverse=True)


def list_to_pay(roles):
    """Заявки «До оплати», видимі цим ролям; найближча дата оплати — першою."""
    rows = [r for r in _reqs().values() if workflow.in_payments(r, roles)]
    return sorted(rows, key=lambda r: (r["pay_date"], r["id"]))


def remap_departments(mapping):
    """Замінити старі коди відділів у заявках ({старий: новий}). Повертає кількість заявок."""
    count = 0
    for r in _reqs().values():
        if r.get("department") in mapping:
            r["department"] = mapping[r["department"]]
            _persist(r)
            count += 1
    return count


def replace_counterparty(old_id, new_id, user, role, comment):
    """Прив'язка тимчасового контрагента до 1С: усі заявки з old_id отримують new_id. Повертає кількість."""
    count = 0
    for r in _reqs().values():
        if r.get("counterparty_id") == old_id:
            r["counterparty_id"] = new_id
            workflow.add_history(r, "cp_link", user, role, comment, r["status"], r["status"])
            _persist(r)
            count += 1
    return count


def get_request(request_id):
    r = _reqs().get(request_id)
    return deepcopy(r) if r else None


def next_number():
    return f"{max(_reqs(), default=0) + 1:09d}"


def save_request(data):
    """Зберегти заявку. Без 'id' — створити нову. Повертає id."""
    data = deepcopy(data)
    if not data.get("id"):
        data["number"] = next_number()
        data["id"] = int(data["number"])
    _persist(data)  # спершу в базу: якщо запис не вдався, у пам'яті теж нічого не змінюється
    _reqs()[data["id"]] = data
    return data["id"]


def find_attachment(file_id):
    """Повертає (заявка, метадані файлу) або (None, None)."""
    for r in _reqs().values():
        for a in r.get("attachments", []):
            if a["id"] == file_id:
                return deepcopy(r), dict(a)
    return None, None
