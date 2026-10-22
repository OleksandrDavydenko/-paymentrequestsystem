"""Заявка: розбір форми, маршрут погодження, вид оплати, контрагенти з 1С, учасники, документи."""
import logging
import os
import re
import uuid
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from flask import flash, g, request, url_for

import app_settings
import cfo_rules
import clock
import counterparties
import currencies
import expense_items
import filestore
import org_structure
import organizations
import payment_accounts
import rates
import storage as db
import user_directory
import workflow as wf

from web.formatting import fmt_datetime


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
    last = db.last_accountant_type(user["email"])
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
    """Чи погоджує фіндиректор цю заявку: режим з адмінки, для «за правилами» — cfo_rules.decide
    (тип бухгалтера, поріг у вибраній валюті за курсом 1С, новий контрагент, статті)."""
    mode = app_settings.get("cfo_mode")
    if mode == "observe":
        return {"cfo_step": False, "cfo_note": ""}
    if mode != "threshold":
        return {"cfo_step": True, "cfo_note": ""}
    step, note = cfo_rules.decide(req)
    return {"cfo_step": step, "cfo_note": note}


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


# Файли — у filestore: Azure Blob Storage (BLOB_ACCOUNT_URL + BLOB_CONTAINER) або папка на диску
def allowed_ext():
    """Дозволені формати документів («Налаштування → Заявка → Документи»)."""
    return set(app_settings.get("doc_extensions") or [])


def max_file_mb():
    return int(app_settings.get("doc_max_mb") or 10)


def doc_limits():
    """Для шаблону й documents.js: формати, розмір, підказка."""
    exts = list(app_settings.get("doc_extensions") or [])
    return {"ext": exts, "max_mb": max_file_mb(), "accept": ",".join("." + e for e in exts),
            "hint": ", ".join(e.upper() for e in exts) + f" — до {max_file_mb()} МБ кожен"}


def add_workdays(day, n):
    """Дата через n робочих днів (субота й неділя — вихідні)."""
    while n > 0:
        day += timedelta(days=1)
        if day.weekday() < 5:
            n -= 1
    return day


def submit_requirements(req):
    """Вимоги «Налаштування → Заявка», що перевіряються при «Відправити на погодження» ({поле: помилка})."""
    errors = {}
    days = int(app_settings.get("req_min_pay_days") or 0)
    if days and req.get("pay_date"):
        earliest = add_workdays(clock.today(), days)
        if req["pay_date"] < earliest:
            errors["pay_date"] = (f"Дата оплати — не раніше {earliest:%d.%m.%Y} "
                                  f"(через {days} робоч. дн.: стільки потрібно на погодження й оплату)")
    if app_settings.get("req_lines_required") and not req.get("lines"):
        errors["lines"] = "Додайте рахунок: номер і суму хоча б в одному рядку"
    mode = app_settings.get("req_document")
    if mode in ("always", "amount") and not req.get("attachments"):
        need, tail = True, ""
        if mode == "amount":
            cur = cfo_rules.get()["currency"]
            limit = int(app_settings.get("req_document_limit") or 0)
            value, _ = rates.convert(req.get("amount"), req.get("currency"), cur)
            need = value is None or value >= limit
            tail = f" (обов'язково для заявок від {wf.fmt_usd(limit)} {cur})"
        if need:
            errors["documents"] = ("Перед відправкою додайте документ — рахунок, договір чи акт" + tail +
                                   ". Збережіть заявку й прикріпіть файл у блоці «Документи» нижче.")
    return errors


INLINE_EXT = {"pdf", "jpg", "jpeg", "png"}  # відкриваються в браузері, решта — скачуються


def _back_target(path):
    """Сторінка, на яку повернути користувача, якщо дію з форми не виконано."""
    m = re.match(r"^/requests/(\d+)(/|$)", path)
    if m:
        return url_for("requests.request_card", request_id=int(m.group(1))) + ("#documents" if path.endswith("/files") else "")
    if path.startswith("/requests/new"):
        return url_for("requests.request_card")
    if path.startswith("/to-pay"):
        return url_for("payments.to_pay")
    if path.startswith("/admin/settings"):
        return url_for("admin.admin_settings")
    if path.startswith("/admin"):
        return url_for("admin.admin_users")
    if path.startswith("/settings"):
        return url_for("settings.notification_settings")
    return "/"


def _ext(filename):
    return filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


def _store_uploads(req, files, user, from_comment=False):
    """Зберегти файли з форми у сховище й додати до заявки. Повертає додані записи; про відхилені — flash."""
    added = []
    for f in files:
        ext = _ext(f.filename)
        f.stream.seek(0, os.SEEK_END)
        size = f.stream.tell()
        f.stream.seek(0)
        if ext not in allowed_ext():
            flash(f"«{f.filename}»: недопустимий тип файлу (дозволено: {', '.join(sorted(allowed_ext()))})", "error")
            continue
        if size > max_file_mb() * 1024 * 1024:
            flash(f"«{f.filename}»: файл більший за {max_file_mb()} МБ", "error")
            continue
        file_id = uuid.uuid4().hex
        try:
            filestore.save(file_id, ext, f.stream)
        except Exception:
            logging.getLogger(__name__).exception("Не вдалося зберегти файл %s", f.filename)
            flash(f"«{f.filename}»: не вдалося зберегти файл — спробуйте ще раз", "error")
            continue
        att = {"id": file_id, "name": os.path.basename(f.filename.replace("\\", "/")), "ext": ext, "size": size,
               "at": clock.now(), "user_name": user["name"], "user_email": user["email"]}
        if from_comment:
            att["from_comment"] = True
        req.setdefault("attachments", []).append(att)
        added.append(att)
    return added
