"""Змінні й фільтри для всіх шаблонів (init_app реєструє їх у додатку)."""
from flask import g

import app_settings
import clock
import counterparties
import currencies
import departments
import expense_items
import holidays
import notifications
import organizations
import payment_accounts
import settings_schema
import storage as db
import workflow as wf

from web.auth import _deciding_roles, _sees_all_requests
from web.formatting import abs_static, expense_label, filesize, fmt_date, fmt_datetime, money, url_with, visible_history
from web.request_helpers import pay_org, payer_label


def inject_refs():
    return {
        "ORGANIZATIONS": organizations.names(),
        "ORG_OPTIONS": organizations.options(),
        "org_name": organizations.name,
        "cp_name": counterparties.name,
        "cp_pending": counterparties.is_pending,
        "pay_account_label": payment_accounts.label,
        "payment_docs": _payment_docs,
        "SETTINGS_TABS": settings_schema.TABS,
        "expense_name": expense_label,
        "expense_title": expense_items.get_name,
        "CURRENCIES": currencies.codes(),
        "CURRENCY_NAMES": currencies.full_names(),
        "PAYMENT_FORMS": db.PAYMENT_FORMS,
        "CHANNELS": wf.CHANNELS,
        "channel_of": wf.channel_of,
        "dept_name": departments.get_name,
        "STATUSES": db.STATUSES,
        "needs_handover": wf.needs_handover,
        "payer_label": payer_label,
        "pay_org": pay_org,
        # У режимі «Бухгалтер» організацію не вказують — колонки й фільтра «Організація» немає
        "show_org": app_settings.get("org_field_mode") != "accountant",
        **theme_context(app_settings.get("color_theme")),
        "app_name": app_name(),
        # Святкове оформлення (<html data-holiday>): ввімкнене свято у свої дні або вибране вручну
        "holiday": holidays.active(clock.today(), app_settings.get("holidays"), app_settings.get("holiday_now")),
    }


def app_name():
    """Назва системи («Налаштування → Дизайн»): шапка, заголовки сторінок, вхід, листи, ярлик на телефоні."""
    return (app_settings.get("app_name") or "").strip() or "PayDesk"


def theme_context(theme):
    """Кольорова тема для <html data-theme> і колір рядка браузера («Налаштування → Дизайн»)."""
    theme = theme if theme in settings_schema.COLOR_THEMES else "blue"
    return {"color_theme": theme, "theme_color": settings_schema.THEME_COLORS[theme]}


def _payment_docs(rows):
    """{id заявки: [платіж з 1С]} для оплачених заявок сторінки списку — «платіж № …» під статусом."""
    import payments
    return payments.docs_by_request([r["id"] for r in rows if r.get("status") == "paid"])


def _reconcile_count(roles):
    """Платежі з 1С без заявки за 30 днів — у меню «Звірка» (фіндиректор, адміністратор)."""
    if not roles & {"cfo", "admin"}:
        return 0
    try:
        import payments
        return payments.status().get("unlinked") or 0
    except Exception:
        return 0


def inject_roles():
    roles = getattr(g, "roles", set())
    return {
        "roles": roles,
        "ROLES": wf.ROLES,
        "ROLE_SHORT": wf.ROLE_SHORT,
        "is_approver": bool(_deciding_roles(roles)) or "cfo" in roles,
        "sees_departments": bool(roles & {"dept_head", "dept_viewer"}),
        "participating_count": db.count("participant", roles, g.user["email"]) if getattr(g, "user", None) else 0,
        "queue_count": db.count("queue", roles, g.user["email"]) if roles & wf.APPROVER_ROLES else 0,
        "can_see_payments": wf.can_see_payments(roles),
        "sees_all_requests": _sees_all_requests(roles),
        "test_mode": notifications.test_mode(),
        "reconcile_count": _reconcile_count(roles),
        "test_mail_muted": notifications.muted_by_test_mode(),
        "payments_count": db.count("to_pay", roles) if wf.can_see_payments(roles) else 0,
    }


def init_app(app):
    """Фільтри, глобальні функції й змінні для всіх шаблонів."""
    for name, fn in (("money", money), ("d", fmt_date), ("dt", fmt_datetime), ("filesize", filesize)):
        app.add_template_filter(fn, name)
    for fn in (url_with, visible_history, abs_static):
        app.add_template_global(fn)
    app.context_processor(inject_refs)
    app.context_processor(inject_roles)
