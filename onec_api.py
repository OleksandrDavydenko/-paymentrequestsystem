"""API для обробки 1С «Платіжки із заявок PayDesk»: 1С сама забирає заявки «До оплати», створює в себе платіжні
доручення й повідомляє PayDesk номер документа (або помилку). PayDesk у 1С нічого не пише.

Доступ — ключем X-Api-Key («Налаштування → API для 1С»): ключ показується один раз, у налаштуваннях зберігається
лише його SHA-256. Опис API — README, розділ «API для 1С». Обробки 1С у цьому репозиторії не зберігаються.
Відмітка 1С у заявці — req["onec_order"] = {"state": "created" / "failed", "doc_ref", "doc_number", "doc_date",
"doc_type", "at", "by", "message"}; кожна зміна — запис в історії заявки.
"""
import hashlib
import hmac
import re
import secrets
from datetime import date, timedelta

import app_settings
import clock
import counterparties
import currencies
import departments
import expense_items
import organizations
import payment_accounts
import payment_types
import storage as db
import workflow as wf

USER = {"name": "1С (обробка платіжок)", "email": ""}  # автор записів в історії заявки
STATES = ("new", "created", "failed", "cancelled", "all")
PURPOSE_MAX = 420  # довжина призначення платежу в платіжному дорученні
_HEX32 = re.compile(r"[0-9A-Fa-f]{32}")


# ---------------------------------------------------------------- ключ доступу

def enabled():
    return bool(app_settings.get("onec_api_enabled")) and bool(app_settings.get("onec_api_key_hash"))


def _hash(key):
    return hashlib.sha256(str(key or "").encode("utf-8")).hexdigest()


def new_key(user):
    """Створити новий ключ (старий перестає діяти). Повертає ключ — його показують адміністратору один раз."""
    key = "pd1c_" + secrets.token_urlsafe(32)
    by = user.get("email") or user.get("name") or ""
    app_settings.set("onec_api_key_hash", _hash(key), by)
    app_settings.set("onec_api_key_info", {"created_at": clock.now().isoformat(timespec="seconds"),
                                           "by": user.get("name") or by, "prefix": key[:9]}, by)
    return key


def key_ok(key):
    stored = app_settings.get("onec_api_key_hash") or ""
    return bool(stored and key) and hmac.compare_digest(_hash(key), stored)


def ip_allowed(ip):
    """Дозволені IP («Налаштування → API для 1С»; порожньо — будь-які)."""
    allowed = [x.strip() for x in re.split(r"[,\s;]+", app_settings.get("onec_api_ips") or "") if x.strip()]
    return not allowed or (ip or "") in allowed


_last = {}  # останнє звернення 1С (у пам'яті процесу — для сторінки налаштувань)


def touch(ip, path):
    _last.update(at=clock.now(), ip=ip, path=path)


def last_access():
    return dict(_last)


# ---------------------------------------------------------------- заявка → платіжка

def ref_1c(guid):
    """Посилання 1С (УникальныйИдентификатор) з GUID у форматі SQL (_IDRRef, 32 hex): у 1С байти переставлені.
    Не 32 hex (демо-коди, тимчасові контрагенти) — None."""
    h = str(guid or "").strip()
    if not _HEX32.fullmatch(h):
        return None
    h = h.lower()
    return f"{h[24:32]}-{h[20:24]}-{h[16:20]}-{h[0:4]}-{h[4:16]}"


def _ref(guid, **extra):
    return {"guid": guid or None, "ref": ref_1c(guid), **extra}


def purpose(req):
    """Призначення платежу: рахунки й договори (або стаття), в кінці — #номер заявки для звірки з платежами 1С."""
    marker = f"#{int(req['number'])}" if req.get("number") else ""
    lines = req.get("lines") or []
    invoices = [f"№ {ln['invoice']}" for ln in lines if ln.get("invoice")]
    deals = list(dict.fromkeys(ln["deal"] for ln in lines if ln.get("deal")))
    if invoices:
        text = "Оплата за рах. " + ", ".join(invoices) + ("; " + "; ".join(deals) if deals else "")
    else:
        text = "Оплата: " + (expense_items.get_name(req.get("expense_code")) or req.get("expense_code") or "")
    room = PURPOSE_MAX - len(marker) - 2
    return (text[:room].rstrip(" ;,") + (", " + marker if marker else "")).strip()


def state_of(req):
    """Стан платіжки 1С для заявки: new / created / failed / cancelled (платіжку створено, а заявка вже не до оплати)."""
    o = req.get("onec_order") or {}
    if o.get("state") == "created":
        return "created" if req["status"] in ("to_pay", "paid") else "cancelled"
    return "failed" if o.get("state") == "failed" else "new"


def order(req, base_url=""):
    """Заявка у форматі API («платіжка»)."""
    org = payment_accounts.organization_of(req.get("payment_account_id")) or (
        {"code": req["organization_id"]} if req.get("organization_id") else None)
    org_rec = organizations.get(org["code"]) if org else None
    cp_id = req.get("counterparty_id")
    cp = counterparties.get(cp_id)
    cur = next((c for c in currencies._table.items() if c["name"] == req.get("currency")), None)
    pt = payment_types.get(req.get("payment_account_id")) if payment_accounts.use_types() else None
    dep = req.get("department")
    approved = next((h["at"] for h in reversed(req.get("history") or []) if h.get("to_status") == "to_pay"), None)
    o = req.get("onec_order") or {}
    return {
        "request_id": req["id"], "number": req["number"], "marker": f"#{int(req['number'])}",
        "status": req["status"], "status_name": wf.STATUSES.get(req["status"], (req["status"],))[0],
        "payment_form": req.get("payment_form"), "pay_date": req["pay_date"].isoformat() if req.get("pay_date") else None,
        "amount": f"{req.get('amount') or 0:.2f}", "currency": req.get("currency"),
        "currency_ref": _ref(cur.get("guid") if cur else None, code=cur.get("code") if cur else None, name=req.get("currency")),
        "organization": _ref(org_rec.get("guid") if org_rec else None, code=org["code"] if org else None,
                             name=org_rec["name"] if org_rec else None, edrpou=org_rec.get("edrpou") if org_rec else None),
        "payment_type": _ref(req.get("payment_account_id") if pt else None, id=req.get("payment_account_id"),
                             name=payment_accounts.label(req.get("payment_account_id")) or None),
        "counterparty": _ref(cp_id if cp and cp.get("status") == "onec" else None, id=cp_id,
                             name=cp["name"] if cp else None, edrpou=(cp or {}).get("edrpou") or None,
                             code=(cp or {}).get("code") or None, in_1c=bool(cp) and cp.get("status") == "onec"),
        "expense_item": {"code": req.get("expense_code"), "name": expense_items.get_name(req.get("expense_code"))},
        "department": _ref(departments.guid_of(dep), code=dep, name=departments.get_name(dep) if dep else None),
        "invoices": [{"number": ln.get("invoice"), "amount": f"{ln['amount']:.2f}" if ln.get("amount") is not None else None,
                      "currency": ln.get("currency"), "deal": ln.get("deal") or None} for ln in req.get("lines") or []],
        "purpose": purpose(req),
        "note": req.get("note") or "",
        "author": {"name": req.get("author_name"), "email": req.get("author_email")},
        "approved_at": approved.isoformat(timespec="seconds") if approved else None,
        "url": f"{base_url.rstrip('/')}/requests/{req['id']}" if base_url else None,
        "onec_state": state_of(req),
        "onec_order": {k: (v.isoformat(timespec="seconds") if hasattr(v, "isoformat") else v) for k, v in o.items()} or None,
    }


def orders(state="new", form="", organization=""):
    """Заявки для обробки: state — new / created / failed / cancelled / all; form — bank / cash / порожньо (усі)."""
    ids = db.payment_candidate_ids(clock.today())  # «До оплати» (оплачені — лише сьогоднішні)
    reqs = list(db.get_many(ids).values())
    if state in ("created", "cancelled", "all"):
        # платіжку створено, а заявку потім повернули чи скасували — щоб 1С могла позначити документ на видалення
        reqs += [r for r in _with_orders() if r["id"] not in {x["id"] for x in reqs}]
    out = []
    for r in sorted(reqs, key=lambda r: (r.get("pay_date") or date.max, r["id"])):
        st = state_of(r)
        if state != "all" and st != state or state == "all" and r["status"] != "to_pay" and st != "cancelled":
            continue
        if state in ("new", "failed") and r["status"] != "to_pay":
            continue
        if form and r.get("payment_form") != form:
            continue
        if organization:
            o = order(r)["organization"]
            if organization not in (o.get("code"), o.get("guid"), o.get("ref")):
                continue
        out.append(r)
    return out


def _with_orders(days=180):
    """Заявки з відміткою 1С (для «cancelled»): повернуті / відхилені / скасовані після створення платіжки."""
    today = clock.today()
    rows = db.scope_rows("all", wf.RoleSet({"admin"}), "", period=("created", today - timedelta(days=days), today))
    ids = [r["id"] for r in rows if r["status"] not in ("to_pay", "paid", "draft")][:500]
    return [r for r in db.get_many(ids).values() if (r.get("onec_order") or {}).get("state") == "created"]


# ---------------------------------------------------------------- відмітки від 1С

class ApiError(Exception):
    def __init__(self, message, status=409):
        super().__init__(message)
        self.status = status


def _update(request_id, change):
    """Змінити заявку з повторами, якщо її саме редагують. change(req) кидає ApiError або змінює req."""
    for _ in range(3):
        req = db.get_request(request_id)
        if not req:
            raise ApiError("Заявку не знайдено", 404)
        result = change(req)
        if result is False:
            return req  # нічого не змінилося (повторний виклик)
        try:
            db.save_request(req)
            return db.get_request(request_id)
        except db.StaleRequestError:
            continue
    raise ApiError("Заявку саме змінюють — повторіть запит", 409)


def _who(data):
    name = str(data.get("user") or "").strip()[:100]
    return {"name": f"1С: {name}" if name else USER["name"], "email": ""}


def mark_created(request_id, data):
    doc_ref = str(data.get("doc_ref") or "").strip()[:64]
    doc_number = str(data.get("doc_number") or "").strip()[:50]
    if not doc_number:
        raise ApiError("Вкажіть doc_number — номер документа в 1С", 400)

    def change(req):
        o = req.get("onec_order") or {}
        if req["status"] != "to_pay":
            raise ApiError(f"Заявка № {req['number']} не «До оплати» ({wf.STATUSES[req['status']][0]}) — платіжку не створюють")
        if o.get("state") == "created":
            if o.get("doc_ref") == doc_ref and o.get("doc_number") == doc_number:
                return False
            if not data.get("replace"):
                raise ApiError(f"Для заявки вже створено платіжку № {o.get('doc_number')} — передайте replace: true, "
                               "якщо її замінили новою")
        user = _who(data)
        req["onec_order"] = {"state": "created", "doc_ref": doc_ref or None, "doc_number": doc_number,
                             "doc_date": str(data.get("doc_date") or "")[:10] or None,
                             "doc_type": str(data.get("doc_type") or "").strip()[:100] or None,
                             "at": clock.now(), "by": user["name"], "message": ""}
        when = f" від {req['onec_order']['doc_date']}" if req["onec_order"]["doc_date"] else ""
        wf.add_history(req, "onec_order", user, wf.accountant_role(req) or "acc_resident",
                       f"Платіжку створено в 1С: № {doc_number}{when}", req["status"], req["status"])
    return _update(request_id, change)


def mark_failed(request_id, data):
    message = str(data.get("message") or "").strip()[:500] or "1С не змогла створити платіжку"

    def change(req):
        o = req.get("onec_order") or {}
        if o.get("state") == "created":
            raise ApiError(f"Для заявки вже створено платіжку № {o.get('doc_number')} — помилку не записано")
        if o.get("state") == "failed" and o.get("message") == message:
            return False
        user = _who(data)
        req["onec_order"] = {"state": "failed", "at": clock.now(), "by": user["name"], "message": message}
        wf.add_history(req, "onec_order_failed", user, wf.accountant_role(req) or "acc_resident",
                       f"1С не створила платіжку: {message}", req["status"], req["status"])
    return _update(request_id, change)


def reset(request_id, data):
    """Платіжку в 1С видалили / позначили на видалення — заявка знову «нова» для обробки."""
    reason = str(data.get("reason") or "").strip()[:300]

    def change(req):
        o = req.get("onec_order")
        if not o:
            return False
        user = _who(data)
        req.pop("onec_order", None)
        wf.add_history(req, "onec_order_reset", user, wf.accountant_role(req) or "acc_resident",
                       f"Відмітку 1С знято (платіжка № {o.get('doc_number') or '—'})" + (f": {reason}" if reason else ""),
                       req["status"], req["status"])
    return _update(request_id, change)
