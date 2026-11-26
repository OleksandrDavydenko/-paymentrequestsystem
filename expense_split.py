"""Розподіл прямої витрати між відділами в заявці (гілка «Непряма витрата»).

Стаття витрат може мати кілька варіантів з різним методом розподілу: пряма («Меблі (прямий)») лягає на відділ рядка
й можлива лише для профітцентру, а її варіант «з розподілом» («Меблі») іде в загальний розподіл. У 1С варіанти не
пов'язані, тож пари «пряма → з розподілом» задає адміністратор («Налаштування → Заявка й погодження → Розподіл
прямих витрат», split_pairs). Позапланові статті (одразу на «Інше») в пари не додають — вони без розподілу.

Коли ініціатор непрямої витрати вибирає пряму статтю з пари:
  • відділ заявки — не профітцентр: суму обов'язково розподіляють на рядки «відділ + стаття + сума»;
  • профітцентр: розподіл за бажанням (кнопка «Розділити між відділами»), без нього — витрата на свій відділ.
У рядку — лише стаття пари: пряма — тільки на профітцентр, з розподілом — на будь-який відділ. Сума рядків = сума
заявки. Рядки — req["split"]: [{"department", "expense_code", "amount"}]; головна стаття заявки лишається пряма.

Статті без пари працюють як і раніше. spend_rows() — на що витрачає заявка (рядки розподілу або сама заявка): за ним
рахують бюджет, правила фіндиректора й дані для 1С (onec_api: allocation).
"""
from decimal import Decimal, InvalidOperation

import app_settings
import departments
import expense_items

# Позначка, що форма надіслала блок розподілу (інакше — збережені рядки лишаються як є)
FORM_SENT = "split_sent"


def pairs():
    """{пряма стаття: стаття з розподілом} з налаштувань."""
    out = {}
    for p in app_settings.get("split_pairs") or []:
        d, a = str(p.get("direct") or "").strip(), str(p.get("allocated") or "").strip()
        if d and a and d != a:
            out[d] = a
    return out


def pair_items(code):
    """[пряма, з розподілом] для прямої статті з пари, інакше []."""
    a = pairs().get(code or "")
    return [code, a] if a else []


def profit_centers():
    return set(departments.profit_centers())


def is_indirect(req):
    """Гілка «Непряма витрата»: усе, крім оплати рахунків підрядника."""
    return req.get("payment_kind") != "contractor"


def applies(req):
    """Чи можна розподілити заявку: непряма витрата з прямою статтею з пари."""
    return is_indirect(req) and bool(pair_items(req.get("expense_code")))


def required(req):
    """Чи обов'язковий розподіл: пряма стаття з пари, а відділ заявки — не профітцентр."""
    return applies(req) and req.get("department") not in profit_centers()


def default_item(code, department):
    """Стаття рядка за замовчуванням: профітцентру — пряма, іншому відділу — з розподілом."""
    items = pair_items(code)
    if not items:
        return code
    return items[0] if department in profit_centers() else items[1]


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
    items, pcs, known = pair_items(req.get("expense_code")), profit_centers(), departments.get_map()
    seen = set()
    for n, r in enumerate(rows, 1):
        dep, item = r.get("department"), r.get("expense_code")
        if not dep or dep not in known:
            return f"Розподіл, рядок {n}: виберіть відділ"
        if dep in seen:
            return f"Розподіл: відділ «{departments.get_name(dep)}» указано двічі — об'єднайте суми в один рядок"
        seen.add(dep)
        if item not in items:
            return (f"Розподіл, рядок {n}: стаття має бути з пари «{expense_items.get_name(items[0]) or items[0]}» / "
                    f"«{expense_items.get_name(items[1]) or items[1]}»")
        if item == items[0] and dep not in pcs:
            return (f"Розподіл, рядок {n}: пряму статтю можна віднести лише на профітцентр — для відділу "
                    f"«{departments.get_name(dep)}» виберіть «{expense_items.get_name(items[1]) or items[1]}»")
        if not r.get("amount") or r["amount"] <= 0:
            return f"Розподіл, рядок {n}: вкажіть суму, більшу за 0"
    return None


def check_complete(req):
    """Що бракує для відправки (або None): розподіл для непрофітцентру й сума рядків = сума заявки."""
    rows = req.get("split") or []
    if required(req) and not rows:
        name = departments.get_name(req.get("department")) or "відділ заявки"
        return (f"Пряму статтю можна віднести лише на профітцентр, а «{name}» — не профітцентр. Розподіліть суму "
                "між відділами: блок «Розподілити між відділами» під статтею витрат")
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
    """Дані для блоку розподілу у формі: пари з назвами, профітцентри, відділи."""
    p = pairs()
    codes = set(p) | set(p.values())
    return {"pairs": p, "names": {c: expense_items.get_name(c) or c for c in codes},
            "pc": sorted(profit_centers()),
            "departments": [{"code": d["code"], "name": d["name"]} for d in departments.get_departments()]}


def fmt(value):
    try:
        return f"{Decimal(str(value)):,.2f}".replace(",", " ").replace(".", ",")
    except (InvalidOperation, TypeError, ValueError):
        return str(value)


def describe(rows):
    """«Море-авіа — 600,00 (Меблі (прямий)); Відділ продажу — 400,00 (Меблі)» — для історії й журналу."""
    return "; ".join(f"{departments.get_name(r.get('department'))} — {fmt(r.get('amount'))} "
                     f"({expense_items.get_name(r.get('expense_code')) or r.get('expense_code')})" for r in rows)
