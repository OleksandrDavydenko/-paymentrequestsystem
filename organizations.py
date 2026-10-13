"""Довідник організацій з 1С (таблиця Organizations семантичної моделі Power BI).

Колонки: Code, Name, EDRPOU, Country, CountryGUID, IsDeleted.
Ключ організації в системі — Code (поки в таблиці немає GUID організації).
Резидентність: Country = Україна — резидент; порожня або будь-яка інша країна — нерезидент.
Від резидентності залежить, який бухгалтер перевіряє безготівкові заявки.
"""
import app_settings
import pbi_cache

UKRAINE = {"україна", "ukraine", "ua", "украина"}

# Мокові організації (без Power BI) — у тому ж форматі
MOCK = [
    {"code": "1", "name": "ТОВ «Фрейт Транс Південь»", "edrpou": "", "country": "Україна"},
    {"code": "2", "name": "ТОВ «Укрбуд Логістик»", "edrpou": "", "country": "Україна"},
    {"code": "3", "name": "ФОП Коваленко І.П.", "edrpou": "", "country": "Україна"},
    {"code": "4", "name": "Litbia", "edrpou": "", "country": "Serbia"},
]


def is_resident_country(country):
    return (country or "").strip().lower() in UKRAINE


def _record(code, name, edrpou, country, deleted):
    return {"code": code, "name": name, "edrpou": edrpou, "country": country,
            "resident": is_resident_country(country), "deleted": deleted}


def parse(rows):
    by_code = {}
    for r in rows:
        code = str(r.get("Code") or "").strip()
        name = str(r.get("Name") or "").strip()
        if not code or not name:
            continue
        country = str(r.get("Country") or "").strip()
        if country.lower() in ("none", "null"):
            country = ""
        by_code[code] = _record(code, name, str(r.get("EDRPOU") or "").strip(), country, pbi_cache.truthy(r.get("IsDeleted")))
    return sorted(by_code.values(), key=lambda o: o["name"].lower())


def _mock():
    return [_record(o["code"], o["name"], o["edrpou"], o["country"], False) for o in MOCK]


_table = pbi_cache.CachedTable("organizations", "PBI_ORGANIZATIONS_TABLE", "Organizations", parse, _mock)


def get_all(include_deleted=False):
    return [o for o in _table.items() if include_deleted or not o["deleted"]]


def get(code):
    return next((o for o in _table.items() if o["code"] == str(code or "")), None)


def names():
    """{код: назва} — усі, зокрема помічені на видалення (для старих заявок)."""
    return {o["code"]: o["name"] for o in _table.items()}


def disabled_codes():
    """Коди організацій, які адмін заборонив вибирати в заявках."""
    return set(app_settings.get("disabled_organizations") or [])


def options():
    """{код: назва} — для вибору в заявці: без помічених на видалення й без вимкнених адміном."""
    off = disabled_codes()
    return {o["code"]: o["name"] for o in get_all() if o["code"] not in off}


def filter_options():
    """{код: назва} — для фільтрів у списках (усі чинні, зокрема вимкнені для вибору)."""
    return {o["code"]: o["name"] for o in get_all()}


def name(code):
    if not code:
        return ""
    o = get(code)
    return o["name"] if o else f"{code} (немає в довіднику)"


def is_nonresident(code):
    """Нерезидент — якщо країна не Україна (зокрема порожня). Невідома організація — резидент."""
    o = get(code)
    return bool(o) and not o["resident"]


def nonresident_codes():
    return sorted(o["code"] for o in _table.items() if not o["resident"])


def accountant_overrides():
    """{код організації: {"cash": роль, "bank": роль}} — призначення бухгалтерів в адмінці."""
    return app_settings.get("org_accountants") or {}


def accountant_for(code, form):
    """Роль бухгалтера, якого адмін призначив організації для форми оплати (cash / bank), або None."""
    return (accountant_overrides().get(str(code or "")) or {}).get(form)


def refresh():
    return _table.refresh()


def status():
    return _table.status()
