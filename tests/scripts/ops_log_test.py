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
import threading, time
c.get("/login")
c.get("/login")
for _ in range(50):  # записується у фоні (перше з'єднання з базою — не в першому запиті)
    if not any(t.name == "ops-startup" for t in threading.enumerate()):
        break
    time.sleep(0.1)
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
ops_log.SLOW_MS = 0
c.get("/robots933456.txt")  # перевірка App Service після перезапуску — не користувач
ops_log.SLOW_MS = 10 ** 9
check("App Service warm-up probe: not recorded", len(rows("slow")) == len(slow))
ops_log.SLOW_MS = 0
c.get("/admin/ops-log")  # сторінка, що читає базу: у записі — хто й куди пішов час
ops_log.SLOW_MS = 10 ** 9
s2 = rows("slow")[0]
check("slow request: who" + (" and database time" if db_sql.configured() else ""),
      s2["name"] == "GET /admin/ops-log" and "adm@ftpua.com" in (s2["detail"] or "")
      and (not db_sql.configured() or "база:" in s2["detail"]))
with m.app.test_request_context("/x"):
    from flask import g
    ops_log.add_timing("pbi", 1.5)
    ops_log.add_timing("pbi", 0.5)
    ops_log.add_timing("db", 0.25)
    check("timing summary", ops_log.timing_summary(g.timings) == "Power BI: 2 запит(ів), 2.0 с · база: 1 запит(ів), 0.2 с")
ops_log.add_timing("db", 1)  # поза запитом — нічого

# ---------- токен Power BI: клієнт MSAL — один на процес (не два зайві звернення до Microsoft на кожен запит)
import expense_items
made = []
class FakeMsal:
    def __init__(self, *a, **kw):
        made.append(1)
    def acquire_token_for_client(self, scopes):
        return {"access_token": "t"}
expense_items.msal.ConfidentialClientApplication, expense_items._msal_app = FakeMsal, None
check("PBI token client is created once", expense_items._get_token() == "t" and expense_items._get_token() == "t"
      and made == [1])

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
