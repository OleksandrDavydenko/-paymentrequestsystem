"""Листи адміністраторам про помилки сайту (простий замінник моніторингу без Azure).

Що надсилається: необроблена помилка під час запиту (користувач бачить сторінку «Щось пішло не так»)
і збій фонової задачі. Кому: адміністратори (група ролі «Адміністратор» + ADMIN_EMAILS).
Та сама помилка (тип + місце в коді) — не частіше ніж раз на годину для всіх процесів сайту разом.
Лист надсилається, коли налаштовано SMTP, незалежно від «Розсилка увімкнена» й тестового режиму;
вимикається в адмінці («Email-сповіщення» → «Листи адміністраторам про помилки сайту»).
У листі немає даних форм і заявок — лише адреса сторінки, користувач і технічний опис помилки.
"""
import hashlib
import html
import logging
import os
import socket
import threading
import traceback
from datetime import timedelta

import app_settings
import clock
import db_sql
import jobs
import mailer
import notifications

logger = logging.getLogger(__name__)

THROTTLE = timedelta(hours=1)
_recent = {}  # без бази: підпис помилки -> коли востаннє надсилали
_lock = threading.Lock()


def signature(exc):
    """Підпис помилки: тип + останнє місце в нашому коді (не в бібліотеках)."""
    frames = traceback.extract_tb(exc.__traceback__)
    ours = [f for f in frames if "site-packages" not in f.filename] or frames
    place = f"{os.path.basename(ours[-1].filename)}:{ours[-1].lineno}" if ours else "?"
    return type(exc).__name__ + "@" + place


def _first_in_window(sig):
    now = clock.now()
    if db_sql.configured():
        key = "alert:" + hashlib.sha1(sig.encode()).hexdigest()[:20]
        return db_sql.lease_acquire(key, jobs.OWNER, now, now + THROTTLE)
    with _lock:
        last = _recent.get(sig)
        if last and now - last < THROTTLE:
            return False
        _recent[sig] = now
        return True


def recipients():
    emails = [m.get("email") for m in notifications.cached_role_members("admin")] + list(notifications.admin_emails())
    out = []
    for e in emails:
        if e and e.lower() not in out:
            out.append(e.lower())
    return out


def report(exc, where, user=None, base_url=None):
    """Повідомити адміністраторів про помилку exc (where — «GET /requests/5» або «фонова задача …»).
    Ніколи не кидає винятків. Повертає список адрес, кому поставлено лист. Помилка завжди йде й у журнал роботи
    сайту (ops_log), навіть коли листи вимкнено."""
    try:
        import ops_log
        ops_log.record("error", where, None, "error", f"{type(exc).__name__}: {exc}")
    except Exception:
        logger.exception("Не вдалося записати помилку в журнал роботи")
    try:
        if not app_settings.get("error_alerts"):
            return []
        cfg = notifications.smtp_config()
        if not cfg.get("host") or not cfg.get("sender"):
            return []
        sig = signature(exc)
        if not _first_in_window(sig):
            return []
        to = recipients()
        if not to:
            return []
        subject = f"[Помилка сайту] {type(exc).__name__}: {str(exc)[:120]}"
        tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))[-8000:]
        rows = [("Коли", clock.now().strftime("%d.%m.%Y %H:%M:%S")), ("Де", where),
                ("Користувач", user or "—"), ("Сервер", socket.gethostname()), ("Помилка", f"{type(exc).__name__}: {exc}")]
        body = "".join(f"<tr><td style='padding:2px 12px 2px 0;color:#666'>{k}</td><td>{html.escape(str(v))}</td></tr>"
                       for k, v in rows)
        link = f"<p><a href='{html.escape(base_url)}'>Відкрити сайт</a></p>" if base_url else ""
        page = (f"<p>На сайті системи заявок сталася помилка.</p><table>{body}</table>{link}"
                f"<pre style='font-size:12px;background:#f6f6f6;padding:10px;white-space:pre-wrap'>{html.escape(tb)}</pre>"
                "<p style='color:#666'>Така сама помилка протягом години повторно не надсилається. "
                "Вимкнути ці листи: «Адміністрування → Email-сповіщення».</p>")
        for addr in to:
            mailer.enqueue(cfg, addr, subject, page, {"event": "error_alert", "detail": where[:200]})
        return to
    except Exception:
        logger.exception("Не вдалося надіслати лист про помилку")
        return []
