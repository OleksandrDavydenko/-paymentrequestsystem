"""Система заявок на оплату — точка входу (gunicorn app:app).

Тут лише створюється й налаштовується додаток Flask; сторінки — у пакеті web/:
  web/requests_routes.py  — «Мої заявки», картка заявки, дії, коментарі, документи, учасники, контрагенти
  web/lists_routes.py     — «На погодження», «Усі заявки», «Заявки відділу»
  web/payments_routes.py  — «До оплати», «Оплачені», «Готівка не підтверджена»
  web/settings_routes.py  — особисті налаштування сповіщень
  web/admin_routes.py     — адміністрування
  web/api_routes.py       — API для Telegram-бота, /health
  web/auth.py, listing.py, request_helpers.py, formatting.py — спільні функції сторінок
  web/context.py, errors.py, background.py — шаблони, обробка помилок, фонові задачі
"""
import os
from datetime import date  # noqa: F401  (для сценарних тестів: m.date)

from flask import Flask
from flask_wtf.csrf import CSRFProtect
from werkzeug.middleware.proxy_fix import ProxyFix

import clock  # noqa: F401  (для сценарних тестів: m.clock)
import db_sql
import sessions_db
import workflow as wf  # noqa: F401  (для сценарних тестів: m.wf)
from web import admin_settings, background, context, errors  # noqa: F401  (admin_settings — маршрути в admin_bp)
from web.admin_routes import bp as admin_bp
from web.api_routes import bp as api_bp
from web.auth import auth
from web.lists_routes import bp as lists_bp
from web.payments_routes import bp as payments_bp
from web.requests_routes import bp as requests_bp
from web.settings_routes import bp as settings_bp

app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.environ["FLASK_SECRET_KEY"],
    SESSION_TYPE="filesystem",  # сесії зберігаються на сервері, не в cookie
    SESSION_PERMANENT=False,
    # Cookie сеансу: лише HTTPS (крім локального запуску) і не надсилається з POST-запитами з чужих сайтів
    SESSION_COOKIE_SECURE=os.environ.get("REDIRECT_URI", "").startswith("https://"),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    # Захист форм (CSRF): токен діє весь сеанс входу — форма, відкрита зранку, спрацює й увечері
    WTF_CSRF_TIME_LIMIT=None,
    MAX_CONTENT_LENGTH=25 * 1024 * 1024,
)
if db_sql.configured():  # сеанси входу в базі — спільні для всіх процесів і екземплярів сайту
    app.config.update(SESSION_TYPE="cachelib", SESSION_CACHELIB=sessions_db.DbSessionCache())
# App Service приймає HTTPS на проксі, а до Flask передає HTTP.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
# Кожна POST-форма має містити csrf_token, fetch-запити — заголовок X-CSRFToken (див. base.html)
csrf = CSRFProtect(app)
auth.init_app(app)

context.init_app(app)
errors.init_app(app)
background.init_app(app)
for bp in (requests_bp, lists_bp, payments_bp, settings_bp, admin_bp, api_bp):
    app.register_blueprint(bp)
csrf.exempt(api_bp)  # бот не має сеансу входу — API захищене ключем X-Api-Key

# Для сценарних тестів і скриптів, що звертаються до функцій як до app.<назва>
from web.auth import GROUPS, _role_members  # noqa: E402,F401
from web.background import run_handover_reminders, run_maintenance  # noqa: E402,F401
from web.formatting import money  # noqa: E402,F401
from web.listing import _currency_order, date_presets, payment_plan  # noqa: E402,F401
from web.request_helpers import payer_label  # noqa: E402,F401

if __name__ == "__main__":
    app.run(port=5000, debug=True)
