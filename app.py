import os
import re
import uuid
from functools import wraps
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from dotenv import load_dotenv
from flask import Flask, abort, flash, g, redirect, render_template, request, send_file, session, url_for
from werkzeug.middleware.proxy_fix import ProxyFix
import identity.flask

import expense_items
import graph
import mock_data as db
import workflow as wf

load_dotenv()

app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.environ["FLASK_SECRET_KEY"],
    SESSION_TYPE="filesystem",  # сесії зберігаються на сервері, не в cookie
    SESSION_PERMANENT=False,
    MAX_CONTENT_LENGTH=25 * 1024 * 1024,
)
# App Service приймає HTTPS на проксі, а до Flask передає HTTP.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

# Single-tenant authority: увійти можуть лише користувачі нашого тенанту Entra ID.
auth = identity.flask.Auth(
    app,
    authority=f"https://login.microsoftonline.com/{os.environ['TENANT_ID']}",
    client_id=os.environ["CLIENT_ID"],
    client_credential=os.environ["CLIENT_SECRET"],
    redirect_uri=os.environ["REDIRECT_URI"],  # напр. http://localhost:5000/getAToken
)


# ---------------------------------------------------------------- фільтри шаблонів

@app.template_filter("money")
def money(value):
    """12345.6 -> '12 345,60'"""
    return f"{Decimal(value):,.2f}".replace(",", " ").replace(".", ",")


@app.template_filter("d")
def fmt_date(value):
    return value.strftime("%d.%m.%Y") if value else ""


@app.template_filter("dt")
def fmt_datetime(value):
    return value.strftime("%d.%m.%Y %H:%M") if value else ""


@app.context_processor
def inject_refs():
    return {
        "ORGANIZATIONS": db.ORGANIZATIONS,
        "COUNTERPARTIES": db.COUNTERPARTIES,
        "expense_name": expense_label,
        # Фільтр «Стаття витрат»: лише статті, що трапляються в заявках
        "exp_options": lambda: sorted({r["expense_code"] for r in db.list_all() if r.get("expense_code")},
                                      key=lambda c: expense_label(c).lower()),
        "CURRENCIES": db.CURRENCIES,
        "PAYMENT_FORMS": db.PAYMENT_FORMS,
        "CHANNELS": wf.CHANNELS,
        "channel_of": wf.channel_of,
        "STATUSES": db.STATUSES,
    }


# ---------------------------------------------------------------- розбір форми

def _parse_amount(raw):
    raw = (raw or "").replace(" ", "").replace(" ", "").replace(",", ".")
    try:
        value = Decimal(raw)
    except InvalidOperation:
        return None
    return value.quantize(Decimal("0.01")) if value.is_finite() else None


def _parse_ref(raw, ref):
    try:
        key = int(raw)
    except (TypeError, ValueError):
        return None
    return key if key in ref else None


def expense_label(code):
    """«Назва (код)» для статті витрат; невідомий код показуємо як є."""
    if not code:
        return ""
    name = expense_items.get_name(code)
    return f"{name} ({code})" if name else code


def parse_request_form(form, current=None):
    """Повертає (дані заявки, словник помилок). Автор/дата/номер не з форми."""
    errors = {}
    data = {
        "counterparty_id": _parse_ref(form.get("counterparty_id"), db.COUNTERPARTIES),
        "organization_id": _parse_ref(form.get("organization_id"), db.ORGANIZATIONS),
        "expense_code": (form.get("expense_code") or "").strip() or None,
        "payment_form": form.get("payment_form") if form.get("payment_form") in db.PAYMENT_FORMS else None,
        "currency": form.get("currency") if form.get("currency") in db.CURRENCIES else None,
        "note": (form.get("note") or "").strip(),
        "amount": _parse_amount(form.get("amount")),
        "pay_date": None,
        "lines": [],
    }
    try:
        data["pay_date"] = date.fromisoformat(form.get("pay_date") or "")
    except ValueError:
        errors["pay_date"] = "Вкажіть дату оплати"

    required = {
        "counterparty_id": "Оберіть контрагента",
        "organization_id": "Оберіть організацію",
        "expense_code": "Оберіть статтю витрат зі списку",
        "payment_form": "Оберіть форму оплати",
        "currency": "Оберіть валюту",
    }
    for field, message in required.items():
        if data[field] is None:
            errors[field] = message
    if data["expense_code"] and data["expense_code"] != (current or {}).get("expense_code") \
            and expense_items.get_name(data["expense_code"]) is None:
        errors["expense_code"] = "Такої статті немає в довіднику — оберіть зі списку"
    if data["amount"] is None or data["amount"] <= 0:
        errors["amount"] = "Сума має бути більшою за 0"
        data["amount_raw"] = form.get("amount", "")

    # Рядки рахунків: lines-<N>-invoice / -amount / -currency / -deal
    indexes = sorted({int(m.group(1)) for k in form if (m := re.fullmatch(r"lines-(\d+)-invoice", k))})
    for i in indexes:
        line = {
            "invoice": (form.get(f"lines-{i}-invoice") or "").strip(),
            "amount_raw": form.get(f"lines-{i}-amount", ""),
            "amount": _parse_amount(form.get(f"lines-{i}-amount")),
            "currency": form.get(f"lines-{i}-currency") if form.get(f"lines-{i}-currency") in db.CURRENCIES else data["currency"],
            "deal": (form.get(f"lines-{i}-deal") or "").strip(),
        }
        if not line["invoice"] and line["amount"] is None and not line["deal"]:
            continue  # порожній рядок — пропускаємо
        if not line["invoice"]:
            errors["lines"] = "У кожному рядку вкажіть номер рахунку"
        if line["amount"] is None or line["amount"] <= 0:
            errors["lines"] = "Сума в рядку рахунку має бути більшою за 0"
        data["lines"].append(line)
    return data, errors


# ---------------------------------------------------------------- користувач і ролі

# Ролі = членство в групах безпеки Entra ID (ID груп приходять у токені, claim "groups").
GROUPS = {
    "admin": os.environ.get("GROUP_ADMIN", ""),
    "initiator": os.environ.get("GROUP_INITIATOR", ""),
    "acc_cash": os.environ.get("GROUP_ACC_CASH", ""),
    # GROUP_ACCOUNTANT — стара назва змінної (одна група бухгалтерів), тепер це бухгалтери-резиденти
    "acc_resident": os.environ.get("GROUP_ACC_RESIDENT") or os.environ.get("GROUP_ACCOUNTANT", ""),
    "acc_nonresident": os.environ.get("GROUP_ACC_NONRESIDENT", ""),
    "cfo": os.environ.get("GROUP_CFO", ""),
}
# Страховка від блокування: ці люди завжди адміністратори
ADMIN_EMAILS = {e.strip().lower() for e in os.environ.get("ADMIN_EMAILS", "od@ftpua.com").split(",") if e.strip()}
OVERAGE_SCOPES = ["https://graph.microsoft.com/GroupMember.Read.All"]


def _token_groups(claims):
    """ID груп користувача з токена; якщо груп забагато (overage) — перевірка через Graph."""
    if "groups" in claims:
        return set(claims["groups"])
    overage = "groups" in (claims.get("_claim_names") or {}) or claims.get("hasgroups")
    if not overage:
        return set()
    cache_key = f"overage_groups:{claims.get('oid')}"
    if cache_key not in session:
        wanted = [gid for gid in GROUPS.values() if gid]
        result = auth._auth.get_token_for_user(OVERAGE_SCOPES)
        try:
            found = graph.check_member_groups(result["access_token"], wanted) if "access_token" in result else set()
        except graph.GraphError:
            app.logger.exception("Overage group check failed")
            found = set()
        session[cache_key] = sorted(found)
    return set(session[cache_key])


def resolve_roles(claims):
    groups = _token_groups(claims)
    roles = {role for role, gid in GROUPS.items() if gid and gid in groups}
    if claims.get("preferred_username", "").lower() in ADMIN_EMAILS:
        roles.add("admin")
    return roles


def requires(*needed):
    """Після @auth.login_required: заповнює g.user / g.roles і перевіряє доступ.
    Без жодної ролі (або без потрібної) — сторінка «Немає доступу»."""
    def deco(view):
        @wraps(view)
        def wrapper(*args, context, **kwargs):
            claims = context["user"]
            email = claims.get("preferred_username", "")
            g.user = {"email": email, "name": claims.get("name") or email, "oid": claims.get("oid")}
            g.roles = resolve_roles(claims)
            if not g.roles or (needed and not g.roles & set(needed)):
                return render_template("no_access.html", user_name=g.user["name"], missing=needed), 403
            return view(*args, context=context, **kwargs)
        return wrapper
    return deco


@app.context_processor
def inject_roles():
    roles = getattr(g, "roles", set())
    return {
        "roles": roles,
        "ROLES": wf.ROLES,
        "ROLE_SHORT": wf.ROLE_SHORT,
        "is_approver": bool(roles & wf.APPROVER_ROLES),
        "queue_count": len(db.list_queue(roles)) if roles & wf.APPROVER_ROLES else 0,
        "can_see_payments": wf.can_see_payments(roles),
        "payments_count": len(db.list_to_pay(roles)) if wf.can_see_payments(roles) else 0,
    }


def _home():
    if "initiator" not in g.roles and g.roles & wf.APPROVER_ROLES:
        return url_for("approvals")
    if g.roles == {"admin"}:
        return url_for("admin_requests")
    return url_for("requests_list")


def _load_visible(request_id):
    req = db.get_request(request_id)
    if not req or not wf.can_view(req, g.roles, g.user["email"]):
        abort(404)
    return req


# ---------------------------------------------------------------- фільтри списків

REF_FILTERS = {  # параметр запиту -> (поле заявки, довідник)
    "org": ("organization_id", db.ORGANIZATIONS),
    "cp": ("counterparty_id", db.COUNTERPARTIES),
    "exp": ("expense_code", None),  # код статті витрат (рядок)
}


def ref_filters(args):
    """Вибрані значення фільтрів довідників: {"org": 1, "cp": None, "exp": "01.001"}."""
    selected = {key: _parse_ref(args.get(key), ref) for key, (_, ref) in REF_FILTERS.items() if ref is not None}
    selected["exp"] = (args.get("exp") or "").strip() or None
    return selected


def apply_ref_filters(rows, selected):
    return [r for r in rows
            if all(not value or r[REF_FILTERS[key][0]] == value for key, value in selected.items())]


# ---------------------------------------------------------------- маршрути

@app.route("/")
@auth.login_required
@requires()
def index(*, context):
    return redirect(_home())


@app.route("/requests")
@auth.login_required
@requires("initiator")
def requests_list(*, context):
    status = request.args.get("status") if request.args.get("status") in db.STATUSES else ""
    refs = ref_filters(request.args)
    return render_template(
        "requests_list.html",
        user_name=g.user["name"],
        requests=apply_ref_filters(db.list_requests(g.user["email"], status or None), refs),
        status_filter=status,
        refs=refs,
    )


@app.route("/approvals")
@auth.login_required
@requires(*wf.APPROVER_ROLES)
def approvals(*, context):
    refs = ref_filters(request.args)
    view = "processed" if request.args.get("view") == "processed" else "queue"
    source = db.list_processed(g.roles) if view == "processed" else db.list_queue(g.roles)
    return render_template(
        "approvals.html",
        user_name=g.user["name"],
        requests=apply_ref_filters(source, refs),
        refs=refs,
        view=view,
        processed_entry=lambda r: wf.processed_entry(r, g.roles),
        ACTIONS=wf.ACTIONS,
    )


@app.route("/requests/new", methods=["GET", "POST"])
@app.route("/requests/<int:request_id>", methods=["GET", "POST"])
@auth.login_required
@requires("initiator", "admin", *wf.APPROVER_ROLES)
def request_card(request_id=None, *, context):
    user, roles = g.user, g.roles
    if request_id is None:
        if "initiator" not in roles:
            flash("Створювати заявки може лише роль «Ініціатор»", "error")
            return redirect(_home())
        existing = {
            "id": None,
            "number": db.next_number(),
            "created_at": datetime.now(),
            "author_email": user["email"],
            "author_name": user["name"],
            "status": "draft",
            "payment_form": "bank",
            "currency": "UAH",
            "lines": [],
            "history": [],
            "attachments": [],
        }
    else:
        existing = _load_visible(request_id)

    errors = {}
    req = existing
    if request.method == "POST":
        if not wf.can_edit(existing, roles, user["email"]):
            flash("Заявку в цьому статусі редагувати не можна", "error")
            return redirect(url_for("request_card", request_id=request_id))
        data, errors = parse_request_form(request.form, existing)
        req = {**existing, **data}
        if not errors:
            for line in req["lines"]:
                line.pop("amount_raw", None)
            action = request.form.get("action")
            if req["id"] is None:
                req["created_at"] = datetime.now()
                wf.add_history(req, "create", user, "initiator", to_status="draft")
            if action == "submit":
                wf.apply_action(req, "submit", roles, user)
            new_id = db.save_request(req)
            number = db.get_request(new_id)["number"]
            if action == "submit":
                flash(f"Заявку № {number} відправлено на погодження", "success")
                return redirect(url_for("requests_list"))
            flash(f"Заявку № {number} записано", "success")
            if action == "ok":
                return redirect(url_for("requests_list"))
            return redirect(url_for("request_card", request_id=new_id))

    available = wf.available_actions(req, roles, user["email"])
    return render_template(
        "request_form.html",
        user_name=user["name"],
        req=req,
        errors=errors,
        invoices=db.INVOICES,
        expense_items=expense_items.get_items(),
        nonresident_orgs=sorted(wf.NONRESIDENT_ORGANIZATIONS),
        channel_roles={c: wf.ROLES[r] for c, r in wf.ACCOUNTANT_BY_CHANNEL.items()},
        editable=wf.can_edit(req, roles, user["email"]),
        can_attach=req["id"] is not None and wf.can_attach(req, roles, user["email"]),
        actions=[a for a in available if a != "submit"],
        acting_role=wf.acting_role(req, roles, user["email"]),
        can_submit="submit" in available,
        stages=wf.stage_states(req),
        ACTIONS=wf.ACTIONS,
        CLOSED_STATUSES=wf.CLOSED_STATUSES,
        draft_comment=session.pop("draft_comment", ""),
        admin_comment=session.pop("admin_comment", ""),
        current_email=user["email"],
        back_url=url_for("requests_list") if wf.is_author(req, user["email"]) and "initiator" in roles
        else _home(),
    )


@app.route("/requests/<int:request_id>/action", methods=["POST"])
@auth.login_required
@requires("initiator", *wf.APPROVER_ROLES)
def request_action(request_id, *, context):
    req = _load_visible(request_id)
    action = request.form.get("action")
    try:
        wf.apply_action(req, action, g.roles, g.user, request.form.get("comment"))
    except wf.WorkflowError as e:
        flash(str(e), "error")
        session["draft_comment"] = request.form.get("comment", "")
        return redirect(url_for("request_card", request_id=request_id))
    db.save_request(req)
    flash(f"Заявка № {req['number']}: {wf.ACTIONS[action][1].lower()}", "success")
    return redirect(url_for("to_pay") if action == "pay" or request.form.get("next") == "to_pay"
                    else url_for("approvals"))


# ---------------------------------------------------------------- списки: дати, підсумки

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
    return db.CURRENCIES.index(cur) if cur in db.CURRENCIES else 99


def currency_totals(rows, today):
    """Підсумки за валютами: сума, кількість, прострочено, розбивка за формою оплати."""
    totals = {}
    for r in rows:
        t = totals.setdefault(r["currency"], {"amount": Decimal(0), "count": 0, "overdue": Decimal(0),
                                              "overdue_count": 0, "by_form": {}})
        t["amount"] += r["amount"]
        t["count"] += 1
        ch = wf.channel_of(r)
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


def _date_filter(args, today):
    """Період з параметрів ?period= або ?from=&to=. Повертає (код пресету, з, по)."""
    presets = date_presets(today)
    period = args.get("period", "")
    if period in presets:
        _, d_from, d_to = presets[period]
        return period, d_from, d_to
    return "", _parse_date(args.get("from")), _parse_date(args.get("to"))


def _in_period(value, d_from, d_to):
    return (not d_from or value >= d_from) and (not d_to or value <= d_to)


@app.route("/requests/<int:request_id>/admin-status", methods=["POST"])
@auth.login_required
@requires("admin")
def admin_change_status(request_id, *, context):
    req = db.get_request(request_id)
    if not req:
        abort(404)
    new_status = request.form.get("status", "")
    try:
        wf.admin_set_status(req, new_status, g.user, request.form.get("comment"))
    except wf.WorkflowError as e:
        flash(str(e), "error")
        session["admin_comment"] = request.form.get("comment", "")
        return redirect(url_for("request_card", request_id=request_id) + "#admin-status")
    db.save_request(req)
    flash(f"Заявка № {req['number']}: статус змінено на «{wf.STATUSES[new_status][0]}»", "success")
    return redirect(url_for("request_card", request_id=request_id))


@app.route("/to-pay")
@auth.login_required
@requires(*wf.ACCOUNTANT_ROLES, "cfo")
def to_pay(*, context):
    roles = g.roles
    today = date.today()
    view = "paid" if request.args.get("view") == "paid" else "to_pay"
    all_rows = db.list_paid(roles) if view == "paid" else db.list_to_pay(roles)
    form_filter = request.args.get("form") if request.args.get("form") in wf.CHANNELS else ""
    refs = ref_filters(request.args)
    period, d_from, d_to = _date_filter(request.args, today)

    def period_date(r):
        # Для оплачених — фактична дата оплати, для решти — планова
        entry = wf.paid_entry(r) if view == "paid" else None
        return entry["at"].date() if entry else r["pay_date"]

    rows = [r for r in apply_ref_filters(all_rows, refs)
            if (not form_filter or wf.channel_of(r) == form_filter)
            and _in_period(period_date(r), d_from, d_to)]

    return render_template(
        "to_pay.html",
        user_name=g.user["name"],
        view=view,
        paid_entry=wf.paid_entry,
        requests=rows,
        totals=currency_totals(rows, today),
        plan=payment_plan(rows),
        today=today,
        form_filter=form_filter,
        refs=refs,
        period=period,
        d_from=d_from,
        d_to=d_to,
        presets=date_presets(today),
        # Фільтр за формою оплати має сенс, якщо видно більше однієї форми
        visible_forms=[f for f in wf.CHANNELS if any(wf.channel_of(r) == f for r in all_rows)],
        payable={r["id"] for r in rows if view == "to_pay" and wf.acting_role(r, roles, g.user["email"])},
    )


@app.route("/admin/requests")
@auth.login_required
@requires("admin")
def admin_requests(*, context):
    today = date.today()
    args = request.args
    status = args.get("status") if args.get("status") in wf.STATUSES else ""
    form_filter = args.get("form") if args.get("form") in wf.CHANNELS else ""
    refs = ref_filters(args)
    date_field = "pay" if args.get("date_field") == "pay" else "created"
    period, d_from, d_to = _date_filter(args, today)
    q = (args.get("q") or "").strip().lower()

    def matches(r):
        if status and r["status"] != status:
            return False
        if form_filter and wf.channel_of(r) != form_filter:
            return False
        value = r["pay_date"] if date_field == "pay" else r["created_at"].date()
        if not _in_period(value, d_from, d_to):
            return False
        if q:
            haystack = " ".join([r["number"], r["author_name"], r["author_email"], r.get("note") or "",
                                 db.COUNTERPARTIES.get(r["counterparty_id"], "")]).lower()
            return q in haystack
        return True

    rows = [r for r in apply_ref_filters(db.list_all(), refs) if matches(r)]
    return render_template(
        "admin_requests.html",
        user_name=g.user["name"],
        requests=rows,
        totals=currency_totals(rows, today),
        today=today,
        status_filter=status,
        form_filter=form_filter,
        refs=refs,
        date_field=date_field,
        period=period,
        d_from=d_from,
        d_to=d_to,
        q=args.get("q", ""),
        presets=date_presets(today),
        ACTIONS=wf.ACTIONS,
    )


@app.route("/to-pay/pay", methods=["POST"])
@auth.login_required
@requires(*wf.ACCOUNTANT_ROLES)
def to_pay_bulk(*, context):
    paid, skipped = [], []
    comment = request.form.get("comment", "")
    for raw in request.form.getlist("ids"):
        req = db.get_request(int(raw)) if raw.isdigit() else None
        if not req:
            continue
        try:
            wf.apply_action(req, "pay", g.roles, g.user, comment)
        except wf.WorkflowError:
            skipped.append(req["number"])
            continue
        db.save_request(req)
        paid.append(req["number"])
    if paid:
        flash(f"Позначено оплаченими: {', '.join('№ ' + n for n in paid)}", "success")
    if skipped:
        flash(f"Не можна оплатити (не ваша форма оплати або вже змінено статус): {', '.join(skipped)}", "error")
    if not paid and not skipped:
        flash("Оберіть заявки галочками", "error")
    return redirect(url_for("to_pay", **{k: v for k, v in request.args.items()}))


# ---------------------------------------------------------------- адміністрування

ADMIN_ROLE_ORDER = ["admin", "initiator", "acc_cash", "acc_resident", "acc_nonresident", "cfo"]


def _azure_group_url(group_id):
    return f"https://portal.azure.com/#view/Microsoft_AAD_IAM/GroupDetailsMenuBlade/~/Members/groupId/{group_id}"


def _configured_groups():
    return {role: gid for role, gid in GROUPS.items() if gid}


@app.route("/admin/users")
@auth.login_required(scopes=graph.ADMIN_SCOPES)
@requires("admin")
def admin_users(*, context):
    token = context["access_token"]
    groups = _configured_groups()
    users, error = {}, None
    try:
        for role, gid in groups.items():
            for u in graph.group_members(token, gid):
                users.setdefault(u["id"], {**u, "roles": set()})["roles"].add(role)
    except graph.GraphError as e:
        error = str(e)
    return render_template(
        "admin_users.html",
        user_name=g.user["name"],
        users=sorted(users.values(), key=lambda u: u["name"].lower()),
        role_order=ADMIN_ROLE_ORDER,
        groups=groups,
        missing_groups=[r for r in ADMIN_ROLE_ORDER if r not in groups],
        group_url=_azure_group_url,
        error=error,
        me_oid=g.user["oid"],
        expense_status=expense_items.status(),
    )


@app.route("/admin/expense-items/refresh", methods=["POST"])
@auth.login_required
@requires("admin")
def admin_expense_refresh(*, context):
    if expense_items.refresh():
        st = expense_items.status()
        flash(f"Довідник статей витрат оновлено: {st['count']} статей", "success")
    else:
        flash("Не вдалося оновити довідник: " + (expense_items.status()["error"] or "невідома помилка"), "error")
    return redirect(url_for("admin_users") + "#expense-items")


@app.route("/admin/users/search")
@auth.login_required(scopes=graph.ADMIN_SCOPES)
@requires("admin")
def admin_users_search(*, context):
    try:
        return {"users": graph.search_users(context["access_token"], request.args.get("q", ""))}
    except graph.GraphError as e:
        return {"error": str(e)}, 502


def _apply_role_changes(token, user_id, wanted, current):
    """Додати/видалити людину в групах так, щоб її ролі стали = wanted. Повертає (додано, знято)."""
    groups = _configured_groups()
    if user_id == g.user["oid"] and "admin" in current and "admin" not in wanted:
        raise graph.GraphError("Не можна зняти роль Адміністратора із самого себе")
    added, removed = [], []
    for role in ADMIN_ROLE_ORDER:
        if role not in groups:
            continue
        if role in wanted and role not in current:
            graph.add_member(token, groups[role], user_id)
            added.append(wf.ROLES[role])
        elif role in current and role not in wanted:
            graph.remove_member(token, groups[role], user_id)
            removed.append(wf.ROLES[role])
    return added, removed


@app.route("/admin/users/save", methods=["POST"])
@auth.login_required(scopes=graph.ADMIN_SCOPES)
@requires("admin")
def admin_users_save(*, context):
    user_id = request.form.get("user_id", "")
    name = request.form.get("name", "")
    wanted = {r for r in request.form.getlist("roles") if r in wf.ROLES}
    current = {r for r in request.form.get("current", "").split(",") if r in wf.ROLES}
    try:
        added, removed = _apply_role_changes(context["access_token"], user_id, wanted, current)
    except graph.GraphError as e:
        flash(str(e), "error")
        return redirect(url_for("admin_users"))
    if added or removed:
        parts = ([f"додано: {', '.join(added)}"] if added else []) + ([f"знято: {', '.join(removed)}"] if removed else [])
        flash(f"{name}: {'; '.join(parts)}. Зміни вже в Azure; користувачу треба вийти й увійти знову.", "success")
        if not wanted:
            flash(f"{name} більше не має доступу до системи.", "success")
    else:
        flash("Змін немає", "success")
    return redirect(url_for("admin_users"))


@app.route("/admin/users/add", methods=["POST"])
@auth.login_required(scopes=graph.ADMIN_SCOPES)
@requires("admin")
def admin_users_add(*, context):
    user_id = request.form.get("user_id", "")
    name = request.form.get("name", "")
    wanted = {r for r in request.form.getlist("roles") if r in wf.ROLES}
    if not user_id:
        flash("Оберіть співробітника зі списку пошуку", "error")
    elif not wanted:
        flash("Оберіть хоча б одну роль", "error")
    else:
        try:
            added, _ = _apply_role_changes(context["access_token"], user_id, wanted, set())
            flash(f"{name}: додано ролі {', '.join(added)}. Зміни вже в Azure.", "success")
        except graph.GraphError as e:
            flash(str(e), "error")
    return redirect(url_for("admin_users"))


# ---------------------------------------------------------------- документи

UPLOAD_DIR = os.environ.get("UPLOAD_DIR") or (
    "/home/data/uploads" if os.environ.get("WEBSITE_SITE_NAME")  # змінна є лише на App Service
    else os.path.join(app.root_path, "uploads")
)
ALLOWED_EXT = {"pdf", "jpg", "jpeg", "png", "doc", "docx", "xls", "xlsx"}
MAX_FILE_SIZE = 10 * 1024 * 1024
INLINE_EXT = {"pdf", "jpg", "jpeg", "png"}  # відкриваються в браузері, решта — скачуються


@app.errorhandler(413)
def too_large(_e):
    flash("Файли завеликі: максимум 10 МБ на файл і 25 МБ за раз", "error")
    return redirect(request.referrer or "/")


def _ext(filename):
    return filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


@app.route("/requests/<int:request_id>/files", methods=["POST"])
@auth.login_required
@requires("initiator", *wf.APPROVER_ROLES)
def upload_files(request_id, *, context):
    user, roles = g.user, g.roles
    req = _load_visible(request_id)
    if not wf.can_attach(req, roles, user["email"]):
        abort(403)
    files = [f for f in request.files.getlist("files") if f and f.filename]
    if not files:
        flash("Оберіть файл", "error")
        return redirect(url_for("request_card", request_id=request_id))

    os.makedirs(UPLOAD_DIR, exist_ok=True)
    added = []
    role = wf.history_role(req, roles, user["email"])
    for f in files:
        ext = _ext(f.filename)
        f.stream.seek(0, os.SEEK_END)
        size = f.stream.tell()
        f.stream.seek(0)
        if ext not in ALLOWED_EXT:
            flash(f"«{f.filename}»: недопустимий тип файлу (дозволено: {', '.join(sorted(ALLOWED_EXT))})", "error")
            continue
        if size > MAX_FILE_SIZE:
            flash(f"«{f.filename}»: файл більший за 10 МБ", "error")
            continue
        file_id = uuid.uuid4().hex
        f.save(os.path.join(UPLOAD_DIR, f"{file_id}.{ext}"))
        name = os.path.basename(f.filename.replace("\\", "/"))
        req["attachments"].append({
            "id": file_id, "name": name, "ext": ext, "size": size, "at": datetime.now(),
            "user_name": user["name"], "user_email": user["email"],
        })
        wf.add_history(req, "file_add", user, role, comment=name)
        added.append(name)
    if added:
        db.save_request(req)
        flash("Додано: " + ", ".join(added), "success")
    return redirect(url_for("request_card", request_id=request_id) + "#documents")


@app.route("/files/<file_id>")
@auth.login_required
@requires("initiator", "admin", *wf.APPROVER_ROLES)
def download_file(file_id, *, context):
    req, att = db.find_attachment(file_id)
    if not req or not wf.can_view(req, g.roles, g.user["email"]):
        abort(404)
    path = os.path.join(UPLOAD_DIR, f"{att['id']}.{att['ext']}")
    if not os.path.exists(path):
        abort(404)
    return send_file(path, download_name=att["name"], as_attachment=att["ext"] not in INLINE_EXT)


@app.route("/files/<file_id>/delete", methods=["POST"])
@auth.login_required
@requires("initiator", *wf.APPROVER_ROLES)
def delete_file(file_id, *, context):
    user, roles = g.user, g.roles
    req, att = db.find_attachment(file_id)
    if not req or not wf.can_view(req, roles, user["email"]):
        abort(404)
    if att["user_email"].lower() != user["email"].lower() or req["status"] in wf.CLOSED_STATUSES:
        abort(403)
    req["attachments"] = [a for a in req["attachments"] if a["id"] != file_id]
    wf.add_history(req, "file_delete", user, wf.history_role(req, roles, user["email"]), comment=att["name"])
    db.save_request(req)
    try:
        os.remove(os.path.join(UPLOAD_DIR, f"{att['id']}.{att['ext']}"))
    except FileNotFoundError:
        pass
    flash(f"Документ «{att['name']}» видалено", "success")
    return redirect(url_for("request_card", request_id=req["id"]) + "#documents")


@app.template_global()
def url_with(changes):
    """Поточна адреса з заміненими параметрами запиту (None/"" — прибрати параметр)."""
    args = request.args.to_dict()
    for key, value in changes.items():
        if value in (None, ""):
            args.pop(key, None)
        else:
            args[key] = value
    return url_for(request.endpoint, **args)


@app.template_filter("filesize")
def filesize(n):
    return f"{n / 1024 / 1024:.1f} МБ" if n >= 1024 * 1024 else f"{max(1, round(n / 1024))} КБ"


@app.route("/health")
def health():
    return {"status": "ok"}


if __name__ == "__main__":
    app.run(port=5000, debug=True)
