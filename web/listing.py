"""Списки заявок: пошук, кнопки-фільтри з кількостями, «Показати ще», періоди, підсумки за валютами."""
from datetime import date, timedelta
from decimal import Decimal

from flask import g, request, url_for

import app_settings
import counterparties
import currencies
import departments
import organizations
import payment_accounts
import request_numbers
import storage as db
import user_prefs
import workflow as wf

from web.formatting import expense_label, money, url_without


def _facet_getters():
    """ключ параметра -> (назва, легкий рядок -> значення, значення -> підпис). Списки працюють з легкими
    рядками (storage/derive.py): фільтри, пошук, кількості й суми — без завантаження повних заявок;
    підписи з довідників обчислюються один раз на значення, а не на кожен рядок."""
    return {
        "status": ("Статус", lambda r: r["status"], lambda v: wf.STATUSES.get(v, (v,))[0]),
        "form": ("Форма оплати", lambda r: r["channel"], lambda v: wf.CHANNELS.get(v, "—")),
        "org": ("Організація", lambda r: r.get("organization_id"), organizations.name),
        "cp": ("Контрагент", lambda r: r.get("counterparty_id"), counterparties.name),
        "exp": ("Стаття витрат", lambda r: r.get("expense_code"), expense_label),
        "dep": ("Відділ", lambda r: r.get("department"), departments.get_name),
        "acc": ("Вид оплати", lambda r: r.get("payment_account_id") or "none",
                lambda v: payment_accounts.label(v) if v != "none" else "Не вибрано"),
        "payorg": ("Платимо з", lambda r: r["pay_org"] or "none",
                   lambda v: db.derive.pay_org_name(v) if v != "none" else "Не визначено"),
    }


def facet_selection(args, keys):
    """Вибрані значення: {"cp": ["1", "4"], ...} — кожен параметр може повторюватися (?cp=1&cp=4)."""
    return {k: [v.strip() for v in args.getlist(k) if v and v.strip()] for k in keys}


def _fval(value_of, r):
    value = value_of(r)
    return "" if value is None else str(value)


def apply_facets(rows, selected, skip=None):
    getters = _facet_getters()
    active = [(getters[k][1], set(v)) for k, v in selected.items() if v and k != skip]
    return [r for r in rows if all(_fval(g_, r) in vals for g_, vals in active)]


def search_rows(rows, q):
    """Пошук одним рядком: усі слова мають знайтися серед № заявки, автора, контрагента (і ЄДРПОУ), статті,
    відділу, організації, примітки, суми."""
    words = [w for w in (q or "").lower().split() if w]
    if not words:
        return rows
    # Слово з «#» чи префіксом (#123, PD123, pd-000123) — номер заявки: порівнюємо точно, а не підрядком
    exact = {w: request_numbers.parse_query(w) for w in words}
    out = []
    labels = {}  # підписи з довідників — один раз на значення

    def label(kind, value, fn):
        key = (kind, value)
        if key not in labels:
            labels[key] = fn(value)
        return labels[key]

    for r in rows:
        cp = label("cp", r.get("counterparty_id"), lambda v: counterparties.get(v) or {})
        amount = r.get("amount")
        hay = " ".join(str(x or "") for x in (
            r.get("number"), request_numbers.label(r.get("number")), r.get("author_name"), r.get("author_email"), cp.get("name"), cp.get("edrpou"),
            label("exp", r.get("expense_code"), expense_label), label("dep", r.get("department"), departments.get_name),
            label("org", r.get("organization_id"), lambda v: organizations.name(v) if v else ""), r.get("note"),
            amount, money(amount) if amount is not None else "", money(amount).replace(" ", "") if amount is not None else "",
        )).lower()
        if all(r.get("number") == exact[w] if exact[w] else w in hay for w in words):
            out.append(r)
    return out


def filter_bar(base_rows, rows, selected, keys, q):
    """Дані для _filter_bar.html: кнопки-фільтри з варіантами й кількістю, активні чипи, підсумок."""
    getters = _facet_getters()
    facets = []
    chips = []
    for key in keys:
        title, value_of, label_of = getters[key]
        counts = {}
        for r in apply_facets(base_rows, selected, skip=key):
            value = _fval(value_of, r)
            if value:
                counts[value] = counts.get(value, 0) + 1
        labels = {v: label_of(v) or v for v in counts}
        chosen = selected.get(key) or []
        for v in chosen:
            labels.setdefault(v, v)
            counts.setdefault(v, 0)
        if not labels:
            continue
        order = list(wf.STATUSES) if key == "status" else None
        opts = sorted(labels, key=lambda v: (order.index(v) if order and v in order else 0,
                                             v in ("none",), str(labels[v]).lower()))
        facets.append({"key": key, "title": title, "selected": chosen,
                       "options": [{"value": v, "label": labels[v], "count": counts[v], "checked": v in chosen}
                                   for v in opts]})
        chips += [{"label": f"{title}: {labels[v]}", "url": url_without(key, v)} for v in chosen]
    if q:
        chips.insert(0, {"label": f"Пошук: «{q}»", "url": url_without("q")})
    keep = {k: v for k, v in request.args.to_dict(flat=False).items() if k in ("view", "date_field")}
    sums = {}
    for r in rows:
        sums[r["currency"]] = sums.get(r["currency"], Decimal(0)) + r["amount"]
    return {"q": q, "facets": facets, "chips": chips, "count": len(rows),
            "sums": sorted(sums.items(), key=lambda kv: _currency_order(kv[0])),
            "active": sum(len(f["selected"]) for f in facets), "reset_url": url_for(request.endpoint, **keep)}


def _filtered(source, keys):
    """Пошук + фільтри для сторінки: (рядки, дані панелі)."""
    q = (request.args.get("q") or "").strip()
    base = search_rows(source, q)
    selected = facet_selection(request.args, keys)
    rows = apply_facets(base, selected)
    return rows, filter_bar(base, rows, selected, keys, q)


PAGE_SIZE = 100  # рядків таблиці за раз; далі — «Показати ще»


def _page(rows):
    """Рядки сторінки: перші limit легких рядків -> повні заявки + дані для «Показати ще» (_more.html)."""
    try:
        limit = max(PAGE_SIZE, int(request.args.get("limit") or PAGE_SIZE))
    except ValueError:
        limit = PAGE_SIZE
    shown = rows[:limit]
    full = db.get_many([r["id"] for r in shown])
    page_rows = [full[r["id"]] for r in shown if r["id"] in full]
    more = len(rows) - len(shown)
    args = request.args.to_dict(flat=False)
    args["limit"] = [str(limit + PAGE_SIZE)]
    return page_rows, {"shown": len(page_rows), "total": len(rows), "more": more, "step": PAGE_SIZE,
                       "more_url": url_for(request.endpoint, **args) if more else None}


def _ref_keys(show_dep=True, extra_before=(), extra_after=()):
    keys = list(extra_before)
    if app_settings.get("org_field_mode") != "accountant":
        keys.append("org")
    keys += ["cp", "exp"]
    if show_dep:
        keys.append("dep")
    return keys + list(extra_after)


def _parse_date(raw):
    try:
        return date.fromisoformat(raw or "")
    except ValueError:
        return None


def date_presets(today):
    """Швидкі періоди для фільтра дати: код -> (назва, з, по)."""
    week_start = today - timedelta(days=today.weekday())
    next_week = week_start + timedelta(days=7)
    month_start = today.replace(day=1)
    next_month = (month_start + timedelta(days=32)).replace(day=1)
    return {
        "overdue": ("Прострочені", None, today - timedelta(days=1)),
        "today": ("Сьогодні", today, today),
        "week": ("Цей тиждень", week_start, week_start + timedelta(days=6)),
        "next_week": ("Наступний тиждень", next_week, next_week + timedelta(days=6)),
        "month": ("Цей місяць", month_start, next_month - timedelta(days=1)),
    }


def _currency_order(cur):
    return currencies.order_key(cur)


def currency_totals(rows, today):
    """Підсумки за валютами: сума, кількість, прострочено, розбивка за формою оплати."""
    totals = {}
    for r in rows:
        t = totals.setdefault(r["currency"], {"amount": Decimal(0), "count": 0, "overdue": Decimal(0),
                                              "overdue_count": 0, "by_form": {}})
        t["amount"] += r["amount"]
        t["count"] += 1
        ch = r["channel"]
        t["by_form"][ch] = t["by_form"].get(ch, Decimal(0)) + r["amount"]
        if r["status"] == "to_pay" and r["pay_date"] < today:
            t["overdue"] += r["amount"]
            t["overdue_count"] += 1
    return dict(sorted(totals.items(), key=lambda kv: _currency_order(kv[0])))


def payment_plan(rows):
    """План платежів: [(дата, {валюта: сума}, кількість)] за зростанням дати + разом."""
    by_date = {}
    for r in rows:
        day = by_date.setdefault(r["pay_date"], {"sums": {}, "count": 0})
        day["sums"][r["currency"]] = day["sums"].get(r["currency"], Decimal(0)) + r["amount"]
        day["count"] += 1
    currencies = sorted({r["currency"] for r in rows}, key=_currency_order)
    total = {c: sum((d["sums"].get(c, Decimal(0)) for d in by_date.values()), Decimal(0)) for c in currencies}
    return {
        "currencies": currencies,
        "days": [(d, v["sums"], v["count"]) for d, v in sorted(by_date.items())],
        "total": total,
        "count": len(rows),
    }


RECENT_DAYS = 365  # архівні списки за замовчуванням — за останні 12 місяців


def recent_presets(today):
    """Пресети архівних списків («Усі заявки», «Оплачені»…): «12 місяців» (за замовчуванням) + звичайні."""
    return {"recent": ("12 місяців", today - timedelta(days=RECENT_DAYS), None), **date_presets(today)}


def _date_filter(args, today, recent=False, remember=None):
    """Період з параметрів ?period= або ?from=&to=. Повертає (код пресету, з, по).
    recent — архівний список: без параметрів — останні 12 місяців, ?period=all — за весь час.
    remember — ключ у user_prefs: вибраний швидкий період запам'ятовується за користувачем, і без параметрів список
    відкривається з ним (після нового входу, з іншого пристрою). Він відносний — «Сьогодні» завтра означає завтра;
    дати, введені вручну, — лише для цього перегляду."""
    presets = recent_presets(today) if recent else date_presets(today)
    if recent:
        presets = {"all": ("За весь час", None, None), **presets}
    period = args.get("period", "")
    d_from, d_to = _parse_date(args.get("from")), _parse_date(args.get("to"))
    if period in presets:
        _, p_from, p_to = presets[period]
        # форма шле код пресету (приховане поле) і поля дат: змінили дати — це вже свій період
        if (d_from if "from" in args else p_from) == p_from and (d_to if "to" in args else p_to) == p_to:
            if remember:
                user_prefs.set_view(g.user["email"], remember, period)
            return period, p_from, p_to
    if d_from or d_to:
        return "", d_from, d_to
    saved = user_prefs.view(g.user["email"], remember) if remember else ""
    if saved in presets:
        return saved, *presets[saved][1:]
    return ("recent", *presets["recent"][1:]) if recent else ("", None, None)


def _in_period(value, d_from, d_to):
    return (not d_from or value >= d_from) and (not d_to or value <= d_to)
