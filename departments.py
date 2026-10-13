"""Довідник відділів компанії — до якого відділу належить співробітник.

Це НЕ поле «Відділ» зі статей витрат: статті, які бачить користувач, налаштовуються окремо
(«Статті бюджету»). Тут — оргструктура: відділ співробітника визначає, якому керівнику
відділу видно його заявки і хто їх погоджує.

Джерело — таблиця Departments семантичної моделі Power BI (дані з 1С), колонки
Код_відділу і Назва_відділу. Якщо Power BI не налаштований — мокові відділи.
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


def parse(rows):
    by_code = {}
    for r in rows:
        code = str(r.get("Код_відділу") or r.get("Code") or "").strip()
        name = str(r.get("Назва_відділу") or r.get("Name") or "").strip()
        if code and name and code.lower() not in ("none", "null"):
            by_code[code] = {"code": code, "name": name, "deleted": pbi_cache.truthy(r.get("IsDeleted"))}
    return sorted(by_code.values(), key=lambda d: d["name"].lower())


def _mock():
    return sorted(({"code": c, "name": n, "deleted": False} for c, n in MOCK_DEPARTMENTS.items()),
                  key=lambda d: d["name"].lower())


_table = pbi_cache.CachedTable("departments", "PBI_DEPARTMENTS_TABLE", "Departments", parse, _mock)


def get_departments():
    """[{"code", "name"}] за назвою — для вибору (без закритих)."""
    return [{"code": d["code"], "name": d["name"]} for d in _table.items() if not d.get("deleted")]


def get_map():
    return {d["code"]: d["name"] for d in _table.items()}


def get_name(code):
    """Назва відділу; код, якого вже немає в довіднику, — з позначкою."""
    if not code:
        return ""
    return get_map().get(code) or f"{code} (немає в довіднику)"


def refresh():
    return _table.refresh()


def status():
    return _table.status()
