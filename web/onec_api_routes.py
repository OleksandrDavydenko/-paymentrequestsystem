"""API для обробки 1С «Платіжки із заявок PayDesk» (/api/1c/v1/…): ключ X-Api-Key, без сеансу входу. Логіка — onec_api.py."""
from functools import wraps

from flask import Blueprint, jsonify, request

import onec_api
import ratelimit

bp = Blueprint("onec_api", __name__, url_prefix="/api/1c/v1")


def client_ip():
    # ProxyFix (app.py) підставляє в remote_addr адресу з X-Forwarded-For; App Service додає до IPv4 ще й порт
    ip = request.remote_addr or ""
    return ip.split(":")[0] if ip.count(":") == 1 else ip


def _error(message, status):
    return jsonify({"ok": False, "error": message}), status


def guarded(fn):
    """Увімкнено в налаштуваннях, не забагато запитів, правильний ключ, дозволений IP. Помилки — JSON з текстом для
    бухгалтера в 1С."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not onec_api.enabled():
            return _error("API для 1С вимкнено або ключ ще не створено («Налаштування → API для 1С» у PayDesk)", 403)
        ip = client_ip()
        if ratelimit.API_BAD_KEYS.blocked(ip):
            return _error("Забагато спроб з невірним ключем — спробуйте за 10 хвилин", 429)
        if not ratelimit.API_REQUESTS.hit(ip):
            return _error("Забагато запитів до PayDesk — спробуйте за хвилину", 429)
        if not onec_api.key_ok(request.headers.get("X-Api-Key")):
            ratelimit.API_BAD_KEYS.hit(ip)
            return _error("Невірний ключ API — перевірте його в налаштуваннях обробки", 403)
        if not onec_api.ip_allowed(ip):
            return _error(f"Доступ з IP {ip} не дозволено («Налаштування → API для 1С» у PayDesk)", 403)
        onec_api.touch(ip, request.path)
        try:
            return fn(*args, **kwargs)
        except onec_api.ApiError as e:
            return _error(str(e), e.status)
    return wrapper


def _base():
    return request.host_url.rstrip("/")


@bp.route("/ping")
@guarded
def ping():
    from web.context import app_name
    return {"ok": True, "app": app_name(), "api": 1, "time": onec_api.clock.now().isoformat(timespec="seconds")}


@bp.route("/payment-orders")
@guarded
def payment_orders():
    a = request.args
    state = a.get("state") if a.get("state") in onec_api.STATES else "new"
    form = a.get("form") if a.get("form") in ("bank", "cash") else ""
    items = [onec_api.order(r, _base()) for r in onec_api.orders(state, form, (a.get("organization") or "").strip())]
    return {"ok": True, "state": state, "count": len(items), "items": items}


@bp.route("/payment-checks")
@guarded
def payment_checks():
    """Заявки з прив'язаними платежами 1С — для перевірки й виправлення документів у 1С (since — з якої дати оплати)."""
    raw = (request.args.get("since") or "").strip()
    try:
        since = onec_api.date.fromisoformat(raw) if raw else onec_api.clock.today() - onec_api.timedelta(days=30)
    except ValueError:
        return _error("since — дата у форматі РРРР-ММ-ДД", 400)
    items = onec_api.checks(since, _base())
    # prefixes — префікси номера заявки (поточний і колишні): обробка шукає «PD000123» у примечаннях, не лише «#123»
    return {"ok": True, "since": since.isoformat(), "count": len(items), "items": items,
            "prefixes": onec_api.request_numbers.all_prefixes()}


@bp.route("/payment-orders/<int:request_id>")
@guarded
def payment_order(request_id):
    req = onec_api.db.get_request(request_id)
    if not req or req["status"] == "draft":
        return _error("Заявку не знайдено", 404)
    return {"ok": True, "item": onec_api.order(req, _base())}


@bp.route("/payment-orders/<int:request_id>/<action>", methods=["POST"])
@guarded
def payment_order_mark(request_id, action):
    handlers = {"created": onec_api.mark_created, "failed": onec_api.mark_failed, "reset": onec_api.reset}
    if action not in handlers:
        return _error("Невідома дія: created, failed або reset", 404)
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        data = {}
    req = handlers[action](request_id, data)
    if action == "created":
        from web import background
        background.payments_sync_soon()  # документ — одразу в копію платежів, поки його не видалили
    return {"ok": True, "item": onec_api.order(req, _base())}
