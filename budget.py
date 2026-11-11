"""Бюджет відділів з 1С (через Power BI): план і факт по місяцях, розшифровка факту й залишок статті для заявки.

Таблиці датасету PayDesk (SQL — sql/BudgetPlanFact.sql і sql/BudgetFactDetails.sql, логіка — як у кубі FTP_Budgeting):
  • BudgetPlanFact (PBI_BUDGET_TABLE) — рядок = місяць × відділ × стаття: Month, DepartmentGUID, DepartmentName,
    ExpenseItemGUID, ExpenseItemCode, ExpenseItemName, ExpenseGroup, OwnerDepartmentGUID, PlanUSD, FactUSD.
    Кешується (pbi_cache) з 1 січня минулого року; оновлення — раз на PBI_BUDGET_MINUTES (30 хв).
  • BudgetFactDetails (PBI_BUDGET_DETAILS_TABLE) — рядки розподілу накладних з документом-джерелом: розшифровка
    факту (живий запит по кліку) і SourceDocumentGUID — які платежі вже потрапили у факт місяця.
Суми — у USD. GUID — 32 hex без 0x (як Departments[DepartmentGUID]); нулі — «Інше / не розподілено».

Відділ у звіті — ключ: код відділу з довідника (departments.code_by_guid), "-" — «Інше / не розподілено» (нульовий
GUID), "?<GUID>" — відділ, якого немає в довіднику відділів PayDesk.

Залишок статті для заявки (remaining) = план місяця − факт 1С − заявки PayDesk у дорозі. Відділ бюджету — Owner
статті (OwnerDepartmentGUID), якщо він є, інакше відділ заявки (так само факт розноситься в 1С). «У дорозі» —
заявки цього місяця з тією ж статтею й відділом бюджету: на погодженні, «До оплати» й оплачені, платіж яких
(payment_links) ще не з'явився серед документів розподілу місяця. Місяць заявки — місяць оплати для оплачених,
інакше місяць «Дати оплати», але не раніше поточного. Стаття «є в бюджеті», якщо у відділу на неї є план у цьому році.
Без Power BI — мокові дані.
"""
import logging
import os
import threading
import time
import zlib
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

import clock
import departments
import expense_items
import pbi_cache

logger = logging.getLogger(__name__)

ZERO_GUID = "0" * 32
OTHER_KEY = "-"  # ключ відділу «Інше / не розподілено» (нульовий GUID)
OTHER_NAME = "Інше / не розподілено"
MONTHS = ("Січень", "Лютий", "Березень", "Квітень", "Травень", "Червень", "Липень", "Серпень", "Вересень",
          "Жовтень", "Листопад", "Грудень")
MONTHS_GEN = ("січня", "лютого", "березня", "квітня", "травня", "червня", "липня", "серпня", "вересня", "жовтня",
              "листопада", "грудня")
MONTHS_SHORT = ("Січ", "Лют", "Бер", "Кві", "Тра", "Чер", "Лип", "Сер", "Вер", "Жов", "Лис", "Гру")
PERIODS = {"month": "Місяць", "quarter": "Квартал", "ytd": "З початку року", "year": "Рік"}
VIEWS = {"table": "Таблиця", "months": "По місяцях"}
OVER_MODES = {"warn": "Лише попередження",
              "cfo": "Попередження, і заявку обов'язково погоджує фіндиректор",
              "block": "Не дозволяти відправити заявку"}
PENDING_LABEL = "на погодженні, до оплати й оплачені, яких ще немає у факті 1С"


def _guid(value):
    value = str(value or "").strip().upper()
    if value.startswith("0X"):
        value = value[2:]
    return value if value.strip("0") and value not in ("NONE", "NULL", "NAN") else None


def _num(value):
    try:
        return round(float(value or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def _dec(value):
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0.00")


def ym(value):
    """'YYYY-MM' з дати, дати-часу або рядка ISO."""
    if isinstance(value, (date, datetime)):
        return f"{value.year:04d}-{value.month:02d}"
    return str(value or "")[:7]


def month_title(month):
    """'2026-11' -> 'листопада 2026' (для «Бюджет листопада 2026»)."""
    y, m = int(month[:4]), int(month[5:7])
    return f"{MONTHS_GEN[m - 1]} {y}"


# ---------------------------------------------------------------- таблиця BudgetPlanFact

def parse(rows):
    out = []
    for r in rows:
        month = ym(r.get("Month"))
        if len(month) != 7:
            continue
        plan, fact = _num(r.get("PlanUSD")), _num(r.get("FactUSD"))
        if not plan and not fact:
            continue
        out.append({
            "month": month,
            "dept_guid": _guid(r.get("DepartmentGUID")),
            "dept_name": str(r.get("DepartmentName") or "").strip(),
            "item_guid": _guid(r.get("ExpenseItemGUID")),
            "item": str(r.get("ExpenseItemCode") or "").strip(),
            "item_name": str(r.get("ExpenseItemName") or "").strip() or "(без статті)",
            "group": str(r.get("ExpenseGroup") or "").strip() or "(без групи)",
            "owner": _guid(r.get("OwnerDepartmentGUID")),
            "plan": plan,
            "fact": fact,
        })
    return out


def _fetch(table):
    """Рядки з 1 січня минулого року — для звіту за два роки й залишку в заявках."""
    t = table.replace("'", "''")
    y = clock.today().year - 1
    return expense_items.query_dax(f"EVALUATE FILTER('{t}', '{t}'[Month] >= DATE({y}, 1, 1))")


# Мокові дані (без Power BI): статті бюджету мокових відділів (departments.MOCK_DEPARTMENTS, GUID "DEP-<код>")
MOCK_GROUPS = {"01": "Логістика й перевезення", "02": "ІТ і зв'язок", "03": "Приміщення", "04": "Офіс",
               "05": "Ремонт і транспорт", "06": "Відрядження", "07": "Персонал", "08": "Професійні послуги",
               "09": "Банк", "10": "Маркетинг"}
MOCK_LINES = {
    "LOG": ["01.001", "01.002", "01.005", "01.007", "04.001", "05.003", "05.004", "06.001", "06.002"],
    "CUS": ["01.003", "01.004", "04.001", "04.003"],
    "ACC": ["04.001", "08.002", "02.005"],
    "FIN": ["09.001", "08.002", "04.001"],
    "SAL": ["10.001", "10.002", "06.001", "06.002", "06.003", "02.002"],
    "IT": ["02.001", "02.003", "02.004", "05.002"],
    "BA": ["01.001", "02.001", "02.002", "04.001", "07.001", "08.003"],
    "ADM": ["03.001", "03.002", "03.003", "03.004", "04.002", "07.002", "07.003"],
}
MOCK_OWNERS = {"02.004": "IT", "09.001": "FIN", "03.001": "ADM"}  # статті з Owner-відділом (непрямі)


def _mock():
    names = dict(expense_items.MOCK_ITEMS)
    today = clock.today()
    cur = (today.year, today.month)
    rows = []
    for dept, codes in MOCK_LINES.items():
        for code in codes:
            h = zlib.crc32(f"{dept}:{code}".encode())
            base = 150 + (h % 37) * 45
            owner = MOCK_OWNERS.get(code)
            for year in (today.year - 1, today.year):
                for m in range(1, 13):
                    hm = zlib.crc32(f"{dept}:{code}:{year}:{m}".encode())
                    plan = 0 if hm % 11 == 0 else base * (1 + (hm % 5 - 2) / 20)
                    factor = 0.55 + (hm % 80) / 100
                    if (year, m) < cur:
                        fact = (plan or base * 0.3) * factor
                    elif (year, m) == cur:
                        fact = (plan or 0) * factor * 0.45
                    else:
                        fact = 0
                    rows.append({"Month": f"{year:04d}-{m:02d}-01T00:00:00", "DepartmentGUID": f"DEP-{dept}",
                                 "DepartmentName": departments.MOCK_DEPARTMENTS.get(dept, dept),
                                 "ExpenseItemGUID": f"ITEM-{code}", "ExpenseItemCode": code,
                                 "ExpenseItemName": names.get(code, code), "ExpenseGroup": MOCK_GROUPS[code[:2]],
                                 "OwnerDepartmentGUID": f"DEP-{owner}" if owner else ZERO_GUID,
                                 "PlanUSD": round(plan, 2), "FactUSD": round(fact, 2)})
    # Витрати без статті — «Інше / не розподілено» (бачать лише ті, кому видно всі відділи)
    for m in range(1, cur[1]):
        rows.append({"Month": f"{today.year:04d}-{m:02d}-01", "DepartmentGUID": ZERO_GUID, "DepartmentName": "Інше",
                     "ExpenseItemGUID": ZERO_GUID, "ExpenseItemCode": "", "ExpenseItemName": "(без статті)",
                     "ExpenseGroup": "(без групи)", "OwnerDepartmentGUID": ZERO_GUID, "PlanUSD": 0,
                     "FactUSD": round(40 + m * 7.5, 2)})
    return parse(rows)


_table = pbi_cache.CachedTable("budget", "PBI_BUDGET_TABLE", "BudgetPlanFact", parse, _mock,
                               ttl_env="PBI_BUDGET_MINUTES", ttl_default_minutes=30, fetch=_fetch)


def rows():
    return _table.items()


def refresh():
    return _table.refresh()


def status():
    return _table.status()


def details_table():
    return os.environ.get("PBI_BUDGET_DETAILS_TABLE") or "BudgetFactDetails"


# ---------------------------------------------------------------- відділи

def dept_key(row):
    """Ключ відділу рядка: код з довідника, OTHER_KEY — «Інше», "?GUID" — відділу немає в довіднику."""
    guid = row["dept_guid"]
    if not guid:
        return OTHER_KEY
    return departments.code_by_guid(guid) or f"?{guid}"


def dept_guid_of(key):
    if key == OTHER_KEY:
        return ZERO_GUID
    if not key:
        return None
    if key.startswith("?"):
        return key[1:]
    return (departments.guid_of(key) or "").upper() or None


def allowed_key(key, allowed):
    """allowed — None (усі відділи) або множина кодів: «Інше» й невідомі відділи — лише для «усіх»."""
    return allowed is None or (key not in ("", OTHER_KEY) and not key.startswith("?") and key in allowed)


def department_options(allowed):
    """[(ключ, назва)] відділів, що є в бюджеті й доступні людині: за назвою, «Інше» — в кінці."""
    seen = {}
    for r in rows():
        key = dept_key(r)
        if key not in seen and allowed_key(key, allowed):
            seen[key] = OTHER_NAME if key == OTHER_KEY else (departments.get_map().get(key) or r["dept_name"] or key)
    return sorted(seen.items(), key=lambda kv: (kv[0] == OTHER_KEY, kv[1].lower()))


def years():
    found = {int(r["month"][:4]) for r in rows()}
    return sorted(found) or [clock.today().year]


# ---------------------------------------------------------------- звіт

def period_months(kind, year, month, quarter):
    """Номери місяців періоду (1..12)."""
    if kind == "month":
        return [month]
    if kind == "quarter":
        return list(range(quarter * 3 - 2, quarter * 3 + 1))
    if kind == "ytd":
        today = clock.today()
        last = today.month if year == today.year else (12 if year < today.year else 0)
        return list(range(1, last + 1))
    return list(range(1, 13))


def level(plan, fact):
    """Колір виконання: none — нічого, ok — до 90 %, warn — 90–100 %, over — понад план (і факт без плану)."""
    if not plan and not fact:
        return "none"
    if not plan:
        return "over" if fact > 0 else "ok"
    pct = fact / plan
    return "over" if pct > 1.0001 else ("warn" if pct >= 0.9 else "ok")


def _natural(code):
    return expense_items._natural_key(code) if code else (("~",),)


def _blank():
    return {"plan": 0.0, "fact": 0.0, "months": [[0.0, 0.0] for _ in range(12)]}


def _add(node, plan_m, fact_m, months):
    for i in range(12):
        node["months"][i][0] += plan_m[i]
        node["months"][i][1] += fact_m[i]
    node["plan"] += sum(plan_m[m - 1] for m in months)
    node["fact"] += sum(fact_m[m - 1] for m in months)


def report(allowed, dept, year, months, view="table"):
    """Дерево відділ → група → стаття з підсумками за період (months) і по місяцях року.
    dept — ключ відділу або "*" (усі доступні). У «Таблиці» — статті з планом чи фактом у періоді,
    «По місяцях» — з планом чи фактом у році."""
    items = {}
    for r in rows():
        if int(r["month"][:4]) != year:
            continue
        key = dept_key(r)
        if not allowed_key(key, allowed) or (dept != "*" and key != dept):
            continue
        ik = (key, r["item"] or r["item_guid"] or "")
        it = items.get(ik)
        if it is None:
            it = items[ik] = {"dept": key, "dept_name": r["dept_name"], "code": r["item"], "name": r["item_name"],
                              "group": r["group"], "owner": bool(r["owner"]),
                              "plan_m": [0.0] * 12, "fact_m": [0.0] * 12}
        m = int(r["month"][5:7]) - 1
        it["plan_m"][m] += r["plan"]
        it["fact_m"][m] += r["fact"]

    total = _blank()
    tree = {}
    over = 0
    for it in items.values():
        node = _blank()
        _add(node, it["plan_m"], it["fact_m"], months)
        if view == "table" and not (round(node["plan"], 2) or round(node["fact"], 2)):
            continue
        if view != "table" and not (any(it["plan_m"]) or any(it["fact_m"])):
            continue
        node.update(code=it["code"], name=it["name"], owner=it["owner"], dept=it["dept"])
        node["level"] = level(node["plan"], node["fact"])
        over += node["level"] == "over"
        d = tree.setdefault(it["dept"], {**_blank(), "key": it["dept"], "groups": {},
                                         "name": OTHER_NAME if it["dept"] == OTHER_KEY else
                                         (departments.get_map().get(it["dept"]) or it["dept_name"] or it["dept"])})
        g = d["groups"].setdefault(it["group"], {**_blank(), "name": it["group"], "rows": []})
        g["rows"].append(node)
        for target in (g, d, total):
            _add(target, it["plan_m"], it["fact_m"], months)

    depts = sorted(tree.values(), key=lambda d: (d["key"] == OTHER_KEY, d["name"].lower()))
    for d in depts:
        d["groups"] = sorted(d["groups"].values(), key=lambda g: (g["name"] == "(без групи)", g["name"].lower()))
        for g in d["groups"]:
            g["rows"].sort(key=lambda i: _natural(i["code"]))
            g["level"] = level(g["plan"], g["fact"])
        d["level"] = level(d["plan"], d["fact"])
    total["level"] = level(total["plan"], total["fact"])
    return {"departments": depts, "total": total, "over": over,
            "count": sum(len(g["rows"]) for d in depts for g in d["groups"])}


# ---------------------------------------------------------------- розшифровка факту

def details(key, item, year, months):
    """Рядки розподілу (документи 1С) за відділом, статтею й місяцями: {"rows", "total", "error"}."""
    months = sorted(months) or [1]
    m1, m2 = months[0], months[-1]
    if not expense_items._configured():
        return {"rows": _mock_details(key, item, year, months), "error": None}
    guid = dept_guid_of(key)
    if not guid:
        return {"rows": [], "error": "Відділу немає в довіднику відділів"}
    t = details_table().replace("'", "''")
    q = expense_items.dax_string
    dax = (f"EVALUATE FILTER('{t}', '{t}'[DepartmentGUID] = {q(guid)} && '{t}'[ExpenseItemCode] = {q(item or '')} "
           f"&& '{t}'[Month] >= DATE({year}, {m1}, 1) && '{t}'[Month] <= DATE({year}, {m2}, 1))")
    try:
        raw = expense_items.query_dax(dax)
    except Exception as e:  # мережа, права, ще не додана таблиця — показати у вікні розшифровки
        logger.exception("budget: details")
        return {"rows": [], "error": str(e)}
    out = [{"date": str(r.get("Date") or "")[:10], "doc_type": r.get("DocumentType") or "",
            "doc_number": str(r.get("DocumentNumber") or "").strip(), "doc_date": str(r.get("DocumentDate") or "")[:10],
            "employee": r.get("EmployeeName") or "", "comment": str(r.get("Comment") or "").strip(),
            "status": r.get("AllocationStatus") or "", "distribution": str(r.get("DistributionNumber") or "").strip(),
            "amount": _num(r.get("AmountUSD"))} for r in raw]
    out.sort(key=lambda r: (r["date"], r["doc_date"], r["doc_number"]))
    return {"rows": out, "error": None}


def _mock_details(key, item, year, months):
    out = []
    kinds = ("Списання", "Списання", "Накладні витрати", "Розподіл комісій банку")
    people = ("Петренко Ірина", "Бондар Олена", "", "Мельник Андрій")
    for r in rows():
        if dept_key(r) != key or r["item"] != (item or "") or int(r["month"][:4]) != year \
                or int(r["month"][5:7]) not in months or not r["fact"]:
            continue
        h = zlib.crc32(f"{key}{item}{r['month']}".encode())
        parts = 1 + h % 3
        left = r["fact"]
        for i in range(parts):
            amount = round(left if i == parts - 1 else r["fact"] * (0.5 if parts == 2 else 0.3), 2)
            left = round(left - amount, 2)
            k = (h >> (i * 3)) % len(kinds)
            day = 3 + (h >> i) % 24
            out.append({"date": f"{r['month']}-28", "doc_type": kinds[k], "doc_number": f"FTP{(h + i) % 100000:08d}",
                        "doc_date": f"{r['month']}-{day:02d}", "employee": people[k], "status":
                        "На Owner статті" if r["owner"] else "Прямий (відділ рядка)",
                        "comment": f"{r['item_name']} — {MONTHS[int(r['month'][5:7]) - 1].lower()}",
                        "distribution": f"РН{int(r['month'][5:7]):04d}", "amount": amount})
    out.sort(key=lambda r: (r["date"], r["doc_date"], r["doc_number"]))
    return out


# ---------------------------------------------------------------- залишок статті для заявки

_index_state = {"items": None, "cell": {}, "year_plan": {}, "owners": {}, "names": {}}
_index_lock = threading.Lock()


def _index():
    items = rows()
    with _index_lock:
        st = _index_state
        if st["items"] is not items:
            cell, year_plan, owners, names = {}, {}, {}, {}
            for r in items:
                if not r["dept_guid"] or not r["item"]:
                    continue
                k = (r["dept_guid"], r["item"], r["month"])
                p, f = cell.get(k, (0.0, 0.0))
                cell[k] = (p + r["plan"], f + r["fact"])
                yk = (r["dept_guid"], r["item"], r["month"][:4])
                year_plan[yk] = year_plan.get(yk, 0.0) + r["plan"]
                if r["owner"]:
                    owners[r["item"]] = r["owner"]
                names.setdefault(r["dept_guid"], r["dept_name"])
                names.setdefault(("item", r["item"]), r["item_name"])
            st.update(items=items, cell=cell, year_plan=year_plan, owners=owners, names=names)
        return st


def budget_department(dept_code, item_code):
    """GUID відділу, з бюджету якого йде стаття: Owner статті, інакше відділ заявки (або None)."""
    owner = _index()["owners"].get(item_code)
    if owner:
        return owner
    if not dept_code:
        return None
    return (departments.guid_of(dept_code) or "").upper() or None


def target_month(pay_date=None):
    """Місяць бюджету для нової заявки: місяць «Дати оплати», але не раніше поточного."""
    cur = ym(clock.today())
    if isinstance(pay_date, str) and pay_date:
        try:
            pay_date = date.fromisoformat(pay_date[:10])
        except ValueError:
            pay_date = None
    m = ym(pay_date) if pay_date else cur
    return max(m, cur)


def request_month(row):
    """Місяць, у якому заявка витрачає бюджет: оплачена — місяць оплати, інакше — target_month(дата оплати)."""
    if row.get("status") == "paid" and row.get("paid_at"):
        return ym(row["paid_at"])
    return target_month(row.get("pay_date"))


_fact_docs = {}
_fact_docs_lock = threading.Lock()


def in_fact_docs(month):
    """GUID документів-джерел, що вже потрапили в розподіл місяця (BudgetFactDetails), — кеш на 30 хв."""
    if not expense_items._configured():
        return frozenset()
    with _fact_docs_lock:
        hit = _fact_docs.get(month)
        if hit and time.time() - hit[0] < _table._ttl():
            return hit[1]
    t = details_table().replace("'", "''")
    y, m = int(month[:4]), int(month[5:7])
    try:
        raw = expense_items.query_dax(
            f"EVALUATE DISTINCT(SELECTCOLUMNS(FILTER('{t}', '{t}'[Month] = DATE({y}, {m}, 1)), "
            f"\"SourceDocumentGUID\", '{t}'[SourceDocumentGUID]))")
        docs = frozenset(g for g in (_guid(r.get("SourceDocumentGUID")) for r in raw) if g)
    except Exception:  # без розшифровки оплачені заявки рахуються «у дорозі» (з запасом), а не губляться
        logger.exception("budget: in_fact_docs")
        docs = frozenset()
    with _fact_docs_lock:
        _fact_docs[month] = (time.time(), docs)
    return docs


def _pending(guid, item_code, month, exclude_id):
    """Заявки PayDesk у дорозі: (сума USD, кількість, заявки без курсу)."""
    import payments
    import rates
    import storage as db
    owner = _index()["owners"].get(item_code)
    start = date(int(month[:4]), int(month[5:7]), 1)
    cand = []
    for r in db.budget_rows(item_code, start):
        if exclude_id and r["id"] == exclude_id:
            continue
        if request_month(r) != month:
            continue
        if not owner and (departments.guid_of(r.get("department")) or "").upper() != guid:
            continue
        cand.append(r)
    paid = [r["id"] for r in cand if r["status"] == "paid"]
    if paid:
        try:
            links = payments.store().links_for_requests(paid)
        except Exception:
            logger.exception("budget: payment links")
            links = {}
        docs = in_fact_docs(month)
        cand = [r for r in cand if not (r["status"] == "paid"
                                        and {str(g).upper() for g in links.get(r["id"], ())} & docs)]
    total, no_rate = Decimal("0.00"), []
    for r in cand:
        usd, _ = rates.to_usd(r.get("amount"), r.get("currency"))
        if usd is None:
            no_rate.append(r["number"])
        else:
            total += usd
    return total, len(cand), no_rate


def remaining(dept_code, item_code, pay_date=None, exclude_id=None):
    """Залишок статті на місяць для заявки або None, якщо статті немає в бюджеті відділу.
    {"month", "month_title", "dept_name", "item", "item_name", "owner", "plan", "fact", "pending", "pending_count",
     "remaining", "used_pct"} — суми в USD (Decimal)."""
    if not item_code:
        return None
    guid = budget_department(dept_code, item_code)
    if not guid:
        return None
    month = target_month(pay_date)
    idx = _index()
    if not idx["year_plan"].get((guid, item_code, month[:4])):
        return None
    plan, fact = idx["cell"].get((guid, item_code, month), (0.0, 0.0))
    plan, fact = _dec(plan), _dec(fact)
    pending, count, no_rate = _pending(guid, item_code, month, exclude_id)
    rest = plan - fact - pending
    code = departments.code_by_guid(guid)
    return {"month": month, "month_title": month_title(month),
            "dept": code or "", "dept_name": (departments.get_map().get(code) if code else None)
            or idx["names"].get(guid) or guid,
            "item": item_code, "item_name": idx["names"].get(("item", item_code)) or item_code,
            "owner": bool(idx["owners"].get(item_code)),
            "plan": plan, "fact": fact, "pending": pending, "pending_count": count, "no_rate": no_rate,
            "remaining": rest,
            "used_pct": int(min(999, round((fact + pending) / plan * 100))) if plan > 0 else None}


def check(req):
    """Знімок бюджету для заявки (під час відправки й для форми): None — статті немає в бюджеті відділу;
    {"error": …} — бюджет не вдалося перевірити; інакше remaining() + сума заявки в USD і перевищення.
    Суми — рядками (знімок зберігається в заявці)."""
    import rates
    try:
        info = remaining(req.get("department"), req.get("expense_code"), req.get("pay_date"), req.get("id"))
    except Exception as e:
        logger.exception("budget: check")
        return {"error": f"{type(e).__name__}: {e}"}
    if info is None:
        return None
    usd, why = rates.to_usd(req.get("amount"), req.get("currency")) if req.get("amount") not in (None, "") \
        else (None, "не вказано суму")
    over = usd is not None and usd > info["remaining"]
    out = {k: (str(v) if isinstance(v, Decimal) else v) for k, v in info.items()}
    # Перевищення — на скільки заявка більша за залишок; залишку вже немає — уся заявка понад бюджет
    over_by = usd - max(info["remaining"], Decimal("0.00")) if over else None
    out.update(amount_usd=str(usd) if usd is not None else None, amount_note=why if usd is None else None,
               over=over, over_by=str(over_by) if over else None, at=clock.now().isoformat(timespec="seconds"))
    return out


def over_text(snap):
    """«понад залишок бюджету статті 04.001 на листопад 2026 на 45,30 USD (план 250,00, факт 120,00, у заявках 80,00)»."""
    def f(v):
        return money(v)
    return (f"понад залишок бюджету статті {snap['item']} на {MONTHS[int(snap['month'][5:7]) - 1].lower()} "
            f"{snap['month'][:4]} (відділ «{snap['dept_name']}») на {f(snap['over_by'])} USD: заявка ≈ "
            f"{f(snap['amount_usd'])} USD, залишок {f(snap['remaining'])} (план {f(snap['plan'])}, "
            f"факт 1С {f(snap['fact'])}, у заявках {f(snap['pending'])})")


def money(value):
    """1 234,50 / −186,98 (нерозривний пробіл тисяч)."""
    try:
        v = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return "—"
    s = f"{abs(v):,.2f}".replace(",", " ").replace(".", ",")
    return ("−" if v < 0 else "") + s
