"""Фонові задачі: нагадування про передачу готівки, щоденне обслуговування, синхронізація налаштувань."""
import logging
import os
import threading
import time
from datetime import timedelta

from flask import request

import alerts
import app_settings
import clock
import db_sql
import docstore
import filestore
import jobs
import mailer
import notifications
import sessions_db
import storage as db
import workflow as wf


REMINDER_INTERVAL = 3600  # перевірка раз на годину
_app = None  # додаток (init_app) — листи з фонового потоку будують посилання через його контекст запиту
_last_base_url = None


_reminders_started = False
_reminders_lock = threading.Lock()


def _public_base_url():
    """Адреса сайту для посилань у листах, які надсилаються не у відповідь на запит."""
    if os.environ.get("APP_BASE_URL"):
        return os.environ["APP_BASE_URL"].rstrip("/")
    if os.environ.get("WEBSITE_HOSTNAME"):  # App Service задає сам
        return "https://" + os.environ["WEBSITE_HOSTNAME"]
    return _last_base_url or "http://localhost:5000"


def run_handover_reminders(today=None):
    """Нагадати авторам, які не підтвердили передачу готівки. Повертає номери заявок, яким надіслано."""
    docstore.sync()  # фоновий потік: налаштування могли змінитися в іншому процесі
    every = app_settings.get("cash_handover_remind_days") or 0
    if not app_settings.get("cash_handover_confirm") or every <= 0 or notifications.test_mode():
        return []
    sent = []
    for r in db.list_requests_full("handover_all"):
        if not wf.handover_reminder_due(r, every, today):
            continue
        with _app.test_request_context(base_url=_public_base_url()):
            if not notifications.send_handover_reminder(r, wf.handover_days(r, today)):
                continue  # розсилку вимкнено — нагадаємо, коли ввімкнуть
        wf.mark_reminded(r, today)
        try:
            db.save_request(r)
        except db.StaleRequestError:  # автор якраз підтвердив передачу — наступна перевірка це врахує
            continue
        sent.append(r["number"])
    return sent


def run_maintenance():
    """Щоденне обслуговування бази: перерахунок похідних колонок заявок (довідники з 1С могли змінитися),
    прибирання прострочених сеансів і надісланих листів, старших за 30 днів (журнал сповіщень лишається)."""
    if not db_sql.configured():
        return {}
    return {"reindexed": db.sql.reindex(), "sessions_removed": sessions_db.cleanup(),
            "mail_removed": db_sql.mail_cleanup(clock.now() - timedelta(days=30))}


def _reminder_loop():
    """Фонові задачі. Кожну виконує лише один процес сайту (jobs.run — «оренда» в базі)."""
    time.sleep(60)
    while True:
        for name, every, fn in (("handover_reminders", jobs.HOUR - timedelta(minutes=5), run_handover_reminders),
                                ("maintenance", jobs.DAY - timedelta(minutes=5), run_maintenance)):
            try:
                jobs.run(name, every, fn)
            except Exception as e:
                logging.getLogger(__name__).exception("Помилка фонової задачі %s", name)
                alerts.report(e, f"фонова задача «{name}»", base_url=_public_base_url())
        time.sleep(REMINDER_INTERVAL)


def _start_background():
    global _last_base_url, _reminders_started
    _last_base_url = request.host_url.rstrip("/")
    filestore.start_migration()
    if _reminders_started or not (os.environ.get("WEBSITE_SITE_NAME") or os.environ.get("HANDOVER_REMINDERS") == "1"):
        return
    with _reminders_lock:
        if not _reminders_started:
            _reminders_started = True
            threading.Thread(target=_reminder_loop, name="background-jobs", daemon=True).start()
            mailer.start_worker()  # листи, що лишилися в черзі після перезапуску


def _sync_shared_state():
    """Налаштування й довідники, змінені іншими процесами сайту, — перечитати (один маленький запит)."""
    try:
        docstore.sync()
    except Exception:
        logging.getLogger(__name__).exception("Не вдалося перевірити зміни налаштувань")


def init_app(app):
    """Підключити до додатку: перевірки на початку кожного запиту й запуск фонових задач."""
    global _app
    _app = app
    app.before_request(_start_background)
    app.before_request(_sync_shared_state)
