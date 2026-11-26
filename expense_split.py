"""Розподіл заявки між відділами (гілка «Непряма витрата»).

Стаття витрат у 1С має варіанти з різною базою розподілу (групи статей — item_groups.py): з розподілом («Меблі»)
1С розносить на відділ-власник статті, пряма («Меблі (прямий)») лягає на відділ рядка й можлива лише для
профітцентру, позапланова («Не розподіляти») іде на «Інше». У 1С варіанти не пов'язані, тож групи складаються за
назвою автоматично, а адміністратор їх править («Налаштування → Заявка й погодження → Групи статей і розподіл»).

Пряма стаття — пряма в групі або з «(прямий)» у назві. Вона ніколи не віднесеться на відділ, що не є профітцентром:
без варіанта з розподілом непрофітцентр розподіляє її лише на профітцентри.

Коли ініціатор непрямої витрати вибирає статтю з групи (або пряму без групи):
  • пряма стаття, відділ заявки — не профітцентр: суму обов'язково розподіляють на рядки «відділ + стаття + сума»;
  • інакше розподіл за бажанням (кнопка «Розділити між відділами»), без нього — як раніше.
У рядку — лише варіант групи: пряма — тільки на профітцентр; з розподілом — на її відділ-власника (Owner статті в 1С;
власник невідомий — будь-який відділ); позапланова — на «Інше» (OTHER). Сума рядків = сума заявки.
Рядки — req["split"]: [{"department", "expense_code", "amount"}]; головна стаття заявки лишається як вибрана.

spend_rows() — на що витрачає заявка (рядки розподілу або сама заявка): за ним рахують бюджет, правила фіндиректора
й дані для 1С (onec_api: allocation; «Інше» — без відділу).
"""
from decimal import Decimal, InvalidOperation

import departments
import expense_items
import item_groups

# Позначка, що форма надіслала блок розподілу (інакше — збережені рядки лишаються як є)
FORM_SENT = "split_sent"


OTHER = "-"  # відділ рядка з позаплановою статтею: «Інше» (як budget.OTHER_KEY)
OTHER_NAME = "Інше (позапланова)"


def pairs():
    """{пряма стаття: стаття з розподілом} — з діючих груп статей (item_groups)."""
    return {g["direct"]: g["allocated"] for g in item_groups.groups() if g["direct"] and g["allocated"]}


DIRECT_MARK = item_groups.DIRECT_MARK


def is_direct(code):
    """Пряма стаття (лише на профітцентр): пряма в групі статей або «(прямий)» у назві."""
    if not code:
        return False
    return item_groups.role(code) == "direct" or DIRECT_MARK in (expense_items.get_name(code) or "").lower()


def variants(code):
    """Варіанти статті для рядків розподілу: {"direct", "allocated", "unplanned"} (коди або None).
    Стаття з групи — варіанти групи; пряма без групи — лише вона; інша — усі None."""
    g = item_groups.group_of(code)
    if g:
        return {r: g[r] or None for r in item_groups.ROLES}
    return {"direct": code if is_direct(code) else None, "allocated": None, "unplanned": None}


def partners(code):
    """(прямі статті, стаття з розподілом або None) для статті — сумісність зі старим кодом."""
    v = variants(code)
    return ([v["direct"]] if v["direct"] else []), v["allocated"]


def pair_items(code):
    """Статті рядків розподілу: пряма, з розподілом, позапланова (які є); пряма без групи — [пряма]; інакше — []."""
    v = variants(code)
    items = [v[r] for r in ("direct", "allocated", "unplanned") if v[r]]
    return items if v["direct"] or len(items) >= 2 else []


def owners(code):
    """Відділи-власники статті з розподілом (коди довідника): на них 1С розносить таку статтю; невідомо — []."""
    return expense_items.department_codes(code) if code else []


def profit_centers():
    return set(departments.profit_centers())


def is_indirect(req):
    """Гілка «Непряма витрата»: усе, крім оплати рахунків підрядника."""
    return req.get("payment_kind") != "contractor"


def applies(req):
    """Чи можна розподілити заявку: непряма витрата з прямою статтею або зі статтею з групи кількох варіантів."""
    return is_indirect(req) and bool(pair_items(req.get("expense_code")))


def required(req):
    """Чи обов'язковий розподіл: пряма стаття, а відділ заявки — не профітцентр."""
    return applies(req) and is_direct(req.get("expense_code")) and req.get("department") not in profit_centers()


def _amount(raw):
    raw = str(raw or "").replace(" ", "").replace(" ", "").replace(",", ".")
    if not raw:
        return None
    try:
        v = Decimal(raw).quantize(Decimal("0.01"))
    except InvalidOperation:
        return False
    return v


def parse(form):
    """Рядки розподілу з форми (split_department / split_item / split_amount) і помилка розбору (або None).
    Порожні рядки (без відділу й суми) пропускаються."""
    deps, items, amounts = form.getlist("split_department"), form.getlist("split_item"), form.getlist("split_amount")
    rows, error = [], None
    for i in range(max(len(deps), len(items), len(amounts))):
        dep = (deps[i] if i < len(deps) else "").strip()
        item = (items[i] if i < len(items) else "").strip()
        raw = amounts[i] if i < len(amounts) else ""
        amount = _amount(raw)
        if not dep and amount is None:
            continue
        if amount is False:
            error = error or f"Розподіл, рядок {len(rows) + 1}: сума «{raw}» — не число"
            amount = None
        rows.append({"department": dep, "expense_code": item, "amount": amount})
    return rows, error


def check_rows(req):
    """Помилка в рядках розподілу (відділ, стаття пари, сума) або None — перевіряється й при збереженні."""
    rows = req.get("split") or []
    if not rows:
        return None
    v = variants(req.get("expense_code"))
    items, pcs, known = pair_items(req.get("expense_code")), profit_centers(), departments.get_map()
    direct, allocated, unplanned = v["direct"], v["allocated"], v["unplanned"]
    own = owners(allocated)
    seen = set()
    for n, r in enumerate(rows, 1):
        dep, item = r.get("department"), r.get("expense_code")
        if item and item == unplanned and dep in ("", None, OTHER):
            dep = r["department"] = OTHER  # позапланова — завжди на «Інше»
        if not dep or (dep not in known and dep != OTHER):
            return f"Розподіл, рядок {n}: виберіть відділ"
        if dep in seen:
            return f"Розподіл: відділ «{dept_name(dep)}» указано двічі — об'єднайте суми в один рядок"
        seen.add(dep)
        if item not in items:
            names = " / ".join(f"«{_item_name(c)}»" for c in items)
            return f"Розподіл, рядок {n}: стаття має бути {'з групи ' if len(items) > 1 else ''}{names}"
        if dep == OTHER and item != unplanned:
            return (f"Розподіл, рядок {n}: на «Інше» йде лише позапланова стаття"
                    + (f" «{_item_name(unplanned)}»" if unplanned else ""))
        if item == unplanned and dep != OTHER:
            return f"Розподіл, рядок {n}: позапланову статтю «{_item_name(unplanned)}» 1С кидає на «Інше» — виберіть «Інше»"
        if item == direct and dep not in pcs:
            if not allocated:
                return (f"Розподіл, рядок {n}: пряму статтю можна віднести лише на профітцентр, а «{departments.get_name(dep)}» "
                        "— не профітцентр. Для цієї статті немає варіанта з розподілом: віднесіть суму на профітцентр, "
                        "виберіть статтю без «(прямий)» або попросіть адміністратора додати групу в «Групи статей і "
                        "розподіл»")
            return (f"Розподіл, рядок {n}: пряму статтю можна віднести лише на профітцентр — для відділу "
                    f"«{departments.get_name(dep)}» виберіть «{_item_name(allocated)}»"
                    + (f" (вона йде на власника: {_dept_names(own)})" if own else ""))
        if item == allocated and own and dep not in own:
            return (f"Розподіл, рядок {n}: статтю «{_item_name(allocated)}» 1С розносить на відділ-власник "
                    f"{_dept_names(own)} — виберіть його"
                    + (f"; на «{departments.get_name(dep)}» — пряму статтю «{_item_name(direct)}»"
                       if dep in pcs and direct else ""))
        if not r.get("amount") or r["amount"] <= 0:
            return f"Розподіл, рядок {n}: вкажіть суму, більшу за 0"
    return None


def dept_name(code):
    """Назва відділу рядка: «Інше (позапланова)» для OTHER."""
    return OTHER_NAME if code == OTHER else (departments.get_name(code) or code)


def _item_name(code):
    return expense_items.get_name(code) or code


def _dept_names(codes):
    return " / ".join(f"«{departments.get_name(c) or c}»" for c in codes)


def check_complete(req):
    """Що бракує для відправки (або None): розподіл для непрофітцентру й сума рядків = сума заявки."""
    rows = req.get("split") or []
    if required(req) and not rows:
        name = departments.get_name(req.get("department")) or "відділ заявки"
        return (f"Пряму статтю можна віднести лише на профітцентр, а «{name}» — не профітцентр. Розподіліть суму "
                "між відділами: блок «Розподілити між відділами» під сумою заявки")
    if rows and req.get("amount") is not None:
        total = sum((r.get("amount") or Decimal(0) for r in rows), Decimal(0))
        if total != Decimal(str(req["amount"])).quantize(Decimal("0.01")):
            return (f"Розподіл: сума рядків {fmt(total)} не дорівнює сумі заявки "
                    f"{fmt(req['amount'])} {req.get('currency') or ''}".rstrip())
    return None


def spend_rows(req):
    """На що витрачає заявка: [{"department", "expense_code", "amount"}] — рядки розподілу або сама заявка."""
    rows = req.get("split") or []
    if rows:
        return [{"department": r.get("department"), "expense_code": r.get("expense_code"), "amount": r.get("amount")}
                for r in rows]
    return [{"department": req.get("department"), "expense_code": req.get("expense_code"), "amount": req.get("amount")}]


def form_config(req):
    """Дані для блоку розподілу у формі: варіанти кожної статті з групи (і прямих без групи), власники статей з
    розподілом, назви, профітцентри, відділи."""
    groups = {}
    for g in item_groups.groups():
        v = {r: g[r] or None for r in item_groups.ROLES}
        for r in item_groups.ROLES:
            if g[r]:
                groups[g[r]] = {**v, "name": g["name"]}
    for i in expense_items.get_items():  # прямі статті без групи — лише на профітцентри
        if i["code"] not in groups and DIRECT_MARK in (i.get("name") or "").lower():
            groups[i["code"]] = {"direct": i["code"], "allocated": None, "unplanned": None, "name": i["name"]}
    current = req.get("expense_code")  # стара стаття заявки, якої вже немає в довіднику, — теж
    if current and current not in groups and is_direct(current):
        groups[current] = {"direct": current, "allocated": None, "unplanned": None, "name": current}
    codes = {c for v in groups.values() for r in item_groups.ROLES if (c := v[r])}
    allocs = {v["allocated"] for v in groups.values() if v["allocated"]}
    return {"groups": groups, "owners": {a: owners(a) for a in allocs},
            "names": {c: expense_items.get_name(c) or c for c in codes},
            "pc": sorted(profit_centers()), "other": OTHER, "other_name": OTHER_NAME,
            "departments": [{"code": d["code"], "name": d["name"]} for d in departments.get_departments()]}


def fmt(value):
    try:
        return f"{Decimal(str(value)):,.2f}".replace(",", " ").replace(".", ",")
    except (InvalidOperation, TypeError, ValueError):
        return str(value)


def describe(rows):
    """«Море-авіа — 600,00 (Меблі (прямий)); Відділ продажу — 400,00 (Меблі)» — для історії й журналу."""
    return "; ".join(f"{dept_name(r.get('department'))} — {fmt(r.get('amount'))} "
                     f"({expense_items.get_name(r.get('expense_code')) or r.get('expense_code')})" for r in rows)
