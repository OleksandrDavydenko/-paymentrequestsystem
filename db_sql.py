"""База даних системи: Azure SQL на App Service (або будь-яка БД за DATABASE_URL — напр. SQLite у тестах).

Вмикається змінними оточення:
  SQL_SERVER=ftp-payreq-sql.database.windows.net, SQL_DATABASE=payrequests — Azure SQL, вхід без пароля:
    на App Service — керованою ідентичністю (Managed Identity), локально — обліковим записом Entra
    (DefaultAzureCredential). Користувача бази для ідентичності сайту створює адмін (README).
  DATABASE_URL=sqlite:///шлях.db — будь-яка БД SQLAlchemy (локальна перевірка, тести).
Без цих змінних база не використовується: заявки — в пам'яті (демо), налаштування — JSON-файли.

Що зберігається:
  requests          — заявки: ключові поля окремими колонками (для звітів / Power BI) + уся заявка в JSON (data);
  request_history   — історія заявок рядками (копія з data — для звітів);
  request_lines     — рядки рахунків (копія з data — для звітів);
  documents         — налаштування й довідники системи (те, що раніше лежало в JSON-файлах у /home/data).
"""
import json
import logging
import os
import struct
import threading
from datetime import date, datetime
from decimal import Decimal

import sqlalchemy as sa

logger = logging.getLogger(__name__)

SQL_SCOPE = "https://database.windows.net/.default"
SQL_COPT_SS_ACCESS_TOKEN = 1256  # атрибут ODBC для передачі токена Entra

_engine = None
_engine_lock = threading.Lock()
_credential = None
_last_error = None

metadata = sa.MetaData()

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
)
sa.Index("ix_requests_status", requests_t.c.status)
sa.Index("ix_requests_author", requests_t.c.author_email)

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

lines_t = sa.Table(
    "request_lines", metadata,
    sa.Column("request_id", sa.Integer, primary_key=True, autoincrement=False),
    sa.Column("seq", sa.Integer, primary_key=True, autoincrement=False),
    sa.Column("invoice", sa.Unicode(200)),
    sa.Column("amount", sa.Numeric(18, 2)),
    sa.Column("currency", sa.Unicode(3)),
    sa.Column("deal", sa.Unicode(500)),
)

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
        _last_error = None
        return {"db": kind(), "requests": count}
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


def load_requests():
    """Усі заявки {id: заявка}."""
    with engine().connect() as c:
        rows = c.execute(sa.select(requests_t.c.id, requests_t.c.data)).all()
    return {r.id: loads(r.data) for r in rows}


def save_requests(reqs):
    """Записати заявки (нові й змінені) однією транзакцією."""
    import clock
    now = clock.now()
    with engine().begin() as c:
        for r in reqs:
            rid = r["id"]
            c.execute(requests_t.delete().where(requests_t.c.id == rid))
            c.execute(history_t.delete().where(history_t.c.request_id == rid))
            c.execute(lines_t.delete().where(lines_t.c.request_id == rid))
            c.execute(requests_t.insert().values(
                id=rid, number=r["number"], status=r["status"], author_email=r["author_email"],
                author_name=r.get("author_name"), department=r.get("department"), created_at=r.get("created_at"),
                pay_date=r.get("pay_date"), organization_id=r.get("organization_id"),
                counterparty_id=r.get("counterparty_id"), expense_code=r.get("expense_code"),
                payment_form=r.get("payment_form"), currency=r.get("currency"), amount=_num(r.get("amount")),
                payment_account_id=r.get("payment_account_id"), updated_at=now, data=dumps(r)))
            history = [{"request_id": rid, "seq": i, "at": h.get("at"), "user_name": h.get("user_name"),
                        "user_email": h.get("user_email"), "role": h.get("role"), "action": h.get("action"),
                        "from_status": h.get("from_status"), "to_status": h.get("to_status"),
                        "comment": h.get("comment") or ""} for i, h in enumerate(r.get("history") or [])]
            if history:
                c.execute(history_t.insert(), history)
            lines = [{"request_id": rid, "seq": i, "invoice": ln.get("invoice"), "amount": _num(ln.get("amount")),
                      "currency": ln.get("currency"), "deal": ln.get("deal")} for i, ln in enumerate(r.get("lines") or [])]
            if lines:
                c.execute(lines_t.insert(), lines)


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
