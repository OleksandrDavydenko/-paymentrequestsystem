"""Мокові дані та сховище заявок у пам'яті.

Тимчасова заміна БД і довідників 1С. Функції list_requests / get_request /
save_request / next_number пізніше буде переписано на роботу з БД — маршрути
в app.py від цього не зміняться.
Увага: нові й змінені заявки зберігаються лише в пам'яті процесу
і зникають після перезапуску.
"""
from copy import deepcopy
from datetime import date, datetime
from decimal import Decimal

import workflow

# ---------------------------------------------------------------- довідники

ORGANIZATIONS = {
    1: "ТОВ «Фрейт Транс Південь»",
    2: "ТОВ «Укрбуд Логістик»",
    3: "ФОП Коваленко І.П.",
}

COUNTERPARTIES = {
    1: "ТОВ «Нова Пошта»",
    2: "ПрАТ «Київстар»",
    3: "ТОВ «Епіцентр К»",
    4: "ТОВ «Ю-Контрол»",
    5: "Maersk A/S",
    6: "ТОВ «Офісний Світ»",
}

EXPENSE_TYPES = {
    1: "Транспортні послуги",
    2: "Зв'язок та інтернет",
    3: "Оренда приміщень",
    4: "Канцтовари та офісні витрати",
    5: "Митні платежі",
    6: "Ремонт та обслуговування",
    7: "Програмне забезпечення",
}

CURRENCIES = ["UAH", "USD", "EUR"]

PAYMENT_FORMS = {
    "cash": "Готівка",
    "bank": "Безготівка",
}

STATUSES = workflow.STATUSES

# Рахунки контрагентів (у майбутньому — підбір з 1С)
INVOICES = [
    {"id": 1, "counterparty_id": 1, "number": "НП-104512", "date": "2026-09-02", "amount": "12450.00", "currency": "UAH", "deal": "Договір №15/2025 від 10.01.2025"},
    {"id": 2, "counterparty_id": 1, "number": "НП-104788", "date": "2026-09-15", "amount": "8300.50", "currency": "UAH", "deal": "Договір №15/2025 від 10.01.2025"},
    {"id": 3, "counterparty_id": 2, "number": "КС-2026-0912", "date": "2026-09-01", "amount": "4820.00", "currency": "UAH", "deal": "Договір про надання послуг зв'язку №77"},
    {"id": 4, "counterparty_id": 3, "number": "ЕП-55123", "date": "2026-09-10", "amount": "23999.99", "currency": "UAH", "deal": "Рахунок-оферта"},
    {"id": 5, "counterparty_id": 4, "number": "UC-0915", "date": "2026-09-15", "amount": "1250.00", "currency": "USD", "deal": "Договір інспекції №UC-12"},
    {"id": 6, "counterparty_id": 4, "number": "UC-0920", "date": "2026-09-20", "amount": "980.00", "currency": "USD", "deal": "Договір інспекції №UC-12"},
    {"id": 7, "counterparty_id": 5, "number": "MSK-7781203", "date": "2026-09-05", "amount": "3400.00", "currency": "EUR", "deal": "Booking 245887123"},
    {"id": 8, "counterparty_id": 5, "number": "MSK-7781455", "date": "2026-09-18", "amount": "5150.00", "currency": "EUR", "deal": "Booking 245889010"},
    {"id": 9, "counterparty_id": 6, "number": "ОС-3321", "date": "2026-09-12", "amount": "3150.00", "currency": "UAH", "deal": "Видаткова накладна №3321"},
]


# ---------------------------------------------------------------- користувачі (мок)

_ME = ("od@ftpua.com", "Oleksandr Davydenko")
_OTHER = ("i.petrenko@ftpua.com", "Ірина Петренко")
_HEAD = ("m.shevchenko@ftpua.com", "Михайло Шевченко")
_ACC = ("o.bondar@ftpua.com", "Олена Бондар")
_CFO = ("a.melnyk@ftpua.com", "Андрій Мельник")

# ---------------------------------------------------------------- заявки

# Типові кроки процесу для мокової історії: (дата-час, користувач, роль, дія, коментар)
def _route(day, *steps):
    return [(f"2026-09-{day + i:02d}T{9 + i:02d}:15:00", *step) for i, step in enumerate(steps)]


_SUBMIT = (_ME, "initiator", "submit", "")
_HEAD_OK = (_HEAD, "head", "approve", "")
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
        "pay_date": date.fromisoformat(pay_date),
        "counterparty_id": cp,
        "organization_id": org,
        "expense_type_id": exp,
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
        old = req["status"]
        new = workflow.WORKFLOW[old][1][action]
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
             _route(1, _SUBMIT, _HEAD_OK, _ACC_OK, _CFO_OK, (_ACC, "accountant", "pay", "Платіжне доручення №1245")),
             "Доставка документів у серпні",
             [("НП-104512", "12450.00", "UAH", "Договір №15/2025 від 10.01.2025")]),
        _req(2, _OTHER, "2026-09-30", 2, 1, 2, "bank", "UAH", "4820.00",
             _route(22, _SUBMIT)),
        _req(3, _ME, "2026-09-26", 3, 2, 4, "bank", "UAH", "23999.99",
             _route(10, _SUBMIT, _HEAD_OK, _ACC_OK, (_CFO, "cfo", "approve", "Оплатити до кінця місяця"))),
        _req(4, _ME, "2026-09-26", 4, 1, 1, "bank", "USD", "2230.00",
             _route(15, _SUBMIT, _HEAD_OK), "",
             [("UC-0915", "1250.00", "USD", "Договір інспекції №UC-12"),
              ("UC-0920", "980.00", "USD", "Договір інспекції №UC-12")]),
        _req(5, _OTHER, "2026-09-29", 5, 2, 1, "bank", "EUR", "5150.00",
             _route(18, _SUBMIT, _HEAD_OK, _ACC_OK),
             "", [("MSK-7781455", "5150.00", "EUR", "Booking 245889010")]),
        _req(6, _ME, "2026-09-22", 6, 3, 4, "cash", "UAH", "3150.00",
             _route(20, _SUBMIT, (_HEAD, "head", "reject", "Закупівля не узгоджена, немає видаткової накладної"))),
        _req(7, _ME, "2026-09-30", 5, 1, 5, "bank", "EUR", "3400.00", [],
             "", [("MSK-7781203", "3400.00", "EUR", "Booking 245887123")]),
        _req(8, _OTHER, "2026-09-29", 3, 3, 6, "cash", "UAH", "1800.00", []),
        _req(9, _ME, "2026-10-02", 1, 1, 1, "bank", "UAH", "8300.50",
             _route(16, _SUBMIT, _HEAD_OK,
                    (_ACC, "accountant", "rework", "Додайте, будь ласка, договір з контрагентом та рахунок у PDF")),
             "Доставка вантажів, вересень",
             [("НП-104788", "8300.50", "UAH", "Договір №15/2025 від 10.01.2025")]),
        _req(10, _OTHER, "2026-10-01", 6, 2, 4, "bank", "UAH", "2640.00",
             _route(21, _SUBMIT, _HEAD_OK), "Папір, картриджі"),
        _req(11, _OTHER, "2026-10-05", 4, 1, 1, "bank", "USD", "980.00",
             _route(23, _SUBMIT)),
    ]
}


def list_requests(author_email, status=None):
    rows = [
        r for r in _REQUESTS.values()
        if r["author_email"].lower() == author_email.lower()
        and (not status or r["status"] == status)
    ]
    return sorted(rows, key=lambda r: r["created_at"], reverse=True)


def list_for_stage(statuses):
    """Заявки, що чекають на дію на вказаних етапах (черга погоджувача)."""
    rows = [r for r in _REQUESTS.values() if r["status"] in statuses]
    return sorted(rows, key=lambda r: r["history"][-1]["at"])


def get_request(request_id):
    r = _REQUESTS.get(request_id)
    return deepcopy(r) if r else None


def next_number():
    return f"{max(_REQUESTS) + 1:09d}"


def save_request(data):
    """Зберегти заявку. Без 'id' — створити нову. Повертає id."""
    data = deepcopy(data)
    if not data.get("id"):
        data["number"] = next_number()
        data["id"] = int(data["number"])
    _REQUESTS[data["id"]] = data
    return data["id"]


def find_attachment(file_id):
    """Повертає (заявка, метадані файлу) або (None, None)."""
    for r in _REQUESTS.values():
        for a in r.get("attachments", []):
            if a["id"] == file_id:
                return deepcopy(r), dict(a)
    return None, None
