"""Форматування для шаблонів і посилання з поточними параметрами (фільтри шаблонів реєструє web/context.py)."""
import os
from decimal import Decimal

from flask import g, request, url_for

import expense_items
import workflow as wf


def money(value):
    """12345.6 -> '12 345,60'"""
    return f"{Decimal(value):,.2f}".replace(",", " ").replace(".", ",")


def fmt_date(value):
    return value.strftime("%d.%m.%Y") if value else ""


def fmt_datetime(value):
    return value.strftime("%d.%m.%Y %H:%M") if value else ""


def filesize(n):
    return f"{n / 1024 / 1024:.1f} МБ" if n >= 1024 * 1024 else f"{max(1, round(n / 1024))} КБ"


def expense_label(code):
    """«Код — Назва» для статті витрат; код, якого немає в довіднику, позначаємо явно."""
    if not code:
        return ""
    name = expense_items.get_name(code)
    return f"{code} — {name}" if name else f"{code} — немає в довіднику"


def url_with(changes):
    """Поточна адреса з заміненими параметрами запиту (None/"" — прибрати параметр). Повторювані параметри
    (кілька вибраних значень фільтра, ?cp=1&cp=4) зберігаються; «Показати ще» скидається."""
    args = request.args.to_dict(flat=False)
    args.pop("limit", None)
    for key, value in changes.items():
        if value in (None, ""):
            args.pop(key, None)
        else:
            args[key] = value
    return url_for(request.endpoint, **args)


def url_without(key, value=None):
    """Поточна адреса без одного значення фільтра (або без усього параметра)."""
    args = request.args.to_dict(flat=False)
    if value is None:
        args.pop(key, None)
    else:
        args[key] = [v for v in args.get(key, []) if v != value]
    return url_for(request.endpoint, **{k: v for k, v in args.items() if v})


def visible_history(req):
    """Історія заявки без записів, які поточному користувачу не призначені (напр. «Вид оплати»)."""
    return wf.visible_history(req, getattr(g, "roles", set()))


def abs_static(filename):
    """Абсолютна адреса статичного файлу — для листів (поза сайтом відносні адреси не працюють)."""
    base = os.environ.get("APP_BASE_URL", "").rstrip("/")
    return base + url_for("static", filename=filename) if base else url_for("static", filename=filename, _external=True)
