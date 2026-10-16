"""База даних системи: Azure SQL на App Service (або будь-яка БД за DATABASE_URL — напр. SQLite у тестах).

Вмикається змінними оточення:
  SQL_SERVER=ftp-payreq-sql.database.windows.net, SQL_DATABASE=payrequests — Azure SQL, вхід без пароля:
    на App Service — керованою ідентичністю (Managed Identity), локально — обліковим записом Entra
    (DefaultAzureCredential). Користувача бази для ідентичності сайту створює адмін (README).
  DATABASE_URL=sqlite:///шлях.db — будь-яка БД SQLAlchemy (локальна перевірка, тести).
Без цих змінних база не використовується: заявки — в пам'яті (демо), налаштування — JSON-файли.

Що зберігається:
  requests             — заявки: уся заявка в JSON (data) + похідні колонки для списків, фільтрів і звітів
                         (storage/derive.py); version — лічильник змін заявки (захист від одночасних змін),
                         rev — номер останньої зміни в усій системі (оновлення кешу заявок «в роботі»);
  request_history      — історія заявок рядками, лише дописується (журнал аудиту, «Опрацьовані»);
  request_lines        — рядки рахунків (для звітів);
  request_participants — учасники заявок (список «Учасник»);
  request_files        — документи заявок (пошук заявки за файлом);
  request_rev          — один рядок: номер останньої зміни заявок;
  request_seq          — видача номерів нових заявок (унікальні навіть для кількох процесів);
  schema_meta          — службові значення (версія похідних колонок, зсув нумерації);
  documents            — налаштування й довідники системи (те, що раніше лежало в JSON-файлах у /home/data).
Нові колонки й індекси додаються до наявних таблиць автоматично (storage/schema.py).
"""
import json
import logging
import os
import struct
import threading
from datetime import date, datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.dialects import mssql

logger = logging.getLogger(__name__)

SQL_SCOPE = "https://database.windows.net/.default"
SQL_COPT_SS_ACCESS_TOKEN = 1256  # атрибут ODBC для передачі токена Entra

_engine = None
_engine_lock = threading.Lock()
_credential = None
_last_error = None

metadata = sa.MetaData()
# Дата-час з мікросекундами (у SQL Server тип DATETIME округлює до ~3 мс)
DateTime2 = sa.DateTime().with_variant(mssql.DATETIME2(), "mssql")

requests_t = sa.Table(
    "requests", metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=False),
    sa.Column("number", sa.Unicode(20), nullable=False),
    sa.Column("status", sa.Unicode(20), nullable=False),
    sa.Column("author_email", sa.Unicode(320), nullable=False),
    sa.Column("author_name", sa.Unicode(200)),
    sa.Column("department", sa.Unicode(64)),
    sa.Column("created_at", sa.DateTime),
    sa.Column("pay_date", sa.Date),
    sa.Column("organization_id", sa.Unicode(64)),
    sa.Column("counterparty_id", sa.Unicode(64)),
    sa.Column("expense_code", sa.Unicode(64)),
    sa.Column("payment_form", sa.Unicode(10)),
    sa.Column("currency", sa.Unicode(3)),
    sa.Column("amount", sa.Numeric(18, 2)),
    sa.Column("payment_account_id", sa.Unicode(64)),
    sa.Column("updated_at", sa.DateTime),
    sa.Column("data", sa.UnicodeText, nullable=False),
    # --- похідні колонки (storage/derive.py), заповнюються при збереженні й перераховуються reindex
    sa.Column("version", sa.Integer, nullable=False, server_default=sa.text("0")),
    sa.Column("rev", sa.BigInteger, nullable=False, server_default=sa.text("0")),
    sa.Column("active", sa.Boolean, nullable=False, server_default=sa.text("0")),
    sa.Column("author_lc", sa.Unicode(320)),
    sa.Column("paid_at", DateTime2),
    sa.Column("last_at", DateTime2),
    sa.Column("channel", sa.Unicode(30)),
    sa.Column("accountant_type", sa.Unicode(30)),
    sa.Column("accountant_role", sa.Unicode(30)),
    sa.Column("actor_role", sa.Unicode(30)),
    sa.Column("pending_roles", sa.Unicode(200)),
    sa.Column("pay_org", sa.Unicode(64)),
    sa.Column("note", sa.UnicodeText),
    sa.Column("handover_status", sa.Unicode(20)),
    sa.Column("handover_since", sa.Date),
)
sa.Index("ix_requests_status", requests_t.c.status)
sa.Index("ix_requests_author", requests_t.c.author_email)
sa.Index("ix_requests_active", requests_t.c.active)
sa.Index("ix_requests_rev", requests_t.c.rev)
sa.Index("ix_requests_author_lc", requests_t.c.author_lc, requests_t.c.created_at)
sa.Index("ix_requests_department", requests_t.c.department, requests_t.c.created_at)
sa.Index("ix_requests_status_pay", requests_t.c.status, requests_t.c.pay_date)
sa.Index("ix_requests_status_paid", requests_t.c.status, requests_t.c.paid_at)
sa.Index("ix_requests_created", requests_t.c.created_at)
sa.Index("ix_requests_counterparty", requests_t.c.counterparty_id)

history_t = sa.Table(
    "request_history", metadata,
    sa.Column("request_id", sa.Integer, primary_key=True, autoincrement=False),
    sa.Column("seq", sa.Integer, primary_key=True, autoincrement=False),
    sa.Column("at", sa.DateTime),
    sa.Column("user_name", sa.Unicode(200)),
    sa.Column("user_email", sa.Unicode(320)),
    sa.Column("role", sa.Unicode(40)),
    sa.Column("action", sa.Unicode(40)),
    sa.Column("from_status", sa.Unicode(20)),
    sa.Column("to_status", sa.Unicode(20)),
    sa.Column("comment", sa.UnicodeText),
)
sa.Index("ix_history_role_action", history_t.c.role, history_t.c.action, history_t.c.request_id)

lines_t = sa.Table(
    "request_lines", metadata,
    sa.Column("request_id", sa.Integer, primary_key=True, autoincrement=False),
    sa.Column("seq", sa.Integer, primary_key=True, autoincrement=False),
    sa.Column("invoice", sa.Unicode(200)),
    sa.Column("amount", sa.Numeric(18, 2)),
    sa.Column("currency", sa.Unicode(3)),
    sa.Column("deal", sa.Unicode(500)),
)

participants_t = sa.Table(
    "request_participants", metadata,
    sa.Column("request_id", sa.Integer, primary_key=True, autoincrement=False),
    sa.Column("email", sa.Unicode(320), primary_key=True),   # у нижньому регістрі
)
sa.Index("ix_participants_email", participants_t.c.email)

files_t = sa.Table(
    "request_files", metadata,
    sa.Column("file_id", sa.Unicode(64), primary_key=True),
    sa.Column("request_id", sa.Integer, nullable=False),
)

rev_t = sa.Table(
    "request_rev", metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=False),
    sa.Column("rev", sa.BigInteger, nullable=False),
)

seq_t = sa.Table(
    "request_seq", metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("created_at", sa.DateTime),
)

meta_t = sa.Table(
    "schema_meta", metadata,
    sa.Column("key", sa.Unicode(100), primary_key=True),
    sa.Column("value", sa.Unicode(1000)),
)

telegram_t = sa.Table(
    "telegram_outbox", metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("email", sa.Unicode(320), nullable=False),
    sa.Column("text", sa.UnicodeText, nullable=False),
    sa.Column("url", sa.Unicode(1000)),
    sa.Column("subject", sa.Unicode(500)),   # запасний лист, якщо людини немає в боті
    sa.Column("html", sa.UnicodeText),
    sa.Column("status", sa.Unicode(20), nullable=False),  # queued / taken / sent / not_found / blocked / failed
    sa.Column("created_at", sa.DateTime),
    sa.Column("taken_at", sa.DateTime),
    sa.Column("done_at", sa.DateTime),
    sa.Column("error", sa.Unicode(500)),
)
sa.Index("ix_telegram_status", telegram_t.c.status)

log_t = sa.Table(
    "notification_log", metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("at", sa.DateTime, nullable=False),
    sa.Column("channel", sa.Unicode(20), nullable=False),     # email / telegram / -
    sa.Column("recipient", sa.Unicode(320)),
    sa.Column("status", sa.Unicode(20), nullable=False),      # sent / queued / failed / not_found / blocked / skipped
    sa.Column("subject", sa.Unicode(500)),
    sa.Column("request_id", sa.Integer),
    sa.Column("request_number", sa.Unicode(20)),
    sa.Column("event", sa.Unicode(40)),
    sa.Column("detail", sa.Unicode(1000)),
    sa.Column("ref", sa.Unicode(40)),                         # напр. «tg:15» — повідомлення в черзі бота
)
sa.Index("ix_notification_log_at", log_t.c.at)
sa.Index("ix_notification_log_ref", log_t.c.ref)
sa.Index("ix_notification_log_request", log_t.c.request_id)

documents_t = sa.Table(
    "documents", metadata,
    sa.Column("name", sa.Unicode(100), primary_key=True),
    sa.Column("data", sa.UnicodeText, nullable=False),
    sa.Column("updated_at", sa.DateTime),
)


def configured():
    return bool(os.environ.get("DATABASE_URL") or (os.environ.get("SQL_SERVER") and os.environ.get("SQL_DATABASE")))


def kind():
    if os.environ.get("DATABASE_URL"):
        return os.environ["DATABASE_URL"].split(":", 1)[0]
    return "azure-sql" if configured() else None


# ---------------------------------------------------------------- з'єднання

def odbc_drivers():
    try:
        import pyodbc
        return [d for d in pyodbc.drivers() if "SQL Server" in d]
    except Exception as e:  # pyodbc не встановлено або немає unixODBC
        return [f"недоступно: {e}"]


def _driver():
    drivers = odbc_drivers()
    for name in ("ODBC Driver 18 for SQL Server", "ODBC Driver 17 for SQL Server"):
        if name in drivers:
            return name
    raise RuntimeError(f"Не знайдено ODBC-драйвер SQL Server 17/18 (є: {drivers})")


def _token():
    global _credential
    if _credential is None:
        from azure.identity import DefaultAzureCredential, ManagedIdentityCredential
        # На App Service — ідентичність сайту; локально — Entra-вхід розробника (az login / VS Code / браузер)
        _credential = ManagedIdentityCredential() if os.environ.get("IDENTITY_ENDPOINT") else \
            DefaultAzureCredential(exclude_interactive_browser_credential=False)
    raw = _credential.get_token(SQL_SCOPE).token.encode("utf-16-le")
    return struct.pack(f"<I{len(raw)}s", len(raw), raw)


def _azure_connect():
    import pyodbc
    conn_str = (f"Driver={{{_driver()}}};Server=tcp:{os.environ['SQL_SERVER']},1433;"
                f"Database={os.environ['SQL_DATABASE']};Encrypt=yes;TrustServerCertificate=no;Connection Timeout=30")
    return pyodbc.connect(conn_str, attrs_before={SQL_COPT_SS_ACCESS_TOKEN: _token()})


def engine():
    """Двигун SQLAlchemy; при першому виклику створює таблиці, яких ще немає."""
    global _engine
    if _engine is None:
        with _engine_lock:
            if _engine is None:
                if os.environ.get("DATABASE_URL"):
                    eng = sa.create_engine(os.environ["DATABASE_URL"], future=True)
                else:
                    eng = sa.create_engine("mssql+pyodbc://", creator=_azure_connect, future=True,
                                           pool_pre_ping=True, pool_recycle=1800, fast_executemany=False)
                metadata.create_all(eng)
                _engine = eng
    return _engine


def reset():
    """Для тестів: забути з'єднання (наступний виклик створить нове)."""
    global _engine
    with _engine_lock:
        if _engine is not None:
            _engine.dispose()
        _engine = None


def status():
    """Для /health: чи працює база."""
    global _last_error
    if not configured():
        return {"db": "off", "odbc_drivers": odbc_drivers()}
    try:
        with engine().connect() as c:
            count = c.execute(sa.select(sa.func.count()).select_from(requests_t)).scalar()
            docs = sorted(c.execute(sa.select(documents_t.c.name)).scalars())
        _last_error = None
        return {"db": kind(), "requests": count, "documents": docs}
    except Exception as e:
        _last_error = str(e)
        logger.exception("База даних недоступна")
        return {"db": kind(), "error": str(e)[:500], "odbc_drivers": odbc_drivers()}


# ---------------------------------------------------------------- JSON з датами й сумами

def _default(o):
    if isinstance(o, datetime):
        return {"__dt": o.isoformat()}
    if isinstance(o, date):
        return {"__d": o.isoformat()}
    if isinstance(o, Decimal):
        return {"__dec": str(o)}
    raise TypeError(f"Не вдається зберегти {type(o).__name__}")


def _hook(d):
    if len(d) == 1:
        if "__dt" in d:
            return datetime.fromisoformat(d["__dt"])
        if "__d" in d:
            return date.fromisoformat(d["__d"])
        if "__dec" in d:
            return Decimal(d["__dec"])
    return d


def dumps(obj):
    return json.dumps(obj, default=_default, ensure_ascii=False)


def loads(text):
    return json.loads(text, object_hook=_hook)


# ---------------------------------------------------------------- заявки

def _num(v):
    return v if isinstance(v, Decimal) or v is None else Decimal(str(v))


# ---------------------------------------------------------------- документи (налаштування, довідники)

def get_document(name):
    """Вміст документа (dict/list) або None, якщо його ще немає."""
    with engine().connect() as c:
        text = c.execute(sa.select(documents_t.c.data).where(documents_t.c.name == name)).scalar()
    return None if text is None else json.loads(text)


def put_document(name, data):
    import clock
    with engine().begin() as c:
        c.execute(documents_t.delete().where(documents_t.c.name == name))
        c.execute(documents_t.insert().values(name=name, data=json.dumps(data, ensure_ascii=False),
                                              updated_at=clock.now()))


# ---------------------------------------------------------------- черга повідомлень для Telegram-бота

def telegram_add(row):
    with engine().begin() as c:
        return c.execute(telegram_t.insert().values(**row)).inserted_primary_key[0]


def telegram_take(limit, now, stale_before):
    """Позначити «взяті» до limit повідомлень: нові й ті, що взяли давно, але не підтвердили. Повертає рядки."""
    with engine().begin() as c:
        rows = c.execute(sa.select(telegram_t).where(
            (telegram_t.c.status == "queued") | ((telegram_t.c.status == "taken") & (telegram_t.c.taken_at < stale_before))
        ).order_by(telegram_t.c.id).limit(limit)).mappings().all()
        ids = [r["id"] for r in rows]
        if ids:
            c.execute(telegram_t.update().where(telegram_t.c.id.in_(ids)).values(status="taken", taken_at=now))
    return [dict(r) for r in rows]


def telegram_finish(msg_id, status, error, now):
    """Записати результат від бота; повертає рядок (для запасного листа) або None."""
    with engine().begin() as c:
        row = c.execute(sa.select(telegram_t).where(telegram_t.c.id == msg_id)).mappings().first()
        if not row or row["status"] not in ("queued", "taken"):
            return None
        c.execute(telegram_t.update().where(telegram_t.c.id == msg_id).values(status=status, error=error, done_at=now))
    return dict(row)


def telegram_counts():
    with engine().connect() as c:
        rows = c.execute(sa.select(telegram_t.c.status, sa.func.count()).group_by(telegram_t.c.status)).all()
    return {r[0]: r[1] for r in rows}


# ---------------------------------------------------------------- журнал сповіщень

def log_add(row):
    with engine().begin() as c:
        c.execute(log_t.insert().values(**row))


def log_update_ref(ref, status, detail, now):
    values = {"status": status, "at": now}
    if detail:
        values["detail"] = detail[:1000]
    with engine().begin() as c:
        c.execute(log_t.update().where(log_t.c.ref == ref).values(**values))


def log_query(limit, channel, status, q, request_id, problems):
    stmt = sa.select(log_t).order_by(log_t.c.id.desc()).limit(limit)
    if channel:
        stmt = stmt.where(log_t.c.channel == channel)
    if status == "problems":
        stmt = stmt.where(log_t.c.status.in_(sorted(problems)))
    elif status:
        stmt = stmt.where(log_t.c.status == status)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(sa.func.lower(log_t.c.recipient).like(like) | log_t.c.request_number.like(like))
    if request_id is not None:
        stmt = stmt.where(log_t.c.request_id == request_id)
    with engine().connect() as c:
        return [dict(r) for r in c.execute(stmt).mappings().all()]
