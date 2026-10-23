"""Довідник відділів компанії — до якого відділу належить співробітник.

Це НЕ поле «Відділ» зі статей витрат: статті, які бачить користувач, налаштовуються окремо
(«Статті бюджету»). Тут — оргструктура: відділ співробітника визначає, якому керівнику
відділу видно його заявки і хто їх погоджує.

Джерело — таблиця Departments семантичної моделі Power BI (дані з 1С): DepartmentCode, DepartmentName,
DepartmentGUID (за ним відділ знаходять співробітники й статті витрат), Profitcenter (співробітникам
профітцентрів за замовчуванням даються прямі статті). Старі назви колонок (Код_відділу, Назва_відділу) теж
читаються. Якщо Power BI не налаштований — мокові відділи.
"""
import pbi_cache

# код -> назва (без Power BI)
MOCK_DEPARTMENTS = {
    "LOG": "Логістика",
    "CUS": "Митне оформлення",
    "ACC": "Бухгалтерія",
    "FIN": "Фінансовий відділ",
    "SAL": "Відділ продажу",
    "IT": "ІТ",
    "BA": "Відділ бізнес-аналізу",
    "ADM": "Адміністрація",
}
MOCK_PROFIT_CENTERS = {"LOG", "CUS", "SAL"}


def _guid(value):
    value = str(value or "").strip().upper()
    return value if value.strip("0") and value not in ("NONE", "NULL") else None


def parse(rows):
    by_code = {}
    for r in rows:
        code = str(r.get("DepartmentCode") or r.get("Код_відділу") or r.get("Code") or "").strip()
        name = str(r.get("DepartmentName") or r.get("Назва_відділу") or r.get("Name") or "").strip()
        if code and name and code.lower() not in ("none", "null"):
            by_code[code] = {"code": code, "name": name, "deleted": pbi_cache.truthy(r.get("IsDeleted")),
                             "guid": _guid(r.get("DepartmentGUID")),
                             "profit_center": pbi_cache.truthy(r.get("Profitcenter", r.get("ProfitCenter")))}
    return sorted(by_code.values(), key=lambda d: d["name"].lower())


def _mock():
    return parse([{"DepartmentCode": c, "DepartmentName": n, "DepartmentGUID": f"DEP-{c}",
                   "Profitcenter": c in MOCK_PROFIT_CENTERS} for c, n in MOCK_DEPARTMENTS.items()])


_table = pbi_cache.CachedTable("departments", "PBI_DEPARTMENTS_TABLE", "Departments", parse, _mock)


def get_departments():
    """[{"code", "name"}] за назвою — для вибору (без закритих)."""
    return [{"code": d["code"], "name": d["name"]} for d in _table.items() if not d.get("deleted")]


def get_map():
    return {d["code"]: d["name"] for d in _table.items()}


_by_guid = {"items": None, "map": {}}


def code_by_guid(guid):
    """Код відділу за DepartmentGUID з 1С (співробітники, статті витрат) або None."""
    guid = _guid(guid)
    if not guid:
        return None
    items = _table.items()
    if _by_guid["items"] is not items:  # довідник оновився — перебудувати покажчик
        _by_guid.update(items=items, map={d["guid"]: d["code"] for d in items if d.get("guid")})
    return _by_guid["map"].get(guid)


def is_profit_center(code):
    """Відділ — профітцентр (поле Profitcenter у 1С): його співробітникам за замовчуванням даються прямі статті."""
    return bool(code) and any(d["code"] == code and d.get("profit_center") for d in _table.items())


def profit_centers():
    return [d["code"] for d in _table.items() if d.get("profit_center") and not d.get("deleted")]


def get_name(code):
    """Назва відділу; код, якого вже немає в довіднику, — з позначкою."""
    if not code:
        return ""
    return get_map().get(code) or f"{code} (немає в довіднику)"


def legacy_mapping():
    """{тестовий код: код з 1С} для тестових відділів, назви яких точно є в довіднику 1С."""
    current = _table.items()
    codes = {d["code"] for d in current}
    by_name = {d["name"].strip().lower(): d["code"] for d in current}
    return {old: by_name[name.strip().lower()] for old, name in MOCK_DEPARTMENTS.items()
            if old not in codes and name.strip().lower() in by_name}


def refresh():
    return _table.refresh()


def status():
    return _table.status()
