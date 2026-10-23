"""Рахунки до оплати з 1С (таблиця InvoiceForPay семантичної моделі Power BI) — «Підбір рахунків» у заявці.

Колонки: InvoiceNumber, InvoiceDate, CounterpartyGUID, DepartmentGUID, Department, Currency, AmountInCurrency,
UnpaidAmountInCurrency, AmountUSD, PaidAmountUSD, UnpaidAmountUSD, PaymentStatus, Deal, DealType.
Таблиця велика, тому для заявки — окремий DAX-запит: рахунки вибраного контрагента лише відділу заявки (два відділи,
що працюють з одним підрядником, не бачать рахунків один одного) і лише з залишком до оплати. Рахунки без відділу
не показуються. Результат тримається в пам'яті 5 хвилин. Без Power BI — мокові рахунки (storage/demo.INVOICES).
"""
import logging
import os
import threading
import time
from decimal import Decimal, InvalidOperation

import departments
import expense_items

logger = logging.getLogger(__name__)

TTL = 300  # с
_cache = {}  # (контрагент, GUID відділу) -> (час, рахунки)
_lock = threading.Lock()

# Відділи й залишки мокових рахунків (за id рахунку в storage/demo.INVOICES); без відділу — не показується
MOCK_DEPARTMENTS = {1: "DEP-LOG", 2: "DEP-LOG", 3: "DEP-ADM", 4: "DEP-SAL", 5: "DEP-LOG", 6: "DEP-SAL",
                    7: "DEP-LOG", 8: "DEP-LOG", 9: None}
MOCK_PAID = {2: Decimal("3000.50"), 8: Decimal("5150.00")}  # частково й повністю оплачені


class InvoicesError(Exception):
    pass


def table():
    return os.environ.get("PBI_INVOICES_TABLE") or "InvoiceForPay"


def _dec(value):
    try:
        return Decimal(str(value)).quantize(Decimal("0.01")) if value not in (None, "") else None
    except (InvalidOperation, ValueError):
        return None


def _record(r):
    number = str(r.get("InvoiceNumber") or "").strip()
    unpaid = _dec(r.get("UnpaidAmountInCurrency"))
    if not number or unpaid is None or unpaid <= 0:
        return None
    return {"number": number, "date": str(r.get("InvoiceDate") or "")[:10],
            "currency": str(r.get("Currency") or "").strip().upper(),
            "amount": str(_dec(r.get("AmountInCurrency")) or unpaid), "unpaid": str(unpaid),
            "deal": str(r.get("Deal") or "").strip(), "deal_type": str(r.get("DealType") or "").strip(),
            "status": str(r.get("PaymentStatus") or "").strip()}


def query(counterparty_id, department_guid):
    """Текст DAX-запиту: рахунки контрагента лише цього відділу й лише з залишком до оплати."""
    t, q = table(), expense_items.dax_string
    return (f"EVALUATE FILTER('{t}', '{t}'[CounterpartyGUID] = {q(counterparty_id)} && "
            f"'{t}'[DepartmentGUID] = {q(department_guid)} && '{t}'[UnpaidAmountInCurrency] > 0)")


def _mock(counterparty_id, department_guid):
    from storage.demo import INVOICES
    rows = []
    for inv in INVOICES:
        dep = MOCK_DEPARTMENTS.get(inv["id"])
        if inv["counterparty_id"] != counterparty_id or not dep or dep != department_guid:
            continue
        amount = Decimal(inv["amount"])
        rows.append({"InvoiceNumber": inv["number"], "InvoiceDate": inv["date"], "Currency": inv["currency"],
                     "AmountInCurrency": amount, "UnpaidAmountInCurrency": amount - MOCK_PAID.get(inv["id"], 0),
                     "Deal": inv["deal"], "DealType": "Договір з постачальником",
                     "PaymentStatus": "Частково оплачено" if inv["id"] in MOCK_PAID else "Не оплачено"})
    return rows


def for_request(counterparty_id, department):
    """Неоплачені рахунки контрагента для відділу заявки (код відділу): [{number, date, currency, amount, unpaid,
    deal, deal_type, status}], від найстаріших до найновіших (оплата по FIFO). Кидає InvoicesError, якщо Power BI
    недоступний."""
    guid = (departments.guid_of(department) or "") if department else ""
    if not counterparty_id or not guid:
        return []
    key = (counterparty_id, guid)
    with _lock:
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < TTL:
            return hit[1]
    if not expense_items._configured():
        rows = _mock(counterparty_id, guid)
    else:
        try:
            rows = expense_items.query_dax(query(counterparty_id, guid))
        except Exception as e:
            logger.exception("Не вдалося отримати рахунки з Power BI")
            raise InvoicesError(f"Не вдалося отримати рахунки з 1С: {e}")
    found = sorted((x for x in (_record(r) for r in rows) if x), key=lambda x: (x["date"], x["number"]))
    with _lock:
        _cache[key] = (time.time(), found)
    return found
