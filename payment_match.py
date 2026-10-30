"""Зіставлення платежу з 1С із заявками PayDesk (чисті функції; дані готує payments.py).

Порядок:
  1. Номер заявки в призначенні платежу — після «#» або з префіксом (#5, PD000005, PD-5 → заявка 000000005;
     request_numbers.numbers_in; кілька номерів — один платіж на кілька заявок).
     Заявку прив'язуємо, лише якщо збігаються валюта, сума (документа = сумі заявок) і — для безготівки —
     контрагент; інакше розбіжність, заявка не закривається.
  2. Без «#» — за реквізитами: валюта й сума, контрагент (для готівки — або одержувач-автор заявки), вид оплати
     (якщо вже вибраний у заявці), дата платежу між відправкою заявки й «датою оплати» + 45 днів.
     Один кандидат — прив'язка, кілька — розбіжність «неоднозначно», жодного — платіж без заявки.
"""
from datetime import timedelta
from decimal import Decimal

import request_numbers

CENT = Decimal("0.01")
DAYS_AFTER_PAY_DATE = 45

ISSUES = {
    "number_status": "№ заявки в призначенні: заявку не знайдено або вона не «До оплати»",
    "number_currency": "№ заявки в призначенні: інша валюта",
    "number_amount": "№ заявки в призначенні: сума не збігається",
    "number_counterparty": "№ заявки в призначенні: інший контрагент",
    "number_cp_pending": "№ заявки в призначенні: контрагента заявки ще немає в 1С",
    "ambiguous": "Кілька заявок із такими реквізитами — виберіть вручну",
    "removed": "Платіж видалено або розпроведено в 1С, а заявку з ним уже пов'язано",
}


def numbers_in(purpose):
    """Номери заявок із призначення платежу: «#5», «# 12», «PD000007», «PD-12» → ["000000005", "000000007", "000000012"]
    (усі вигляди — request_numbers.numbers_in)."""
    return request_numbers.numbers_in(purpose)


def _same_amount(a, b):
    return a is not None and b is not None and abs(Decimal(a) - Decimal(b)) < CENT


def _counterparty_ok(doc, req, ctx):
    """Контрагент платежу = контрагент заявки: за GUID або (якщо GUID різні) за ЄДРПОУ."""
    cp = req.get("counterparty_id")
    if not cp:
        return False
    if cp in doc["counterparties"]:
        return True
    code = ctx["edrpou"](cp)
    return bool(code) and any(ctx["edrpou"](c) == code for c in doc["counterparties"])


def _recipient_is_author(doc, req, ctx):
    email = ctx["employee_email"](doc.get("recipient")) if doc.get("recipient") else None
    return bool(email) and email.lower() == (req.get("author_email") or "").lower()


def _date_ok(doc, req, ctx):
    submitted = ctx["submitted"](req)
    if submitted and doc["date"] < submitted - timedelta(days=1):
        return False
    dates = [d for d in (req.get("pay_date"), ctx["paid_on"](req)) if d]
    return not dates or doc["date"] <= max(dates) + timedelta(days=DAYS_AFTER_PAY_DATE)


def _by_number(doc, numbers, by_number, cands, ctx):
    reqs = [by_number.get(n) for n in numbers]
    if any(r is None or r["id"] not in cands for r in reqs):
        return {"issue": "number_status", "detail": ", ".join(numbers), "requests": []}
    if any(r.get("currency") != doc["currency"] for r in reqs):
        return {"issue": "number_currency", "detail": doc["currency"], "requests": []}
    total = sum((Decimal(r.get("amount") or 0) for r in reqs), Decimal(0))
    if not _same_amount(total, doc["amount"]):
        return {"issue": "number_amount", "detail": f"{doc['amount']} ≠ {total}", "requests": []}
    if doc["kind"] != "Cash":
        if any(ctx["pending"](r.get("counterparty_id")) for r in reqs):
            return {"issue": "number_cp_pending", "detail": "", "requests": []}
        if not all(_counterparty_ok(doc, r, ctx) for r in reqs):
            return {"issue": "number_counterparty", "detail": "", "requests": []}
    return {"issue": None, "match": "number", "requests": [r["id"] for r in reqs]}


def _by_attributes(doc, cands, ctx):
    found = []
    for r in cands.values():
        if r.get("currency") != doc["currency"] or not _same_amount(r.get("amount"), doc["amount"]):
            continue
        if not (_counterparty_ok(doc, r, ctx) or (doc["kind"] == "Cash" and _recipient_is_author(doc, r, ctx))):
            continue
        if r.get("payment_account_id") and doc.get("payment_type") and r["payment_account_id"] != doc["payment_type"]:
            continue
        if not _date_ok(doc, r, ctx):
            continue
        found.append(r)
    if len(found) == 1:
        return {"issue": None, "match": "auto", "requests": [found[0]["id"]]}
    if len(found) > 1:
        return {"issue": "ambiguous", "detail": ", ".join(request_numbers.label(n) for n in sorted(r["number"] for r in found)), "requests": []}
    return {"issue": None, "match": None, "requests": []}


def match(doc, cands, by_number, ctx):
    """Зіставити документ платежу з заявками.

    doc — {"kind", "date", "amount", "currency", "counterparties", "payment_type", "recipient", "purpose"};
    cands — {id: заявка}: «До оплати» й оплачені без платежу (ще не прив'язані до інших платежів);
    by_number — {номер: заявка} для номерів із призначення (будь-який статус);
    ctx — функції: edrpou(cp_id), employee_email(guid), pending(cp_id), submitted(req) -> date, paid_on(req) -> date.
    Повертає {"issue": код або None, "detail", "match": "number" / "auto" / None, "requests": [id]}."""
    numbers = numbers_in(doc.get("purpose"))
    if numbers:
        return _by_number(doc, numbers, by_number, cands, ctx)
    return _by_attributes(doc, cands, ctx)
