import json, os, sys, tempfile
from datetime import date, datetime
from decimal import Decimal
sys.path.insert(0, os.getcwd())
tmp = tempfile.mkdtemp()
# налаштування, що вже лежали у файлі до підключення бази — мають перенестися
with open(os.path.join(tmp, "s.json"), "w", encoding="utf-8") as f:
    json.dump({"cfo_mode": {"value": "observe", "updated_by": "old@ftpua.com", "updated_at": "2026-10-01T10:00:00"}}, f)
os.environ.update(UPLOAD_DIR=tmp, APP_SETTINGS_FILE=os.path.join(tmp, "s.json"), USER_PREFS_FILE=os.path.join(tmp, "p.json"),
                  USER_DIRECTORY_FILE=os.path.join(tmp, "d.json"), ORG_STRUCTURE_FILE=os.path.join(tmp, "o.json"),
                  COUNTERPARTIES_PENDING_FILE=os.path.join(tmp, "cp.json"), USER_BUDGET_FILE=os.path.join(tmp, "b.json"),
                  USER_ONEC_FILE=os.path.join(tmp, "u1c.json"),
                  DATABASE_URL=os.environ.get("TEST_DATABASE_URL") or "sqlite:///" + os.path.join(tmp, "test.db").replace("\\", "/"),
                  GROUP_ADMIN="g-a", GROUP_INITIATOR="g-i", GROUP_ACC_RESIDENT="g-r", GROUP_ACC_CASH="g-c", GROUP_CFO="g-f",
                  ADMIN_EMAILS="")
import app as m
import user_budget; user_budget.get = lambda oid: {"direct": True, "all_departments": True, "departments": []}
import app_settings, mailer, notifications, user_directory, org_structure, db_sql, notify_log
import sqlalchemy as sa
import storage as db
import storage.sql as dbs

def restart():
    """«Перезапуск» процесу: кеш заявок в роботі й підготовка схеми — заново."""
    dbs._cache.reset(); dbs._ready_engine = None

ok = True
def check(label, cond):
    global ok
    ok &= bool(cond)
    print(("PASS " if cond else "FAIL ") + label)

mailer.transport = lambda cfg, to, subject, html: None
notifications.role_members = lambda role: []
claims = {}
def login(email, groups):
    claims.clear(); claims.update({"name": email.split("@")[0].title(), "preferred_username": email, "oid": email, "groups": groups})
m.auth._auth.get_user = lambda: dict(claims)
m.auth._auth.get_token_for_user = lambda s: {"access_token": "t", "scope": " ".join(s)}
c = m.app.test_client()

def admin_page():
    """Сторінки адмінки разом (користувачі + вкладки налаштувань) — для перевірок вмісту."""
    return "".join(c.get(u).get_data(as_text=True) for u in ["/admin/users"] + [
        f"/admin/settings?tab={t}" for t in ("approval", "request", "payment", "notify", "data")])

check("db mode: work store empty (no demo)", db.list_all() == [] and db_sql.configured() and db.work_mode())
h = c.get("/health")
check("health: db, 0 requests", h.status_code == 200 and h.get_json()["db"] == db_sql.kind() and h.get_json()["requests"] == 0)

# ---------- налаштування: перенесення з файлу і запис у базу
check("settings migrated from file", app_settings.get("cfo_mode") == "observe")
check("migrated doc stored in db", db_sql.get_document("app_settings")["cfo_mode"]["value"] == "observe")
app_settings.set("cfo_mode", "approve", "t")
with open(os.path.join(tmp, "s.json"), encoding="utf-8") as f:
    file_val = json.load(f)["cfo_mode"]["value"]
check("new value in db, file untouched (backup)", db_sql.get_document("app_settings")["cfo_mode"]["value"] == "approve"
      and file_val == "observe")
app_settings.reload()
check("settings survive reload", app_settings.get("cfo_mode") == "approve")
user_directory.remember([{"id": "w", "email": "w@ftpua.com", "name": "Вікторія"}], active=True)
org_structure.set_department("w", "w@ftpua.com", "Вікторія", "LOG", "t")
check("other stores -> db", db_sql.get_document("user_directory") and db_sql.get_document("org_structure")
      and not os.path.exists(os.path.join(tmp, "o.json")))

# ---------- заявки
BASE = {"pay_date": "2026-10-20", "organization_id": "1", "counterparty_id": "1", "expense_code": "01.001",
        "currency": "UAH", "amount": "1234,56", "payment_form": "bank", "participants_sent": "1",
        "participants": ["w@ftpua.com"], "lines-0-invoice": "НП-1", "lines-0-amount": "1234.56", "lines-0-currency": "UAH",
        "lines-0-deal": "Договір 1"}
login("author@ftpua.com", ["g-i"])
r = c.post("/requests/new", data={**BASE, "action": "submit"})
rid = db.last_id()
check("first request number 000000001", rid == 1 and db.get_request(1)["number"] == "000000001")
login("res@ftpua.com", ["g-r"])
c.post(f"/requests/{rid}/action", data={"action": "approve", "payment_account_id": "1"})
before = db.get_request(rid)

with db_sql.engine().connect() as conn:
    row = conn.execute(sa.select(db_sql.requests_t).where(db_sql.requests_t.c.id == rid)).one()
    hist = conn.execute(sa.select(sa.func.count()).select_from(db_sql.history_t).where(db_sql.history_t.c.request_id == rid)).scalar()
    lines = conn.execute(sa.select(db_sql.lines_t)).all()
check("key columns", row.status == "cfo" and row.amount == Decimal("1234.56") and row.pay_date == date(2026, 10, 20)
      and row.author_email == "author@ftpua.com")
check("history + lines rows", hist == len(before["history"]) and len(lines) == 1 and lines[0].invoice == "НП-1")

# ---------- «перезапуск»: пам'ять очищено, читаємо з бази
restart()
after = db.get_request(rid)
check("request survives restart (identical)", after == before)
check("types restored", isinstance(after["created_at"], datetime) and isinstance(after["pay_date"], date)
      and isinstance(after["amount"], Decimal) and isinstance(after["history"][0]["at"], datetime)
      and isinstance(after["participants"][0]["added_at"], datetime))
login("author@ftpua.com", ["g-i"])
c.post("/requests/new", data={**BASE, "action": "save"})
check("numbering continues after restart", db.last_id() == 2)
page = c.get(f"/requests/{rid}").get_data(as_text=True)
check("card renders from db", "000000001" in page and "Вікторія" in page)

# ---------- масові зміни зберігаються
db.replace_counterparty("1", "2", {"name": "T", "email": "t@x"}, "admin", "тест")
restart()
check("replace_counterparty persisted", db.get_request(1)["counterparty_id"] == "2" and db.get_request(2)["counterparty_id"] == "2")

# ---------- помилка запису в базу не змінює пам'ять
def rev():
    with db_sql.engine().connect() as conn:
        return conn.execute(sa.select(db_sql.rev_t.c.rev)).scalar()
real = dbs._write_children
dbs._write_children = lambda c, r: (_ for _ in ()).throw(RuntimeError("db down"))
req = db.get_request(1); req["note"] = "не має зберегтися"
rev0 = rev()
try:
    db.save_request(req); failed = False
except RuntimeError:
    failed = True
dbs._write_children = real
restart()
check("db write failure -> exception, nothing saved (one transaction)",
      failed and db.get_request(1)["note"] != "не має зберегтися" and rev() == rev0)

# ---------- одночасні зміни: виграє перший, другий отримує StaleRequestError
a, b = db.get_request(1), db.get_request(1)
a["note"] = "перша зміна"; db.save_request(a)
b["note"] = "друга зміна"
try:
    db.save_request(b); stale = None
except db.StaleRequestError as e:
    stale = e
check("concurrent save -> StaleRequestError with current request",
      stale is not None and stale.current["note"] == "перша зміна" and db.get_request(1)["note"] == "перша зміна")
check("version grows by one per save", db.get_request(1)["version"] == a["version"] and a["version"] >= 2)

# ---------- історія лише дописується; учасники й файли — окремими таблицями
login("author@ftpua.com", ["g-i"])
n_hist = len(db.get_request(1)["history"])
c.post("/requests/1/comment", data={"comment": "Перевірка історії", "csrf_token": ""})
with db_sql.engine().connect() as conn:
    seqs = list(conn.execute(sa.select(db_sql.history_t.c.seq).where(db_sql.history_t.c.request_id == 1)
                             .order_by(db_sql.history_t.c.seq)).scalars())
    parts = list(conn.execute(sa.select(db_sql.participants_t.c.email).where(db_sql.participants_t.c.request_id == 1)).scalars())
check("history appended (seq 0..n)", seqs == list(range(len(db.get_request(1)["history"]))) and len(seqs) == n_hist + 1)
check("participants table", parts == ["w@ftpua.com"])
check("participant list from db (new first)", [r["id"] for r in db.list_participating("W@ftpua.com")] == [2, 1])

# ---------- перемикач «Тестові дані»
sent = []
mailer.transport = lambda cfg, to, subject, html: sent.append(subject)
for k, v in {"mail_enabled": True, "mail_sender": "noreply@ftpua.com"}.items():
    app_settings.set(k, v, "t")
login("admin@ftpua.com", ["g-a"])
page = admin_page()
check("admin: data mode block", 'name="data_mode"' in page and "Тестові дані" in page)
c.post("/admin/settings", data={"approval_mode": "sequential", "dept_head_mode": "view", "pending_cp_payment": "block",
                                "cfo_mode": "approve", "cp_onec_owner": "accountant", "data_mode": "test"})
check("test mode on", app_settings.get("data_mode") == "test" and not db.work_mode())
check("test mode: demo requests shown", len(db.list_all()) > 10 and "000000020" in c.get("/admin/requests").get_data(as_text=True))
check("banner for everyone", "Тестовий режим" in c.get("/admin/requests").get_data(as_text=True))
check("banner: mail off", "email-розсилку вимкнено" in c.get("/admin/requests").get_data(as_text=True))
admin_page = admin_page()
check("switching to test mode unticks 'mailing on', no separate switch",
      app_settings.get("mail_enabled") is False and app_settings.get("mail_enabled_work") is True
      and 'name="test_mode_mail"' not in admin_page and "розсилку вимкнено автоматично" in admin_page)
login("author@ftpua.com", ["g-i"])
c.post("/requests/new", data={**BASE, "action": "submit"})
mailer.wait()
check("test mode: mailing off by default", sent == [])
app_settings.set("mail_enabled", True, "admin@ftpua.com")  # адмін сам ставить галочку «Розсилка увімкнена»
check("mailing ticked in test mode -> banner says [ТЕСТ]",
      "позначкою «[ТЕСТ]»" in c.get("/admin/requests").get_data(as_text=True))
login("author@ftpua.com", ["g-i"])
c.post("/requests/new", data={**BASE, "action": "submit"})
mailer.wait()
check("test mode + switch: mails marked [ТЕСТ]", sent and all(x.startswith("[ТЕСТ] ") for x in sent))
check("journal marks test mode", all("тестовий режим" in (r["detail"] or "") for r in notify_log.query(limit=3)))
with db_sql.engine().connect() as conn:
    n_db = conn.execute(sa.select(sa.func.count()).select_from(db_sql.requests_t)).scalar()
check("test mode: new requests not in db", n_db == 2)
check("test mode: no reminders", m.run_handover_reminders() == [])
login("admin@ftpua.com", ["g-a"])
c.post("/admin/settings", data={"approval_mode": "sequential", "dept_head_mode": "view", "pending_cp_payment": "block",
                                "cfo_mode": "approve", "cp_onec_owner": "accountant", "data_mode": "work"})
check("back to work: only db requests", sorted(r["id"] for r in db.list_all()) == [1, 2]
      and "Тестовий режим" not in c.get("/admin/requests").get_data(as_text=True))
check("back to work: mailing as before test mode", app_settings.get("mail_enabled") is True
      and app_settings.get("mail_enabled_work") is None)
c.post("/admin/settings", data={"approval_mode": "sequential", "dept_head_mode": "view", "pending_cp_payment": "block",
                                "cfo_mode": "approve", "cp_onec_owner": "accountant", "data_mode": "test"})
app_settings.set("mail_enabled", False, "t")  # розсилка була вимкнена й до тестового режиму
app_settings.set("mail_enabled_work", False, "t")
c.post("/admin/settings", data={"approval_mode": "sequential", "dept_head_mode": "view", "pending_cp_payment": "block",
                                "cfo_mode": "approve", "cp_onec_owner": "accountant", "data_mode": "work"})
check("mailing that was off stays off after test mode", app_settings.get("mail_enabled") is False)

# ---------- старий перемикач test_mode_mail переноситься один раз
doc = db_sql.get_document("app_settings")
doc.update(data_mode={"value": "test"}, mail_enabled={"value": True}, test_mode_mail={"value": False})
doc.pop("mail_enabled_work", None)
db_sql.put_document("app_settings", doc)
app_settings.reload()
check("legacy: test mode + old switch off -> mailing off, remembered for work",
      app_settings.get("mail_enabled") is False and app_settings.get("mail_enabled_work") is True
      and "test_mode_mail" not in db_sql.get_document("app_settings"))

print("ALL OK" if ok else "SOME FAILED")
