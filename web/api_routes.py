"""API для Telegram-бота (захищене ключем X-Api-Key), /health, /favicon.ico і manifest для ярлика на телефоні."""
import json

from flask import Blueprint, abort, current_app, request

import db_sql
import filestore
import ratelimit
import storage as db
import telegram_notify
from web.onec_api_routes import client_ip


bp = Blueprint("api", __name__)


def _bot_guard():
    """Лише для бота: Telegram підключено (TELEGRAM_BOT_KEY), не забагато запитів і правильний X-Api-Key."""
    if not telegram_notify.enabled():
        abort(404)
    ip = client_ip()
    if ratelimit.API_BAD_KEYS.blocked(ip) or not ratelimit.API_REQUESTS.hit(ip):
        abort(429)
    if not telegram_notify.key_ok(request.headers.get("X-Api-Key")):
        ratelimit.API_BAD_KEYS.hit(ip)
        abort(403)


@bp.route("/api/telegram/outbox")
def telegram_outbox():
    _bot_guard()
    try:
        limit = max(1, min(int(request.args.get("limit", 50)), 200))
    except ValueError:
        limit = 50
    return {"messages": telegram_notify.take(limit)}


@bp.route("/api/telegram/ack", methods=["POST"])
def telegram_ack():
    _bot_guard()
    data = request.get_json(silent=True) or {}
    return {"accepted": telegram_notify.ack(data.get("results") or [])}


@bp.route("/api/telegram/linked", methods=["POST"])
def telegram_linked():
    _bot_guard()
    data = request.get_json(silent=True) or {}
    return {"linked": telegram_notify.set_linked(data.get("emails") or [])}


@bp.route("/site.webmanifest")
def webmanifest():
    """Ярлик на головному екрані телефона: назва — з «Налаштування → Дизайн»."""
    from web.context import app_name
    name = app_name()
    body = {"name": f"{name} — заявки на оплату", "short_name": name,
            "icons": [{"src": "/static/icon-192.png", "sizes": "192x192", "type": "image/png"},
                      {"src": "/static/icon-512.png", "sizes": "512x512", "type": "image/png"}],
            "theme_color": "#0f6cbd", "background_color": "#ffffff", "display": "standalone", "start_url": "/"}
    return current_app.response_class(json.dumps(body, ensure_ascii=False), mimetype="application/manifest+json")


@bp.route("/favicon.ico")
def favicon():
    return current_app.send_static_file("favicon.ico")


@bp.route("/health")
def health():
    """Стан сайту й бази даних (без персональних даних)."""
    tg = telegram_notify.status()
    info = {**db_sql.status(), **filestore.status(), "telegram": "on" if tg["enabled"] else "off"}
    if db_sql.configured() and not info.get("error"):
        info.update(db.sql.status())
    if tg["enabled"]:
        info["telegram_last_poll"] = tg["last_poll"].isoformat(timespec="seconds") if tg["last_poll"] else None
    return ({"status": "error", **info}, 503) if info.get("error") else {"status": "ok", **info}
