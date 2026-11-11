"""Вкладка «Бюджет» (план і факт бюджету відділів з 1С) і залишок бюджету статті для форми заявки.

Хто бачить вкладку — розділ «budget» (sections.py), чий бюджет — budget_access. Дані — budget.py.
"""
from flask import Blueprint, abort, g, jsonify, render_template, request

import app_settings
import budget
import budget_access
import clock
import storage as db
import user_prefs
from web.auth import login_required, requires, requires_section

bp = Blueprint("budget", __name__)

PREFS_KEY = "budget"  # запам'ятований вигляд: «період|вигляд|відділ»


def _int(value, default, lo, hi):
    try:
        v = int(value)
    except (TypeError, ValueError):
        return default
    return v if lo <= v <= hi else default


def _allowed():
    return budget_access.departments_for(g.user["oid"], g.roles)


def _choices(args, options, allowed):
    """Рік, відділ, період і вигляд із запиту; без них — запам'ятовані (період, вигляд, відділ) або типові."""
    today = clock.today()
    years = budget.years()
    year = _int(args.get("y"), today.year if today.year in years else years[-1], 2000, 2100)
    saved = (user_prefs.view(g.user["email"], PREFS_KEY) or "").split("|")
    saved += [""] * (3 - len(saved))
    kind = args.get("p") or saved[0]
    kind = kind if kind in budget.PERIODS else "month"
    view = args.get("v") or saved[1]
    view = view if view in budget.VIEWS else "table"
    keys = [k for k, _ in options]
    can_all = len(keys) > 1
    dep = args.get("dep")
    if dep is not None and dep != "*" and dep not in keys:
        abort(403)  # чужий відділ (або відділу немає в бюджеті)
    if dep is None:
        dep = saved[2] if (saved[2] in keys or (saved[2] == "*" and can_all)) else ("*" if can_all or not keys else keys[0])
    if dep == "*" and not can_all and keys:
        dep = keys[0]
    default_month = today.month if year == today.year else 12
    month = _int(args.get("m"), default_month, 1, 12)
    quarter = _int(args.get("q"), (month - 1) // 3 + 1, 1, 4)
    if any(args.get(k) for k in ("p", "v", "dep")):
        user_prefs.set_view(g.user["email"], PREFS_KEY, f"{kind}|{view}|{dep}")
    return year, dep, kind, month, quarter, view, years


@bp.route("/budget")
@login_required
@requires_section("budget")
def index(*, context):
    """План і факт бюджету відділів за період: таблиця за групами статей або по місяцях року."""
    allowed = _allowed()
    options = budget.department_options(allowed)
    year, dep, kind, month, quarter, view, years = _choices(request.args, options, allowed)
    months = budget.period_months(kind, year, month, quarter)
    # Типово під відділом одразу статті; за групами статей — лише відділи з налаштування (напр. маркетинг)
    grouped = frozenset(app_settings.get("budget_group_departments") or [])
    rep = budget.report(allowed, dep, year, months, view, grouped) if options else None
    today = clock.today()
    return render_template(
        "budget.html", user_name=g.user["name"], rep=rep, options=options, dep=dep, year=year, years=years,
        kind=kind, month=month, quarter=quarter, view=view, months=months, PERIODS=budget.PERIODS,
        VIEWS=budget.VIEWS, MONTHS=budget.MONTHS, MONTHS_SHORT=budget.MONTHS_SHORT, status=budget.status(),
        cur_month=today.month if year == today.year else (13 if year < today.year else 0),
        period_label=_period_label(kind, year, month, quarter, months), own_only=allowed is not None, bm=budget.money)


def _period_label(kind, year, month, quarter, months):
    if kind == "month":
        return f"{budget.MONTHS[month - 1]} {year}"
    if kind == "quarter":
        return f"{quarter} квартал {year}"
    if kind == "ytd":
        return (f"Січень – {budget.MONTHS[months[-1] - 1].lower()} {year}" if months else f"{year}: рік ще не почався")
    return f"{year} рік"


@bp.route("/budget/details")
@login_required
@requires_section("budget")
def details(*, context):
    """Розшифровка факту: документи 1С за відділом, статтею й місяцями (JSON для вікна на вкладці)."""
    a = request.args
    dep = a.get("dep", "")
    if not budget.allowed_key(dep, _allowed()):
        abort(403)
    year = _int(a.get("y"), clock.today().year, 2000, 2100)
    months = sorted({m for m in (_int(x, 0, 1, 12) for x in (a.get("m") or "").split(",")) if m})
    if not months:
        abort(400)
    data = budget.details(dep, a.get("item", ""), year, months)
    total = round(sum(r["amount"] for r in data["rows"]), 2)
    return jsonify(rows=data["rows"], total=total, error=data["error"])


@bp.route("/budget/remaining")
@login_required
@requires()
def remaining(*, context):
    """Залишок бюджету статті для форми заявки (бачать усі, хто створює заявки) і перевищення сумою заявки."""
    if not budget.enabled() or not app_settings.get("budget_hint"):
        return jsonify(enabled=False)
    a = request.args
    req = {"department": a.get("department") or None, "expense_code": (a.get("item") or "").strip() or None,
           "pay_date": a.get("pay_date") or None, "amount": (a.get("amount") or "").replace(" ", "").replace(",", ".")
           or None, "currency": a.get("currency") or "UAH",
           "id": _int(a.get("id"), None, 1, 10**12)}
    if req["id"]:
        own = db.get_request(req["id"])
        if not own:
            req["id"] = None
    snap = budget.check(req)
    mode = app_settings.get("budget_over_mode")
    if snap is None:
        return jsonify(enabled=True, in_budget=False)
    if snap.get("error"):
        return jsonify(enabled=True, in_budget=False, error="Не вдалося перевірити бюджет")
    fmt = {k: budget.money(snap[k]) for k in ("plan", "fact", "pending", "remaining")}
    if snap.get("amount_usd"):
        fmt["amount_usd"] = budget.money(snap["amount_usd"])
    if snap.get("over_by"):
        fmt["over_by"] = budget.money(snap["over_by"])
    return jsonify(enabled=True, in_budget=True, over_mode=mode, over_mode_label=budget.OVER_MODES.get(mode, ""),
                   **{k: snap[k] for k in ("month", "month_title", "dept_name", "item", "item_name", "owner",
                                           "pending_count", "used_pct", "over", "remaining")},
                   fmt=fmt, negative=float(snap["remaining"]) < 0)
