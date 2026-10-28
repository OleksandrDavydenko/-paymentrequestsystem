"""Оплати: «До оплати», «Оплачені», «Готівка не підтверджена», масова оплата."""
from datetime import date, timedelta
from decimal import Decimal

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

import app_settings
import clock
import counterparties
import notifications
import payment_accounts
import payments
import storage as db
import workflow as wf

from web import background
from web.auth import login_required, requires
from web.listing import _date_filter, _filtered, _in_period, _page, _ref_keys, currency_totals, date_presets, payment_plan, recent_presets
from web.formatting import money
from web.request_helpers import _pay_guard, _try_live_link


bp = Blueprint("payments", __name__)


@bp.route("/to-pay")
@login_required
@requires(*wf.ACCOUNTANT_ROLES, "cfo")
def to_pay(*, context):
    roles = g.roles
    today = clock.today()
    view = request.args.get("view") if request.args.get("view") in ("paid", "handover") else "to_pay"
    handover_rows = _handover_rows(roles)
    if view == "handover":
        return _handover_view(handover_rows, today)
    period, d_from, d_to = _date_filter(request.args, today, recent=view == "paid")
    if view == "paid":
        all_rows = db.scope_rows("paid", roles, g.user["email"], period=("paid", d_from, d_to))
    else:
        all_rows = db.scope_rows("to_pay", roles, g.user["email"])

    def period_date(r):
        # Для оплачених — фактична дата оплати, для решти — планова
        return r["paid_at"].date() if view == "paid" and r["paid_at"] else r["pay_date"]

    found, fbar = _filtered([r for r in all_rows if _in_period(period_date(r), d_from, d_to)],
                            _ref_keys(extra_before=("form", "acc", "payorg")))
    account_totals, org_totals = {}, {}
    for r in found:
        key = payment_accounts.label(r.get("payment_account_id")) if r.get("payment_account_id") else "Не вибрано"
        t = account_totals.setdefault(key, {})
        t[r["currency"]] = t.get(r["currency"], Decimal(0)) + r["amount"]
        t = org_totals.setdefault(db.derive.pay_org_name(r["pay_org"]) or "Не визначено", {})
        t[r["currency"]] = t.get(r["currency"], Decimal(0)) + r["amount"]
    rows, page = _page(found)
    blocked = {r["id"] for r in rows if _pay_guard(r)["cp_pending"] and _pay_guard(r)["block_pending"]}
    no_account = {r["id"] for r in rows if not r.get("payment_account_id")}
    auto = {r["id"] for r in rows if not payments.manual_pay_allowed(r)}
    # Відмітки обробки 1С про платіжки (лише для «До оплати»: у легких рядках їх немає — беремо повні заявки сторінки)
    onec_orders = {} if view == "paid" else {i: r["onec_order"] for i, r in db.get_many([r["id"] for r in rows]).items()
                                             if r.get("onec_order")}
    background.kick_payments_sync()

    return render_template(
        "to_pay.html",
        **_handover_tab(handover_rows),
        fbar=fbar,
        has_rows=bool(all_rows),
        account_totals=sorted(account_totals.items()),
        org_totals=sorted(org_totals.items(), key=lambda kv: (kv[0] == "Не визначено", kv[0].lower())),
        blocked=blocked,
        no_account=no_account,
        pending_cp_mode=app_settings.get("pending_cp_payment"),
        user_name=g.user["name"],
        view=view,
        paid_entry=wf.paid_entry,
        onec_orders=onec_orders,
        requests=rows,
        page=page,
        totals=currency_totals(found, today),
        plan=payment_plan(found),
        today=today,
        period=period,
        d_from=d_from,
        d_to=d_to,
        presets=recent_presets(today) if view == "paid" else date_presets(today),
        recent_default=view == "paid",
        payable={r["id"] for r in rows if view == "to_pay" and wf.acting_role(r, roles, g.user["email"])
                 and r["id"] not in blocked and r["id"] not in no_account and r["id"] not in auto},
        # Мої заявки до оплати, які зараз не можна оплатити, — з причиною
        locked={r["id"]: ("Контрагента ще немає в 1С — оплату заблоковано" if r["id"] in blocked
                          else f"«Оплачено» поставиться автоматично за платежем з 1С — у призначенні вкажіть #{int(r['number'])}"
                          if r["id"] in auto
                          else "Не вибрано «Вид оплати» — відкрийте заявку й виберіть рахунок або касу")
                for r in rows if view == "to_pay" and wf.acting_role(r, roles, g.user["email"])
                and (r["id"] in blocked or r["id"] in no_account or r["id"] in auto)},
    )


def _handover_rows(roles):
    """Оплачені готівкові заявки, де автор ще не підтвердив передачу, — для бухгалтера цих заявок (легкі рядки)."""
    if not app_settings.get("cash_handover_confirm"):
        return []
    return db.scope_rows("handover", roles, g.user["email"])


def _handover_tab(rows):
    # Вкладка — у бухгалтера по готівці (і в того, кому готівку організації призначено в адмінці)
    return {"show_handover_tab": bool(app_settings.get("cash_handover_confirm"))
            and ("acc_cash" in g.roles or bool(rows)), "handover_tab_count": len(rows)}


def _handover_view(all_rows, today):
    period, d_from, d_to = _date_filter(request.args, today)
    found, fbar = _filtered([r for r in all_rows if _in_period(r["handover_since"], d_from, d_to)], _ref_keys())
    by_author = {}
    for r in found:
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
        **dict(zip(("requests", "page"), _page(found))),
        fbar=fbar,
        by_author=sorted(by_author.values(), key=lambda a: a["name"].lower()),
        days_waiting=lambda r: wf.handover_days(r, today),
        overdue_days=remind_days or 1,
        max_reminders=wf.handover_max(),
        today=today,
        period=period,
        d_from=d_from,
        d_to=d_to,
        presets=date_presets(today),
        paid_entry=wf.paid_entry,
    )


@bp.route("/to-pay/pay", methods=["POST"])
@login_required
@requires(*wf.ACCOUNTANT_ROLES)
def to_pay_bulk(*, context):
    paid, skipped = [], []
    comment = request.form.get("comment", "")
    for raw in request.form.getlist("ids"):
        req = db.get_request(int(raw)) if raw.isdigit() else None
        if not req:
            continue
        if not payments.manual_pay_allowed(req):  # режим «Автоматично» — «Оплачено» ставить звірка з 1С
            skipped.append(req["number"])
            continue
        try:
            _try_live_link(req, g.user, wf.history_role(req, g.roles, g.user["email"]))
            wf.apply_action(req, "pay", g.roles, g.user, comment, guard=_pay_guard(req))
        except wf.WorkflowError:
            skipped.append(req["number"])
            continue
        try:
            db.save_request(req)
        except db.StaleRequestError:  # заявку щойно змінив хтось інший
            skipped.append(req["number"])
            continue
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
    return redirect(url_for("payments.to_pay", **{k: v for k, v in request.args.items()}))



# ---------------------------------------------------------------- «Звірка»: платежі з 1С без заявок

def _parse_day(raw, default):
    try:
        return date.fromisoformat(raw) if raw else default
    except ValueError:
        return default


@bp.route("/payments/reconcile")
@login_required
@requires("cfo", "admin")
def reconcile(*, context):
    """Платежі з 1С за період: без заявки, розбіжності, зіставлені з заявками, дозволені без заявки
    (фіндиректор і адміністратор)."""
    today = clock.today()
    a = request.args
    tab = a.get("tab") if a.get("tab") in payments.REPORT_TABS else "unlinked"
    d_to = _parse_day(a.get("to"), today)
    d_from = _parse_day(a.get("from"), today - timedelta(days=payments.REPORT_DAYS))
    pt, q = (a.get("pt") or "").strip(), (a.get("q") or "").strip().lower()
    background.kick_payments_sync()
    data = payments.report(d_from, d_to)
    period_types = {d["payment_type"] for v in data.values() for d in v if d.get("payment_type")}

    def keep(d):
        if pt in ("kind:Bank", "kind:Cash"):
            if d["kind"] != pt[5:]:
                return False
        elif pt and d.get("payment_type") != pt:
            return False
        if not q:
            return True
        hay = " ".join([d["number"] or "", d["purpose"] or "", d.get("operation") or ""]
                       + [counterparties.name(c) for c in d["counterparties"]]
                       + [f"{r['number']} {r['counterparty']}" for r in d.get("requests") or []])
        return q in hay.lower()

    shown = {k: [d for d in v if keep(d)] for k, v in data.items()}
    rows = shown[tab]
    totals = {}
    for d in rows:
        totals[d["currency"] or "—"] = totals.get(d["currency"] or "—", Decimal(0)) + d["amount"]
    return render_template(
        "payments_reconcile.html", user_name=g.user["name"], tab=tab, TABS=payments.REPORT_TABS,
        counts={k: len(v) for k, v in shown.items()}, rows=rows[:500], total_rows=len(rows),
        totals=sorted(totals.items()), d_from=d_from, d_to=d_to, pt=pt, q=a.get("q") or "",
        period_types=sorted(((i, payment_accounts.label(i)) for i in period_types), key=lambda kv: kv[1].lower()),
        item_departments=payments.item_departments(), can_link=payments.can_link_any(g.roles),
        can_allow="admin" in g.roles, sync=payments.status())


@bp.route("/payments/link-candidates")
@login_required
@requires(*wf.ACCOUNTANT_ROLES, "cfo", "admin")
def payment_link_candidates(*, context):
    """Заявки для вікна «Знайти заявку» у звірці (JSON): спершу з #номером у призначенні, далі той самий контрагент і сума."""
    doc, found = payments.request_candidates((request.args.get("guid") or "").strip().upper(), g.roles, request.args.get("q", ""))
    if not doc:
        abort(404)
    fmt = lambda d: d.strftime("%d.%m.%Y") if d else ""
    return {"items": [{"id": b["id"], "number": b["number"], "status": b["status_label"], "status_color": b["status_color"],
                       "counterparty": b["counterparty"], "amount": money(b["amount"]), "currency": b["currency"],
                       "author": b["author"], "pay_date": fmt(b["pay_date"]), "by_number": b["by_number"],
                       "same_cp": b["same_cp"], "same_amount": b["same_amount"],
                       "url": url_for("requests.request_card", request_id=b["id"])} for b in found]}


@bp.route("/payments/allow", methods=["POST"])
@login_required
@requires("admin")
def payment_allow(*, context):
    """Зі звірки: такі платежі не вважати помилкою («Дозволені без заявки») — додати виняток у налаштуваннях."""
    key, value = request.form.get("key", ""), request.form.get("value", "")
    if request.form.get("pick"):  # одна форма меню: кнопка — «ключ::значення»
        key, _, value = request.form["pick"].partition("::")
    raw_until = (request.form.get("until") or "").strip()
    try:
        until = date.fromisoformat(raw_until) if raw_until else None
    except ValueError:
        flash("«Діє до»: вкажіть дату", "error")
        return redirect(_next_url(url_for("payments.reconcile")))
    try:
        payments.allow(key, value, g.user, until)
    except wf.WorkflowError as e:
        flash(str(e), "error")
    else:
        what = {"operation": "вид операції", "payment_type": "вид оплати", "item": "стаття",
                "department": "відділ статті"}.get(key, "")
        shown = payment_accounts.label(value) if key == "payment_type" else value
        flash(f"Тепер платежі без заявки ({what} «{shown}»){f' до {until:%d.%m.%Y}' if until else ''} — «Дозволені без "
              "заявки». Змінити: «Налаштування → Інтеграція з 1С».", "success")
    return redirect(_next_url(url_for("payments.reconcile")))


def _next_url(default):
    nxt = request.form.get("next") or ""
    return nxt if nxt.startswith("/") and not nxt.startswith("//") else default


@bp.route("/payments/link", methods=["POST"])
@login_required
@requires(*wf.ACCOUNTANT_ROLES, "cfo", "admin")
def payment_link(*, context):
    """Прив'язати платіж з 1С до заявки вручну (з картки заявки або зі звірки за номером заявки)."""
    guid = (request.form.get("payment_guid") or "").strip().upper()
    raw = (request.form.get("request_id") or "").strip()
    number = (request.form.get("request_number") or "").strip().lstrip("#")
    request_id = int(raw) if raw.isdigit() else None
    if request_id is None and number.isdigit():
        request_id = db.ids_by_numbers([f"{int(number):09d}"]).get(f"{int(number):09d}")
    if not guid or not request_id:
        flash("Вкажіть платіж і номер заявки", "error")
        return redirect(_next_url(url_for("payments.reconcile")))
    try:
        req = payments.link_manual(guid, request_id, g.user, g.roles)
    except wf.WorkflowError as e:
        flash(str(e), "error")
        return redirect(_next_url(url_for("payments.reconcile")))
    flash(f"Платіж прив'язано до заявки № {req['number']}" + (" — заявку позначено оплаченою" if req["status"] == "paid" else ""),
          "success")
    return redirect(_next_url(url_for("requests.request_card", request_id=request_id)))


@bp.route("/requests/<int:request_id>/unlink-payment", methods=["POST"])
@login_required
@requires("admin")
def payment_unlink(request_id, *, context):
    """Адміністратор відв'язує помилково прив'язаний платіж; статус заявки — окремо, через «Змінити статус»."""
    payments.unlink((request.form.get("payment_guid") or "").strip().upper(), request_id, g.user)
    flash("Платіж відв'язано від заявки. Якщо заявку позначено оплаченою помилково — змініть статус нижче.", "success")
    return redirect(url_for("requests.request_card", request_id=request_id) + "#admin-status")
