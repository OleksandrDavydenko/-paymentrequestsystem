"""Списки заявок («області»): хто що бачить і в якому порядку.

Правила видимості — функції workflow.py; тут вони лише зібрані за назвами списків. Це еталон:
сховище в пам'яті застосовує їх напряму, а сховище в базі для великих списків будує рівноцінні
SQL-умови (storage/sql.py) — тест еквівалентності перевіряє, що результати однакові.

Активні області (заявки «в роботі», їх небагато) обчислюються завжди в Python; архівні (усі заявки
за роки) — у базі запитом.
"""
import workflow as wf

from . import derive

ACTIVE = {"queue", "at_accountants", "to_pay", "handover", "handover_all", "handover_mine", "awaiting_decision"}
ARCHIVE = {"all", "author", "mine", "mine_paid", "participant", "department", "paid", "processed"}
SCOPES = ACTIVE | ARCHIVE


def match(scope, req, roles, email):
    """Чи входить заявка до списку scope для користувача (roles — RoleSet, email — його пошта)."""
    if scope == "all":
        return True
    if scope == "author":
        return wf.is_author(req, email)
    if scope == "mine":
        return wf.is_author(req, email) and req["status"] != "paid"
    if scope == "mine_paid":
        return wf.is_author(req, email) and req["status"] == "paid"
    if scope == "participant":
        return wf.is_participant(req, email)
    if scope == "department":
        return wf.in_department(req, roles)
    if scope == "paid":
        return wf.in_paid(req, roles)
    if scope == "processed":
        return wf.processed_entry(req, roles) is not None
    if scope == "queue":
        return wf.in_queue(req, roles, email)
    if scope == "at_accountants":
        return wf.at_accountants(req)
    if scope == "to_pay":
        return wf.in_payments(req, roles)
    if scope == "handover":
        return wf.needs_handover(req) and wf.is_handover_accountant(req, roles)
    if scope == "handover_all":
        return wf.needs_handover(req)
    if scope == "awaiting_decision":  # на будь-якому етапі погодження (нагадування погоджувачам)
        return req["status"] in wf.APPROVAL_STAGES
    if scope == "handover_mine":
        return wf.needs_handover(req) and wf.is_author(req, email)
    raise ValueError(f"Невідомий список {scope}")


def processed_at(req, roles):
    entry = wf.processed_entry(req, roles)
    return entry["at"] if entry else None


def sort_rows(scope, rows):
    """Порядок списку (на легких рядках). Однакові значення — за номером, щоб порядок був стабільним."""
    rows.sort(key=lambda r: r["id"])
    if scope in ("all", "author", "mine", "participant", "department"):
        rows.sort(key=lambda r: r["created_at"], reverse=True)          # нові — першими
    elif scope in ("mine_paid", "paid"):
        rows.sort(key=lambda r: r["paid_at"] or r["created_at"], reverse=True)   # найсвіжіша оплата — першою
    elif scope == "processed":
        rows.sort(key=lambda r: r["sort_at"], reverse=True)              # найсвіжіше рішення — першим
    elif scope in ("queue", "at_accountants", "awaiting_decision"):
        rows.sort(key=lambda r: r["last_at"])                            # найдовше чекають — першими
    elif scope == "to_pay":
        rows.sort(key=lambda r: r["pay_date"])                           # найближча оплата — першою
    elif scope in ("handover", "handover_all", "handover_mine"):
        rows.sort(key=lambda r: r["handover_since"])
    return rows


def light_rows(scope, reqs, roles, email):
    """Легкі рядки списку з повних заявок (сховище в пам'яті й активні заявки в базі)."""
    rows = []
    for req in reqs:
        if match(scope, req, roles, email):
            row = derive.light(req)
            if scope == "processed":
                row["sort_at"] = processed_at(req, roles)
            rows.append(row)
    return sort_rows(scope, rows)
