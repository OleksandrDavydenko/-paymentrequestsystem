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
import mailer
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
}

# Склад груп ролей: role -> [{"email", "name"}]. Встановлюється з app.py (через Graph, з кешем).
role_members = lambda role: []  # noqa: E731
_members_cache = {}
MEMBERS_TTL = 600


def cached_role_members(role):
    hit = _members_cache.get(role)
    if hit and time.time() - hit[0] < MEMBERS_TTL:
        return hit[1]
    try:
        members = role_members(role)
    except Exception:
        logger.exception("Не вдалося отримати склад ролі %s", role)
        members = hit[1] if hit else []
    _members_cache[role] = (time.time(), members)
    return members


def responsible_roles(req):
    """Ролі, які мають діяти на поточному етапі заявки."""
    if req["status"] == "approval":
        return wf.pending_roles(req)
    if req["status"] in ("accountant", "cfo", "to_pay"):
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
    html = render_template("email/notification.html", **template_args)
    mailer.enqueue(smtp_config(), to, subject, html)


def notify(event, req, actor, comment=""):
    """Надіслати листи після події event ("submit", "approve", "rework", "reject", "pay",
    "admin_status", "comment"). Помилки не ламають дію користувача."""
    if not enabled():
        return []
    try:
        return _notify(event, req, actor, comment)
    except Exception:
        logger.exception("Помилка підготовки сповіщень")
        return []


def _notify(event, req, actor, comment):
    actor_email = (actor.get("email") or "").lower()
    last = (req.get("history") or [{}])[-1]
    status_changed = last.get("from_status") != last.get("to_status")
    recipients = {}  # email -> (подія налаштувань, причина, тема)

    def add(email, pref, reason, subject):
        email = (email or "").lower()
        if email and email != actor_email and email not in recipients and user_prefs.wants(email, pref):
            recipients[email] = (pref, reason, subject)

    # 1. Автор заявки
    if event in AUTHOR_EVENTS:
        add(req["author_email"], AUTHOR_EVENTS[event], "ви автор цієї заявки", SUBJECTS[event])

    # 2. Ті, хто тепер має діяти (нова заявка в їхній черзі чи «До оплати»)
    if event != "comment" and status_changed:
        kind = "task_pay" if req["status"] == "to_pay" else "task_new"
        for role in responsible_roles(req):
            for m in cached_role_members(role):
                add(m["email"], "task_new", f"ви — {wf.ROLES.get(role, role).lower()}", SUBJECTS[kind])

    # 3. Коментар — учасникам-погоджувачам, які вже ухвалювали рішення
    if event == "comment":
        for h in req.get("history", []):
            if h["action"] in wf.DECISION_ACTIONS and h["role"] in wf.APPROVER_ROLES:
                add(h.get("user_email"), "comment_participant", "ви погоджували цю заявку", SUBJECTS["comment"])

    for email, (pref, reason, subject_tail) in recipients.items():
        _send(email, f"Заявка № {req['number']}: {subject_tail}", {
            "req": req,
            "event": event,
            "actor": actor,
            "comment": comment or last.get("comment") or "",
            "status": wf.STATUSES.get(req["status"], (req["status"],))[0],
            "headline": subject_tail[:1].upper() + subject_tail[1:],
            "reason": reason,
            "link": _link(req),
            "settings_link": (os.environ.get("APP_BASE_URL", "").rstrip("/") + url_for("notification_settings"))
            if os.environ.get("APP_BASE_URL") else url_for("notification_settings", _external=True),
        })
    return list(recipients)
