"""Похідні поля заявки: «легкий рядок» для списків і окремі колонки в базі.

Списки (фільтри, пошук, кількості, суми, сортування) працюють з легкими рядками — без історії, файлів
і рядків рахунків. Повна заявка завантажується лише для рядків, які показуються на сторінці.

У пам'яті (демо, тести) легкий рядок обчислюється з заявки щоразу. У базі — зберігається колонками
при кожному збереженні (storage/sql.py) і перераховується для всіх заявок, коли змінюються довідники
(reindex): канал оплати, бухгалтер і «Платимо з» залежать від налаштувань організацій.
"""
import organizations
import payment_accounts
import workflow as wf

# Заявки «в роботі»: чекають рішення, оплати або підтвердження передачі готівки. Їх небагато,
# тож у базі вони тримаються в пам'яті процесу (storage/sql.py), решта — читаються запитами.
ACTIVE_STATUSES = frozenset({"dept_head", "approval", "accountant", "cfo", "to_pay"})


def is_active(req):
    return req["status"] in ACTIVE_STATUSES or wf.needs_handover(req)


def pay_org_code(req):
    """З якої організації платимо: організація вибраного виду оплати, інакше — організація заявки."""
    o = payment_accounts.organization_of(req.get("payment_account_id"))
    if o:
        return o["code"]
    return req.get("organization_id") or None


def pay_org_name(code):
    return organizations.name(code) if code else ""


def actor_role(req):
    """Хто діє на поточному етапі (для черги в базі): роль, "author" / "parallel" або None."""
    return wf.stage_actor(req)


def pending_key(req):
    """Паралельні погоджувачі, які ще не погодили, рядком «|acc_resident|cfo|» (для пошуку LIKE у базі)."""
    if req["status"] != "approval":
        return ""
    roles = wf.pending_roles(req)
    return "|" + "|".join(roles) + "|" if roles else ""


def light(req):
    """Легкий рядок заявки: усе, що потрібно спискам, без історії й вкладень."""
    paid = wf.paid_entry(req) if req["status"] == "paid" else None
    history = req.get("history") or []
    handover = req.get("handover") or {}
    return {
        "id": req["id"],
        "number": req["number"],
        "status": req["status"],
        "author_email": req["author_email"],
        "author_name": req.get("author_name"),
        "department": req.get("department"),
        "created_at": req.get("created_at"),
        "pay_date": req.get("pay_date"),
        "paid_at": paid["at"] if paid else None,
        "last_at": history[-1]["at"] if history else req.get("created_at"),
        "organization_id": req.get("organization_id"),
        "counterparty_id": req.get("counterparty_id"),
        "expense_code": req.get("expense_code"),
        "payment_form": req.get("payment_form"),
        "channel": wf.channel_of(req),
        "accountant_type": req.get("accountant_type"),
        "accountant_role": wf.accountant_role(req),
        "currency": req.get("currency"),
        "amount": req.get("amount"),
        "payment_account_id": req.get("payment_account_id"),
        "pay_org": pay_org_code(req),
        "note": req.get("note") or "",
        "handover_status": handover.get("status"),
        "handover_since": handover.get("since"),
    }


def columns(req):
    """Колонки таблиці requests (крім id і data), які обчислюються з заявки."""
    row = light(req)
    row.pop("id")
    row.update(author_lc=(req["author_email"] or "").lower(), actor_role=actor_role(req),
               pending_roles=pending_key(req), active=is_active(req))
    return row
