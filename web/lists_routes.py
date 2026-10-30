"""Списки: «На погодження», «Усі заявки», «Заявки відділу»."""
from datetime import timedelta

from flask import Blueprint, abort, g, redirect, render_template, request, url_for

import clock
import departments
import storage as db
import workflow as wf

from web.auth import login_required, requires
from web.listing import RECENT_DAYS, _date_filter, _filtered, _in_period, _page, _ref_keys, currency_totals, recent_presets


bp = Blueprint("lists", __name__)


@bp.route("/approvals")
@login_required
@requires(*wf.APPROVER_ROLES)
def approvals(*, context):
    if not g.roles & wf.APPROVER_ROLES - wf.pays_only(g.roles):  # бухгалтер «Лише оплачує»: погодження немає
        return redirect(url_for("payments.to_pay"))
    view = request.args.get("view") if request.args.get("view") in ("processed", "accountants") else "queue"
    if view == "accountants" and "cfo" not in g.roles:
        abort(403)
    scope = {"processed": "processed", "accountants": "at_accountants", "queue": "queue"}[view]
    # «Опрацьовані» — архів рішень: за замовчуванням за останні 12 місяців
    all_time = request.args.get("period") == "all"
    since = None if all_time or view != "processed" else clock.today() - timedelta(days=RECENT_DAYS)
    source = db.scope_rows(scope, g.roles, g.user["email"], period=("decided", since, None) if since else None)
    if since:
        source = [r for r in source if r["sort_at"].date() >= since]
    found, fbar = _filtered(source, _ref_keys(extra_before=("form",)))
    rows, page = _page(found)
    return render_template(
        "approvals.html",
        user_name=g.user["name"],
        requests=rows,
        page=page,
        fbar=fbar,
        view=view,
        processed_entry=lambda r: wf.processed_entry(r, g.roles),
        all_time=all_time,
        accountants_count=db.count("at_accountants") if "cfo" in g.roles else 0,
        accountant_role=wf.accountant_role,
        ACTIONS=wf.ACTIONS,
    )


@bp.route("/admin/requests")
@login_required
@requires("admin", "cfo")
def admin_requests(*, context):
    return _requests_overview("all", title="Усі заявки", table_id="all-requests")


@bp.route("/department")
@login_required
@requires("dept_head", "dept_viewer")
def department_requests(*, context):
    deps = sorted(wf.head_deps(g.roles) | wf.view_deps(g.roles), key=lambda c: departments.get_name(c).lower())
    title = "Заявки відділу" + (": " + ", ".join(departments.get_name(c) for c in deps) if deps else "")
    return _requests_overview("department", title=title, table_id="department-requests", no_departments=not deps,
                              show_dep_filter=len(deps) > 1)


def _requests_overview(scope, title, table_id, **extra):
    today = clock.today()
    args = request.args
    date_field = "pay" if args.get("date_field") == "pay" else "created"
    period, d_from, d_to = _date_filter(args, today, recent=True, remember=f"requests_{scope}_period")
    source = db.scope_rows(scope, g.roles, g.user["email"], period=(date_field, d_from, d_to))
    in_period = [r for r in source
                 if _in_period(r["pay_date"] if date_field == "pay" else r["created_at"].date(), d_from, d_to)]
    found, fbar = _filtered(in_period, _ref_keys(show_dep=extra.get("show_dep_filter", True),
                                                 extra_before=("status", "form")))
    rows, page = _page(found)
    return render_template(
        "admin_requests.html",
        title=title,
        table_id=table_id,
        **extra,
        user_name=g.user["name"],
        requests=rows,
        page=page,
        totals=currency_totals(found, today),
        today=today,
        fbar=fbar,
        date_field=date_field,
        period=period,
        d_from=d_from,
        d_to=d_to,
        presets=recent_presets(today),
        recent_default=True,
        ACTIONS=wf.ACTIONS,
    )
