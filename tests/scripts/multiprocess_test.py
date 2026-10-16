"""Кілька процесів сайту на одній базі: номери заявок, кеш заявок у роботі, захист від одночасних змін,
злиття налаштувань, черга листів, «оренда» фонових задач і сеанси входу."""
import os, subprocess, sys, tempfile, textwrap
from datetime import timedelta
sys.path.insert(0, os.getcwd())
tmp = tempfile.mkdtemp()
os.environ.update(UPLOAD_DIR=tmp, DATABASE_URL=os.environ.get("TEST_DATABASE_URL") or "sqlite:///" + os.path.join(tmp, "mp.db").replace("\\", "/"),
                  APP_SETTINGS_FILE=os.path.join(tmp, "s.json"))
import app_settings, clock, db_sql, docstore, jobs, mailer, sessions_db
import storage.sql as dbs
from workflow import RoleSet

ok = True
def check(label, cond):
    global ok
    ok &= bool(cond); print(("PASS " if cond else "FAIL ") + label)

def other(code):
    """Виконати код в іншому процесі з тією самою базою; повертає його stdout."""
    prog = "import os, sys\nsys.path.insert(0, os.getcwd())\n" + textwrap.dedent(code)
    r = subprocess.run([sys.executable, "-c", prog], capture_output=True, text=True, encoding="utf-8", env=os.environ)
    if r.returncode:
        print(r.stderr[-3000:])
    return r.stdout.strip()

REQ = """
from datetime import datetime, date
from decimal import Decimal
def new_req(status="draft", author="a@ftpua.com"):
    return {"author_email": author, "author_name": "A", "department": None, "created_at": datetime(2026, 10, 1, 9),
            "pay_date": date(2026, 10, 20), "organization_id": "1", "counterparty_id": "1", "expense_code": "01.001",
            "payment_form": "bank", "currency": "UAH", "amount": Decimal("100.00"), "status": status, "note": "",
            "lines": [], "history": [], "attachments": []}
"""
exec(REQ)

# ---------- номери заявок: два процеси одночасно
script = REQ + "import storage.sql as dbs\nprint(','.join(str(dbs.save_request(new_req())) for _ in range(15)))"
procs = [subprocess.Popen([sys.executable, "-c", "import os, sys\nsys.path.insert(0, os.getcwd())\n" + script],
                          stdout=subprocess.PIPE, text=True, env=os.environ) for _ in range(2)]
mine = [dbs.save_request(new_req()) for _ in range(15)]
theirs = [int(x) for p in procs for x in p.communicate()[0].strip().split(",")]
all_ids = mine + theirs
check("unique request numbers across 3 processes", len(all_ids) == 45 and len(set(all_ids)) == 45)

# ---------- кеш заявок у роботі бачить зміни іншого процесу
roles = RoleSet({"cfo"})
before = {r["id"] for r in dbs.scope_rows("to_pay", roles, "")}
new_id = int(other(REQ + "import storage.sql as dbs\nprint(dbs.save_request(new_req('to_pay')))"))
check("to-pay list sees request saved by another process", new_id in {r["id"] for r in dbs.scope_rows("to_pay", roles, "")}
      and new_id not in before)
other(f"""
import storage.sql as dbs
r = dbs.get_request({new_id}); r["status"] = "paid"; dbs.save_request(r)
""")
check("…and sees it leave the list", new_id not in {r["id"] for r in dbs.scope_rows("to_pay", roles, "")})

# ---------- одночасна зміна заявки іншим процесом
mine_copy = dbs.get_request(new_id)
other(f"""
import storage.sql as dbs
r = dbs.get_request({new_id}); r["note"] = "інший процес"; dbs.save_request(r)
""")
mine_copy["note"] = "цей процес"
try:
    dbs.save_request(mine_copy); stale = False
except dbs.StaleRequestError as e:
    stale = e.current["note"] == "інший процес"
check("stale copy rejected across processes", stale and dbs.get_request(new_id)["note"] == "інший процес")

# ---------- налаштування: злиття змін і синхронізація
app_settings.set("approval_mode", "parallel", "a")
other("""
import app_settings
app_settings.set("cfo_mode", "observe", "b")
""")
check("before sync: this process still has old value", app_settings.get("cfo_mode") != "observe")
app_settings.set("dept_head_mode", "approve", "a")  # пише, не знаючи про зміну іншого процесу
saved = db_sql.get_document("app_settings")
check("concurrent settings writes merged (nothing lost)", saved["cfo_mode"]["value"] == "observe"
      and saved["dept_head_mode"]["value"] == "approve" and saved["approval_mode"]["value"] == "parallel")
check("merged value visible in this process", app_settings.get("cfo_mode") == "observe")
other("""
import app_settings
app_settings.set("approval_mode", "sequential", "b")
""")
changed = docstore.sync()
check("sync re-reads documents changed elsewhere", "app_settings" in changed and app_settings.get("approval_mode") == "sequential")
check("sync without changes does nothing", docstore.sync() == [])

# ---------- черга листів: не губиться й не дублюється
sent = []
mailer.transport = lambda cfg, to, subject, html: sent.append(to)
mailer.config_provider = lambda: {}
mailer._worker = object()  # без фонового потоку: листи лишаються в черзі, як після перезапуску
mailer.start_worker = lambda: None
for i in range(5):
    mailer.enqueue({}, f"u{i}@ftpua.com", f"Тема {i}", "<p>x</p>", {"event": "test"})
check("mails queued in db", db_sql.mail_pending() == 5 and sent == [])
out = other("""
import mailer, db_sql
got = []
mailer.transport = lambda cfg, to, subject, html: got.append(to)
mailer.config_provider = lambda: {}
mailer.process_outbox()
print(len(got), db_sql.mail_pending())
""")
check("another process (after 'restart') sends them", out == "5 0")
check("this process does not send them again", mailer.process_outbox() == 0 and sent == [])

# ---------- фонові задачі: лише один процес
calls = []
jobs.run("demo", timedelta(hours=1), lambda: calls.append(1))
out = other("""
import jobs
from datetime import timedelta
print(jobs.run("demo", timedelta(hours=1), lambda: "ran"))
""")
check("job runs in one process only", calls == [1] and out == "None")
with db_sql.engine().begin() as c:
    c.execute(db_sql.lease_t.update().values(until=clock.now() - timedelta(minutes=1)))
out = other("""
import jobs
from datetime import timedelta
print(jobs.run("demo", timedelta(hours=1), lambda: "ran"))
""")
check("expired lease -> another process runs it", out == "ran")

# ---------- сеанси входу спільні
cache = sessions_db.DbSessionCache()
cache.set("sid-1", {"user": {"name": "Тест"}, "_flashes": [("success", "ok")]}, timeout=60)
out = other("""
import sessions_db
print(sessions_db.DbSessionCache().get("sid-1")["user"]["name"])
""")
check("session written by one process is read by another", out == "Тест")
cache.set("sid-old", {"x": 1}, timeout=1)
with db_sql.engine().begin() as c:
    c.execute(db_sql.sessions_t.update().where(db_sql.sessions_t.c.id == "sid-old")
              .values(expires_at=clock.now() - timedelta(seconds=5)))
check("expired session not returned, cleanup removes it", cache.get("sid-old") is None and sessions_db.cleanup() == 1
      and cache.get("sid-1") is not None)
print("ALL OK" if ok else "SOME FAILED")
