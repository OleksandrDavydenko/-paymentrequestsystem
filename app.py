import os
import re
import uuid
from functools import wraps
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from dotenv import load_dotenv
from flask import Flask, abort, flash, g, redirect, render_template, request, send_file, session, url_for
from werkzeug.middleware.proxy_fix import ProxyFix
import identity.flask

import expense_items
import app_settings
import clock
import departments
import org_structure
import graph
import mailer
import notifications
import secret_box
import user_prefs
import user_budget
import user_directory
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
        "expense_title": expense_items.get_name,
        # Фільтр «Стаття витрат»: лише статті, що трапляються в заявках
        "exp_options": lambda: sorted({r["expense_code"] for r in db.list_all() if r.get("expense_code")},
                                      key=lambda c: expense_label(c).lower()),
        "CURRENCIES": db.CURRENCIES,
        "PAYMENT_FORMS": db.PAYMENT_FORMS,
        "CHANNELS": wf.CHANNELS,
        "channel_of": wf.channel_of,
        "dept_name": departments.get_name,
        # Фільтр «Відділ»: лише відділи, що трапляються в заявках
        "dep_options": lambda: sorted({r["department"] for r in db.list_all() if r.get("department")},
                                      key=lambda c: departments.get_name(c).lower()),
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
    """«Код — Назва» для статті витрат; код, якого немає в довіднику, позначаємо явно."""
    if not code:
        return ""
    name = expense_items.get_name(code)
    return f"{code} — {name}" if name else f"{code} — немає в довіднику"


def parse_request_form(form, current=None, allowed_codes=None):
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
    if data["expense_code"] and data["expense_code"] != (current or {}).get("expense_code"):
        # Стаття має бути в довіднику і в бюджеті автора (стара стаття цієї ж заявки — приймається)
        if expense_items.get_name(data["expense_code"]) is None:
            errors["expense_code"] = "Такої статті немає в довіднику — оберіть зі списку"
        elif allowed_codes is not None and data["expense_code"] not in allowed_codes:
            errors["expense_code"] = "Ця стаття не входить у ваш бюджет — оберіть зі списку"
    if allowed_codes is not None and not allowed_codes and not data["expense_code"]:
        errors["expense_code"] = "Вам ще не призначено статті бюджету. Зверніться до адміністратора"
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
    "dept_head": os.environ.get("GROUP_DEPT_HEAD", ""),
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
            user_directory.remember([{"id": g.user["oid"], "email": email, "name": g.user["name"]}])
            roles = resolve_roles(claims)
            g.roles = wf.RoleSet(roles, org_structure.head_departments(g.user["oid"]) if "dept_head" in roles else ())
            if not g.roles or (needed and not g.roles & set(needed)):
                return render_template("no_access.html", user_name=g.user["name"], missing=needed), 403
            return view(*args, context=context, **kwargs)
        return wrapper
    return deco


def _role_members(role):
    """Учасники групи ролі з поштою. Дозвіл додатку дає лише ID — пошту беремо з довідника."""
    gid = GROUPS.get(role)
    if not gid:
        return []
    members, unknown = [], 0
    for m in graph.group_members_app(gid):
        if not m.get("email"):
            m = {**m, **(user_directory.lookup(m["id"]) or {})}
        if m.get("email"):
            members.append(m)
        else:
            unknown += 1
    if unknown:
        mailer.log_problem(
            f"роль «{wf.ROLES.get(role, role)}»", "Невідома пошта учасників",
            f"Для {unknown} з учасників групи не вдалося визначити пошту — вони не отримають лист. "
            "Відкрийте «Адміністрування» (пошта підтягнеться з Azure) або додайте додатку дозвіл "
            "Microsoft Graph → Application → User.ReadBasic.All з Grant admin consent.")
    return members


notifications.role_members = _role_members


@app.context_processor
def inject_roles():
    roles = getattr(g, "roles", set())
    return {
        "roles": roles,
        "ROLES": wf.ROLES,
        "ROLE_SHORT": wf.ROLE_SHORT,
        "is_approver": bool(_deciding_roles(roles)),
        "is_dept_head": "dept_head" in roles,
        "queue_count": len(db.list_queue(roles)) if roles & wf.APPROVER_ROLES else 0,
        "can_see_payments": wf.can_see_payments(roles),
        "payments_count": len(db.list_to_pay(roles)) if wf.can_see_payments(roles) else 0,
    }


def _deciding_roles(roles):
    """Ролі, якими користувач ухвалює рішення. Керівник відділу — лише якщо він погоджує (налаштування)."""
    deciding = set(roles & wf.APPROVER_ROLES)
    if app_settings.get("dept_head_mode") != "approve":
        deciding.discard("dept_head")
    return deciding


def _home():
    if "initiator" not in g.roles and _deciding_roles(g.roles):
        return url_for("approvals")
    if "initiator" not in g.roles and "dept_head" in g.roles:
        return url_for("department_requests")
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
    "dep": ("department", None),  # код відділу автора (рядок)
}


def ref_filters(args):
    """Вибрані значення фільтрів довідників: {"org": 1, "cp": None, "exp": "01.001"}."""
    selected = {key: _parse_ref(args.get(key), ref) for key, (_, ref) in REF_FILTERS.items() if ref is not None}
    selected["exp"] = (args.get("exp") or "").strip() or None
    selected["dep"] = (args.get("dep") or "").strip() or None
    return selected


def apply_ref_filters(rows, selected):
    return [r for r in rows
            if all(not value or r.get(REF_FILTERS[key][0]) == value for key, value in selected.items())]


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
        show_dep_filter=False,
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
            "created_at": clock.now(),
            "author_email": user["email"],
            "author_name": user["name"],
            "department": org_structure.department_of(user["oid"]),
            "status": "draft",
            "payment_form": "bank",
            "currency": "UAH",
            "lines": [],
            "history": [],
            "attachments": [],
        }
    else:
        existing = _load_visible(request_id)

    # Статті, які може вибрати автор (редагує заявку лише автор, тож беремо поточного користувача)
    budget_items = user_budget.allowed_items(user_budget.get(user["oid"]), expense_items.get_items())

    errors = {}
    req = existing
    if request.method == "POST":
        if not wf.can_edit(existing, roles, user["email"]):
            flash("Заявку в цьому статусі редагувати не можна", "error")
            return redirect(url_for("request_card", request_id=request_id))
        data, errors = parse_request_form(request.form, existing, {i["code"] for i in budget_items})
        req = {**existing, **data}
        if not errors:
            for line in req["lines"]:
                line.pop("amount_raw", None)
            action = request.form.get("action")
            if req["id"] is None:
                req["created_at"] = clock.now()
                wf.add_history(req, "create", user, "initiator", to_status="draft")
            if not req.get("department"):  # заявки, створені до появи відділів
                req["department"] = org_structure.department_of(user["oid"])
            if action == "submit":
                wf.apply_action(req, "submit", roles, user, **_route_settings(req))
            new_id = db.save_request(req)
            number = db.get_request(new_id)["number"]
            if action == "submit":
                notifications.notify("submit", db.get_request(new_id), user)
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
        expense_items=budget_items,
        KIND_LABELS=expense_items.KIND_LABELS,
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
        comment_draft=session.pop("comment_draft", ""),
        parallel=req.get("route_mode") == "parallel",
        head_names=", ".join(h["name"] or h["email"] for h in org_structure.heads_of(req.get("department"))),
        approvals=req.get("approvals") or {},
        pending_roles=wf.pending_roles(req) if req["status"] == "approval" else [],
        current_email=user["email"],
        back_url=url_for("requests_list") if wf.is_author(req, user["email"]) and "initiator" in roles
        else _home(),
    )


def _route_settings(req):
    """Налаштування маршруту для відправки заявки: режим, роль керівника, керівники відділу автора."""
    return {
        "mode": app_settings.get("approval_mode"),
        "head_mode": app_settings.get("dept_head_mode"),
        "heads": [h["email"] for h in org_structure.heads_of(req.get("department")) if h["email"]],
    }


@app.route("/requests/<int:request_id>/action", methods=["POST"])
@auth.login_required
@requires("initiator", *wf.APPROVER_ROLES)
def request_action(request_id, *, context):
    req = _load_visible(request_id)
    action = request.form.get("action")
    try:
        wf.apply_action(req, action, g.roles, g.user, request.form.get("comment"), **_route_settings(req))
    except wf.WorkflowError as e:
        flash(str(e), "error")
        session["draft_comment"] = request.form.get("comment", "")
        return redirect(url_for("request_card", request_id=request_id))
    db.save_request(req)
    notifications.notify(action, req, g.user)
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


@app.route("/requests/<int:request_id>/comment", methods=["POST"])
@auth.login_required
@requires("initiator", "admin", *wf.APPROVER_ROLES)
def request_comment(request_id, *, context):
    req = _load_visible(request_id)
    try:
        wf.add_comment(req, g.user, wf.history_role(req, g.roles, g.user["email"]), request.form.get("comment"))
    except wf.WorkflowError as e:
        flash(str(e), "error")
        session["comment_draft"] = request.form.get("comment", "")
        return redirect(url_for("request_card", request_id=request_id) + "#comment")
    db.save_request(req)
    notifications.notify("comment", req, g.user)
    flash("Коментар додано", "success")
    return redirect(url_for("request_card", request_id=request_id) + "#history")


@app.route("/settings/notifications", methods=["GET", "POST"])
@auth.login_required
@requires()
def notification_settings(*, context):
    email = g.user["email"]
    groups = []
    if "initiator" in g.roles:
        groups.append("author")
    if g.roles & wf.APPROVER_ROLES:
        groups.append("approver")
    events = [(code, label, group) for code, (label, group, _) in user_prefs.EVENTS.items() if group in groups]
    if request.method == "POST":
        enabled = request.form.get("enabled") == "1"
        prev = user_prefs.get(email)["events"]
        chosen = set(request.form.getlist("events"))
        # Коли листи вимкнено, поля неактивні й не надсилаються — зберігаємо попередній вибір
        new_events = {code: (code in chosen) if enabled else prev[code] for code in user_prefs.EVENTS}
        # Події не своєї ролі не чіпаємо
        for code, (_, group, _) in user_prefs.EVENTS.items():
            if group not in groups:
                new_events[code] = prev[code]
        user_prefs.save(email, enabled, new_events)
        flash("Налаштування сповіщень збережено", "success")
        return redirect(url_for("notification_settings"))
    allow_custom = app_settings.get("allow_custom_email")
    return render_template("notification_settings.html", user_name=g.user["name"], user_email=email,
                           prefs=user_prefs.get(email), events=events, mail_enabled=notifications.enabled(),
                           allow_custom=allow_custom, delivery=user_prefs.delivery_address(email, allow_custom),
                           confirm_hours=int(user_prefs.CONFIRM_TTL.total_seconds() // 3600))


def _send_address_confirmation(email, alt_email):
    """Лист з посиланням підтвердження на іншу адресу. Повертає None або текст помилки."""
    token = user_prefs.set_alt(email, alt_email)
    link = url_for("notification_address_confirm", token=token, _external=True)
    base = os.environ.get("APP_BASE_URL", "").rstrip("/")
    if base:
        link = base + url_for("notification_address_confirm", token=token)
    html = render_template("email/confirm_address.html", user_name=g.user["name"], login_email=email,
                           alt_email=alt_email, link=link,
                           hours=int(user_prefs.CONFIRM_TTL.total_seconds() // 3600))
    return mailer.deliver(notifications.smtp_config(), alt_email, "Підтвердіть адресу для сповіщень — Система заявок на оплату", html)


@app.route("/settings/notifications/address", methods=["POST"])
@auth.login_required
@requires()
def notification_address(*, context):
    email = g.user["email"]
    back = redirect(url_for("notification_settings") + "#address")
    if not app_settings.get("allow_custom_email"):
        flash("Адміністратор не дозволив вказувати іншу пошту — листи надходять на робочу", "error")
        return back
    action = request.form.get("action")
    if action == "clear" or (action == "save" and request.form.get("target") == "work"):
        user_prefs.clear_alt(email)
        flash(f"Листи надходитимуть на робочу пошту {email}", "success")
        return back
    if action == "resend":
        alt_email = user_prefs.get(email)["alt_email"]
    else:
        alt_email = (request.form.get("alt_email") or "").strip()
    if not alt_email or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", alt_email) or len(alt_email) > 254:
        flash("Вкажіть коректну адресу пошти", "error")
        return back
    if alt_email.lower() == email.lower():
        user_prefs.clear_alt(email)
        flash("Це ваша робоча пошта — листи надходитимуть на неї", "success")
        return back
    prefs = user_prefs.get(email)
    if prefs["alt_email"].lower() == alt_email.lower() and prefs["alt_verified"]:
        flash("Цю адресу вже підтверджено", "success")
        return back
    if not notifications.enabled():
        flash("Розсилку листів зараз вимкнено — лист підтвердження надіслати неможливо", "error")
        return back
    wait = user_prefs.resend_wait(email)
    if wait:
        flash(f"Лист підтвердження вже надіслано — повторити можна через {wait} с", "error")
        return back
    error = _send_address_confirmation(email, alt_email)
    if error:
        flash("Лист підтвердження не надіслано: " + error, "error")
    else:
        flash(f"На {alt_email} надіслано лист із посиланням для підтвердження. "
              f"Доки адресу не підтверджено, листи надходять на {email}.", "success")
    return back


@app.route("/settings/notifications/confirm/<token>")
@auth.login_required
@requires()
def notification_address_confirm(token, *, context):
    email = g.user["email"]
    alt_email = user_prefs.confirm_alt(email, token) if app_settings.get("allow_custom_email") else None
    if alt_email:
        flash(f"Адресу підтверджено — листи надходитимуть на {alt_email}", "success")
    else:
        flash("Посилання недійсне: воно протерміноване, вже використане або належить іншому користувачу. "
              "Увійдіть тим обліковим записом, для якого вказували адресу, або надішліть лист ще раз.", "error")
    return redirect(url_for("notification_settings") + "#address")


@app.route("/admin/mail", methods=["POST"])
@auth.login_required
@requires("admin")
def admin_mail(*, context):
    f = request.form
    sender = (f.get("mail_sender") or "").strip()
    host = (f.get("smtp_host") or "").strip()
    try:
        port = int(f.get("smtp_port") or 0)
    except ValueError:
        port = 0
    security = f.get("smtp_security") if f.get("smtp_security") in mailer.SECURITY else "starttls"
    errors = []
    if sender and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", sender):
        errors.append("некоректна адреса відправника")
    if not 0 < port < 65536:
        errors.append("порт має бути числом від 1 до 65535")
    if errors:
        flash("Налаштування не збережено: " + "; ".join(errors), "error")
        return redirect(url_for("admin_users") + "#mail-settings")

    who = g.user["email"]
    app_settings.set("mail_sender", sender, who)
    app_settings.set("mail_sender_name", (f.get("mail_sender_name") or "").strip()
                     or app_settings.DEFAULTS["mail_sender_name"], who)
    app_settings.set("smtp_host", host, who)
    app_settings.set("smtp_port", port, who)
    app_settings.set("smtp_security", security, who)
    app_settings.set("smtp_username", (f.get("smtp_username") or "").strip(), who)
    # Пароль: порожнє поле — лишити збережений; галочка — видалити
    if f.get("smtp_password_clear") == "1":
        app_settings.set("smtp_password", "", who)
    elif f.get("smtp_password"):
        app_settings.set("smtp_password", secret_box.encrypt(f.get("smtp_password")), who)

    enabled = f.get("mail_enabled") == "1"
    if enabled and not (sender and host):
        flash("Щоб увімкнути розсилку, вкажіть SMTP-сервер і пошту відправника", "error")
        enabled = False
    app_settings.set("mail_enabled", enabled, who)
    app_settings.set("allow_custom_email", f.get("allow_custom_email") == "1", who)
    flash("Налаштування пошти збережено" + (" — розсилку увімкнено" if enabled else " — розсилку вимкнено"),
          "success")
    return redirect(url_for("admin_users") + "#mail-settings")


@app.route("/admin/mail/password", methods=["POST"])
@auth.login_required
@requires("admin")
def admin_mail_password(*, context):
    """Збережений пароль SMTP — лише на явний запит адміна (кнопки «Показати / Копіювати»), не в HTML сторінки."""
    password = secret_box.decrypt(app_settings.get("smtp_password"))
    if password is None:
        return {"error": "Пароль не задано або його не вдалося розшифрувати"}, 404
    resp = app.make_response({"password": password})
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/admin/mail/test", methods=["POST"])
@auth.login_required
@requires("admin")
def admin_mail_test(*, context):
    cfg = notifications.smtp_config()
    to = g.user["email"]
    if not (cfg["sender"] and cfg["host"]):
        error = "Спочатку вкажіть і збережіть SMTP-сервер і пошту відправника"
    elif app_settings.get("smtp_password") and cfg["password"] is None:
        error = "Збережений пароль не вдалося розшифрувати (змінився FLASK_SECRET_KEY?) — введіть пароль заново"
    else:
        html = render_template("email/test.html", user_name=g.user["name"], sender=cfg["sender"], host=cfg["host"])
        error = mailer.deliver(cfg, to, "Тестовий лист — Система заявок на оплату", html)
    if error:
        flash("Тестовий лист не надіслано: " + error, "error")
    else:
        flash(f"Тестовий лист надіслано на {to} від {cfg['sender']}. Перевірте пошту (і папку «Спам»).", "success")
    return redirect(url_for("admin_users") + "#mail-settings")


@app.route("/admin/settings", methods=["POST"])
@auth.login_required
@requires("admin")
def admin_settings(*, context):
    mode = request.form.get("approval_mode")
    head_mode = request.form.get("dept_head_mode", app_settings.get("dept_head_mode"))
    if mode not in wf.APPROVAL_MODES or head_mode not in wf.HEAD_MODES:
        flash("Невідомий режим погодження", "error")
    elif mode != app_settings.get("approval_mode") or head_mode != app_settings.get("dept_head_mode"):
        app_settings.set("approval_mode", mode, g.user["email"])
        app_settings.set("dept_head_mode", head_mode, g.user["email"])
        flash(f"Маршрут: {wf.route_label(mode, head_mode)}. Діє для нових і повторно відправлених заявок.", "success")
    else:
        flash("Змін немає", "success")
    return redirect(url_for("admin_users") + "#process-settings")


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
        db.save_request(req)
        notifications.notify("admin_status", req, g.user)
    except wf.WorkflowError as e:
        flash(str(e), "error")
        session["admin_comment"] = request.form.get("comment", "")
        return redirect(url_for("request_card", request_id=request_id) + "#admin-status")
    flash(f"Заявка № {req['number']}: статус змінено на «{wf.STATUSES[new_status][0]}»", "success")
    return redirect(url_for("request_card", request_id=request_id))


@app.route("/to-pay")
@auth.login_required
@requires(*wf.ACCOUNTANT_ROLES, "cfo")
def to_pay(*, context):
    roles = g.roles
    today = clock.today()
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
    return _requests_overview(db.list_all(), title="Усі заявки", table_id="all-requests")


@app.route("/department")
@auth.login_required
@requires("dept_head")
def department_requests(*, context):
    deps = sorted(wf.head_deps(g.roles), key=lambda c: departments.get_name(c).lower())
    title = "Заявки відділу" + (": " + ", ".join(departments.get_name(c) for c in deps) if deps else "")
    rows = [r for r in db.list_all() if wf.in_department(r, g.roles)]
    return _requests_overview(rows, title=title, table_id="department-requests", no_departments=not deps,
                              show_dep_filter=len(deps) > 1)


def _requests_overview(source, title, table_id, **extra):
    today = clock.today()
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
                                 db.COUNTERPARTIES.get(r["counterparty_id"], ""),
                                 departments.get_name(r.get("department"))]).lower()
            return q in haystack
        return True

    rows = [r for r in apply_ref_filters(source, refs) if matches(r)]
    return render_template(
        "admin_requests.html",
        title=title,
        table_id=table_id,
        **extra,
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
        notifications.notify("pay", req, g.user)
        paid.append(req["number"])
    if paid:
        flash(f"Позначено оплаченими: {', '.join('№ ' + n for n in paid)}", "success")
    if skipped:
        flash(f"Не можна оплатити (не ваша форма оплати або вже змінено статус): {', '.join(skipped)}", "error")
    if not paid and not skipped:
        flash("Оберіть заявки галочками", "error")
    return redirect(url_for("to_pay", **{k: v for k, v in request.args.items()}))


# ---------------------------------------------------------------- адміністрування

ADMIN_ROLE_ORDER = ["admin", "initiator", "acc_cash", "acc_resident", "acc_nonresident", "cfo", "dept_head"]


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
    user_directory.remember(users.values())  # пошта погоджувачів для листів
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
        approval_mode=app_settings.info("approval_mode"),
        head_mode=app_settings.info("dept_head_mode"),
        HEAD_MODES=wf.HEAD_MODES,
        route_label=wf.route_label,
        departments_list=departments.get_departments(),
        departments_status=departments.status(),
        user_departments={uid: org_structure.department_of(uid) for uid in users},
        head_departments={uid: org_structure.head_departments(uid) for uid in users},
        mail={k: app_settings.get(k) for k in ("mail_enabled", "mail_sender", "mail_sender_name", "smtp_host",
                                                "smtp_port", "smtp_security", "smtp_username",
                                                "allow_custom_email")},
        alt_addresses=user_prefs.confirmed_addresses() if app_settings.get("allow_custom_email") else {},
        mail_password=app_settings.info("smtp_password"),
        SMTP_PRESETS=mailer.PRESETS,
        SMTP_SECURITY=mailer.SECURITY,
        mail_log=list(mailer.LOG)[:20],
        APPROVAL_MODES=wf.APPROVAL_MODES,
        budgets={uid: user_budget.get(uid) for uid in users},
        budget_summary=user_budget.summary,
        budget_departments=expense_items.get_departments(),
        budget_items=[{"k": i["kind"], "d": i["departments"]} for i in expense_items.get_items()],
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


@app.route("/admin/users/budget", methods=["POST"])
@auth.login_required
@requires("admin")
def admin_users_budget(*, context):
    user_id = request.form.get("user_id", "").strip()
    if not user_id:
        abort(400)
    mode = request.form.get("mode")
    known = {d for d, _ in expense_items.get_departments()}
    previous = set((user_budget.get(user_id) or {}).get("departments") or [])
    # Відділи, яких уже немає в довіднику, лишаються, лише якщо адмін їх не зняв
    departments = [d for d in request.form.getlist("departments") if d in known or d in previous] \
        if mode == "selected" else []
    saved = user_budget.save_settings(
        user_id, request.form.get("email", ""), request.form.get("name", ""),
        direct=request.form.get("direct") == "1",
        all_departments=mode == "all",
        departments=departments,
        updated_by=g.user["email"],
    )
    items = user_budget.allowed_items(saved, expense_items.get_items())
    flash(f"{saved['name']}: статті бюджету збережено — доступно {len(items)} статей", "success")
    return redirect(url_for("admin_users") + "#admin-users")


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


def _save_department(user_id, email, name):
    """Відділ співробітника з форми (поле department). Повертає True, якщо змінився."""
    if "department" not in request.form:
        return False
    dep = request.form.get("department") or None
    if dep and dep not in departments.get_map() and dep != org_structure.department_of(user_id):
        return False
    if dep == org_structure.department_of(user_id):
        return False
    org_structure.set_department(user_id, email, name, dep, g.user["email"])
    return True


def admin_department_flash(name, user_id):
    dep = org_structure.department_of(user_id)
    flash(f"{name}: відділ — {departments.get_name(dep) if dep else 'не вказано'}", "success")


@app.route("/admin/users/save", methods=["POST"])
@auth.login_required(scopes=graph.ADMIN_SCOPES)
@requires("admin")
def admin_users_save(*, context):
    user_id = request.form.get("user_id", "")
    name = request.form.get("name", "")
    email = request.form.get("email", "")
    wanted = {r for r in request.form.getlist("roles") if r in wf.ROLES}
    current = {r for r in request.form.get("current", "").split(",") if r in wf.ROLES}
    try:
        added, removed = _apply_role_changes(context["access_token"], user_id, wanted, current)
    except graph.GraphError as e:
        flash(str(e), "error")
        return redirect(url_for("admin_users"))
    if "dept_head" in current and "dept_head" not in wanted:
        org_structure.set_head_departments(user_id, email, name, [], g.user["email"])
    dep_changed = _save_department(user_id, email, name)
    if dep_changed:
        admin_department_flash(name, user_id)
    if added or removed:
        parts = ([f"додано: {', '.join(added)}"] if added else []) + ([f"знято: {', '.join(removed)}"] if removed else [])
        flash(f"{name}: {'; '.join(parts)}. Зміни вже в Azure; користувачу треба вийти й увійти знову.", "success")
        if not wanted:
            flash(f"{name} більше не має доступу до системи.", "success")
    elif not dep_changed:
        flash("Змін немає", "success")
    return redirect(url_for("admin_users"))


@app.route("/admin/users/head", methods=["POST"])
@auth.login_required
@requires("admin")
def admin_users_head(*, context):
    """Відділи, які очолює керівник."""
    user_id = request.form.get("user_id", "").strip()
    if not user_id:
        abort(400)
    known = set(departments.get_map())
    previous = set(org_structure.head_departments(user_id))
    deps = [d for d in request.form.getlist("departments") if d in known or d in previous]
    name = request.form.get("name", "")
    org_structure.set_head_departments(user_id, request.form.get("email", ""), name, deps, g.user["email"])
    flash(f"{name}: очолює — {', '.join(departments.get_name(d) for d in sorted(deps)) or 'жодного відділу'}",
          "success")
    return redirect(url_for("admin_users") + "#admin-users")


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
            if _save_department(user_id, request.form.get("email", ""), name):
                admin_department_flash(name, user_id)
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
            "id": file_id, "name": name, "ext": ext, "size": size, "at": clock.now(),
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
