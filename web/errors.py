"""Обробка помилок: застаріла сторінка, CSRF, завеликі файли, повторний вхід посеред дії."""
import logging

from flask import flash, g, got_request_exception, redirect, render_template, request
from flask_wtf.csrf import CSRFError

import alerts
import storage as db

from web.request_helpers import _back_target


def method_not_allowed(e):
    """Дія з форми (POST), яку перервав повторний вхід: після входу браузер повертається на ту саму адресу вже GET.
    Замість «Method Not Allowed» — назад на сторінку, звідки була дія, з поясненням."""
    if request.method != "GET":
        return e
    flash("Сеанс входу оновився, тому дію не виконано" +
          (" і файл не додано" if request.path.endswith("/files") else "") + " — повторіть її, будь ласка.", "error")
    return redirect(_back_target(request.path))


def stale_request(e):
    """Заявку змінив хтось інший, поки користувач дивився на неї: дію не виконано, показуємо актуальну."""
    cur = e.current or {}
    last = (cur.get("history") or [None])[-1]
    who = f" — {last['user_name']} о {last['at']:%H:%M}" if last and last.get("user_name") else ""
    message = (f"Заявку № {cur.get('number', '')} щойно змінили{who}. Дію не виконано — "
               "перегляньте актуальний стан і повторіть, якщо потрібно.")
    if request.headers.get("X-Requested-With") == "fetch":
        return {"error": message}, 409
    flash(message, "error")
    return redirect(_back_target(request.path))


def csrf_error(e):
    """Форма без дійсного токена: сторінку відкрито до повторного входу або запит прийшов з чужого сайту."""
    logging.getLogger(__name__).warning("CSRF: %s %s — %s", request.method, request.path, e.description)
    message = "Сторінка застаріла (оновився сеанс входу), тому дію не виконано — повторіть її, будь ласка."
    if request.headers.get("X-Requested-With") == "fetch":
        return {"error": message}, 400
    flash(message, "error")
    return redirect(_back_target(request.path))


def too_large(_e):
    flash("Файли завеликі: максимум 10 МБ на файл і 25 МБ за раз", "error")
    return redirect(request.referrer or "/")


def _on_exception(sender, exception, **extra):
    """Необроблена помилка під час запиту — лист адміністраторам (alerts.py)."""
    user = getattr(g, "user", None)
    alerts.report(exception, f"{request.method} {request.path}", user=user and user.get("email"),
                  base_url=request.host_url)


def internal_error(_e):
    """Замість «Internal Server Error» — зрозуміла сторінка (без base.html: вона сама могла зламатися)."""
    return render_template("error_500.html"), 500


def init_app(app):
    got_request_exception.connect(_on_exception, app)
    app.register_error_handler(500, internal_error)
    app.register_error_handler(405, method_not_allowed)
    app.register_error_handler(db.StaleRequestError, stale_request)
    app.register_error_handler(CSRFError, csrf_error)
    app.register_error_handler(413, too_large)
