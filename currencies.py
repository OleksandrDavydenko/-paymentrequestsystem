"""Довідник валют з 1С (таблиця Currencies семантичної моделі Power BI).

Колонки: GUID, Code (цифровий, 980), Name (буквений код, UAH), FullName, IsDeleted.
Валюта в заявці — буквений код (Name): так її бачать люди й банк. Позначені на видалення
в заявках не вибираються, але старі заявки з ними показуються як є.
"""
import pbi_cache

# Ці валюти — першими в списках і підсумках, решта — за абеткою
PREFERRED = ["UAH", "USD", "EUR"]

MOCK = [
    {"guid": "", "code": "980", "name": "UAH", "full_name": "Гривня"},
    {"guid": "", "code": "840", "name": "USD", "full_name": "Долар США"},
    {"guid": "", "code": "978", "name": "EUR", "full_name": "Євро"},
]


def _record(guid, code, name, full_name, deleted):
    return {"guid": guid, "code": code, "name": name, "full_name": full_name, "deleted": deleted}


def order_key(name):
    return (PREFERRED.index(name) if name in PREFERRED else len(PREFERRED), name)


def parse(rows):
    by_name = {}
    for r in rows:
        name = str(r.get("Name") or "").strip().upper()
        if not name:
            continue
        rec = _record(str(r.get("GUID") or "").strip(), str(r.get("Code") or "").strip(), name,
                      str(r.get("FullName") or "").strip(), pbi_cache.truthy(r.get("IsDeleted")))
        # той самий код двічі — перевага не видаленій
        if name not in by_name or by_name[name]["deleted"] and not rec["deleted"]:
            by_name[name] = rec
    return sorted(by_name.values(), key=lambda c: order_key(c["name"]))


def _mock():
    return [_record(c["guid"], c["code"], c["name"], c["full_name"], False) for c in MOCK]


_table = pbi_cache.CachedTable("currencies", "PBI_CURRENCIES_TABLE", "Currencies", parse, _mock)


def codes():
    """Буквені коди валют, які можна вибрати в заявці (без позначених на видалення)."""
    return [c["name"] for c in _table.items() if not c["deleted"]] or [c["name"] for c in _mock()]


def full_names():
    return {c["name"]: c["full_name"] for c in _table.items()}


def code_by_guid(guid):
    """Буквений код валюти за GUID з 1С (платежі) або None."""
    guid = str(guid or "").strip().upper()
    return next((c["name"] for c in _table.items() if c["guid"] and c["guid"].upper() == guid), None) if guid else None


def is_valid(code):
    return code in codes()


def refresh():
    return _table.refresh()


def status():
    return _table.status()
