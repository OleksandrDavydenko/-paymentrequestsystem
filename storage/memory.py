"""Сховище заявок у пам'яті: демо-дані (тестовий режим), локальний запуск і тести без бази.

Це еталонна реалізація: усі списки — прямо правилами workflow.py (storage/scopes.py).
"""
import threading
from copy import deepcopy

import workflow as wf

from . import derive, scopes
from .demo import _REQUESTS
from .errors import StaleRequestError

_lock = threading.RLock()


def get_request(request_id):
    r = _REQUESTS.get(request_id)
    return deepcopy(r) if r else None


def get_many(ids):
    return {i: deepcopy(_REQUESTS[i]) for i in ids if i in _REQUESTS}


def last_id():
    return max(_REQUESTS, default=0)


def save_request(req):
    """Зберегти заявку. Без 'id' — створити нову. Повертає id; у req оновлюється version."""
    with _lock:
        data = deepcopy(req)
        old = _REQUESTS.get(data.get("id")) if data.get("id") else None
        if old is not None and (old.get("version") or 0) != (data.get("version") or 0):
            raise StaleRequestError(old)
        if not data.get("id"):
            data["id"] = last_id() + 1
            data["number"] = f"{data['id']:09d}"
        data["version"] = (data.get("version") or 0) + 1
        _REQUESTS[data["id"]] = data
        req["version"] = data["version"]
        return data["id"]


def scope_rows(scope, roles, email, period=None):
    return scopes.light_rows(scope, list(_REQUESTS.values()), roles, email)


def count(scope, roles, email):
    return sum(1 for r in _REQUESTS.values() if scopes.match(scope, r, roles, email))


def find_attachment(file_id):
    for r in _REQUESTS.values():
        for a in r.get("attachments", []):
            if a["id"] == file_id:
                return deepcopy(r), dict(a)
    return None, None


def _ids_where(pred):
    return [r["id"] for r in _REQUESTS.values() if pred(r)]


def ids_with_counterparty(cp_id):
    return _ids_where(lambda r: r.get("counterparty_id") == cp_id)


def ids_with_departments(codes):
    return _ids_where(lambda r: r.get("department") in codes)


def last_accountant_type(email):
    rows = sorted((r for r in _REQUESTS.values() if wf.is_author(r, email)), key=lambda r: r["id"])
    rows.sort(key=lambda r: r["created_at"], reverse=True)
    return next((r["accountant_type"] for r in rows if r.get("accountant_type") in wf.ACCOUNTANT_CHOICES), None)


def first_request_with_counterparty(cp_id):
    rows = scopes.sort_rows("all", [derive.light(r) for r in _REQUESTS.values() if r.get("counterparty_id") == cp_id])
    return rows[0]["id"] if rows else None


def reindex():
    return 0  # похідні поля в пам'яті обчислюються щоразу
