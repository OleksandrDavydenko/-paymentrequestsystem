"""Сповіщення через корпоративний Telegram-бот (tb_ftp на Heroku).

Бот сам забирає повідомлення з черги (раз на ~30 с) через захищені маршрути /api/telegram/* із заголовком
X-Api-Key = TELEGRAM_BOT_KEY (той самий секрет — у Config Vars бота). Людину бот знаходить за робочою поштою
(Employees[Email] у Power BI). Якщо людини немає в боті або вона заблокувала бота — система шле запасний лист
(для тих, хто вибрав «лише Telegram»). Без TELEGRAM_BOT_KEY Telegram вимкнено.

Черга — у базі (таблиця telegram_outbox), а без бази — у пам'яті процесу.
"""
import hmac
import logging
import os
import threading
from datetime import timedelta

import clock
import db_sql
import docstore

logger = logging.getLogger(__name__)

STALE = timedelta(minutes=5)   # взяте, але не підтверджене за цей час — віддаємо ботові знову
CHANNELS = {"email": "Пошта", "telegram": "Telegram", "both": "Пошта і Telegram"}
DONE_LABELS = {"sent": "надіслано в Telegram", "not_found": "немає в боті", "blocked": "бот заблоковано користувачем",
               "failed": "помилка Telegram"}

_lock = threading.Lock()
_memory = []           # черга без бази
_state = {"last_poll": None, "linked": None}
on_fallback = None     # (row, причина) -> надіслати запасний лист; встановлюється з notifications
on_result = None       # (рядок черги, статус, помилка або None) -> журнал


def enabled():
    return bool(os.environ.get("TELEGRAM_BOT_KEY"))


def key_ok(key):
    secret = os.environ.get("TELEGRAM_BOT_KEY") or ""
    return bool(secret) and hmac.compare_digest(str(key or ""), secret)


def _path():
    base = "/home/data" if os.environ.get("WEBSITE_SITE_NAME") else os.path.join(os.path.dirname(__file__), "data")
    return os.environ.get("TELEGRAM_LINKED_FILE") or os.path.join(base, "telegram_linked.json")


# ---------------------------------------------------------------- черга

def enqueue(email, text, url, subject=None, html=None):
    """Поставити повідомлення в чергу для бота. subject/html — запасний лист (якщо людини немає в боті)."""
    row = {"email": (email or "").lower(), "text": text[:3900], "url": url, "subject": subject, "html": html,
           "status": "queued", "created_at": clock.now(), "taken_at": None, "done_at": None, "error": None}
    if db_sql.configured():
        return db_sql.telegram_add(row)
    with _lock:
        row["id"] = len(_memory) + 1
        _memory.append(row)
        return row["id"]


def take(limit=50):
    """Повідомлення для бота (позначаються «взятими»)."""
    now = clock.now()
    _state["last_poll"] = now
    if db_sql.configured():
        rows = db_sql.telegram_take(limit, now, now - STALE)
    else:
        with _lock:
            rows = [r for r in _memory if r["status"] == "queued"
                    or (r["status"] == "taken" and r["taken_at"] and r["taken_at"] < now - STALE)][:limit]
            for r in rows:
                r.update(status="taken", taken_at=now)
            rows = [dict(r) for r in rows]
    import user_prefs  # пошта для бота: вказана адміном вручну або робоча
    return [{"id": r["id"], "email": user_prefs.telegram_lookup(r["email"]), "text": r["text"], "url": r["url"]}
            for r in rows]


def ack(results):
    """Результати від бота: [{"id", "status": sent|not_found|blocked|failed, "error"}]. Повертає кількість прийнятих."""
    count = 0
    now = clock.now()
    for res in results or []:
        status = res.get("status")
        if status not in DONE_LABELS:
            continue
        error = (str(res.get("error") or "")[:500]) or None
        try:
            msg_id = int(res.get("id"))
        except (TypeError, ValueError):
            continue
        if db_sql.configured():
            row = db_sql.telegram_finish(msg_id, status, error, now)
        else:
            with _lock:
                found = next((r for r in _memory if r["id"] == msg_id and r["status"] in ("queued", "taken")), None)
                if found:
                    found.update(status=status, error=error, done_at=now)
                row = dict(found) if found else None
        if not row:
            continue
        count += 1
        if on_result:
            on_result(row, status, None if status == "sent" else (error or DONE_LABELS[status]))
        if status != "sent" and row.get("subject") and row.get("html") and on_fallback:
            on_fallback(row, DONE_LABELS[status])
    return count


def counts():
    if db_sql.configured():
        return db_sql.telegram_counts()
    with _lock:
        out = {}
        for r in _memory:
            out[r["status"]] = out.get(r["status"], 0) + 1
        return out


# ---------------------------------------------------------------- хто є в боті

def set_linked(emails):
    data = {"emails": sorted({(e or "").strip().lower() for e in emails or [] if e and "@" in e}),
            "updated_at": clock.now().isoformat(timespec="seconds")}
    docstore.write("telegram_linked", _path(), data)
    _state["linked"] = data
    return len(data["emails"])


def _linked():
    if _state["linked"] is None:
        try:
            _state["linked"] = docstore.read("telegram_linked", _path())
        except (OSError, ValueError):
            _state["linked"] = {"emails": [], "updated_at": None}
    return _state["linked"]


def is_linked(email):
    """True / False — чи знає бот людину; None — бот ще не надсилав список."""
    data = _linked()
    if not data.get("updated_at"):
        return None
    return (email or "").lower() in set(data.get("emails") or [])


def status():
    data = _linked()
    return {"enabled": enabled(), "last_poll": _state["last_poll"], "linked": len(data.get("emails") or []),
            "linked_updated": data.get("updated_at"), "counts": counts() if enabled() else {}}


# Інший процес сайту оновив список — перечитати (docstore.sync)
docstore.register("telegram_linked", lambda: _state.update(linked=None))
