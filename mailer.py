"""Відправка листів через SMTP (будь-яка пошта: Gmail, Microsoft 365, SendPulse…).

Сервер, логін і пароль додатку налаштовує адмін в інтерфейсі; пароль зберігається
зашифрованим (secret_box). Листи ставляться в чергу й надсилаються у фоновому потоці,
щоб дії в системі не чекали пошти. Токен Microsoft Graph (app_token) потрібен лише
для читання складу груп ролей — щоб знати, кому з погоджувачів надіслати лист.

З базою черга — таблиця mail_outbox: лист не губиться при перезапуску сайту, кожен лист надсилає рівно
один процес, а налаштування SMTP (з паролем) у черзі не зберігаються — їх бере config_provider
у момент відправки.
"""
import logging
import os
import queue
import smtplib
import socket
import ssl
import threading
import time
from collections import deque
from datetime import timedelta
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

import msal

import clock
import db_sql

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
_wake = threading.Event()
LOG = deque(maxlen=50)  # останні листи: {at, to, subject, ok, error}
config_provider = None   # () -> налаштування SMTP; задає notifications (для черги в базі)
STALE_AFTER = timedelta(minutes=10)  # лист «узяв» процес, що зупинився, — через 10 хв його візьме інший


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


def log_problem(to, subject, error):
    """Записати в журнал проблему, через яку лист не було сформовано (напр., не прочитано групу)."""
    _log(to, subject, error)


def deliver(cfg, to, subject, html, meta=None):
    """Надіслати й записати в журнал. meta — {"request_id", "number", "event", "recipient", "detail"}.
    Повертає None або текст помилки."""
    import notify_log
    meta = meta or {}
    log_args = {"subject": subject, "request_id": meta.get("request_id"), "number": meta.get("number"),
                "event": meta.get("event", "")}
    recipient = meta.get("recipient") or to
    detail = meta.get("detail") or ""
    if recipient.lower() != (to or "").lower():
        detail = (detail + "; " if detail else "") + f"на адресу {to}"
    try:
        transport(cfg, to, subject, html)
        _log(to, subject)
        notify_log.record("email", recipient, "sent", detail=detail, **log_args)
        return None
    except Exception as e:  # мережа, права — у журнал, дію користувача не ламаємо
        logger.warning("Не вдалося надіслати лист %s: %s", to, e)
        _log(to, subject, str(e))
        notify_log.record("email", recipient, "failed", detail=(detail + "; " if detail else "") + str(e), **log_args)
        return str(e)


def _run():
    while True:
        job = _queue.get()
        try:
            deliver(*job)
        finally:
            _queue.task_done()


def process_outbox(limit=20):
    """Надіслати листи з черги в базі, які ще ніхто не надсилає. Повертає кількість спроб."""
    n = 0
    while True:
        now = clock.now()
        rows = db_sql.mail_take(limit, now, now - STALE_AFTER)
        if not rows:
            return n
        cfg = config_provider() if config_provider else {}
        for r in rows:
            error = deliver(cfg, r["to_addr"], r["subject"], r["html"], db_sql.loads(r["meta"]) if r["meta"] else None)
            db_sql.mail_finish(r["id"], "failed" if error else "sent", error, clock.now())
            n += 1


def _run_outbox():
    while True:
        _wake.wait(timeout=30)  # лист з цього процесу — одразу; з інших процесів і після перезапуску — до 30 с
        _wake.clear()
        try:
            process_outbox()
        except Exception:
            logger.exception("Помилка відправки черги листів")


def start_worker():
    """Запустити фонову відправку (раз на процес)."""
    global _worker
    with _worker_lock:
        if _worker is None or not _worker.is_alive():
            target = _run_outbox if db_sql.configured() else _run
            _worker = threading.Thread(target=target, name="mailer", daemon=True)
            _worker.start()


def enqueue(cfg, to, subject, html, meta=None):
    """Поставити лист у чергу (надсилається у фоні). meta — для журналу сповіщень (заявка, подія)."""
    start_worker()
    if db_sql.configured():
        db_sql.mail_add({"to_addr": to, "subject": subject[:500], "html": html, "status": "queued",
                         "meta": db_sql.dumps(meta) if meta else None, "created_at": clock.now()})
        _wake.set()
        return
    _queue.put((cfg, to, subject, html, meta))


def wait(timeout=30):
    """Дочекатися відправки всієї черги (для тестів)."""
    if db_sql.configured():
        deadline = time.time() + timeout
        while time.time() < deadline:
            process_outbox()
            if not db_sql.mail_pending():
                break
            time.sleep(0.05)
    _queue.join()
