"""Відправка листів через SMTP (будь-яка пошта: Gmail, Microsoft 365, SendPulse…).

Сервер, логін і пароль додатку налаштовує адмін в інтерфейсі; пароль зберігається
зашифрованим (secret_box). Листи ставляться в чергу й надсилаються у фоновому потоці,
щоб дії в системі не чекали пошти. Токен Microsoft Graph (app_token) потрібен лише
для читання складу груп ролей — щоб знати, кому з погоджувачів надіслати лист.
"""
import logging
import os
import queue
import smtplib
import socket
import ssl
import threading
from collections import deque
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

import msal

import clock

logger = logging.getLogger(__name__)

GRAPH_SCOPE = ["https://graph.microsoft.com/.default"]

# Готові налаштування для популярних поштових сервісів
PRESETS = {
    "gmail": ("Gmail / Google Workspace", "smtp.gmail.com", 587, "starttls"),
    "m365": ("Microsoft 365 / Outlook", "smtp.office365.com", 587, "starttls"),
    "sendpulse": ("SendPulse", "smtp-pulse.com", 465, "ssl"),
}
SECURITY = {"starttls": "STARTTLS (порт 587)", "ssl": "SSL/TLS (порт 465)", "none": "Без шифрування"}

_app = None
_queue = queue.Queue()
_worker = None
_worker_lock = threading.Lock()
LOG = deque(maxlen=50)  # останні листи: {at, to, subject, ok, error}


class MailError(Exception):
    pass


def app_token():
    """Токен додатку для Microsoft Graph (client credentials) — для читання груп ролей."""
    global _app
    if _app is None:
        _app = msal.ConfidentialClientApplication(
            os.environ["CLIENT_ID"],
            authority=f"https://login.microsoftonline.com/{os.environ['TENANT_ID']}",
            client_credential=os.environ["CLIENT_SECRET"],
        )
    result = _app.acquire_token_for_client(scopes=GRAPH_SCOPE)
    if "access_token" not in result:
        raise MailError(f"Не вдалося отримати токен Microsoft Graph: {result.get('error')}")
    return result["access_token"]


def _explain(exc, host, has_password=True):
    code = getattr(exc, "smtp_code", None)
    if code == 530 or (code in (550, 553) and not has_password):
        return ("Сервер вимагає авторизацію: " + ("пароль не задано — " if not has_password else "")
                + "вкажіть логін і пароль додатку й збережіть налаштування.")
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return ("Сервер не прийняв логін або пароль. Для Gmail потрібен «пароль додатку» "
                "(Google-акаунт → Безпека → Двоетапна перевірка → Паролі додатків), а не звичайний пароль. "
                f"({exc.smtp_code} {exc.smtp_error.decode(errors='ignore')[:120] if isinstance(exc.smtp_error, bytes) else exc.smtp_error})")
    if isinstance(exc, smtplib.SMTPSenderRefused):
        return f"Сервер не дозволяє надсилати від цієї адреси: {exc.smtp_code} {exc.smtp_error!s:.150}"
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        return f"Сервер відхилив одержувача: {list(exc.recipients)[:1]}"
    if isinstance(exc, (socket.gaierror, ConnectionRefusedError, socket.timeout, TimeoutError)):
        return f"Не вдалося з'єднатися з {host}: перевірте сервер, порт і тип шифрування ({exc})"
    if isinstance(exc, ssl.SSLError):
        return f"Помилка шифрування з {host}: перевірте, чи правильно вибрано STARTTLS / SSL ({exc})"
    return f"Помилка SMTP: {exc}"


def send_now(cfg, to, subject, html, text=None):
    """Надіслати лист одразу (синхронно). cfg — dict з налаштуваннями SMTP. Кидає MailError."""
    if not cfg.get("host") or not cfg.get("sender"):
        raise MailError("Не вказано SMTP-сервер або адресу відправника")
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr((cfg.get("sender_name") or "", cfg["sender"]))
    msg["To"] = to
    msg["Message-ID"] = make_msgid(domain=cfg["sender"].split("@")[-1])
    msg.set_content(text or "Відкрийте цей лист у програмі, що підтримує HTML.")
    msg.add_alternative(html, subtype="html")
    host, port, security = cfg["host"], int(cfg.get("port") or 587), cfg.get("security") or "starttls"
    try:
        context = ssl.create_default_context()
        if security == "ssl":
            server = smtplib.SMTP_SSL(host, port, timeout=30, context=context)
        else:
            server = smtplib.SMTP(host, port, timeout=30)
        with server:
            if security == "starttls":
                server.starttls(context=context)
            if cfg.get("username") and cfg.get("password"):
                server.login(cfg["username"], cfg["password"])
            server.send_message(msg)
    except MailError:
        raise
    except Exception as e:
        raise MailError(_explain(e, host, bool(cfg.get("password")))) from e


# Транспорт можна підмінити в тестах
transport = send_now


def _log(to, subject, error=None):
    LOG.appendleft({"at": clock.now(), "to": to, "subject": subject, "ok": error is None, "error": error})


def deliver(cfg, to, subject, html):
    """Надіслати й записати в журнал. Повертає None або текст помилки."""
    try:
        transport(cfg, to, subject, html)
        _log(to, subject)
        return None
    except Exception as e:  # мережа, права — у журнал, дію користувача не ламаємо
        logger.warning("Не вдалося надіслати лист %s: %s", to, e)
        _log(to, subject, str(e))
        return str(e)


def _run():
    while True:
        job = _queue.get()
        try:
            deliver(*job)
        finally:
            _queue.task_done()


def enqueue(cfg, to, subject, html):
    """Поставити лист у чергу (надсилається у фоні)."""
    global _worker
    with _worker_lock:
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_run, name="mailer", daemon=True)
            _worker.start()
    _queue.put((cfg, to, subject, html))


def wait():
    """Дочекатися відправки всієї черги (для тестів)."""
    _queue.join()
