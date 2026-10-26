"""Особисті налаштування сповіщень (пошта, Telegram, інша адреса)."""
import os
import re

from flask import Blueprint, flash, g, redirect, render_template, request, url_for

import app_settings
import mailer
import notifications
import telegram_notify
import user_prefs
import workflow as wf

from web.auth import _deciding_roles, login_required, requires


bp = Blueprint("settings", __name__)


@bp.route("/settings/notifications", methods=["GET", "POST"])
@login_required
@requires()
def notification_settings(*, context):
    email = g.user["email"]
    groups = ["author"]  # учасником заявки може стати будь-хто
    if _deciding_roles(g.roles):
        groups.append("approver")
    if g.roles & {"dept_head", "dept_viewer"}:
        groups.append("department")
    if g.roles & wf.ACCOUNTANT_ROLES and app_settings.get("cash_handover_confirm"):
        groups.append("cash")
    if "admin" in g.roles and app_settings.get("cp_onec_owner") == "admin":
        groups.append("admin")
    events = [(code, label, group) for code, (label, group, _) in user_prefs.EVENTS.items() if group in groups]
    if request.method == "POST":
        enabled = request.form.get("enabled") == "1"
        prev = user_prefs.get(email)["events"]
        chosen = set(request.form.getlist("events"))
        # Коли листи вимкнено, поля неактивні й не надсилаються — зберігаємо попередній вибір
        new_events = {code: (code in chosen) if enabled else prev[code] for code in user_prefs.EVENTS}
        # Події не своєї ролі не чіпаємо
        for code, (_, group, _) in user_prefs.EVENTS.items():
            if group not in groups:
                new_events[code] = prev[code]
        user_prefs.save(email, enabled, new_events)
        if telegram_notify.enabled() and request.form.get("channel") in user_prefs.CHANNEL_VALUES:
            user_prefs.set_channel(email, request.form["channel"])
        flash("Налаштування сповіщень збережено", "success")
        return redirect(url_for("settings.notification_settings"))
    allow_custom = app_settings.get("allow_custom_email")
    return render_template("notification_settings.html", user_name=g.user["name"], user_email=email,
                           prefs=user_prefs.get(email), events=events, mail_enabled=notifications.enabled(),
                           allow_custom=allow_custom, delivery=user_prefs.delivery_address(email, allow_custom),
                           confirm_hours=int(user_prefs.CONFIRM_TTL.total_seconds() // 3600),
                           telegram_on=telegram_notify.enabled(),
                           tg_linked=telegram_notify.is_linked(user_prefs.telegram_lookup(email)),
                           CHANNELS=telegram_notify.CHANNELS)


def _send_address_confirmation(email, alt_email):
    """Лист з посиланням підтвердження на іншу адресу. Повертає None або текст помилки."""
    token = user_prefs.set_alt(email, alt_email)
    link = url_for("settings.notification_address_confirm", token=token, _external=True)
    base = os.environ.get("APP_BASE_URL", "").rstrip("/")
    if base:
        link = base + url_for("settings.notification_address_confirm", token=token)
    html = render_template("email/confirm_address.html", user_name=g.user["name"], login_email=email,
                           alt_email=alt_email, link=link,
                           hours=int(user_prefs.CONFIRM_TTL.total_seconds() // 3600))
    from web.context import app_name
    return mailer.deliver(notifications.smtp_config(), alt_email, f"Підтвердіть адресу для сповіщень — {app_name()}",
                          html, {"event": "confirm_email", "recipient": email})


@bp.route("/settings/notifications/address", methods=["POST"])
@login_required
@requires()
def notification_address(*, context):
    email = g.user["email"]
    back = redirect(url_for("settings.notification_settings") + "#address")
    if not app_settings.get("allow_custom_email"):
        flash("Адміністратор не дозволив вказувати іншу пошту — листи надходять на робочу", "error")
        return back
    action = request.form.get("action")
    if action == "clear" or (action == "save" and request.form.get("target") == "work"):
        user_prefs.clear_alt(email)
        flash(f"Листи надходитимуть на робочу пошту {email}", "success")
        return back
    if action == "resend":
        alt_email = user_prefs.get(email)["alt_email"]
    else:
        alt_email = (request.form.get("alt_email") or "").strip()
    if not alt_email or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", alt_email) or len(alt_email) > 254:
        flash("Вкажіть коректну адресу пошти", "error")
        return back
    if alt_email.lower() == email.lower():
        user_prefs.clear_alt(email)
        flash("Це ваша робоча пошта — листи надходитимуть на неї", "success")
        return back
    prefs = user_prefs.get(email)
    if prefs["alt_email"].lower() == alt_email.lower() and prefs["alt_verified"]:
        flash("Цю адресу вже підтверджено", "success")
        return back
    if not notifications.enabled():
        flash("Розсилку листів зараз вимкнено — лист підтвердження надіслати неможливо", "error")
        return back
    wait = user_prefs.resend_wait(email)
    if wait:
        flash(f"Лист підтвердження вже надіслано — повторити можна через {wait} с", "error")
        return back
    error = _send_address_confirmation(email, alt_email)
    if error:
        flash("Лист підтвердження не надіслано: " + error, "error")
    else:
        flash(f"На {alt_email} надіслано лист із посиланням для підтвердження. "
              f"Доки адресу не підтверджено, листи надходять на {email}.", "success")
    return back


@bp.route("/settings/notifications/confirm/<token>")
@login_required
@requires()
def notification_address_confirm(token, *, context):
    email = g.user["email"]
    alt_email = user_prefs.confirm_alt(email, token) if app_settings.get("allow_custom_email") else None
    if alt_email:
        flash(f"Адресу підтверджено — листи надходитимуть на {alt_email}", "success")
    else:
        flash("Посилання недійсне: воно протерміноване, вже використане або належить іншому користувачу. "
              "Увійдіть тим обліковим записом, для якого вказували адресу, або надішліть лист ще раз.", "error")
    return redirect(url_for("settings.notification_settings") + "#address")
