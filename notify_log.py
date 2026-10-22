"""Журнал сповіщень (пошта й Telegram): коли, кому, про яку заявку, результат — і кому НЕ надіслано та чому.

У базі — таблиця notification_log (переживає перезапуск); без бази — останні 1000 записів у пам'яті.
Помилки журналу ніколи не ламають надсилання.
"""
import logging
import threading
from collections import deque

import clock
import db_sql

logger = logging.getLogger(__name__)

# статус -> (підпис, колір мітки)
STATUSES = {
    "sent": ("надіслано", "green"),
    "queued": ("у черзі бота", "amber"),
    "failed": ("помилка", "red"),
    "not_found": ("немає в боті", "amber"),
    "blocked": ("бот заблоковано", "red"),
    "skipped": ("не надсилалось", "grey"),
}
PROBLEMS = {"failed", "not_found", "blocked"}
EVENT_LABELS = {
    "submit": "відправлено на погодження", "approve": "погоджено", "rework": "повернуто на доопрацювання",
    "reject": "відхилено", "pay": "оплачено", "admin_status": "статус змінив адмін", "comment": "коментар",
    "participant_add": "додано учасника", "handover": "передача готівки", "handover_reminder": "нагадування про готівку",
    "fallback": "запасний лист", "test": "тестове", "confirm_email": "підтвердження пошти",
    "error_alert": "помилка сайту", "withdraw": "відкликано автором", "cancel": "скасовано автором",
    "approver_reminder": "нагадування погоджувачу",
}
CHANNELS = {"email": "Пошта", "telegram": "Telegram", "-": "—"}

_lock = threading.Lock()
_memory = deque(maxlen=1000)
_next_id = [0]


def record(channel, recipient, status, subject="", request_id=None, number=None, event="", detail="", ref=None):
    if db_sql.configured():
        import app_settings
        if app_settings.get("data_mode") == "test":
            detail = "тестовий режим (демо-заявка)" + (f"; {detail}" if detail else "")
    row = {"at": clock.now(), "channel": channel, "recipient": (recipient or "")[:320], "status": status,
           "subject": (subject or "")[:500], "request_id": request_id, "request_number": number,
           "event": (event or "")[:40], "detail": (detail or "")[:1000] or None, "ref": ref}
    try:
        if db_sql.configured():
            db_sql.log_add(row)
            return
        with _lock:
            _next_id[0] += 1
            _memory.appendleft({**row, "id": _next_id[0]})
    except Exception:
        logger.exception("Не вдалося записати в журнал сповіщень")


def update_ref(ref, status, detail=None):
    """Оновити запис за посиланням (напр. повідомлення бота «tg:15»: у черзі → надіслано)."""
    try:
        if db_sql.configured():
            db_sql.log_update_ref(ref, status, detail, clock.now())
            return
        with _lock:
            for r in _memory:
                if r.get("ref") == ref:
                    r.update(status=status, detail=detail or r.get("detail"), at=clock.now())
    except Exception:
        logger.exception("Не вдалося оновити журнал сповіщень")


def query(limit=300, channel="", status="", q="", request_id=None):
    """Найновіші записи з фільтрами: channel (email/telegram), status (problems/skipped/код), q (пошта або № заявки)."""
    q = (q or "").strip().lower()
    if db_sql.configured():
        rows = db_sql.log_query(limit, channel, status, q, request_id, PROBLEMS)
    else:
        with _lock:
            rows = [dict(r) for r in _memory]
        rows = [r for r in rows if (not channel or r["channel"] == channel)
                and (not status or (r["status"] in PROBLEMS if status == "problems" else r["status"] == status))
                and (not q or q in (r["recipient"] or "").lower() or q in str(r["request_number"] or ""))
                and (request_id is None or r["request_id"] == request_id)][:limit]
    return rows
