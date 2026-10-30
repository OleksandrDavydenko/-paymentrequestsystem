"""Журнал роботи сайту («Адміністрування → Журнал роботи»): що гальмувало, що падало, як стартував сайт.

Записується:
  slow    — запит, довший за SLOW_MS (web/request_timing.py): метод і шлях без параметрів (у них бувають коди входу);
  job     — кожен запуск фонової задачі (звірка з 1С, нагадування, обслуговування): тривалість і підсумок;
  startup — старт процесу сайту: скільки імпортувався додаток і через скільки прийшов перший запит;
  error   — необроблена помилка запиту чи фонової задачі (alerts.report).
У базі — таблиця ops_log, 30 днів (чистить щоденне обслуговування); без бази — останні 2000 записів у пам'яті.
Помилки журналу ніколи не ламають сам сайт.

Те, що сайт сам записати не може (процес завис чи впав), ловить Health check App Service з листом адміністратору
(README, «Журнал роботи сайту»).
"""
import logging
import socket
import threading
import time
from collections import deque
from contextlib import contextmanager
from datetime import timedelta

import clock
import db_sql

logger = logging.getLogger(__name__)

SLOW_MS = 3000          # запит, довший за 3 с, — у журнал
KEEP_DAYS = 30
KINDS = {"slow": "Повільний запит", "job": "Фонова задача", "startup": "Старт сайту", "error": "Помилка",
         "profile": "Профіль запиту"}
JOB_NAMES = {"payments_sync": "Звірка з платежами 1С", "payments_sync_now": "Звірка з 1С («Синхронізувати зараз»)",
             "payments_sync_soon": "Звірка з 1С (після платіжки з обробки)", "handover_reminders": "Нагадування про готівку",
             "approver_reminders": "Нагадування погоджувачам", "onec_sync": "Довідники з 1С",
             "maintenance": "Щоденне обслуговування"}
HOST = socket.gethostname()[:100]

_lock = threading.Lock()
_memory = deque(maxlen=2000)
_next_id = [0]


def record(kind, name, duration_ms=None, status="", detail=""):
    row = {"at": clock.now(), "kind": kind, "name": (name or "")[:200],
           "duration_ms": int(duration_ms) if duration_ms is not None else None,
           "status": (str(status) if status is not None else "")[:20], "detail": (detail or "")[:1000] or None, "host": HOST}
    try:
        if db_sql.configured():
            db_sql.ops_add(row)
            return
        with _lock:
            _next_id[0] += 1
            _memory.appendleft({**row, "id": _next_id[0]})
    except Exception:
        logger.exception("Не вдалося записати в журнал роботи сайту")


def query(limit=500, kind="", hours=24):
    since = clock.now() - timedelta(hours=hours)
    kind = kind if kind in KINDS else ""
    try:
        if db_sql.configured():
            return db_sql.ops_query(limit, kind, since)
    except Exception:
        logger.exception("Не вдалося прочитати журнал роботи сайту")
        return []
    with _lock:
        rows = [dict(r) for r in _memory]
    return [r for r in rows if r["at"] >= since and (not kind or r["kind"] == kind)][:limit]


def cleanup():
    """Записи, старші за KEEP_DAYS, — геть (щоденне обслуговування). Повертає кількість."""
    before = clock.now() - timedelta(days=KEEP_DAYS)
    if db_sql.configured():
        return db_sql.ops_cleanup(before)
    with _lock:
        n = len(_memory)
        kept = [r for r in _memory if r["at"] >= before]
        _memory.clear()
        _memory.extend(kept)
        return n - len(kept)


def summary(result):
    """Підсумок фонової задачі для журналу: (статус, текст)."""
    if isinstance(result, dict):
        if result.get("error"):
            return "error", str(result["error"])[:500]
        if result.get("busy"):
            return "ok", "уже йшла в іншому потоці — пропущено"
        parts = [f"нових / змінених рядків {result['loaded_last']}" if "loaded_last" in result else "",
                 f"прив'язано {result['linked_last']}" if result.get("linked_last") else "",
                 f"оплачено {len(result['paid_last'])}" if result.get("paid_last") else "",
                 f"повернуто в «До оплати» {len(result['unpaid_last'])}" if result.get("unpaid_last") else "",
                 f"зниклих рядків {result['removed_last']}" if result.get("removed_last") else ""]
        text = " · ".join(p for p in parts if p)
        if not text:
            text = " · ".join(f"{k}: {v}" for k, v in result.items() if isinstance(v, (int, str)))[:500]
        return "ok", text
    if isinstance(result, (list, tuple, set)):
        return "ok", f"оброблено: {len(result)}"
    return "ok", "" if result is None else str(result)[:500]


TIMING_LABELS = {"db": "база", "db_connect": "нові з'єднання з базою", "pbi": "Power BI"}


def add_timing(kind, seconds):
    """Звернення до бази чи Power BI під час запиту сторінки — у її підсумок (g.timings): у журналі повільних запитів
    видно, куди пішов час. Поза запитом (фонові задачі) нічого не робить."""
    try:
        from flask import g, has_request_context
        if has_request_context():
            timings = g.setdefault("timings", {})
            n, total = timings.get(kind, (0, 0.0))
            timings[kind] = (n + 1, total + seconds)
    except Exception:
        pass


def timing_summary(timings):
    """«база: 23 запити, 0.4 с · Power BI: 1 запит, 2.1 с»."""
    return " · ".join(f"{TIMING_LABELS.get(k, k)}: {n} запит(ів), {total:.1f} с" for k, (n, total) in (timings or {}).items())


@contextmanager
def timed(name):
    """Фонова задача: тривалість і підсумок (info["result"] = що повернула) — у журнал; помилка — статус error."""
    t0 = time.monotonic()
    info = {}
    try:
        yield info
    except Exception as e:
        record("job", name, (time.monotonic() - t0) * 1000, "error", f"{type(e).__name__}: {e}")
        raise
    status, detail = summary(info.get("result"))
    record("job", name, (time.monotonic() - t0) * 1000, status, detail)


# ---------------------------------------------------------------- старт процесу

_started = {"import_s": None, "t0": None, "logged": False}


def startup_begin(t0):
    _started["t0"] = t0


def startup_imported():
    if _started["t0"] is not None:
        _started["import_s"] = time.monotonic() - _started["t0"]


def first_request():
    """Перший запит процесу: записати старт (раз на процес)."""
    if _started["logged"] or _started["t0"] is None:
        return
    with _lock:
        if _started["logged"]:
            return
        _started["logged"] = True
    total = time.monotonic() - _started["t0"]
    record("startup", "Старт сайту", total * 1000, "ok",
           f"додаток завантажився за {(_started['import_s'] or 0):.1f} с; перший запит — через {total:.1f} с після старту")
