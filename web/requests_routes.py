"""Заявки: «Мої заявки», картка, дії, коментарі, документи, учасники, контрагенти."""
import io
import logging
import mimetypes

from flask import Blueprint, abort, flash, g, redirect, render_template, request, send_file, session, url_for

import app_settings
import clock
import counterparties
import departments
import expense_items
import filestore
import notifications
import org_structure
import organizations
import payment_accounts
import storage as db
import user_budget
import user_directory
import workflow as wf

from web.auth import auth, _home, _load_visible, requires
from web.listing import _filtered, _page, _parse_date, _ref_keys
from web.request_helpers import INLINE_EXT, _apply_participants, _apply_payment_account, _can_link_cp, _default_accountant, _link_counterparty, _participants_state, _pay_guard, _route_settings, _store_uploads, _try_live_link, parse_request_form


bp = Blueprint("requests", __name__)


@bp.route("/")
@auth.login_required
@requires()
def index(*, context):
    return redirect(_home())


@bp.route("/home")
@auth.login_required
@requires()
def _home_redirect(*, context):
    return redirect(_home())


@bp.route("/requests")
@auth.login_required
@requires()
def requests_list(*, context):
    email = g.user["email"]
    participating_total = db.count("participant", g.roles, email)
    view = request.args.get("view") if request.args.get("view") in ("participant", "paid") else "mine"
    if "initiator" not in g.roles:
        view = "participant"
    if view == "participant" and not participating_total and "initiator" not in g.roles:
        return redirect(_home())
    # «Створені мною» — заявки в роботі, без оплачених; оплачені — окремою вкладкою, найсвіжіша оплата першою
    scope = {"participant": "participant", "paid": "mine_paid"}.get(view, "mine")
    found, fbar = _filtered(db.scope_rows(scope, g.roles, email),
                            _ref_keys(show_dep=False, extra_before=() if view == "paid" else ("status",)))
    rows, page = _page(found)
    return render_template(
        "requests_list.html",
        user_name=g.user["name"],
        requests=rows,
        page=page,
        fbar=fbar,
        view=view,
        participating_total=participating_total,
        handover_count=db.count("handover_mine", g.roles, email),
        paid_entry=wf.paid_entry,
    )


@bp.route("/requests/new", methods=["GET", "POST"])
@bp.route("/requests/<int:request_id>", methods=["GET", "POST"])
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
            "number": None,  # номер присвоює сховище при першому збереженні
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
            return redirect(url_for("requests.request_card", request_id=request_id))
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
                return redirect(url_for("requests.requests_list"))
            flash(f"Заявку № {number} записано", "success")
            if action == "ok":
                return redirect(url_for("requests.requests_list"))
            return redirect(url_for("requests.request_card", request_id=new_id))

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
        # Автор має роль погоджувача, але погоджувати власну заявку заборонено (налаштування адміністратора)
        self_blocked=wf.self_approval_blocked(req, user["email"])
        and wf.acting_role(req, roles, user["email"], ignore_self=True) is not None,
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
        attachments_by_id={a["id"]: a for a in req.get("attachments") or []},
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
        back_url=url_for("requests.requests_list") if wf.is_author(req, user["email"]) and "initiator" in roles
        else _home(),
    )


@bp.route("/requests/<int:request_id>/payment-account", methods=["POST"])
@auth.login_required
@requires(*wf.ACCOUNTANT_ROLES, "cfo", "admin")
def request_payment_account(request_id, *, context):
    req = _load_visible(request_id)
    try:
        changed = _apply_payment_account(req, request.form.get("payment_account_id", ""), g.user, g.roles)
    except wf.WorkflowError as e:
        flash(str(e), "error")
        return redirect(url_for("requests.request_card", request_id=request_id) + "#payment-account")
    if changed:
        db.save_request(req)
        label = payment_accounts.label(req.get("payment_account_id")) or "не вибрано"
        flash(f"Вид оплати: {label}", "success")
    else:
        flash("Змін немає", "success")
    return redirect(url_for("requests.request_card", request_id=request_id) + "#payment-account")


@bp.route("/requests/<int:request_id>/handover", methods=["POST"])
@auth.login_required
@requires()
def request_handover(request_id, *, context):
    """Підтвердження передачі готівки підряднику (автор, а за нього — бухгалтер по готівці чи адмін)."""
    req = _load_visible(request_id)
    back = request.form.get("next")
    back = back if back and back.startswith("/") and not back.startswith("//") else \
        url_for("requests.request_card", request_id=request_id) + "#handover"
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


@bp.route("/counterparties/search")
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


@bp.route("/counterparties/new", methods=["POST"])
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


@bp.route("/counterparties/<cp_id>/check", methods=["POST"])
@auth.login_required
@requires(*wf.ACCOUNTANT_ROLES, "cfo", "admin")
def counterparty_check(cp_id, *, context):
    """«Перевірити в 1С зараз»: живий запит за ЄДРПОУ і автоприв'язка."""
    if not _can_link_cp(g.roles):
        abort(403)
    nxt = request.form.get("next") or ""
    back = nxt if nxt.startswith("/") and not nxt.startswith("//") and "\\" not in nxt else url_for("requests._home_redirect")
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


@bp.route("/counterparties/<cp_id>/link", methods=["POST"])
@auth.login_required
@requires(*wf.ACCOUNTANT_ROLES, "cfo", "admin")
def counterparty_link(cp_id, *, context):
    if not _can_link_cp(g.roles):
        abort(403)
    nxt = request.form.get("next") or ""
    # Лише відносна адреса всередині системи (без переходу на сторонні сайти)
    back = nxt if nxt.startswith("/") and not nxt.startswith("//") and "\\" not in nxt else url_for("requests._home_redirect")
    try:
        role = next((r for r in (*sorted(wf.ACCOUNTANT_ROLES), "cfo", "admin") if r in g.roles), "admin")
        pending, target, count = _link_counterparty(cp_id, request.form.get("onec_id") or "", g.user, role)
    except (ValueError, TypeError) as e:
        flash(str(e) or "Оберіть контрагента з 1С", "error")
        return redirect(back)
    flash(f"Контрагента «{pending['name']}» прив'язано до «{target['name']}» з 1С (заявок: {count})", "success")
    return redirect(back)


@bp.route("/users/search")
@auth.login_required
@requires()
def users_search(*, context):
    """Підказки для вибору учасника заявки: користувачі системи (з роллю)."""
    me = g.user["email"].lower()
    return {"users": [{"email": u["email"], "name": u["name"]}
                      for u in user_directory.search(request.args.get("q", "")) if u["email"].lower() != me]}


@bp.route("/requests/<int:request_id>/participants", methods=["POST"])
@auth.login_required
@requires()
def request_participants(request_id, *, context):
    req = _load_visible(request_id)
    back = redirect(url_for("requests.request_card", request_id=request_id) + "#participants")
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


@bp.route("/requests/<int:request_id>/action", methods=["POST"])
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
        return redirect(url_for("requests.request_card", request_id=request_id))
    db.save_request(req)
    notifications.notify(action, req, g.user)
    flash(f"Заявка № {req['number']}: {wf.ACTIONS[action][1].lower()}", "success")
    return redirect(url_for("payments.to_pay") if action == "pay" or request.form.get("next") == "to_pay"
                    else url_for("lists.approvals"))


@bp.route("/requests/<int:request_id>/comment", methods=["POST"])
@auth.login_required
@requires()
def request_comment(request_id, *, context):
    req = _load_visible(request_id)
    files = [f for f in request.files.getlist("files") if f and f.filename]
    text = (request.form.get("comment") or "").strip()
    if not text and not files:
        flash("Напишіть текст коментаря або прикріпіть файл", "error")
        return redirect(url_for("requests.request_card", request_id=request_id) + "#comment")
    if len(text) > wf.MAX_COMMENT:
        flash(f"Коментар задовгий (максимум {wf.MAX_COMMENT} символів)", "error")
        session["comment_draft"] = text
        return redirect(url_for("requests.request_card", request_id=request_id) + "#comment")
    stored = _store_uploads(req, files, g.user, from_comment=True)
    if files and not stored and not text:
        session["comment_draft"] = text
        return redirect(url_for("requests.request_card", request_id=request_id) + "#comment")
    try:
        wf.add_comment(req, g.user, wf.history_role(req, g.roles, g.user["email"]), text, [a["id"] for a in stored])
    except wf.WorkflowError as e:
        flash(str(e), "error")
        session["comment_draft"] = text
        return redirect(url_for("requests.request_card", request_id=request_id) + "#comment")
    try:
        db.save_request(req)
    except db.StaleRequestError:
        session["comment_draft"] = text  # текст не губиться: він повернеться в поле коментаря
        raise
    names = ", ".join(a["name"] for a in stored)
    notifications.notify("comment", req, g.user, comment=text + (("\n" if text else "") + "📎 " + names if names else ""))
    flash("Коментар додано" + (f" (файли: {names})" if names else ""), "success")
    return redirect(url_for("requests.request_card", request_id=request_id) + "#history")


@bp.route("/requests/<int:request_id>/admin-status", methods=["POST"])
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
        return redirect(url_for("requests.request_card", request_id=request_id) + "#admin-status")
    flash(f"Заявка № {req['number']}: статус змінено на «{wf.STATUSES[new_status][0]}»", "success")
    return redirect(url_for("requests.request_card", request_id=request_id))


@bp.route("/requests/<int:request_id>/files", methods=["POST"])
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
        return redirect(url_for("requests.request_card", request_id=request_id))

    role = wf.history_role(req, roles, user["email"])
    added = []
    for att in _store_uploads(req, files, user):
        wf.add_history(req, "file_add", user, role, comment=att["name"])
        added.append(att["name"])
    if added:
        db.save_request(req)
        flash("Додано: " + ", ".join(added), "success")
    return redirect(url_for("requests.request_card", request_id=request_id) + "#documents")


@bp.route("/files/<file_id>")
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
                     as_attachment=att["ext"] not in INLINE_EXT or request.args.get("download") == "1")


@bp.route("/files/<file_id>/delete", methods=["POST"])
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
    return redirect(url_for("requests.request_card", request_id=req["id"]) + "#documents")
