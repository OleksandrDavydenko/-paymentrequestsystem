import os
import re
import uuid
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from dotenv import load_dotenv
from flask import Flask, abort, flash, redirect, render_template, request, send_file, session, url_for
from werkzeug.middleware.proxy_fix import ProxyFix
import identity.flask

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
        "EXPENSE_TYPES": db.EXPENSE_TYPES,
        "CURRENCIES": db.CURRENCIES,
        "PAYMENT_FORMS": db.PAYMENT_FORMS,
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


def parse_request_form(form):
    """Повертає (дані заявки, словник помилок). Автор/дата/номер не з форми."""
    errors = {}
    data = {
        "counterparty_id": _parse_ref(form.get("counterparty_id"), db.COUNTERPARTIES),
        "organization_id": _parse_ref(form.get("organization_id"), db.ORGANIZATIONS),
        "expense_type_id": _parse_ref(form.get("expense_type_id"), db.EXPENSE_TYPES),
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
        "expense_type_id": "Оберіть вид витрат",
        "payment_form": "Оберіть форму оплати",
        "currency": "Оберіть валюту",
    }
    for field, message in required.items():
        if data[field] is None:
            errors[field] = message
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


# ---------------------------------------------------------------- користувач і роль

# Мок-режим: роль вибирається вручну в шапці. Пізніше — App Roles з токена Entra ID.
ROLE_SWITCHER = os.environ.get("ROLE_SWITCHER", "1") == "1"

# Які статуси бачить у черзі кожна роль погоджувача
QUEUE_STATUSES = {
    "head": {"head"},
    "accountant": {"accountant", "to_pay"},
    "cfo": {"cfo"},
}


def _user(context):
    user = context["user"]
    email = user.get("preferred_username", "")
    return {"email": email, "name": user.get("name") or email}


def current_role():
    role = session.get("role", "initiator") if ROLE_SWITCHER else "initiator"
    return role if role in wf.ROLES else "initiator"


@app.context_processor
def inject_role():
    role = current_role()
    queue = db.list_for_stage(QUEUE_STATUSES[role]) if role in QUEUE_STATUSES else []
    return {"role": role, "ROLES": wf.ROLES, "ROLE_SWITCHER": ROLE_SWITCHER, "queue_count": len(queue)}


def _load_visible(request_id, user, role):
    req = db.get_request(request_id)
    if not req or not wf.can_view(req, role, user["email"]):
        abort(404)
    return req


# ---------------------------------------------------------------- маршрути

@app.route("/")
def index():
    return redirect(url_for("requests_list"))


@app.route("/role", methods=["POST"])
@auth.login_required
def set_role(*, context):
    role = request.form.get("role")
    if ROLE_SWITCHER and role in wf.ROLES:
        session["role"] = role
    return redirect(url_for("approvals") if role in QUEUE_STATUSES else url_for("requests_list"))


@app.route("/requests")
@auth.login_required
def requests_list(*, context):
    user = _user(context)
    status = request.args.get("status") if request.args.get("status") in db.STATUSES else ""
    return render_template(
        "requests_list.html",
        user_name=user["name"],
        requests=db.list_requests(user["email"], status or None),
        status_filter=status,
    )


@app.route("/approvals")
@auth.login_required
def approvals(*, context):
    user = _user(context)
    rows = db.list_for_stage(QUEUE_STATUSES.get(current_role(), set()))
    return render_template("approvals.html", user_name=user["name"], requests=rows)


@app.route("/requests/new", methods=["GET", "POST"])
@app.route("/requests/<int:request_id>", methods=["GET", "POST"])
@auth.login_required
def request_card(request_id=None, *, context):
    user = _user(context)
    role = current_role()
    if request_id is None:
        if role != "initiator":
            flash("Створювати заявки може роль «Ініціатор»", "error")
            return redirect(url_for("requests_list"))
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
        existing = _load_visible(request_id, user, role)

    errors = {}
    req = existing
    if request.method == "POST":
        if not wf.can_edit(existing, role, user["email"]):
            flash("Заявку в цьому статусі редагувати не можна", "error")
            return redirect(url_for("request_card", request_id=request_id))
        data, errors = parse_request_form(request.form)
        req = {**existing, **data}
        if not errors:
            for line in req["lines"]:
                line.pop("amount_raw", None)
            action = request.form.get("action")
            if req["id"] is None:
                req["created_at"] = datetime.now()
                wf.add_history(req, "create", user, role, to_status="draft")
            if action == "submit":
                wf.apply_action(req, "submit", role, user)
            new_id = db.save_request(req)
            number = db.get_request(new_id)["number"]
            if action == "submit":
                flash(f"Заявку № {number} відправлено на погодження", "success")
                return redirect(url_for("requests_list"))
            flash(f"Заявку № {number} записано", "success")
            if action == "ok":
                return redirect(url_for("requests_list"))
            return redirect(url_for("request_card", request_id=new_id))

    available = wf.available_actions(req, role, user["email"])
    return render_template(
        "request_form.html",
        user_name=user["name"],
        req=req,
        errors=errors,
        invoices=db.INVOICES,
        editable=wf.can_edit(req, role, user["email"]),
        can_attach=req["id"] is not None and wf.can_attach(req, role, user["email"]),
        actions=[a for a in available if a != "submit"],
        can_submit="submit" in available,
        stages=wf.stage_states(req),
        ACTIONS=wf.ACTIONS,
        CLOSED_STATUSES=wf.CLOSED_STATUSES,
        draft_comment=session.pop("draft_comment", ""),
        current_email=user["email"],
    )


@app.route("/requests/<int:request_id>/action", methods=["POST"])
@auth.login_required
def request_action(request_id, *, context):
    user = _user(context)
    role = current_role()
    req = _load_visible(request_id, user, role)
    action = request.form.get("action")
    try:
        wf.apply_action(req, action, role, user, request.form.get("comment"))
    except wf.WorkflowError as e:
        flash(str(e), "error")
        session["draft_comment"] = request.form.get("comment", "")
        return redirect(url_for("request_card", request_id=request_id))
    db.save_request(req)
    flash(f"Заявка № {req['number']}: {wf.ACTIONS[action][1].lower()}", "success")
    return redirect(url_for("approvals") if role in QUEUE_STATUSES else url_for("requests_list"))


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
    return redirect(request.referrer or url_for("requests_list"))


def _ext(filename):
    return filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


@app.route("/requests/<int:request_id>/files", methods=["POST"])
@auth.login_required
def upload_files(request_id, *, context):
    user = _user(context)
    role = current_role()
    req = _load_visible(request_id, user, role)
    if not wf.can_attach(req, role, user["email"]):
        abort(403)
    files = [f for f in request.files.getlist("files") if f and f.filename]
    if not files:
        flash("Оберіть файл", "error")
        return redirect(url_for("request_card", request_id=request_id))

    os.makedirs(UPLOAD_DIR, exist_ok=True)
    added = []
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
def download_file(file_id, *, context):
    user = _user(context)
    req, att = db.find_attachment(file_id)
    if not req or not wf.can_view(req, current_role(), user["email"]):
        abort(404)
    path = os.path.join(UPLOAD_DIR, f"{att['id']}.{att['ext']}")
    if not os.path.exists(path):
        abort(404)
    return send_file(path, download_name=att["name"], as_attachment=att["ext"] not in INLINE_EXT)


@app.route("/files/<file_id>/delete", methods=["POST"])
@auth.login_required
def delete_file(file_id, *, context):
    user = _user(context)
    role = current_role()
    req, att = db.find_attachment(file_id)
    if not req or not wf.can_view(req, role, user["email"]):
        abort(404)
    if att["user_email"].lower() != user["email"].lower() or req["status"] in wf.CLOSED_STATUSES:
        abort(403)
    req["attachments"] = [a for a in req["attachments"] if a["id"] != file_id]
    wf.add_history(req, "file_delete", user, role, comment=att["name"])
    db.save_request(req)
    try:
        os.remove(os.path.join(UPLOAD_DIR, f"{att['id']}.{att['ext']}"))
    except FileNotFoundError:
        pass
    flash(f"Документ «{att['name']}» видалено", "success")
    return redirect(url_for("request_card", request_id=req["id"]) + "#documents")


@app.template_filter("filesize")
def filesize(n):
    return f"{n / 1024 / 1024:.1f} МБ" if n >= 1024 * 1024 else f"{max(1, round(n / 1024))} КБ"


@app.route("/health")
def health():
    return {"status": "ok"}


if __name__ == "__main__":
    app.run(port=5000, debug=True)
