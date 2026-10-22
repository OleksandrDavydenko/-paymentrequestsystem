"""Журнал змін налаштувань: хто, коли, яке налаштування й як змінилося (було → стало).

У базі — таблиця settings_log; без бази — останні 500 змін у пам'яті. Значення зберігаються JSON-ом;
пароль — лише як «••••». Помилки журналу ніколи не заважають зберегти налаштування.
"""
import json
import logging
import threading
from collections import deque

import clock
import db_sql
import settings_schema

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_memory = deque(maxlen=500)
_next_id = [0]


def _dump(key, value):
    if settings_schema.SETTINGS.get(key) and settings_schema.SETTINGS[key].kind == "secret":
        return json.dumps("••••" if value else "", ensure_ascii=False)
    return json.dumps(value, ensure_ascii=False, default=str)


def record(key, old, new, by):
    row = {"at": clock.now(), "key": key[:100], "old": _dump(key, old), "new": _dump(key, new), "by": (by or "")[:320]}
    try:
        if db_sql.configured():
            db_sql.settings_log_add(row)
            return
        with _lock:
            _next_id[0] += 1
            _memory.appendleft({**row, "id": _next_id[0]})
    except Exception:
        logger.exception("Не вдалося записати зміну налаштування в журнал")


def query(limit=300, key=None):
    """Останні зміни (новіші першими) з підписами для сторінки."""
    if db_sql.configured():
        rows = db_sql.settings_log_query(limit, key)
    else:
        rows = [r for r in list(_memory) if not key or r["key"] == key][:limit]
    out = []
    for r in rows:
        old, new = json.loads(r["old"]) if r["old"] else None, json.loads(r["new"]) if r["new"] else None
        s = settings_schema.SETTINGS.get(r["key"])
        out.append({**r, "label": settings_schema.label(r["key"]),
                    "tab": settings_schema.TAB_NAMES.get(s.tab, "") if s else "",
                    "old_label": old if s and s.kind == "secret" else settings_schema.value_label(r["key"], old),
                    "new_label": new if s and s.kind == "secret" else settings_schema.value_label(r["key"], new)})
    return out
