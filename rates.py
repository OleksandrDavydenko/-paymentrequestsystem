"""Курси валют з 1С (таблиця ExchangeRates семантичної моделі Power BI): Date, GUID (валюти), RateBU.

RateBU — гривень за 1 одиницю валюти. Потрібен останній відомий курс кожної валюти — для порогу
погодження фіндиректора (USD, EUR чи UAH — див. cfo_rules.py). З Power BI читаються лише рядки за останні 31 день.
"""
from datetime import timedelta
from decimal import Decimal, InvalidOperation

import clock
import currencies
import expense_items
import pbi_cache

WINDOW_DAYS = 31

# Мокові курси (без Power BI) — гривень за 1 одиницю
MOCK = {"UAH": "1", "USD": "44.9801", "EUR": "50.3453", "PLN": "11.5297", "CZK": "2.0669", "GBP": "59.6643",
        "RSD": "0.42874", "JPY": "0.28387"}


def _fetch(table):
    """Рядки курсів за останні WINDOW_DAYS днів (а не вся історія з 1980 року)."""
    today = clock.today()
    start = today - timedelta(days=WINDOW_DAYS)
    t = table.replace("'", "''")
    return expense_items.query_dax(
        f"EVALUATE FILTER('{t}', '{t}'[Date] >= DATE({start.year}, {start.month}, {start.day}) "
        f"&& '{t}'[Date] <= DATE({today.year}, {today.month}, {today.day}))")


def parse(rows):
    """Останній курс кожної валюти: [{"name", "guid", "date", "rate"}]. Код валюти — з довідника Currencies за GUID."""
    by_guid = {c["guid"]: c["name"] for c in currencies._table.items() if c.get("guid")}
    latest = {}
    for r in rows:
        guid = str(r.get("GUID") or "").strip()
        day = str(r.get("Date") or "")[:10]
        try:
            rate = Decimal(str(r.get("RateBU")))
        except (InvalidOperation, TypeError):
            continue
        if not guid or not day or rate <= 0:
            continue
        if guid not in latest or day > latest[guid]["date"]:
            latest[guid] = {"guid": guid, "name": by_guid.get(guid, ""), "date": day, "rate": str(rate)}
    return sorted((x for x in latest.values() if x["name"]), key=lambda x: currencies.order_key(x["name"]))


def _mock():
    day = clock.today().isoformat()
    return [{"guid": "", "name": n, "date": day, "rate": r} for n, r in MOCK.items()]


_table = pbi_cache.CachedTable("rates", "PBI_RATES_TABLE", "ExchangeRates", parse, _mock,
                               ttl_default_minutes=60, fetch=_fetch)


def latest():
    """{код валюти: (курс у гривнях за 1 од., дата курсу 'YYYY-MM-DD')}."""
    return {x["name"]: (Decimal(x["rate"]), x["date"]) for x in _table.items()}


def convert(amount, currency, to="USD"):
    """Еквівалент у валюті to за останнім відомим курсом 1С: (сума, дата курсу) або (None, причина)."""
    try:
        amount = Decimal(str(amount))
    except (InvalidOperation, TypeError):
        return None, "не вказано суму"
    if currency == to:
        return amount, None
    rates = latest()
    rate = lambda code: (Decimal(1), None) if code == "UAH" else rates.get(code)  # noqa: E731
    src, dst = rate(currency), rate(to)
    if not dst:
        return None, f"немає курсу {to}"
    if not src:
        return None, f"немає курсу {currency}"
    day = max((d for d in (src[1], dst[1]) if d), default=None)
    return (amount * src[0] / dst[0]).quantize(Decimal("0.01")), day


def to_usd(amount, currency):
    """Еквівалент у USD за останнім відомим курсом: (сума, дата курсу) або (None, причина)."""
    return convert(amount, currency, "USD")


def refresh():
    return _table.refresh()


def status():
    return _table.status()
