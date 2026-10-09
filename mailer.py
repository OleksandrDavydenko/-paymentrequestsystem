"""Відправка листів через Microsoft Graph (sendMail) від імені додатку — без паролів і SMTP.

Лист надсилається зі скриньки, яку адмін вказав у налаштуваннях. Права:
application-дозвіл Mail.Send, обмежений у Exchange лише цією скринькою.
Листи ставляться в чергу й надсилаються у фоновому потоці, щоб дії в системі не чекали пошти.
"""
import logging
import os
import queue
import threading
from collections import deque

import msal
import requests

import clock

logger = logging.getLogger(__name__)

GRAPH = "https://graph.microsoft.com/v1.0"
SCOPE = ["https://graph.microsoft.com/.default"]

_app = None
_queue = queue.Queue()
_worker = None
_worker_lock = threading.Lock()
LOG = deque(maxlen=50)  # останні листи: {at, to, subject, ok, error}


class MailError(Exception):
    pass


def app_token():
    """Токен додатку для Microsoft Graph (client credentials)."""
    global _app
    if _app is None:
        _app = msal.ConfidentialClientApplication(
            os.environ["CLIENT_ID"],
            authority=f"https://login.microsoftonline.com/{os.environ['TENANT_ID']}",
            client_credential=os.environ["CLIENT_SECRET"],
        )
    result = _app.acquire_token_for_client(scopes=SCOPE)
    if "access_token" not in result:
        raise MailError(f"Не вдалося отримати токен Microsoft Graph: {result.get('error')}")
    return result["access_token"]


def _explain(resp, sender):
    try:
        err = resp.json().get("error", {})
        code, message = err.get("code", ""), err.get("message", "")
    except ValueError:
        code, message = "", resp.text[:200]
    hints = {
        401: "Перевірте секрет додатку (CLIENT_SECRET).",
        403: f"Немає права надсилати від {sender}: додайте дозвіл Mail.Send (Application) з admin consent "
             f"і перевірте, що політика доступу Exchange дозволяє цю скриньку.",
        404: f"Скриньку {sender} не знайдено — перевірте адресу відправника.",
    }
    return f"Graph {resp.status_code} {code}: {message} {hints.get(resp.status_code, '')}".strip()


def send_now(sender, sender_name, to, subject, html):
    """Надіслати лист одразу (синхронно). Кидає MailError."""
    if not sender:
        raise MailError("Не вказано пошту відправника")
    resp = requests.post(
        f"{GRAPH}/users/{sender}/sendMail",
        headers={"Authorization": f"Bearer {app_token()}"},
        json={
            "message": {
                "subject": subject,
                "body": {"contentType": "HTML", "content": html},
                "from": {"emailAddress": {"address": sender, "name": sender_name or sender}},
                "toRecipients": [{"emailAddress": {"address": to}}],
            },
            "saveToSentItems": False,
        },
        timeout=30,
    )
    if resp.status_code != 202:
        raise MailError(_explain(resp, sender))


# Транспорт можна підмінити в тестах
transport = send_now


def _log(to, subject, error=None):
    LOG.appendleft({"at": clock.now(), "to": to, "subject": subject, "ok": error is None, "error": error})


def deliver(sender, sender_name, to, subject, html):
    """Надіслати й записати в журнал. Повертає None або текст помилки."""
    try:
        transport(sender, sender_name, to, subject, html)
        _log(to, subject)
        return None
    except Exception as e:  # мережа, права — у журнал, дію користувача не ламаємо
        logger.exception("Не вдалося надіслати лист %s", to)
        _log(to, subject, str(e))
        return str(e)


def _run():
    while True:
        job = _queue.get()
        try:
            deliver(*job)
        finally:
            _queue.task_done()


def enqueue(sender, sender_name, to, subject, html):
    """Поставити лист у чергу (надсилається у фоні)."""
    global _worker
    with _worker_lock:
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_run, name="mailer", daemon=True)
            _worker.start()
    _queue.put((sender, sender_name, to, subject, html))


def wait():
    """Дочекатися відправки всієї черги (для тестів)."""
    _queue.join()
