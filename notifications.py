"""Email-сповіщення про події із заявками: кому, про що і з яким текстом.

notify(event, req, actor) викликається після дії в системі (у контексті запиту Flask).
Листи йдуть, лише якщо адмін увімкнув розсилку і вказав відправника, а одержувач
не вимкнув цю подію. Самому автору дії листи не надсилаються.
"""
import html as html_lib
import logging
import os
import time
from decimal import Decimal

from flask import render_template, url_for

import app_settings
import counterparties
import departments
import mailer
import notify_log
import org_structure
import secret_box
import telegram_notify
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
    "cancel": "my_rejected",  # автор скасував — учасникам (сам автор — ініціатор дії)
}

SUBJECTS = {
    "approve": "погоджено",
    "rework": "повернуто на доопрацювання",
    "reject": "відхилено",
    "pay": "оплачено",
    "admin_status": "статус змінено адміністратором",
    "comment": "новий коментар",
    "withdraw": "відкликано автором на редагування",
    "cancel": "скасовано автором",
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


def test_mode():
    """Тестові дані замість робочої бази (перемикач в адмінці)."""
    import db_sql
    return db_sql.configured() and app_settings.get("data_mode") == "test"


def muted_by_test_mode():
    """Тестовий режим і розсилку вимкнено (при переході в тестовий режим вона вимикається автоматично;
    адмін може ввімкнути її в «Email-сповіщення» — тоді листи й Telegram з позначкою «[ТЕСТ]»)."""
    return test_mode() and not app_settings.get("mail_enabled")


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


mailer.config_provider = smtp_config  # черга листів у базі бере налаштування в момент відправки


def enabled():
    return bool(app_settings.get("mail_enabled") and app_settings.get("mail_sender") and app_settings.get("smtp_host"))


def _link(req):
    base = os.environ.get("APP_BASE_URL", "").rstrip("/")
    path = url_for("requests.request_card", request_id=req["id"])
    return base + path if base else url_for("requests.request_card", request_id=req["id"], _external=True)


def _money(value):
    try:
        return f"{Decimal(value):,.2f}".replace(",", " ").replace(".", ",")
    except Exception:
        return str(value or "")


def telegram_text(args):
    """Коротка версія листа для Telegram (HTML-розмітка Telegram)."""
    esc = html_lib.escape
    req = args["req"]
    lines = [f"<b>Заявка № {esc(req['number'])}</b> — {esc(args['headline'])}"]
    if args.get("event") == "handover_reminder":
        lines.append(f"Ви отримали готівку {args.get('handover_days', 0)} дн. тому — підтвердіть передачу підряднику.")
    elif args.get("actor") and args["actor"].get("name"):
        lines.append(f"Від: {esc(args['actor']['name'])}")
    cp = counterparties.name(req.get("counterparty_id"))
    if cp:
        lines.append(f"Контрагент: {esc(cp)}")
    lines.append(f"Сума: <b>{_money(req.get('amount'))} {esc(req.get('currency') or '')}</b>")
    if args.get("status"):
        lines.append(f"Статус: {esc(args['status'])}")
    comment = (args.get("comment") or "").strip()
    if comment:
        lines.append("💬 " + esc(comment[:500] + ("…" if len(comment) > 500 else "")))
    return "\n".join(lines)


def _send(to, subject, template_args):
    """to — робоча пошта одержувача. Канал — з його налаштувань: пошта, Telegram або обидва.
    Лист іде на підтверджену іншу адресу, якщо адмін це дозволив."""
    if test_mode():
        subject = "[ТЕСТ] " + subject
    html = render_template("email/notification.html", **template_args)
    address = user_prefs.delivery_address(to, app_settings.get("allow_custom_email"))
    req = template_args["req"]
    reason = template_args.get("reason") or ""  # чому людина отримує сповіщення — видно в журналі
    meta = {"request_id": req.get("id"), "number": req.get("number"), "event": template_args.get("event", ""),
            "recipient": to, "detail": reason}
    channel = user_prefs.channel(to) if telegram_notify.enabled() else "email"
    lookup = user_prefs.telegram_lookup(to)
    if channel != "email" and telegram_notify.is_linked(lookup) is False:
        channel = "email"  # бот людину не знає — одразу листом
        meta["detail"] = "; ".join(x for x in (reason, f"вибрано Telegram, але бот не знає {lookup} — листом") if x)
    if channel in ("telegram", "both"):
        only_telegram = channel == "telegram"
        text = ("🧪 <b>ТЕСТ</b> — демо-дані системи заявок\n" if test_mode() else "") + telegram_text(template_args)
        msg_id = telegram_notify.enqueue(to, text, template_args.get("link"),
                                         subject if only_telegram else None, html if only_telegram else None)
        notify_log.record("telegram", to, "queued", subject, meta["request_id"], meta["number"], meta["event"],
                          detail="; ".join(x for x in (reason, f"бот шукає за {lookup}" if lookup != to.lower() else "") if x),
                          ref=f"tg:{msg_id}")
    if channel in ("email", "both"):
        mailer.enqueue(smtp_config(), address, subject, html, meta)


def _fallback_mail(row, reason):
    """Повідомлення не дійшло в Telegram — той самий лист на пошту."""
    if not enabled():
        return
    address = user_prefs.delivery_address(row["email"], app_settings.get("allow_custom_email"))
    mailer.enqueue(smtp_config(), address, row["subject"], row["html"],
                   {"recipient": row["email"], "event": "fallback", "detail": f"запасний лист: Telegram — {reason}"})
    mailer.log_problem(f"Telegram → {row['email']}", row["subject"], f"{reason} — надіслано листом")


def _telegram_result(row, status, error):
    notify_log.update_ref(f"tg:{row['id']}", status, error)
    if error is None:
        mailer._log(f"Telegram → {row['email']}", telegram_notify.DONE_LABELS[status])


telegram_notify.on_fallback = _fallback_mail
telegram_notify.on_result = _telegram_result


def notify(event, req, actor, comment="", target=None, before=None):
    """Надіслати листи після події event ("submit", "approve", "rework", "reject", "pay",
    "admin_status", "comment", "participant_add" — target: пошта доданого). Помилки не ламають дію."""
    if muted_by_test_mode():
        return []
    if not enabled():
        notify_log.record("-", "", "skipped", f"Заявка № {req.get('number')}: {notify_log.EVENT_LABELS.get(event, event)}",
                          req.get("id"), req.get("number"), event, detail="розсилку вимкнено в адмінці (або не налаштовано SMTP)")
        return []
    try:
        return _notify(event, req, actor, comment, target, before)
    except Exception:
        logger.exception("Помилка підготовки сповіщень")
        return []


def _notify(event, req, actor, comment, target=None, before=None):
    actor_email = (actor.get("email") or "").lower()
    last = wf.last_action(req)  # без службових приміток, інакше «статус не змінився» і погоджувачі без листа
    status_changed = last.get("from_status") != last.get("to_status")
    recipients = {}  # email -> (подія налаштувань, причина, тема)
    skipped = {}     # email -> чому не надсилаємо (для журналу)

    def add(email, pref, reason, subject):
        email = (email or "").lower()
        if not email or email in recipients:
            return
        if email == actor_email:
            skipped.setdefault(email, f"{reason}, але дію виконали ви самі — про власні дії сповіщень немає")
        elif not user_prefs.get(email)["enabled"]:
            skipped.setdefault(email, f"{reason}, але людина вимкнула всі сповіщення")
        elif not user_prefs.wants(email, pref):
            skipped.setdefault(email, f"{reason}, але подію «{user_prefs.EVENTS[pref][0]}» вимкнено в її налаштуваннях")
        else:
            recipients[email] = (pref, reason, subject)
            skipped.pop(email, None)

    def no_members(role):
        skipped.setdefault(f"роль «{wf.ROLES.get(role, role)}»", "у групі ролі немає людей (або не вдалося прочитати групу)")

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
            if not members:
                no_members(role)
            for m in members:
                if kind == "task_new" and wf.self_approval_blocked(req, m["email"]):
                    continue  # власну заявку погоджує інша людина (параметр «Розподіл обов'язків»)
                add(m["email"], "task_new", f"ви — {wf.ROLES.get(role, role).lower()}", SUBJECTS[kind])

    # 2а. Автор відкликав або скасував заявку (before — стан до дії): тим, у кого вона чекала рішення,
    #     і тим, хто вже погодив її в цьому колі
    if event in ("withdraw", "cancel") and before:
        for role in responsible_roles(before):
            members = org_structure.heads_of(before.get("department")) if role == "dept_head"                 else cached_role_members(role)
            for m in members:
                add(m["email"], "task_withdrawn", f"заявка чекала вашого рішення ({wf.ROLES.get(role, role).lower()})",
                    SUBJECTS[event])
        for email, role in wf.round_approvers(before):
            add(email, "task_withdrawn", f"ви вже погодили цю заявку ({wf.ROLES.get(role, role).lower()})",
                SUBJECTS[event])

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
            "settings_link": (os.environ.get("APP_BASE_URL", "").rstrip("/") + url_for("settings.notification_settings"))
            if os.environ.get("APP_BASE_URL") else url_for("settings.notification_settings", _external=True),
        })
    for who, why in skipped.items():
        notify_log.record("-", who, "skipped", f"Заявка № {req['number']}: {notify_log.EVENT_LABELS.get(event, event)}",
                          req.get("id"), req.get("number"), event, detail=why)
    if not recipients and not skipped:
        notify_log.record("-", "", "skipped", f"Заявка № {req['number']}: {notify_log.EVENT_LABELS.get(event, event)}",
                          req.get("id"), req.get("number"), event, detail="для цієї події одержувачів немає")
    return list(recipients)


def send_handover_reminder(req, days):
    """Нагадування автору: підтвердіть, що передали готівку підряднику. Лише автору й без особистих
    налаштувань (це контроль, його вимикає тільки адмін). Потрібен контекст запиту Flask (для посилань).
    Повертає True, якщо лист поставлено в чергу."""
    if not enabled() or test_mode():  # нагадування про готівку в тестовому режимі — ніколи
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
            "settings_link": (os.environ.get("APP_BASE_URL", "").rstrip("/") + url_for("settings.notification_settings"))
            if os.environ.get("APP_BASE_URL") else url_for("settings.notification_settings", _external=True),
        })
    except Exception:
        logger.exception("Не вдалося підготувати нагадування про передачу готівки")
        return False
    return True
