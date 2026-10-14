"""Email-сповіщення про події із заявками: кому, про що і з яким текстом.

notify(event, req, actor) викликається після дії в системі (у контексті запиту Flask).
Листи йдуть, лише якщо адмін увімкнув розсилку і вказав відправника, а одержувач
не вимкнув цю подію. Самому автору дії листи не надсилаються.
"""
import logging
import os
import time

from flask import render_template, url_for

import app_settings
import counterparties
import departments
import mailer
import org_structure
import secret_box
import user_prefs
import workflow as wf

logger = logging.getLogger(__name__)

# Подія в системі -> подія налаштувань автора заявки
AUTHOR_EVENTS = {
    "approve": "my_approved",
    "rework": "my_rework",
    "reject": "my_rejected",
    "pay": "my_paid",
    "admin_status": "my_admin_status",
    "comment": "my_comment",
}

SUBJECTS = {
    "approve": "погоджено",
    "rework": "повернуто на доопрацювання",
    "reject": "відхилено",
    "pay": "оплачено",
    "admin_status": "статус змінено адміністратором",
    "comment": "новий коментар",
    "task_pay": "чекає оплати",
    "task_new": "чекає вашого рішення",
    "participant_add": "вас додано учасником",
    "dept_new": "нова заявка відділу",
    "pay_handover": "готівку видано — підтвердіть передачу підряднику",
    "handover": "готівку передано підряднику",
    "handover_for_you": "бухгалтер підтвердив передачу готівки за вас",
    "handover_reminder": "нагадування — підтвердіть передачу готівки підряднику",
    "cp_new": "новий контрагент — потрібно завести в 1С",
}

# Склад груп ролей: role -> [{"email", "name"}]. Встановлюється з app.py (через Graph, з кешем).
role_members = lambda role: []  # noqa: E731
admin_emails = lambda: []  # noqa: E731  — ADMIN_EMAILS із app.py (адміністратори поза групою)
_members_cache = {}
MEMBERS_TTL = 600
FAILED_TTL = 60  # після помилки пробуємо знову через хвилину, а не через 10
MEMBERS_HINT = ("Щоб листи отримували погоджувачі, додатку потрібен дозвіл Microsoft Graph → "
                "Application permissions → GroupMember.Read.All з Grant admin consent.")


def cp_pending_check(cp_id):
    return counterparties.is_pending(cp_id)


def cached_role_members(role):
    hit = _members_cache.get(role)
    if hit and time.time() - hit[0] < (MEMBERS_TTL if hit[2] else FAILED_TTL):
        return hit[1]
    try:
        members, ok = role_members(role), True
    except Exception as e:
        logger.exception("Не вдалося отримати склад ролі %s", role)
        members, ok = (hit[1] if hit else []), False
        # У журнал листів адмінки — інакше погоджувачі мовчки не отримують листів
        mailer.log_problem(f"роль «{wf.ROLES.get(role, role)}»", "Не вдалося визначити одержувачів",
                           f"{MEMBERS_HINT} ({e})")
    _members_cache[role] = (time.time(), members, ok)
    return members


def responsible_roles(req):
    """Ролі, які мають діяти на поточному етапі заявки."""
    if req["status"] == "approval":
        return wf.pending_roles(req)
    if req["status"] in ("dept_head", "accountant", "cfo", "to_pay"):
        role = wf.stage_actor(req)
        return [role] if role else []
    return []


def smtp_config():
    """Налаштування SMTP з адмінки (пароль розшифровується лише тут, у пам'яті)."""
    return {
        "host": app_settings.get("smtp_host"),
        "port": app_settings.get("smtp_port"),
        "security": app_settings.get("smtp_security"),
        "username": app_settings.get("smtp_username") or app_settings.get("mail_sender"),
        "password": secret_box.decrypt(app_settings.get("smtp_password")),
        "sender": app_settings.get("mail_sender"),
        "sender_name": app_settings.get("mail_sender_name"),
    }


def enabled():
    return bool(app_settings.get("mail_enabled") and app_settings.get("mail_sender") and app_settings.get("smtp_host"))


def _link(req):
    base = os.environ.get("APP_BASE_URL", "").rstrip("/")
    path = url_for("request_card", request_id=req["id"])
    return base + path if base else url_for("request_card", request_id=req["id"], _external=True)


def _send(to, subject, template_args):
    """to — робоча пошта одержувача; лист іде на підтверджену іншу адресу, якщо адмін це дозволив."""
    html = render_template("email/notification.html", **template_args)
    address = user_prefs.delivery_address(to, app_settings.get("allow_custom_email"))
    mailer.enqueue(smtp_config(), address, subject, html)


def notify(event, req, actor, comment="", target=None):
    """Надіслати листи після події event ("submit", "approve", "rework", "reject", "pay",
    "admin_status", "comment", "participant_add" — target: пошта доданого). Помилки не ламають дію."""
    if not enabled():
        return []
    try:
        return _notify(event, req, actor, comment, target)
    except Exception:
        logger.exception("Помилка підготовки сповіщень")
        return []


def _notify(event, req, actor, comment, target=None):
    actor_email = (actor.get("email") or "").lower()
    last = (req.get("history") or [{}])[-1]
    status_changed = last.get("from_status") != last.get("to_status")
    recipients = {}  # email -> (подія налаштувань, причина, тема)

    def add(email, pref, reason, subject):
        email = (email or "").lower()
        if email and email != actor_email and email not in recipients and user_prefs.wants(email, pref):
            recipients[email] = (pref, reason, subject)

    # 0. Доданий учасник — лише він
    if event == "participant_add":
        add(target, "participant_added", "вас додали учасником цієї заявки", SUBJECTS["participant_add"])

    # 1. Автор заявки і учасники (отримують ті самі події, що й автор)
    if event in AUTHOR_EVENTS:
        add(req["author_email"], AUTHOR_EVENTS[event], "ви автор цієї заявки",
            SUBJECTS["pay_handover"] if event == "pay" and wf.needs_handover(req) else SUBJECTS[event])
        for p in req.get("participants") or []:
            add(p["email"], AUTHOR_EVENTS[event], "ви учасник цієї заявки", SUBJECTS[event])

    # 2. Ті, хто тепер має діяти (нова заявка в їхній черзі чи «До оплати»)
    if event != "comment" and status_changed:
        kind = "task_pay" if req["status"] == "to_pay" else "task_new"
        for role in responsible_roles(req):
            # Керівник відділу — лише керівники відділу автора, а не вся група
            members = org_structure.heads_of(req.get("department")) if role == "dept_head" \
                else cached_role_members(role)
            for m in members:
                add(m["email"], "task_new", f"ви — {wf.ROLES.get(role, role).lower()}", SUBJECTS[kind])

    # 2б. Нова заявка відділу — керівнику і тим, хто бачить заявки відділу (якщо ввімкнули)
    if event == "submit" and req.get("department"):
        dep_name = departments.get_name(req["department"])
        for w in org_structure.watchers_of(req["department"]):
            add(w["email"], "dept_new", f"ви бачите заявки відділу «{dep_name}»", SUBJECTS["dept_new"])

    # 2в. Бухгалтер чи адмін підтвердив передачу за автора — автору (підтверджувати самому вже не треба)
    if event == "handover" and (req.get("handover") or {}).get("on_behalf"):
        add(req["author_email"], "my_paid", "ви автор цієї заявки", SUBJECTS["handover_for_you"])

    # Автор підтвердив передачу готівки — бухгалтеру заявки (якщо ввімкнув у налаштуваннях)
    if event == "handover":
        role = wf.accountant_role(req)
        for m in cached_role_members(role) if role else []:
            add(m["email"], "handover_done", f"ви — {wf.ROLES.get(role, role).lower()}", SUBJECTS["handover"])

    # 2г. Новий контрагент, якого заводить в 1С адміністратор (параметр в адмінці) — адміністраторам
    if event == "submit" and app_settings.get("cp_onec_owner") == "admin" and cp_pending_check(req.get("counterparty_id")):
        admins = [m["email"] for m in cached_role_members("admin")] + list(admin_emails())
        for email in admins:
            add(email, "cp_new", "нових контрагентів у 1С заводить адміністратор", SUBJECTS["cp_new"])

    # 3. Коментар — учасникам-погоджувачам, які вже ухвалювали рішення
    if event == "comment":
        for h in req.get("history", []):
            if h["action"] in wf.DECISION_ACTIONS and h["role"] in wf.APPROVER_ROLES:
                add(h.get("user_email"), "comment_participant", "ви погоджували цю заявку", SUBJECTS["comment"])

    for email, (pref, reason, subject_tail) in recipients.items():
        handover_ask = event == "pay" and wf.needs_handover(req) and email == req["author_email"].lower()
        _send(email, f"Заявка № {req['number']}: {subject_tail}", {
            "req": req,
            "event": event,
            "handover_ask": handover_ask,
            "handover_days": 0,
            "actor": actor,
            "comment": comment or last.get("comment") or "",
            "status": wf.STATUSES.get(req["status"], (req["status"],))[0],
            "headline": subject_tail[:1].upper() + subject_tail[1:],
            "reason": reason,
            "link": _link(req) + ("#handover" if handover_ask else ""),
            "settings_link": (os.environ.get("APP_BASE_URL", "").rstrip("/") + url_for("notification_settings"))
            if os.environ.get("APP_BASE_URL") else url_for("notification_settings", _external=True),
        })
    return list(recipients)


def send_handover_reminder(req, days):
    """Нагадування автору: підтвердіть, що передали готівку підряднику. Лише автору й без особистих
    налаштувань (це контроль, його вимикає тільки адмін). Потрібен контекст запиту Flask (для посилань).
    Повертає True, якщо лист поставлено в чергу."""
    if not enabled():
        return False
    try:
        _send(req["author_email"], f"Заявка № {req['number']}: {SUBJECTS['handover_reminder']}", {
            "req": req,
            "event": "handover_reminder",
            "handover_ask": True,
            "handover_days": days,
            "actor": wf.SYSTEM_USER,
            "comment": "",
            "status": wf.STATUSES[req["status"]][0],
            "headline": "Підтвердіть передачу готівки підряднику",
            "reason": "ви автор заявки й отримали готівку, щоб передати її підряднику",
            "link": _link(req) + "#handover",
            "settings_link": (os.environ.get("APP_BASE_URL", "").rstrip("/") + url_for("notification_settings"))
            if os.environ.get("APP_BASE_URL") else url_for("notification_settings", _external=True),
        })
    except Exception:
        logger.exception("Не вдалося підготувати нагадування про передачу готівки")
        return False
    return True
