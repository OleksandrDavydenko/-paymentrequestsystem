"""Журнал роботи сайту: повільні запити (шлях без параметрів), фонові задачі з тривалістю й підсумком (і помилкою),
помилки, старт сайту; сторінка «Адміністрування → Журнал роботи» з фільтрами; очищення записів, старших за 30 днів."""
import os, sys, tempfile
from datetime import timedelta
sys.path.insert(0, os.getcwd())
tmp = tempfile.mkdtemp()
os.environ.update(UPLOAD_DIR=tmp, ADMIN_EMAILS="adm@ftpua.com")
import app as m
import alerts, clock, db_sql, jobs, ops_log

ok = True
def check(label, cond):
    global ok
    ok &= bool(cond); print(("PASS " if cond else "FAIL ") + label)

claims = {"name": "Adm", "preferred_username": "adm@ftpua.com", "oid": "adm@ftpua.com"}
m.auth._auth.get_user = lambda: dict(claims)
c = m.app.test_client()
rows = lambda kind="": ops_log.query(100, kind, 24)

# ---------- старт сайту — з першим запитом, раз на процес
c.get("/login")
c.get("/login")
st = rows("startup")
check("startup recorded once, with timings", len(st) == 1 and st[0]["duration_ms"] is not None
      and "додаток завантажився за" in st[0]["detail"])

# ---------- повільний запит: метод і шлях без параметрів, код, хто
ops_log.SLOW_MS = 0
c.get("/login?next=/requests&code=SECRET123")
ops_log.SLOW_MS = 10 ** 9
slow = rows("slow")
check("slow request: path without query string", slow and slow[0]["name"] == "GET /login" and slow[0]["status"] in ("200", "302")
      and "SECRET" not in str(slow[0]))
c.get("/login")
check("fast request: not recorded", len(rows("slow")) == len(slow))

# ---------- фонові задачі
res = jobs.run("payments_sync", timedelta(minutes=9), lambda: {"loaded_last": 3, "linked_last": 1, "paid_last": ["1"]})
j = rows("job")[0]
check("job: duration and summary", res["loaded_last"] == 3 and j["name"] == "payments_sync" and j["status"] == "ok"
      and "нових / змінених рядків 3" in j["detail"] and "прив'язано 1" in j["detail"] and j["duration_ms"] is not None)
jobs.run("payments_sync_x", timedelta(minutes=9), lambda: {"error": "Power BI недоступний"})
check("job result with error -> status error", rows("job")[0]["status"] == "error" and "Power BI" in rows("job")[0]["detail"])
def boom():
    raise RuntimeError("зламалось")
try:
    jobs.run("maintenance", timedelta(hours=1), boom)
    raised = False
except RuntimeError:
    raised = True
j = rows("job")[0]
check("job exception: recorded and re-raised", raised and j["name"] == "maintenance" and j["status"] == "error"
      and "зламалось" in j["detail"])
check("job lists: count", ops_log.summary(["a", "b"]) == ("ok", "оброблено: 2"))

# ---------- помилки (alerts.report — навіть коли листи вимкнено)
alerts.report(ValueError("погано"), "GET /requests/5")
e = rows("error")[0]
check("error recorded", e["name"] == "GET /requests/5" and "ValueError: погано" in e["detail"])

# ---------- сторінка адмінки
html = c.get("/admin/ops-log").get_data(as_text=True)
check("admin page: summary and rows", "Журнал роботи сайту" in html and "Остання звірка з 1С" in html
      and "Звірка з платежами 1С" in html and "GET /login" in html and "зламалось" in html)
html = c.get("/admin/ops-log?kind=job&period=7d").get_data(as_text=True)
check("filter: only jobs", "Щоденне обслуговування" in html and "GET /requests/5" not in html)
check("admin tabs link", 'href="/admin/ops-log"' in c.get("/admin/notifications").get_data(as_text=True))
claims.update(preferred_username="nobody@ftpua.com", oid="nobody@ftpua.com", name="Nobody")
check("non-admin: no access", c.get("/admin/ops-log").status_code in (302, 403))
claims.update(preferred_username="adm@ftpua.com", oid="adm@ftpua.com", name="Adm")

# ---------- очищення: старші за 30 днів — геть
old = {"at": clock.now() - timedelta(days=31), "kind": "job", "name": "old", "duration_ms": 1, "status": "ok",
       "detail": None, "host": "x"}
if db_sql.configured():
    db_sql.ops_add(old)
else:
    ops_log._memory.append({**old, "id": 0})
n = len(ops_log.query(1000, "", 24 * 40))
removed = ops_log.cleanup()
check("cleanup removes rows older than 30 days", removed == 1 and len(ops_log.query(1000, "", 24 * 40)) == n - 1)
if db_sql.configured():
    check("maintenance cleans the log too", "ops_removed" in m.run_maintenance())
print("ALL OK" if ok else "SOME FAILED")
