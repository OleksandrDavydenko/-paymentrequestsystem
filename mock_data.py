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

# код -> (назва, css-клас бейджа)
STATUSES = {
    "draft": ("Чернетка", "grey"),
    "pending": ("На погодженні", "amber"),
    "approved": ("Погоджено", "green"),
    "rejected": ("Відхилено", "red"),
    "paid": ("Оплачено", "blue"),
}

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

# ---------------------------------------------------------------- заявки

_ME = ("od@ftpua.com", "Oleksandr Davydenko")
_OTHER = ("i.petrenko@ftpua.com", "Ірина Петренко")


def _req(id_, created, author, pay_date, cp, org, exp, form, cur, amount, status, note="", lines=()):
    return {
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
        "status": status,
        "note": note,
        "lines": [
            {"invoice": n, "amount": Decimal(a), "currency": c, "deal": d}
            for n, a, c, d in lines
        ],
    }


_REQUESTS = {
    r["id"]: r
    for r in [
        _req(1, "2026-09-02T09:14:00", _ME, "2026-09-05", 1, 1, 1, "bank", "UAH", "12450.00", "paid",
             "Доставка документів у серпні",
             [("НП-104512", "12450.00", "UAH", "Договір №15/2025 від 10.01.2025")]),
        _req(2, "2026-09-03T11:40:00", _OTHER, "2026-09-06", 2, 1, 2, "bank", "UAH", "4820.00", "approved"),
        _req(3, "2026-09-10T15:02:00", _ME, "2026-09-12", 3, 2, 4, "bank", "UAH", "23999.99", "approved",
             "Меблі для нового офісу",
             [("ЕП-55123", "23999.99", "UAH", "Рахунок-оферта")]),
        _req(4, "2026-09-15T10:21:00", _ME, "2026-09-26", 4, 1, 1, "bank", "USD", "2230.00", "pending",
             "",
             [("UC-0915", "1250.00", "USD", "Договір інспекції №UC-12"),
              ("UC-0920", "980.00", "USD", "Договір інспекції №UC-12")]),
        _req(5, "2026-09-18T13:55:00", _OTHER, "2026-09-25", 5, 2, 1, "bank", "EUR", "5150.00", "pending"),
        _req(6, "2026-09-20T08:30:00", _ME, "2026-09-22", 6, 3, 4, "cash", "UAH", "3150.00", "rejected",
             "Відхилено: немає накладної"),
        _req(7, "2026-09-23T16:12:00", _ME, "2026-09-30", 5, 1, 5, "bank", "EUR", "3400.00", "draft",
             "",
             [("MSK-7781203", "3400.00", "EUR", "Booking 245887123")]),
        _req(8, "2026-09-24T12:05:00", _OTHER, "2026-09-29", 3, 3, 6, "cash", "UAH", "1800.00", "draft"),
    ]
}


def list_requests(author_email, status=None):
    rows = [
        r for r in _REQUESTS.values()
        if r["author_email"].lower() == author_email.lower()
        and (not status or r["status"] == status)
    ]
    return sorted(rows, key=lambda r: r["created_at"], reverse=True)


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
