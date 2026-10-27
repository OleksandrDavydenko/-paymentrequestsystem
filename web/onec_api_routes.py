"""API для обробки 1С «Платіжки із заявок PayDesk» (/api/1c/v1/…): ключ X-Api-Key, без сеансу входу. Логіка — onec_api.py."""
from functools import wraps

from flask import Blueprint, jsonify, request

import onec_api

bp = Blueprint("onec_api", __name__, url_prefix="/api/1c/v1")


def _client_ip():
    # ProxyFix (app.py) підставляє в remote_addr адресу з X-Forwarded-For; App Service додає до IPv4 ще й порт
    ip = request.remote_addr or ""
    return ip.split(":")[0] if ip.count(":") == 1 else ip


def _error(message, status):
    return jsonify({"ok": False, "error": message}), status


def guarded(fn):
    """Увімкнено в налаштуваннях, правильний ключ, дозволений IP. Помилки — JSON з текстом для бухгалтера в 1С."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not onec_api.enabled():
            return _error("API для 1С вимкнено або ключ ще не створено («Налаштування → API для 1С» у PayDesk)", 403)
        if not onec_api.key_ok(request.headers.get("X-Api-Key")):
            return _error("Невірний ключ API — перевірте його в налаштуваннях обробки", 403)
        ip = _client_ip()
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
    return {"ok": True, "item": onec_api.order(req, _base())}
