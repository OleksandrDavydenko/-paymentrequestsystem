import os
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from dotenv import load_dotenv
from flask import Flask, abort, flash, redirect, render_template, request, url_for
from werkzeug.middleware.proxy_fix import ProxyFix
import identity.flask

import mock_data as db

load_dotenv()

app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.environ["FLASK_SECRET_KEY"],
    SESSION_TYPE="filesystem",  # сесії зберігаються на сервері, не в cookie
    SESSION_PERMANENT=False,
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
        "status": form.get("status") if form.get("status") in db.STATUSES else "draft",
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


# ---------------------------------------------------------------- маршрути

def _user(context):
    user = context["user"]
    return user.get("preferred_username", ""), user.get("name") or user.get("preferred_username", "")


@app.route("/")
def index():
    return redirect(url_for("requests_list"))


@app.route("/requests")
@auth.login_required
def requests_list(*, context):
    email, name = _user(context)
    status = request.args.get("status") if request.args.get("status") in db.STATUSES else ""
    return render_template(
        "requests_list.html",
        user_name=name,
        requests=db.list_requests(email, status or None),
        status_filter=status,
    )


@app.route("/requests/new", methods=["GET", "POST"])
@app.route("/requests/<int:request_id>", methods=["GET", "POST"])
@auth.login_required
def request_card(request_id=None, *, context):
    email, name = _user(context)
    if request_id is None:
        existing = {
            "id": None,
            "number": db.next_number(),
            "created_at": datetime.now(),
            "author_email": email,
            "author_name": name,
            "status": "draft",
            "payment_form": "bank",
            "currency": "UAH",
            "lines": [],
        }
    else:
        existing = db.get_request(request_id)
        if not existing or existing["author_email"].lower() != email.lower():
            abort(404)

    errors = {}
    req = existing
    if request.method == "POST":
        data, errors = parse_request_form(request.form)
        req = {**existing, **data}
        if not errors:
            for line in req["lines"]:
                line.pop("amount_raw", None)
            if req["id"] is None:
                req["created_at"] = datetime.now()
            new_id = db.save_request(req)
            flash(f"Заявку № {db.get_request(new_id)['number']} записано", "success")
            if request.form.get("action") == "ok":
                return redirect(url_for("requests_list"))
            return redirect(url_for("request_card", request_id=new_id))

    return render_template(
        "request_form.html",
        user_name=name,
        req=req,
        errors=errors,
        invoices=db.INVOICES,
    )


@app.route("/health")
def health():
    return {"status": "ok"}


if __name__ == "__main__":
    app.run(port=5000, debug=True)
