import io
import logging
import mimetypes
import os
import re
import threading
import time
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
import counterparties
import currencies
import rates
import db_sql
import departments
import filestore
import org_structure
import organizations
import payment_accounts
import payment_types
import user_onec
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
        "ORGANIZATIONS": organizations.names(),
        "ORG_OPTIONS": organizations.options(),
        "ORG_FILTER": organizations.filter_options(),
        "org_name": organizations.name,
        "cp_name": counterparties.name,
        "cp_pending": counterparties.is_pending,
        # Фільтр «Контрагент»: ті, кого можна вибрати (без уже прив'язаних тимчасових)
        # Фільтр «Контрагент»: лише ті, що трапляються в заявках (у довіднику — десятки тисяч)
        "cp_filter_options": lambda: dict(sorted(counterparties.used_names(
            {r.get("counterparty_id") for r in db.list_all()}).items(), key=lambda kv: kv[1].lower())),
        "pay_account_label": payment_accounts.label,
        "expense_name": expense_label,
        "expense_title": expense_items.get_name,
        # Фільтр «Стаття витрат»: лише статті, що трапляються в заявках
        "exp_options": lambda: sorted({r["expense_code"] for r in db.list_all() if r.get("expense_code")},
                                      key=lambda c: expense_label(c).lower()),
        "CURRENCIES": currencies.codes(),
        "CURRENCY_NAMES": currencies.full_names(),
        "PAYMENT_FORMS": db.PAYMENT_FORMS,
        "CHANNELS": wf.CHANNELS,
        "channel_of": wf.channel_of,
        "dept_name": departments.get_name,
        # Фільтр «Відділ»: лише відділи, що трапляються в заявках
        "dep_options": lambda: sorted({r["department"] for r in db.list_all() if r.get("department")},
                                      key=lambda c: departments.get_name(c).lower()),
        "STATUSES": db.STATUSES,
        "needs_handover": wf.needs_handover,
        "payer_label": payer_label,
        "pay_org": pay_org,
        # У режимі «Бухгалтер» організацію не вказують — колонки й фільтра «Організація» немає
        "show_org": app_settings.get("org_field_mode") != "accountant",
    }


# ---------------------------------------------------------------- розбір форми

def _parse_amount(raw):
    raw = (raw or "").replace(" ", "").replace(" ", "").replace(",", ".")
    try:
        value = Decimal(raw)
    except InvalidOperation:
        return None
    return value.quantize(Decimal("0.01")) if value.is_finite() else None


def _parse_counterparty(raw, current=None):
    """Контрагент з форми: з 1С (не помічений на видалення), тимчасовий або той, що вже був у заявці."""
    cp_id = (raw or "").strip()
    return cp_id if cp_id and (cp_id == current or counterparties.is_selectable(cp_id)) else None


def _parse_code(raw, allowed):
    """Рядковий код довідника (напр. організації з 1С) або None, якщо такого немає."""
    code = (raw or "").strip()
    return code if code and code in allowed else None


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


def _parse_currency(raw, current=None):
    """Валюта з довідника 1С; стара валюта цієї ж заявки (навіть якщо її вже прибрали з довідника) — приймається."""
    raw = (raw or "").strip().upper()
    return raw if raw and (currencies.is_valid(raw) or raw == current) else None


def parse_request_form(form, current=None, allowed_codes=None):
    """Повертає (дані заявки, словник помилок). Автор/дата/номер не з форми."""
    errors = {}
    data = {
        "counterparty_id": _parse_counterparty(form.get("counterparty_id"), (current or {}).get("counterparty_id")),
        "organization_id": _parse_code(form.get("organization_id"),
                                       set(organizations.options()) | {(current or {}).get("organization_id")}),
        "expense_code": (form.get("expense_code") or "").strip() or None,
        "payment_form": form.get("payment_form") if form.get("payment_form") in db.PAYMENT_FORMS else None,
        "currency": _parse_currency(form.get("currency"), (current or {}).get("currency")),
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
    if app_settings.get("org_field_mode") == "payer":
        # Ініціатор вибирає лише платника; організацію (якщо вже вказана бухгалтером) лишаємо, поки платник той самий
        data["payer"] = form.get("payer") if form.get("payer") in wf.PAYERS else None
        cur_org = (current or {}).get("organization_id")
        data["organization_id"] = cur_org if cur_org and data["payer"] == wf.payer_of({"organization_id": cur_org}) \
            else None
        required.pop("organization_id")
        if data["payer"] is None:
            errors["payer"] = "Вкажіть платника — українська чи іноземна компанія"
    else:
        data["payer"] = None
    if app_settings.get("org_field_mode") == "accountant":
        # Ініціатор одразу вибирає тип бухгалтера; організація не потрібна (стару — лишаємо як є)
        data["accountant_type"] = form.get("accountant_type") if form.get("accountant_type") in wf.ACCOUNTANT_CHOICES else None
        data["organization_id"] = (current or {}).get("organization_id")
        required.pop("organization_id")
        if data["accountant_type"] is None:
            errors["accountant_type"] = "Оберіть, який бухгалтер перевірить заявку"
    else:
        data["accountant_type"] = None
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
            "currency": _parse_currency(form.get(f"lines-{i}-currency"), (current or {}).get("currency"))
            or data["currency"],
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
# Керівник відділу і «бачить заявки відділу» — не групи Azure, а призначення в картці користувача (org_structure)
# (org_structure); ролі dept_head / dept_viewer додаються під час входу.
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
            roles = resolve_roles(claims)
            # Керівник відділу / «бачить заявки відділу» — з картки користувача, не з груп Azure
            heads = org_structure.head_departments(g.user["oid"])
            views = org_structure.viewer_departments(g.user["oid"])
            if heads:
                roles.add("dept_head")
            if views:
                roles.add("dept_viewer")
            user_directory.remember([{"id": g.user["oid"], "email": email, "name": g.user["name"]}],
                                    active=bool(roles))
            g.roles = wf.RoleSet(roles, heads, views)
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
notifications.admin_emails = lambda: sorted(ADMIN_EMAILS)


@app.context_processor
def inject_roles():
    roles = getattr(g, "roles", set())
    return {
        "roles": roles,
        "ROLES": wf.ROLES,
        "ROLE_SHORT": wf.ROLE_SHORT,
        "is_approver": bool(_deciding_roles(roles)) or "cfo" in roles,
        "sees_departments": bool(roles & {"dept_head", "dept_viewer"}),
        "participating_count": len(db.list_participating(g.user["email"])) if getattr(g, "user", None) else 0,
        "queue_count": len(db.list_queue(roles)) if roles & wf.APPROVER_ROLES else 0,
        "can_see_payments": wf.can_see_payments(roles),
        "sees_all_requests": _sees_all_requests(roles),
        "test_mode": notifications.test_mode(),
        "payments_count": len(db.list_to_pay(roles)) if wf.can_see_payments(roles) else 0,
    }


def _deciding_roles(roles):
    """Ролі, якими користувач ухвалює рішення. Керівник відділу — лише якщо він погоджує (налаштування)."""
    deciding = set(roles & wf.APPROVER_ROLES)
    if app_settings.get("dept_head_mode") != "approve":
        deciding.discard("dept_head")
    # Фіндиректор-спостерігач — без черги (крім заявок, відправлених ще з його кроком)
    if "cfo" in deciding and app_settings.get("cfo_mode") == "observe" and not db.list_queue(frozenset({"cfo"})):
        deciding.discard("cfo")
    return deciding


def _sees_all_requests(roles):
    """«Усі заявки»: адміністратор і фіндиректор (лише перегляд)."""
    return bool(roles & {"admin", "cfo"})


def _home():
    if "initiator" not in g.roles and _deciding_roles(g.roles):
        return url_for("approvals")
    if "initiator" not in g.roles and g.roles & {"dept_head", "dept_viewer"}:
        return url_for("department_requests")
    if g.roles == {"admin"} or ("initiator" not in g.roles and "cfo" in g.roles):
        return url_for("admin_requests")
    return url_for("requests_list")


def _load_visible(request_id):
    req = db.get_request(request_id)
    if not req or not wf.can_view(req, g.roles, g.user["email"]):
        abort(404)
    return req


# ---------------------------------------------------------------- фільтри списків

REF_FILTERS = {  # параметр запиту -> (поле заявки, довідник)
    "org": ("organization_id", None),  # код організації (рядок)
    "cp": ("counterparty_id", None),  # id контрагента (рядок)
    "exp": ("expense_code", None),  # код статті витрат (рядок)
    "dep": ("department", None),  # код відділу автора (рядок)
}


def ref_filters(args):
    """Вибрані значення фільтрів довідників: {"org": 1, "cp": None, "exp": "01.001"}."""
    selected = {key: _parse_ref(args.get(key), ref() if callable(ref) else ref)
                for key, (_, ref) in REF_FILTERS.items() if ref is not None}
    selected["exp"] = (args.get("exp") or "").strip() or None
    selected["org"] = _parse_code(args.get("org"), set(organizations.names()))
    cp = (args.get("cp") or "").strip()
    selected["cp"] = cp if cp and counterparties.get(cp) else None  # невідомий id — фільтр ігнорується
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


@app.route("/home")
@auth.login_required
@requires()
def _home_redirect(*, context):
    return redirect(_home())


@app.route("/requests")
@auth.login_required
@requires()
def requests_list(*, context):
    status = request.args.get("status") if request.args.get("status") in db.STATUSES else ""
    refs = ref_filters(request.args)
    participating = db.list_participating(g.user["email"])
    view = request.args.get("view") if request.args.get("view") in ("participant", "paid") else "mine"
    if "initiator" not in g.roles:
        view = "participant"
    if view == "participant" and not participating and "initiator" not in g.roles:
        return redirect(_home())
    if view == "participant":
        source = [r for r in participating if not status or r["status"] == status]
    elif view == "paid":  # оплачені — окремою вкладкою, найсвіжіша оплата першою
        source = sorted(db.list_requests(g.user["email"], "paid"),
                        key=lambda r: (wf.paid_entry(r) or {}).get("at") or r["created_at"], reverse=True)
    else:  # «Створені мною» — заявки в роботі, без оплачених
        source = [r for r in db.list_requests(g.user["email"], status or None) if r["status"] != "paid"]
    return render_template(
        "requests_list.html",
        user_name=g.user["name"],
        requests=apply_ref_filters(source, refs),
        status_filter=status,
        refs=refs,
        view=view,
        participating_total=len(participating),
        handover_count=sum(1 for r in db.list_requests(g.user["email"], "paid") if wf.needs_handover(r)),
        paid_entry=wf.paid_entry,
        show_dep_filter=False,
    )


@app.route("/approvals")
@auth.login_required
@requires(*wf.APPROVER_ROLES)
def approvals(*, context):
    refs = ref_filters(request.args)
    view = request.args.get("view") if request.args.get("view") in ("processed", "accountants") else "queue"
    if view == "accountants" and "cfo" not in g.roles:
        abort(403)
    source = {"processed": lambda: db.list_processed(g.roles), "accountants": db.list_at_accountants,
              "queue": lambda: db.list_queue(g.roles)}[view]()
    return render_template(
        "approvals.html",
        user_name=g.user["name"],
        requests=apply_ref_filters(source, refs),
        refs=refs,
        view=view,
        processed_entry=lambda r: wf.processed_entry(r, g.roles),
        accountants_count=len(db.list_at_accountants()) if "cfo" in g.roles else 0,
        accountant_role=wf.accountant_role,
        ACTIONS=wf.ACTIONS,
    )


@app.route("/requests/new", methods=["GET", "POST"])
@app.route("/requests/<int:request_id>", methods=["GET", "POST"])
@auth.login_required
@requires()
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
            **_default_accountant(user),
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
        if (existing.get("organization_id") and not data["organization_id"] and not errors
                and app_settings.get("org_field_mode") == "payer"):
            wf.add_history(req, "organization", user, "initiator",
                           f"{organizations.name(existing['organization_id'])} → — (скинуто: змінено платника)",
                           req["status"], req["status"])
        if existing.get("payment_account_id") and (
                data["organization_id"] != existing.get("organization_id")
                or data["payment_form"] != existing.get("payment_form")):
            wf.set_payment_account(req, None, "", payment_accounts.label(existing["payment_account_id"]) +
                                   " (скинуто: змінено організацію або форму оплати)", user, "initiator")
        if not errors:
            for line in req["lines"]:
                line.pop("amount_raw", None)
            req.pop("accountant_hint", None)
            action = request.form.get("action")
            if req["id"] is None:
                req["created_at"] = clock.now()
                wf.add_history(req, "create", user, "initiator", to_status="draft")
            added_participants = []
            if "participants_sent" in request.form and wf.can_manage_participants(req, roles, user["email"]):
                added_participants = _apply_participants(req, request.form.getlist("participants"), user, roles)
            if not req.get("department") or req["department"] not in departments.get_map():
                # заявки, створені до появи відділів, або з відділом, якого вже немає в довіднику
                req["department"] = org_structure.department_of(user["oid"])
            if action == "submit":
                wf.apply_action(req, "submit", roles, user, **_route_settings(req))
            new_id = db.save_request(req)
            number = db.get_request(new_id)["number"]
            for email in added_participants:
                notifications.notify("participant_add", db.get_request(new_id), user, target=email)
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
        nonresident_orgs=organizations.nonresident_codes(),
        org_options={**organizations.options(), **({req["organization_id"]: organizations.name(req["organization_id"])}
                     if req.get("organization_id") and req["organization_id"] not in organizations.options() else {})},
        channel_roles={c: wf.ROLES[r] for c, r in wf.ACCOUNTANT_BY_CHANNEL.items()},
        org_accountants={code: {f: wf.ROLES[r] for f, r in forms.items() if r in wf.ROLES}
                         for code, forms in organizations.accountant_overrides().items()},
        editable=wf.can_edit(req, roles, user["email"]),
        can_attach=req["id"] is not None and wf.can_attach(req, roles, user["email"]),
        actions=[a for a in available if a != "submit"],
        acting_role=wf.acting_role(req, roles, user["email"]) or (
            "cfo" if wf.cfo_can_intervene(req, roles, user["email"]) else None),
        cfo_intervene=wf.cfo_can_intervene(req, roles, user["email"]),
        accountant_role_of_req=wf.accountant_role(req),
        can_submit="submit" in available,
        stages=wf.stage_states(req),
        ACTIONS=wf.ACTIONS,
        CLOSED_STATUSES=wf.CLOSED_STATUSES,
        draft_comment=session.pop("draft_comment", ""),
        admin_comment=session.pop("admin_comment", ""),
        comment_draft=session.pop("comment_draft", ""),
        parallel=req.get("route_mode") == "parallel",
        head_names=", ".join(h["name"] or h["email"] for h in org_structure.heads_of(req.get("department"))),
        participants=req.get("participants") or [],
        # Учасники, як їх бачить форма: збережені або (після помилки в полях) ті, що користувач уже набрав
        participants_state=_participants_state(req, existing, errors),
        history=wf.visible_history(req, roles),
        cp=counterparties.get(req.get("counterparty_id")),
        cp_brief=counterparties.brief(counterparties.get(req["counterparty_id"]))
        if counterparties.get(req.get("counterparty_id")) else None,
        can_see_account=wf.can_see_payment_account(roles),
        account_options=payment_accounts.options_for(req, user, roles) if wf.can_see_payment_account(roles) else [],
        account_empty_hint=payment_accounts.no_options_hint(req, user, roles),
        account_org=payment_accounts.organization_of(req.get("payment_account_id")),
        account_required=wf.acting_role(req, roles, user["email"]) in wf.ACCOUNTANT_ROLES
        and req["status"] in ("accountant", "approval") and bool(payment_accounts.options_for(req, user, roles))
        and bool(app_settings.get("require_payment_account")),
        can_link_cp=counterparties.is_pending(req.get("counterparty_id")) and _can_link_cp(roles),
        # Бухгалтер бачить, що нового контрагента заводить адміністратор
        cp_owner_note=counterparties.is_pending(req.get("counterparty_id")) and not _can_link_cp(roles)
        and bool(roles & (wf.ACCOUNTANT_ROLES | {"cfo"})),
        cp_suggested=next((m for p_, m in counterparties.auto_link_candidates(only_id=req.get("counterparty_id"))), None)
        if counterparties.is_pending(req.get("counterparty_id")) else None,
        pay_blocked=_pay_guard(req)["cp_pending"] and _pay_guard(req)["block_pending"],
        handover_enabled=bool(app_settings.get("cash_handover_confirm")),
        org_mode=app_settings.get("org_field_mode"),
        PAYERS=wf.PAYERS,
        ACCOUNTANT_CHOICES=wf.ACCOUNTANT_CHOICES,
        payer_value=wf.payer_of(req),
        handover=req.get("handover"),
        handover_pending=wf.needs_handover(req),
        handover_done=wf.handover_done(req),
        handover_days=wf.handover_days(req),
        can_confirm_handover=wf.can_confirm_handover(req, roles, user["email"]),
        today=clock.today(),
        pending_cp_block=app_settings.get("pending_cp_payment") == "block",
        can_manage_participants=wf.can_manage_participants(req, roles, user["email"]),
        approvals=req.get("approvals") or {},
        pending_roles=wf.pending_roles(req) if req["status"] == "approval" else [],
        current_email=user["email"],
        back_url=url_for("requests_list") if wf.is_author(req, user["email"]) and "initiator" in roles
        else _home(),
    )


def _can_link_cp(roles):
    """Хто заводить нового контрагента в 1С і прив'язує його в заявці (параметр в адмінці)."""
    if app_settings.get("cp_onec_owner") == "admin":
        return "admin" in roles
    return bool(roles & (wf.ACCOUNTANT_ROLES | {"cfo", "admin"}))


def _pay_guard(req):
    """Чи можна оплатити заявку з контрагентом, якого ще немає в 1С (параметр в адмінці)."""
    return {"cp_pending": counterparties.is_pending(req.get("counterparty_id")),
            "block_pending": app_settings.get("pending_cp_payment") == "block",
            "handover": bool(app_settings.get("cash_handover_confirm")),
            "account_required": bool(app_settings.get("require_payment_account")) and bool(
                payment_accounts.options_for(req, getattr(g, "user", None), getattr(g, "roles", frozenset())))}


def _apply_payment_account(req, raw, user, roles):
    """Зберегти «Вид оплати» з форми (лише бухгалтер / фіндиректор). Кидає WorkflowError."""
    if not wf.can_see_payment_account(roles) or raw is None:
        return False
    raw = raw.strip()
    if raw and raw != str(req.get("payment_account_id") or "") and not payment_accounts.valid_for(req, raw, user, roles):
        raise wf.WorkflowError("Такого виду оплати немає серед доступних вам — оберіть зі списку")
    new_id = raw or None
    old_label = payment_accounts.label(req.get("payment_account_id")) if req.get("payment_account_id") else ""
    return wf.set_payment_account(req, new_id, payment_accounts.label(new_id) if new_id else "", old_label,
                                  user, wf.history_role(req, roles, user["email"]))


def _default_accountant(user):
    """Тип бухгалтера для нової заявки (режим «Бухгалтер»): з картки користувача, інакше як у попередній заявці."""
    if app_settings.get("org_field_mode") != "accountant":
        return {}
    acc = org_structure.accountant_default_of(user["oid"])
    if acc in wf.ACCOUNTANT_CHOICES:
        return {"accountant_type": acc, "accountant_hint": "за замовчуванням з вашої картки"}
    last = next((r["accountant_type"] for r in db.list_requests(user["email"])
                 if r.get("accountant_type") in wf.ACCOUNTANT_CHOICES), None)
    return {"accountant_type": last, "accountant_hint": "як у вашій попередній заявці"} if last else {}


def pay_org(req):
    """З якої організації платимо: організація вибраного виду оплати, інакше — організація заявки. {"code","name"} або None."""
    o = payment_accounts.organization_of(req.get("payment_account_id"))
    if o:
        return o
    if req.get("organization_id"):
        return {"code": req["organization_id"], "name": organizations.name(req["organization_id"])}
    return None


def payer_label(req):
    """Організація заявки, а якщо ініціатор вибрав лише платника — «Українська компанія» / «Іноземна компанія»."""
    if req.get("organization_id"):
        return organizations.name(req["organization_id"])
    payer = wf.payer_of(req)
    return wf.PAYERS[payer] if payer else ""


def _route_settings(req):
    """Налаштування маршруту для відправки заявки: режим, роль керівника, керівники відділу автора."""
    return {
        "mode": app_settings.get("approval_mode"),
        "head_mode": app_settings.get("dept_head_mode"),
        "heads": [h["email"] for h in org_structure.heads_of(req.get("department")) if h["email"]],
        **_cfo_route(req),
    }


def _cfo_route(req):
    """Чи погоджує фіндиректор цю заявку (режим з адмінки; для «від суми» — еквівалент у USD за останнім курсом 1С)."""
    mode = app_settings.get("cfo_mode")
    if mode == "observe":
        return {"cfo_step": False, "cfo_note": ""}
    if mode != "threshold":
        return {"cfo_step": True, "cfo_note": ""}
    limit = Decimal(str(app_settings.get("cfo_threshold_usd") or 0))
    usd, day = rates.to_usd(req.get("amount"), req.get("currency"))
    limit_s = wf.fmt_usd(limit)
    if usd is None:
        return {"cfo_step": True, "cfo_note": f"Потрібне погодження фіндиректора: не вдалося перерахувати суму в USD ({day})"}
    when = f" (курс 1С на {date.fromisoformat(day):%d.%m.%Y})" if day else ""
    if usd >= limit:
        return {"cfo_step": True,
                "cfo_note": f"Потрібне погодження фіндиректора: ≈ {money(usd)} USD — не менше за поріг {limit_s} USD{when}"}
    return {"cfo_step": False,
            "cfo_note": f"Фіндиректор не погоджує: ≈ {money(usd)} USD — менше за поріг {limit_s} USD{when}"}


@app.route("/requests/<int:request_id>/payment-account", methods=["POST"])
@auth.login_required
@requires(*wf.ACCOUNTANT_ROLES, "cfo", "admin")
def request_payment_account(request_id, *, context):
    req = _load_visible(request_id)
    try:
        changed = _apply_payment_account(req, request.form.get("payment_account_id", ""), g.user, g.roles)
    except wf.WorkflowError as e:
        flash(str(e), "error")
        return redirect(url_for("request_card", request_id=request_id) + "#payment-account")
    if changed:
        db.save_request(req)
        label = payment_accounts.label(req.get("payment_account_id")) or "не вибрано"
        flash(f"Вид оплати: {label}", "success")
    else:
        flash("Змін немає", "success")
    return redirect(url_for("request_card", request_id=request_id) + "#payment-account")


@app.route("/requests/<int:request_id>/handover", methods=["POST"])
@auth.login_required
@requires()
def request_handover(request_id, *, context):
    """Підтвердження передачі готівки підряднику (автор, а за нього — бухгалтер по готівці чи адмін)."""
    req = _load_visible(request_id)
    back = request.form.get("next")
    back = back if back and back.startswith("/") and not back.startswith("//") else \
        url_for("request_card", request_id=request_id) + "#handover"
    try:
        if request.form.get("action") == "undo":
            if "admin" not in g.roles:
                abort(403)
            wf.undo_handover(req, g.user, request.form.get("comment"))
            message = "Підтвердження передачі скасовано — заявка знову чекає підтвердження"
        else:
            if not wf.can_confirm_handover(req, g.roles, g.user["email"]):
                abort(403)
            wf.confirm_handover(req, g.user, g.roles, _parse_date(request.form.get("date")),
                                request.form.get("comment"))
            message = f"Заявка № {req['number']}: передачу готівки підряднику підтверджено"
    except wf.WorkflowError as e:
        flash(str(e), "error")
        return redirect(back)
    db.save_request(req)
    if request.form.get("action") != "undo":
        notifications.notify("handover", req, g.user)
    flash(message, "success")
    return redirect(back)


@app.route("/counterparties/search")
@auth.login_required
@requires()
def counterparties_search(*, context):
    """Пошук контрагентів для вікна вибору (q, kind = resident|nonresident|pending, scope = onec, limit)."""
    try:
        limit = max(1, min(int(request.args.get("limit", 20)), 200))
    except ValueError:
        limit = 20
    kind = request.args.get("kind", "") if request.args.get("kind") in ("resident", "nonresident", "pending") else ""
    found, total = counterparties.search(request.args.get("q", ""), limit, kind,
                                         "onec" if request.args.get("scope") == "onec" else "all")
    return {"items": [counterparties.brief(c) for c in found], "total": total}


@app.route("/counterparties/new", methods=["POST"])
@auth.login_required
@requires("initiator")
def counterparty_new(*, context):
    """Новий (тимчасовий) контрагент, якого ще немає в 1С. JSON: {item} | {errors} | {duplicate}."""
    data = request.get_json(silent=True) or request.form
    item, errors, duplicate = counterparties.create_pending(data, g.user)
    if errors:
        return {"errors": errors}, 400
    if duplicate:
        return {"duplicate": {"id": duplicate["id"], "name": duplicate["name"], "edrpou": duplicate.get("edrpou", ""),
                              "pending": duplicate["status"] == "pending"}}, 409
    return {"item": counterparties.brief(item)}


def _try_live_link(req, user, role):
    """Якщо контрагент заявки тимчасовий — перевірити в 1С наживо за ЄДРПОУ і прив'язати. True, якщо прив'язано."""
    if not counterparties.is_pending(req.get("counterparty_id")):
        return False
    pair = next(iter(counterparties.auto_link_candidates(live=True, only_id=req["counterparty_id"])), None)
    if not pair:
        return False
    pending, target, _ = _link_counterparty(pair[0]["id"], pair[1]["id"], user, role)
    req["counterparty_id"] = target["id"]
    wf.add_history(req, "cp_link", user, role, f"{pending['name']} → {target['name']} (з 1С)", req["status"], req["status"])
    return True


def _link_counterparty(pending_id, onec_id, user, role):
    """Прив'язати тимчасового контрагента до запису з 1С і перевести на нього всі заявки."""
    pending, target = counterparties.link(pending_id, onec_id, user)
    return pending, target, db.replace_counterparty(pending["id"], target["id"], user, role,
                                                    f"{pending['name']} → {target['name']} (з 1С)")


@app.route("/counterparties/<cp_id>/check", methods=["POST"])
@auth.login_required
@requires(*wf.ACCOUNTANT_ROLES, "cfo", "admin")
def counterparty_check(cp_id, *, context):
    """«Перевірити в 1С зараз»: живий запит за ЄДРПОУ і автоприв'язка."""
    if not _can_link_cp(g.roles):
        abort(403)
    nxt = request.form.get("next") or ""
    back = nxt if nxt.startswith("/") and not nxt.startswith("//") and "\\" not in nxt else url_for("_home_redirect")
    role = next((r for r in (*sorted(wf.ACCOUNTANT_ROLES), "cfo", "admin") if r in g.roles), "admin")
    pair = next(iter(counterparties.auto_link_candidates(live=True, only_id=cp_id)), None)
    if not pair:
        cp = counterparties.get(cp_id)
        why = "у 1С ще немає контрагента з таким ЄДРПОУ" if cp and cp.get("edrpou") else \
            "у тимчасового контрагента не вказано ЄДРПОУ — знайдіть його в 1С вручну нижче"
        flash(f"Не прив'язано: {why}", "error")
        return redirect(back)
    pending, target, count = _link_counterparty(pair[0]["id"], pair[1]["id"], g.user, role)
    flash(f"Знайдено в 1С: «{target['name']}». Контрагента прив'язано (заявок: {count}) — тепер заявку можна оплатити",
          "success")
    return redirect(back)


@app.route("/counterparties/<cp_id>/link", methods=["POST"])
@auth.login_required
@requires(*wf.ACCOUNTANT_ROLES, "cfo", "admin")
def counterparty_link(cp_id, *, context):
    if not _can_link_cp(g.roles):
        abort(403)
    nxt = request.form.get("next") or ""
    # Лише відносна адреса всередині системи (без переходу на сторонні сайти)
    back = nxt if nxt.startswith("/") and not nxt.startswith("//") and "\\" not in nxt else url_for("_home_redirect")
    try:
        role = next((r for r in (*sorted(wf.ACCOUNTANT_ROLES), "cfo", "admin") if r in g.roles), "admin")
        pending, target, count = _link_counterparty(cp_id, request.form.get("onec_id") or "", g.user, role)
    except (ValueError, TypeError) as e:
        flash(str(e) or "Оберіть контрагента з 1С", "error")
        return redirect(back)
    flash(f"Контрагента «{pending['name']}» прив'язано до «{target['name']}» з 1С (заявок: {count})", "success")
    return redirect(back)


@app.route("/users/search")
@auth.login_required
@requires()
def users_search(*, context):
    """Підказки для вибору учасника заявки: користувачі системи (з роллю)."""
    me = g.user["email"].lower()
    return {"users": [{"email": u["email"], "name": u["name"]}
                      for u in user_directory.search(request.args.get("q", "")) if u["email"].lower() != me]}


def _apply_participants(req, emails, user, roles):
    """Звести учасників заявки до списку з форми; кожна зміна — запис в історії.
    Повертає пошти доданих (їм — лист «Вас додано»). Невідомих користувачів пропускає з повідомленням."""
    want = []
    for e in emails:
        e = (e or "").strip().lower()
        if e and e not in want:
            want.append(e)
    role = wf.history_role(req, roles, user["email"])
    for p in list(req.get("participants") or []):
        if p["email"].lower() not in want:
            wf.remove_participant(req, p["email"], user, role)
    current = {p["email"].lower() for p in req.get("participants") or []}
    added = []
    for e in want:
        if e in current:
            continue
        person = user_directory.find_active(e)
        try:
            if not person:
                raise wf.WorkflowError(f"{e} — такого користувача немає в системі")
            wf.add_participant(req, person, user, role)
            added.append(person["email"])
        except wf.WorkflowError as ex:
            flash(f"Учасника не додано: {ex}", "error")
    return added


def _participants_state(req, existing, errors):
    """Учасники для форми: після помилки в полях заявки — набрані користувачем (ще не збережені)."""
    saved = [{"email": p["email"], "name": p["name"],
              "title": f"{p['email']} · додав(-ла) {p.get('added_by', '')}, {fmt_datetime(p.get('added_at'))}"}
             for p in (existing or {}).get("participants") or []]
    if request.method != "POST" or not errors or "participants_sent" not in request.form:
        return saved
    known = {s["email"].lower(): s for s in saved}
    state = []
    for e in request.form.getlist("participants"):
        e = (e or "").strip().lower()
        if not e or any(s["email"].lower() == e for s in state):
            continue
        person = known.get(e) or user_directory.find_active(e)
        if person:
            state.append({"email": person["email"], "name": person["name"], "title": person.get("title") or person["email"]})
    return state


@app.route("/requests/<int:request_id>/participants", methods=["POST"])
@auth.login_required
@requires()
def request_participants(request_id, *, context):
    req = _load_visible(request_id)
    back = redirect(url_for("request_card", request_id=request_id) + "#participants")
    if not wf.can_manage_participants(req, g.roles, g.user["email"]):
        abort(403)
    if request.form.get("action") == "set":
        if wf.can_edit(req, g.roles, g.user["email"]):
            # у чернетці учасники зберігаються разом із заявкою (кнопка «Записати»)
            flash("Збережіть заявку — учасники зберігаються разом із нею", "error")
            return back
        added = _apply_participants(req, request.form.getlist("participants"), g.user, g.roles)
        db.save_request(req)
        for email in added:
            notifications.notify("participant_add", req, g.user, target=email)
        flash("Учасників заявки збережено", "success")
        return back
    email = (request.form.get("email") or "").strip()
    role = wf.history_role(req, g.roles, g.user["email"])
    try:
        if request.form.get("action") == "remove":
            name = wf.remove_participant(req, email, g.user, role)
            db.save_request(req)
            flash(f"{name} більше не учасник заявки", "success")
            return back
        person = user_directory.find_active(email)
        if not person:
            raise wf.WorkflowError("Такого користувача немає в системі — оберіть зі списку підказок")
        wf.add_participant(req, person, g.user, role)
    except wf.WorkflowError as e:
        flash(str(e), "error")
        return back
    db.save_request(req)
    notifications.notify("participant_add", req, g.user, target=person["email"])
    flash(f"{person['name']} — тепер учасник заявки: бачить її, може коментувати й додавати файли", "success")
    return back


@app.route("/requests/<int:request_id>/action", methods=["POST"])
@auth.login_required
@requires("initiator", *wf.APPROVER_ROLES)
def request_action(request_id, *, context):
    req = _load_visible(request_id)
    action = request.form.get("action")
    try:
        _apply_payment_account(req, request.form.get("payment_account_id"), g.user, g.roles)
        if action == "pay" and _try_live_link(req, g.user, wf.history_role(req, g.roles, g.user["email"])):
            flash("Контрагента знайдено в 1С і прив'язано автоматично", "success")
        wf.apply_action(req, action, g.roles, g.user, request.form.get("comment"), **_route_settings(req),
                        guard=_pay_guard(req))
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
    return currencies.order_key(cur)


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
@requires()
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
    groups = ["author"]  # учасником заявки може стати будь-хто
    if _deciding_roles(g.roles):
        groups.append("approver")
    if g.roles & {"dept_head", "dept_viewer"}:
        groups.append("department")
    if g.roles & wf.ACCOUNTANT_ROLES and app_settings.get("cash_handover_confirm"):
        groups.append("cash")
    if "admin" in g.roles and app_settings.get("cp_onec_owner") == "admin":
        groups.append("admin")
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


PENDING_CP_MODES = {
    "block": "заборонити позначати «Оплачено», доки контрагента не прив'язано до 1С",
    "allow": "дозволити оплату з попередженням",
}


@app.route("/admin/organizations", methods=["POST"])
@auth.login_required
@requires("admin")
def admin_organizations(*, context):
    """Які організації можна вибирати в заявках. Зберігаємо вимкнені — нові з 1С дозволені автоматично."""
    allowed = set(request.form.getlist("allowed"))
    known = {o["code"] for o in organizations.get_all()}
    if not allowed & known:
        flash("Залиште дозволеною хоча б одну організацію — інакше заявку не можна буде створити", "error")
        return redirect(url_for("admin_users") + "#organizations")
    # Вимкнені, яких зараз немає в довіднику (напр. помічені на видалення), лишаються вимкненими
    disabled = sorted((known - allowed) | (organizations.disabled_codes() - known))
    app_settings.set("disabled_organizations", disabled, g.user["email"])
    # Хто з бухгалтерів веде організацію (порожньо — автоматично); призначення зниклих з довідника — лишаються
    overrides = {c: v for c, v in organizations.accountant_overrides().items() if c not in known}
    for code in known:
        forms = {f: request.form.get(f"acc_{f}_{code}") for f in ("cash", "bank")}
        forms = {f: r for f, r in forms.items() if r in wf.ACCOUNTANT_ROLES}
        if forms:
            overrides[code] = forms
    app_settings.set("org_accountants", overrides, g.user["email"])
    flash(f"Організації збережено: дозволено для вибору {len(known & allowed)} з {len(known)}; "
          f"призначено бухгалтерів для {len([c for c in overrides if c in known])}. "
          "Заявки, що вже в роботі, переходять до призначеного бухгалтера одразу.", "success")
    return redirect(url_for("admin_users") + "#organizations")


@app.route("/admin/settings", methods=["POST"])
@auth.login_required
@requires("admin")
def admin_settings(*, context):
    mode = request.form.get("approval_mode")
    head_mode = request.form.get("dept_head_mode", app_settings.get("dept_head_mode"))
    cp_mode = request.form.get("pending_cp_payment", app_settings.get("pending_cp_payment"))
    cfo_mode = request.form.get("cfo_mode", app_settings.get("cfo_mode"))
    if "cfo_threshold_usd" in request.form:
        try:
            threshold = int(Decimal((request.form.get("cfo_threshold_usd") or "").replace(" ", "").replace(",", ".")))
        except (InvalidOperation, ValueError):
            threshold = 0
        if threshold <= 0 and cfo_mode == "threshold":
            flash("Вкажіть поріг фіндиректора — суму в USD, більшу за 0", "error")
            return redirect(url_for("admin_users") + "#process-settings")
        if threshold > 0 and threshold != app_settings.get("cfo_threshold_usd"):
            app_settings.set("cfo_threshold_usd", threshold, g.user["email"])
            flash(f"Поріг фіндиректора: від {wf.fmt_usd(threshold)} USD", "success")
            changed = True
    cp_owner = request.form.get("cp_onec_owner", app_settings.get("cp_onec_owner"))
    mode = mode or app_settings.get("approval_mode")  # радіокнопки неактивні, поки фіндиректор спостерігає
    if (mode not in wf.APPROVAL_MODES or head_mode not in wf.HEAD_MODES or cp_mode not in PENDING_CP_MODES
            or cfo_mode not in wf.CFO_MODES or cp_owner not in wf.CP_OWNER_MODES):
        flash("Невідомий режим погодження", "error")
        return redirect(url_for("admin_users") + "#process-settings")
    changed = False
    if (mode != app_settings.get("approval_mode") or head_mode != app_settings.get("dept_head_mode")
            or cfo_mode != app_settings.get("cfo_mode")):
        app_settings.set("approval_mode", mode, g.user["email"])
        app_settings.set("dept_head_mode", head_mode, g.user["email"])
        app_settings.set("cfo_mode", cfo_mode, g.user["email"])
        flash(f"Маршрут: {wf.route_label(mode, head_mode, cfo_mode, app_settings.get('cfo_threshold_usd'))}. "
              "Діє для нових і повторно відправлених заявок.",
              "success")
        changed = True
    org_mode = request.form.get("org_field_mode")
    if org_mode in wf.ORG_FIELD_MODES and org_mode != app_settings.get("org_field_mode"):
        app_settings.set("org_field_mode", org_mode, g.user["email"])
        flash("У заявці ініціатор вибирає: " + {"payer": "лише платника — українська чи іноземна компанія",
                                                 "accountant": "тип бухгалтера"}.get(org_mode, "організацію"), "success")
        changed = True
    data_mode = request.form.get("data_mode")
    if data_mode in ("work", "test") and db_sql.configured() and data_mode != app_settings.get("data_mode"):
        app_settings.set("data_mode", data_mode, g.user["email"])
        flash("Система показує: " + ("тестові дані — нові заявки не зберігаються в базі, листи позначено «[ТЕСТ]»"
                                     if data_mode == "test" else "робочу базу даних"), "success")
        changed = True
    if cp_owner != app_settings.get("cp_onec_owner"):
        app_settings.set("cp_onec_owner", cp_owner, g.user["email"])
        flash("Нових контрагентів у 1С заводить: " + ("адміністратор" if cp_owner == "admin" else "бухгалтер"),
              "success")
        changed = True
    require_acc = request.form.get("require_payment_account") == "1"
    if "require_payment_account_sent" in request.form and require_acc != bool(app_settings.get("require_payment_account")):
        app_settings.set("require_payment_account", require_acc, g.user["email"])
        flash("«Вид оплати» при погодженні бухгалтером: " + ("обов'язковий" if require_acc else "необов'язковий"), "success")
        changed = True
    if "cash_handover_sent" in request.form:
        handover_on = request.form.get("cash_handover_confirm") == "1"
        try:
            remind_days = max(0, min(int(request.form.get("cash_handover_remind_days") or 0), 30))
        except ValueError:
            remind_days = app_settings.get("cash_handover_remind_days")
        if handover_on != bool(app_settings.get("cash_handover_confirm")):
            app_settings.set("cash_handover_confirm", handover_on, g.user["email"])
            flash("Підтвердження передачі готівки підряднику: " + ("увімкнено" if handover_on else "вимкнено"), "success")
            changed = True
        if remind_days != app_settings.get("cash_handover_remind_days"):
            app_settings.set("cash_handover_remind_days", remind_days, g.user["email"])
            flash(f"Нагадування про передачу готівки: кожні {remind_days} дн." if remind_days
                  else "Нагадування про передачу готівки вимкнено", "success")
            changed = True
    if cp_mode != app_settings.get("pending_cp_payment"):
        app_settings.set("pending_cp_payment", cp_mode, g.user["email"])
        flash(f"Контрагент, якого ще немає в 1С: {PENDING_CP_MODES[cp_mode]}", "success")
        changed = True
    if not changed:
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
        wf.admin_set_status(req, new_status, g.user, request.form.get("comment"),
                            handover=bool(app_settings.get("cash_handover_confirm")))
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
    view = request.args.get("view") if request.args.get("view") in ("paid", "handover") else "to_pay"
    handover_rows = _handover_rows(roles)
    if view == "handover":
        return _handover_view(handover_rows, today)
    all_rows = db.list_paid(roles) if view == "paid" else db.list_to_pay(roles)
    form_filter = request.args.get("form") if request.args.get("form") in wf.CHANNELS else ""
    refs = ref_filters(request.args)
    period, d_from, d_to = _date_filter(request.args, today)

    def period_date(r):
        # Для оплачених — фактична дата оплати, для решти — планова
        entry = wf.paid_entry(r) if view == "paid" else None
        return entry["at"].date() if entry else r["pay_date"]

    acc_filter = request.args.get("acc", "")
    payorg_filter = request.args.get("payorg", "")
    rows = [r for r in apply_ref_filters(all_rows, refs)
            if (not form_filter or wf.channel_of(r) == form_filter)
            and (not acc_filter or str(r.get("payment_account_id") or "none") == acc_filter)
            and (not payorg_filter or ((pay_org(r) or {}).get("code") or "none") == payorg_filter)
            and _in_period(period_date(r), d_from, d_to)]
    account_totals, org_totals = {}, {}
    for r in rows:
        key = payment_accounts.label(r.get("payment_account_id")) if r.get("payment_account_id") else "Не вибрано"
        t = account_totals.setdefault(key, {})
        t[r["currency"]] = t.get(r["currency"], Decimal(0)) + r["amount"]
        t = org_totals.setdefault((pay_org(r) or {}).get("name") or "Не визначено", {})
        t[r["currency"]] = t.get(r["currency"], Decimal(0)) + r["amount"]
    blocked = {r["id"] for r in rows if _pay_guard(r)["cp_pending"] and _pay_guard(r)["block_pending"]}
    no_account = {r["id"] for r in rows if not r.get("payment_account_id")}

    return render_template(
        "to_pay.html",
        **_handover_tab(handover_rows),
        acc_filter=acc_filter,
        account_options=sorted({r.get("payment_account_id") for r in all_rows if r.get("payment_account_id")},
                               key=lambda a: payment_accounts.label(a).lower()),
        has_rows=bool(all_rows),
        account_totals=sorted(account_totals.items()),
        org_totals=sorted(org_totals.items(), key=lambda kv: (kv[0] == "Не визначено", kv[0].lower())),
        payorg_filter=payorg_filter,
        payorg_options=_payorg_options(all_rows),
        blocked=blocked,
        no_account=no_account,
        pending_cp_mode=app_settings.get("pending_cp_payment"),
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
        payable={r["id"] for r in rows if view == "to_pay" and wf.acting_role(r, roles, g.user["email"])
                 and r["id"] not in blocked and r["id"] not in no_account},
        # Мої заявки до оплати, які зараз не можна оплатити, — з причиною
        locked={r["id"]: ("Контрагента ще немає в 1С — оплату заблоковано" if r["id"] in blocked
                          else "Не вибрано «Вид оплати» — відкрийте заявку й виберіть рахунок або касу")
                for r in rows if view == "to_pay" and wf.acting_role(r, roles, g.user["email"])
                and (r["id"] in blocked or r["id"] in no_account)},
    )


def _payorg_options(rows):
    """Організації-платники, що трапляються в рядках: [(код, назва)] за назвою."""
    seen = {o["code"]: o["name"] for o in (pay_org(r) for r in rows) if o}
    return sorted(seen.items(), key=lambda kv: kv[1].lower())


def _handover_rows(roles):
    """Оплачені готівкові заявки, де автор ще не підтвердив передачу, — для бухгалтера цих заявок."""
    if not app_settings.get("cash_handover_confirm"):
        return []
    rows = [r for r in db.list_all() if wf.needs_handover(r) and wf.is_handover_accountant(r, roles)]
    return sorted(rows, key=lambda r: (r["handover"]["since"], r["id"]))


def _handover_tab(rows):
    # Вкладка — у бухгалтера по готівці (і в того, кому готівку організації призначено в адмінці)
    return {"show_handover_tab": bool(app_settings.get("cash_handover_confirm"))
            and ("acc_cash" in g.roles or bool(rows)), "handover_tab_count": len(rows)}


def _handover_view(all_rows, today):
    refs = ref_filters(request.args)
    period, d_from, d_to = _date_filter(request.args, today)
    rows = [r for r in apply_ref_filters(all_rows, refs) if _in_period(r["handover"]["since"], d_from, d_to)]
    by_author = {}
    for r in rows:
        a = by_author.setdefault(r["author_email"].lower(), {"name": r["author_name"], "count": 0, "sums": {}})
        a["count"] += 1
        a["sums"][r["currency"]] = a["sums"].get(r["currency"], Decimal(0)) + r["amount"]
    remind_days = app_settings.get("cash_handover_remind_days") or 0
    tab = _handover_tab(all_rows)
    if not tab["show_handover_tab"]:
        abort(403)
    return render_template(
        "to_pay.html",
        **tab,
        view="handover",
        user_name=g.user["name"],
        requests=rows,
        by_author=sorted(by_author.values(), key=lambda a: a["name"].lower()),
        days_waiting=lambda r: wf.handover_days(r, today),
        overdue_days=remind_days or 1,
        max_reminders=wf.HANDOVER_MAX_REMINDERS,
        today=today,
        refs=refs,
        period=period,
        d_from=d_from,
        d_to=d_to,
        presets=date_presets(today),
        paid_entry=wf.paid_entry,
    )


@app.route("/admin/requests")
@auth.login_required
@requires("admin", "cfo")
def admin_requests(*, context):
    return _requests_overview(db.list_all(), title="Усі заявки", table_id="all-requests")


@app.route("/department")
@auth.login_required
@requires("dept_head", "dept_viewer")
def department_requests(*, context):
    deps = sorted(wf.head_deps(g.roles) | wf.view_deps(g.roles), key=lambda c: departments.get_name(c).lower())
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
                                 counterparties.name(r.get("counterparty_id")),
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
            _try_live_link(req, g.user, wf.history_role(req, g.roles, g.user["email"]))
            wf.apply_action(req, "pay", g.roles, g.user, comment, guard=_pay_guard(req))
        except wf.WorkflowError:
            skipped.append(req["number"])
            continue
        db.save_request(req)
        notifications.notify("pay", req, g.user)
        paid.append(req["number"])
    if paid:
        flash(f"Позначено оплаченими: {', '.join('№ ' + n for n in paid)}", "success")
    if skipped:
        flash("Не можна оплатити: " + ", ".join(skipped) +
              " — не ваша форма оплати, вже змінено статус, не вибрано «Вид оплати» або контрагента ще немає в 1С",
              "error")
    if not paid and not skipped:
        flash("Оберіть заявки галочками", "error")
    return redirect(url_for("to_pay", **{k: v for k, v in request.args.items()}))


# ---------------------------------------------------------------- адміністрування

ADMIN_ROLE_ORDER = ["admin", "initiator", "acc_cash", "acc_resident", "acc_nonresident", "cfo"]


def _azure_group_url(group_id):
    return f"https://portal.azure.com/#view/Microsoft_AAD_IAM/GroupDetailsMenuBlade/~/Members/groupId/{group_id}"


def _configured_groups():
    return {role: gid for role, gid in GROUPS.items() if gid}


def _group_users(token):
    """Користувачі з груп ролей: ({id: {id, name, email, roles}}, текст помилки або None)."""
    users, error = {}, None
    try:
        for role, gid in _configured_groups().items():
            for u in graph.group_members(token, gid):
                users.setdefault(u["id"], {**u, "roles": set()})["roles"].add(role)
    except graph.GraphError as e:
        error = str(e)
    user_directory.remember(users.values(), active=True)  # пошта погоджувачів для листів, список користувачів
    return users, error


def _migrate_legacy_departments():
    """Тестові відділи з тими самими назвами, що й у 1С, — на коди з 1С (одноразово, повторно — без змін)."""
    if departments.status()["source"] not in ("powerbi", "copy"):
        return
    mapping = departments.legacy_mapping()
    if mapping and (org_structure.remap_departments(mapping, g.user["email"]) + db.remap_departments(mapping)):
        flash("Відділи з тими самими назвами, що й у 1С, переведено на довідник 1С: "
              + ", ".join(departments.get_name(v) for v in mapping.values()), "success")


@app.route("/admin/users")
@auth.login_required(scopes=graph.ADMIN_SCOPES)
@requires("admin")
def admin_users(*, context):
    _migrate_legacy_departments()
    users, error = _group_users(context["access_token"])
    groups = _configured_groups()
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
        cfo_mode=app_settings.info("cfo_mode"),
        cfo_threshold=app_settings.get("cfo_threshold_usd"),
        rates_status=rates.status(),
        db_on=db_sql.configured(),
        org_mode=app_settings.get("org_field_mode"),
        ORG_FIELD_MODES=wf.ORG_FIELD_MODES,
        data_mode=app_settings.get("data_mode"),
        CFO_MODES=wf.CFO_MODES,
        cp_owner=app_settings.get("cp_onec_owner"),
        CP_OWNER_MODES=wf.CP_OWNER_MODES,
        pending_cp_requests={cid: next((r["id"] for r in db.list_all() if r.get("counterparty_id") == cid), None)
                             for cid in [c["id"] for c in counterparties.pending_items()]},
        pending_cp=app_settings.info("pending_cp_payment"),
        require_payment_account=app_settings.get("require_payment_account"),
        cash_handover_confirm=app_settings.get("cash_handover_confirm"),
        cash_handover_remind_days=app_settings.get("cash_handover_remind_days"),
        handover_max_reminders=wf.HANDOVER_MAX_REMINDERS,
        PENDING_CP_MODES=PENDING_CP_MODES,
        pending_counterparties=counterparties.pending_items(),
        route_label=wf.route_label,
        departments_list=departments.get_departments(),
        departments_status=departments.status(),
        organizations_status=organizations.status(),
        currencies_status=currencies.status(),
        counterparties_status=counterparties.status(),
        payment_types_status=payment_types.status(),
        org_list=organizations.get_all(),
        org_disabled=organizations.disabled_codes(),
        org_accountants=organizations.accountant_overrides(),
        ACCOUNTANT_ROLE_LIST=[(r, wf.ROLE_SHORT[r]) for r in ("acc_cash", "acc_resident", "acc_nonresident")],
        user_departments={uid: org_structure.department_of(uid) for uid in users},
        onec_links=user_onec.all_links(),
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
    if not payment_types.refresh():
        flash("Не вдалося оновити види оплати з 1С: " + (payment_types.status()["error"] or "невідома помилка"), "error")
    if not counterparties.refresh():
        flash("Не вдалося оновити довідник контрагентів: " + (counterparties.status()["error"] or "невідома помилка"), "error")
    for pending, target in counterparties.auto_link_candidates():
        _, _, count = _link_counterparty(pending["id"], target["id"], wf.SYSTEM_USER, "admin")
        flash(f"Контрагента «{pending['name']}» автоматично прив'язано до «{target['name']}» з 1С (заявок: {count})",
              "success")
    for name, ref in (("організацій", organizations), ("відділів", departments), ("валют", currencies),
                      ("курсів валют", rates)):
        if not ref.refresh():
            flash(f"Не вдалося оновити довідник {name}: " + (ref.status()["error"] or "невідома помилка"), "error")
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


@app.route("/admin/users/<user_id>", methods=["GET", "POST"])
@auth.login_required(scopes=graph.ADMIN_SCOPES)
@requires("admin")
def admin_user_card(user_id, *, context):
    """Картка користувача: ролі, відділ, керівництво й перегляд відділів, статті бюджету — в одному місці."""
    token = context["access_token"]
    users, error = _group_users(token)
    person = users.get(user_id)
    if not person:  # новий: ще не має жодної ролі
        try:
            person = {**graph.get_user(token, user_id), "roles": set()}
        except graph.GraphError as e:
            flash(f"Користувача не знайдено в Azure: {e}", "error")
            return redirect(url_for("admin_users"))
    if request.method == "POST":
        if error:  # не знаємо поточних ролей — нічого не змінюємо
            flash("Не вдалося прочитати ролі з Azure, тож зміни не збережено: " + error, "error")
            return redirect(url_for("admin_user_card", user_id=user_id))
        return _save_user_card(context["access_token"], person)
    allow_alt = app_settings.get("allow_custom_email")
    return render_template(
        "admin_user_card.html",
        user_name=g.user["name"],
        person=person,
        is_new=not person["roles"],
        error=error,
        me_oid=g.user["oid"],
        groups=_configured_groups(),
        group_url=_azure_group_url,
        ROLE_GROUPS=wf.ROLE_GROUPS,
        ROLE_HINTS=wf.ROLE_HINTS,
        departments_list=departments.get_departments(),
        user_department=org_structure.department_of(user_id),
        dept_staff={d["code"]: org_structure.staff(d["code"]) for d in departments.get_departments()},
        head_deps=org_structure.head_departments(user_id),
        view_deps=org_structure.viewer_departments(user_id),
        head_mode=app_settings.get("dept_head_mode"),
        accountant_default=org_structure.accountant_default_of(user_id),
        ACCOUNTANT_CHOICES=wf.ACCOUNTANT_CHOICES,
        org_mode=app_settings.get("org_field_mode"),
        budget=user_budget.get(user_id) or {},
        budget_departments=expense_items.get_departments(),
        budget_items=[{"k": i["kind"], "d": i["departments"]} for i in expense_items.get_items()],
        expense_status=expense_items.status(),
        delivery=user_prefs.delivery_address(person["email"], allow_alt),
        onec_link=user_onec.get(user_id),
        onec_users=payment_types.onec_users(),
        onec_suggested=None if user_onec.get(user_id) else payment_types.suggest_user(person["name"], person["email"]),
        onec_types={u["guid"]: [t["name"] for t in payment_types.types_for_user(u["guid"])]
                    for u in payment_types.onec_users()},
        is_accountant_or_cfo=bool(set(person["roles"]) & (wf.ACCOUNTANT_ROLES | {"cfo"})),
    )


def _save_user_card(token, person):
    f = request.form
    uid, name, email, who = person["id"], person["name"], person["email"], g.user["email"]
    current = set(person["roles"])
    changes = []

    # Ролі (групи Azure). Свою роль адміністратора зняти не можна — поле вимкнене.
    wanted = {r for r in f.getlist("roles") if r in ADMIN_ROLE_ORDER}
    if uid == g.user["oid"] and "admin" in current:
        wanted.add("admin")
    try:
        added, removed = _apply_role_changes(token, uid, wanted, current)
    except graph.GraphError as e:
        flash(str(e), "error")
        return redirect(url_for("admin_user_card", user_id=uid))
    if added:
        changes.append("додано ролі: " + ", ".join(added))
    if removed:
        changes.append("знято ролі: " + ", ".join(removed))

    # Відділ співробітника
    known = departments.get_map()
    dep = f.get("department") or None
    if dep != org_structure.department_of(uid) and (dep is None or dep in known):
        org_structure.set_department(uid, email, name, dep, who)
        changes.append(f"відділ — {departments.get_name(dep) if dep else 'не вказано'}")

    # Бухгалтер за замовчуванням (режим «Бухгалтер» у заявці)
    if "accountant_default" in f:
        acc = f.get("accountant_default") or None
        if acc in (None, *wf.ACCOUNTANT_CHOICES) and acc != org_structure.accountant_default_of(uid):
            org_structure.set_accountant_default(uid, email, name, acc, who)
            changes.append("бухгалтер за замовчуванням — " + (wf.ACCOUNTANT_CHOICES[acc] if acc else "не задано"))

    # Які відділи очолює / заявки яких бачить (відділи, яких уже немає в довіднику, не чіпаємо)
    old_heads, old_views = org_structure.head_departments(uid), org_structure.viewer_departments(uid)
    heads = {d for d in f.getlist("head_deps") if d in known} | {d for d in old_heads if d not in known}
    views = ({d for d in f.getlist("view_deps") if d in known} | {d for d in old_views if d not in known}) - heads
    if heads != set(old_heads) or views != set(old_views):
        replaced = org_structure.set_person_assignments({"id": uid, "email": email, "name": name}, heads, views, who)
        changes.append("очолює: " + (", ".join(departments.get_name(d) for d in sorted(heads)) or "жодного відділу"))
        changes.append("бачить заявки: " + (", ".join(departments.get_name(d) for d in sorted(views)) or "жодного відділу"))
        for code, prev in replaced:
            changes.append(f"у відділі «{departments.get_name(code)}» замінено керівника {prev['name']}")

    # Користувач 1С (для «Видів оплати»)
    if "onec_user" in f:
        guid = (f.get("onec_user") or "").strip()
        known_onec = {u["guid"]: u["name"] for u in payment_types.onec_users()}
        current_link = user_onec.guid_for(uid)
        if guid != (current_link or "") and (not guid or guid in known_onec):
            user_onec.set_link(uid, email, guid, known_onec.get(guid, ""), who)
            changes.append("користувач 1С — " + (known_onec[guid] if guid else "не зв'язано"))

    # Статті бюджету
    mode = f.get("mode")
    known_exp = {d for d, _ in expense_items.get_departments()}
    prev = user_budget.get(uid) or {}
    exp_deps = [d for d in f.getlist("departments") if d in known_exp or d in (prev.get("departments") or [])] \
        if mode == "selected" else []
    new = {"direct": f.get("direct") == "1", "all_departments": mode == "all", "departments": exp_deps}
    norm = lambda b: (bool(b.get("direct")), bool(b.get("all_departments")), sorted(b.get("departments") or []))
    if norm(prev) != norm(new):
        saved = user_budget.save_settings(uid, email, name, updated_by=who, **new)
        changes.append(f"статті бюджету — доступно {len(user_budget.allowed_items(saved, expense_items.get_items()))}")

    if changes:
        flash(f"{name}: " + "; ".join(changes) + ".", "success")
        if added or removed:
            flash("Ролі вже змінено в Azure. Щоб вони запрацювали, користувачу треба вийти з системи й увійти знову.",
                  "success")
        if current and not wanted:
            flash(f"{name} більше не має жодної ролі — доступу до системи немає.", "success")
    else:
        flash("Змін немає", "success")
    return redirect(url_for("admin_user_card", user_id=uid))


# ---------------------------------------------------------------- документи

# Файли — у filestore: Azure Blob Storage (BLOB_ACCOUNT_URL + BLOB_CONTAINER) або папка на диску
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
@requires()
def upload_files(request_id, *, context):
    user, roles = g.user, g.roles
    req = _load_visible(request_id)
    if not wf.can_attach(req, roles, user["email"]):
        abort(403)
    files = [f for f in request.files.getlist("files") if f and f.filename]
    if not files:
        flash("Оберіть файл", "error")
        return redirect(url_for("request_card", request_id=request_id))

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
        try:
            filestore.save(file_id, ext, f.stream)
        except Exception:
            logging.getLogger(__name__).exception("Не вдалося зберегти файл %s", f.filename)
            flash(f"«{f.filename}»: не вдалося зберегти файл — спробуйте ще раз", "error")
            continue
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
@requires()
def download_file(file_id, *, context):
    req, att = db.find_attachment(file_id)
    if not req or not wf.can_view(req, g.roles, g.user["email"]):
        abort(404)
    data = filestore.read(att["id"], att["ext"])
    if data is None:
        abort(404)
    mimetype = mimetypes.guess_type(att["name"])[0] or "application/octet-stream"
    return send_file(io.BytesIO(data), mimetype=mimetype, download_name=att["name"],
                     as_attachment=att["ext"] not in INLINE_EXT)


@app.route("/files/<file_id>/delete", methods=["POST"])
@auth.login_required
@requires()
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
        filestore.delete(att["id"], att["ext"])
    except Exception:
        logging.getLogger(__name__).exception("Не вдалося видалити файл %s", att["id"])
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


@app.template_global()
def visible_history(req):
    """Історія заявки без записів, які поточному користувачу не призначені (напр. «Вид оплати»)."""
    return wf.visible_history(req, getattr(g, "roles", set()))


@app.template_filter("filesize")
def filesize(n):
    return f"{n / 1024 / 1024:.1f} МБ" if n >= 1024 * 1024 else f"{max(1, round(n / 1024))} КБ"


# ---------------------------------------------------------------- нагадування про передачу готівки

REMINDER_INTERVAL = 3600  # перевірка раз на годину
_last_base_url = None
_reminders_started = False
_reminders_lock = threading.Lock()


def _public_base_url():
    """Адреса сайту для посилань у листах, які надсилаються не у відповідь на запит."""
    if os.environ.get("APP_BASE_URL"):
        return os.environ["APP_BASE_URL"].rstrip("/")
    if os.environ.get("WEBSITE_HOSTNAME"):  # App Service задає сам
        return "https://" + os.environ["WEBSITE_HOSTNAME"]
    return _last_base_url or "http://localhost:5000"


def run_handover_reminders(today=None):
    """Нагадати авторам, які не підтвердили передачу готівки. Повертає номери заявок, яким надіслано."""
    every = app_settings.get("cash_handover_remind_days") or 0
    if not app_settings.get("cash_handover_confirm") or every <= 0 or notifications.test_mode():
        return []
    sent = []
    for r in db.list_all():
        if not wf.handover_reminder_due(r, every, today):
            continue
        req = db.get_request(r["id"])
        with app.test_request_context(base_url=_public_base_url()):
            if not notifications.send_handover_reminder(req, wf.handover_days(req, today)):
                continue  # розсилку вимкнено — нагадаємо, коли ввімкнуть
        wf.mark_reminded(req, today)
        db.save_request(req)
        sent.append(req["number"])
    return sent


def _reminder_loop():
    time.sleep(60)
    while True:
        try:
            run_handover_reminders()
        except Exception:
            logging.getLogger(__name__).exception("Помилка нагадувань про передачу готівки")
        time.sleep(REMINDER_INTERVAL)


@app.before_request
def _start_background():
    global _last_base_url, _reminders_started
    _last_base_url = request.host_url.rstrip("/")
    filestore.start_migration()
    if _reminders_started or not (os.environ.get("WEBSITE_SITE_NAME") or os.environ.get("HANDOVER_REMINDERS") == "1"):
        return
    with _reminders_lock:
        if not _reminders_started:
            _reminders_started = True
            threading.Thread(target=_reminder_loop, name="handover-reminders", daemon=True).start()


@app.route("/health")
def health():
    """Стан сайту й бази даних (без персональних даних)."""
    info = {**db_sql.status(), **filestore.status()}
    return ({"status": "error", **info}, 503) if info.get("error") else {"status": "ok", **info}


if __name__ == "__main__":
    app.run(port=5000, debug=True)
