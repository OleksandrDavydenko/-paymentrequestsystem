"""Оновлення схеми старої бази (як на сайті до сховища storage/): нові колонки, індекси, DATETIME2 на
SQL Server, перерахунок похідних колонок — і наявна заявка далі відкривається, редагується й видна в списках."""
import json, os, sys, tempfile
from datetime import date, datetime
sys.path.insert(0, os.getcwd())
tmp = tempfile.mkdtemp()
url = os.environ.get("TEST_DATABASE_URL") or "sqlite:///" + os.path.join(tmp, "old.db").replace("\\", "/")
os.environ.update(UPLOAD_DIR=tmp, DATABASE_URL=url)
import sqlalchemy as sa

ok = True
def check(label, cond):
    global ok
    ok &= bool(cond); print(("PASS " if cond else "FAIL ") + label)

# ---------- стара схема (до 16.10.2026) і одна заявка в ній
old = sa.MetaData()
sa.Table("requests", old,
         sa.Column("id", sa.Integer, primary_key=True, autoincrement=False), sa.Column("number", sa.Unicode(20), nullable=False),
         sa.Column("status", sa.Unicode(20), nullable=False), sa.Column("author_email", sa.Unicode(320), nullable=False),
         sa.Column("author_name", sa.Unicode(200)), sa.Column("department", sa.Unicode(64)), sa.Column("created_at", sa.DateTime),
         sa.Column("pay_date", sa.Date), sa.Column("organization_id", sa.Unicode(64)), sa.Column("counterparty_id", sa.Unicode(64)),
         sa.Column("expense_code", sa.Unicode(64)), sa.Column("payment_form", sa.Unicode(10)), sa.Column("currency", sa.Unicode(3)),
         sa.Column("amount", sa.Numeric(18, 2)), sa.Column("payment_account_id", sa.Unicode(64)), sa.Column("updated_at", sa.DateTime),
         sa.Column("data", sa.UnicodeText, nullable=False))
sa.Index("ix_requests_status", old.tables["requests"].c.status)
sa.Table("request_history", old,
         sa.Column("request_id", sa.Integer, primary_key=True, autoincrement=False),
         sa.Column("seq", sa.Integer, primary_key=True, autoincrement=False), sa.Column("at", sa.DateTime),
         sa.Column("user_name", sa.Unicode(200)), sa.Column("user_email", sa.Unicode(320)), sa.Column("role", sa.Unicode(40)),
         sa.Column("action", sa.Unicode(40)), sa.Column("from_status", sa.Unicode(20)), sa.Column("to_status", sa.Unicode(20)),
         sa.Column("comment", sa.UnicodeText))
sa.Table("documents", old, sa.Column("name", sa.Unicode(100), primary_key=True),
         sa.Column("data", sa.UnicodeText, nullable=False), sa.Column("updated_at", sa.DateTime))
eng = sa.create_engine(url)
old.create_all(eng)
created = datetime(2026, 10, 1, 9, 30, 15, 123456)
req = {"id": 7, "number": "000000007", "status": "to_pay", "author_email": "Author@ftpua.com", "author_name": "Автор",
       "department": None, "created_at": {"__dt": created.isoformat()}, "pay_date": {"__d": "2026-10-20"},
       "organization_id": "1", "counterparty_id": "1", "expense_code": "01.001", "payment_form": "bank",
       "currency": "UAH", "amount": {"__dec": "100.00"}, "note": "стара заявка", "lines": [], "attachments": [],
       "history": [{"at": {"__dt": created.isoformat()}, "user_name": "Автор", "user_email": "Author@ftpua.com",
                    "role": "initiator", "action": "submit", "from_status": "draft", "to_status": "accountant", "comment": ""}]}
with eng.begin() as c:
    c.execute(old.tables["requests"].insert().values(
        id=7, number="000000007", status="to_pay", author_email="Author@ftpua.com", created_at=created,
        pay_date=date(2026, 10, 20), amount=100, data=json.dumps(req, ensure_ascii=False)))
    c.execute(old.tables["documents"].insert().values(name="app_settings", data=json.dumps({}), updated_at=created))
eng.dispose()

# ---------- оновлення: перше звернення до бази
import db_sql
import storage.sql as dbs
from workflow import RoleSet
db_sql.engine()
insp = sa.inspect(db_sql.engine())
cols = {c["name"].lower(): type(c["type"]).__name__ for c in insp.get_columns("requests")}
need = {c.name.lower() for c in db_sql.requests_t.columns}
check("all new request columns added", need <= set(cols))
check("documents.version added", "version" in {c["name"].lower() for c in insp.get_columns("documents")})
idx = {i["name"] for i in insp.get_indexes("requests")}
check("indexes created", {"ix_requests_active", "ix_requests_author_lc", "ix_requests_created"} <= idx)
check("new tables", all(insp.has_table(t) for t in ("request_rev", "request_seq", "schema_meta", "mail_outbox",
                                                     "job_lease", "web_sessions", "request_participants", "request_files")))
if db_sql.engine().dialect.name == "mssql":
    check("SQL Server: created_at widened to DATETIME2", cols["created_at"] == "DATETIME2"
          and type({c["name"]: c["type"] for c in insp.get_columns("request_history")}["at"]).__name__ == "DATETIME2")

# ---------- стара заявка працює
r = dbs.get_request(7)
check("old request readable (version 0)", r and r["note"] == "стара заявка" and r["version"] == 0)
check("derived columns backfilled -> visible in lists",
      [x["id"] for x in dbs.scope_rows("all", RoleSet({"admin"}), "")] == [7]
      and [x["id"] for x in dbs.scope_rows("author", RoleSet({"initiator"}), "author@ftpua.com")] == [7]
      and [x["id"] for x in dbs.scope_rows("to_pay", RoleSet({"cfo"}), "")] == [7])
r["note"] = "змінено після оновлення"
dbs.save_request(r)
check("old request saved with version 1", dbs.get_request(7)["version"] == 1 and dbs.get_request(7)["note"] == "змінено після оновлення")
new_id = dbs.save_request({**{k: v for k, v in dbs.get_request(7).items() if k not in ("id", "number", "version")},
                           "history": []})
check("numbering continues after old requests", new_id == 8)
import db_schema
db_schema.upgrade(db_sql.engine())
check("second upgrade is a no-op", dbs.get_request(8)["id"] == 8)
print("ALL OK" if ok else "SOME FAILED")
