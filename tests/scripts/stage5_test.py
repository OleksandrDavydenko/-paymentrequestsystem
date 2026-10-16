import os, re, sys, tempfile
from datetime import date, timedelta
from decimal import Decimal
sys.path.insert(0, os.getcwd())
os.environ.update(UPLOAD_DIR=tempfile.mkdtemp(), GROUP_ADMIN="g-admin", GROUP_INITIATOR="g-init",
                  GROUP_ACC_CASH="g-cash", GROUP_ACC_RESIDENT="g-res", GROUP_ACC_NONRESIDENT="g-nonres",
                  GROUP_CFO="g-cfo", ADMIN_EMAILS="")
import app as m
import user_budget; user_budget.get = lambda oid: {'direct': True, 'all_departments': True, 'departments': []}
import storage as db

ok = True
def check(label, cond):
    global ok
    ok &= bool(cond)
    print(("PASS " if cond else "FAIL ") + label)

claims = {}
def login(email, groups):
    claims.clear(); claims.update({"name": email, "preferred_username": email, "oid": email, "groups": groups})
m.auth._auth.get_user = lambda: dict(claims)
c = m.app.test_client()
rows = lambda t: t.count('<tr class="clickable')
today = date.today()

def expected(pred):
    return [r for r in db.list_to_pay({"cfo"}) if pred(r["pay_date"])]

login("cfo@ftpua.com", ["g-cfo"])
presets = m.date_presets(today)
for code, (label, d_from, d_to) in presets.items():
    t = c.get(f"/to-pay?period={code}").get_data(as_text=True)
    exp = expected(lambda d: (not d_from or d >= d_from) and (not d_to or d <= d_to))
    check(f"preset {code}: {len(exp)} rows", rows(t) == len(exp))
    check(f"preset {code}: chip active", f'chip active' in t and f'period={code}' in t)

d1, d2 = date(2026, 10, 8), date(2026, 10, 12)
t = c.get(f"/to-pay?from={d1}&to={d2}").get_data(as_text=True).replace(" ", " ")
exp = expected(lambda d: d1 <= d <= d2)
check(f"range {d1}..{d2}: {len(exp)} rows", rows(t) == len(exp))
uah = sum((r["amount"] for r in exp if r["currency"] == "UAH"), Decimal(0))
check(f"range UAH total {uah}", m.money(uah).replace(" ", " ") in t)
check("plan block present", "План платежів за датами" in t)
plan = m.payment_plan(exp)
check("plan days = distinct dates", len(plan["days"]) == len({r["pay_date"] for r in exp}))
check("plan total = sum", all(plan["total"][cur] == sum(r["amount"] for r in exp if r["currency"] == cur) for cur in plan["currencies"]))
check("bad date ignored", rows(c.get("/to-pay?from=abc").get_data(as_text=True)) == len(db.list_to_pay({"cfo"})))
check("sortable table + data-sort", 'class="stack sortable"' in t and 'data-sort="UAH|' in t)

# Усі заявки
check("cfo -> 200 (read-only all requests)", c.get("/admin/requests").status_code == 200)
login("i@ftpua.com", ["g-init"])
check("initiator -> 403", c.get("/admin/requests").status_code == 403)
login("admin@ftpua.com", ["g-admin"])
t = c.get("/admin/requests").get_data(as_text=True)
check(f"admin sees all {len(db.list_all())}", rows(t) == len(db.list_all()))
check("admin nav has Усі заявки", "Усі заявки" in t)
check("admin-only / -> all requests", c.get("/").headers["Location"].endswith("/admin/requests"))
check("filter status draft", rows(c.get("/admin/requests?status=draft").get_data(as_text=True)) ==
      sum(1 for r in db.list_all() if r["status"] == "draft"))
check("filter form cash", rows(c.get("/admin/requests?form=cash").get_data(as_text=True)) ==
      sum(1 for r in db.list_all() if r["payment_form"] == "cash"))
check("search Maersk", rows(c.get("/admin/requests?q=maersk").get_data(as_text=True)) ==
      sum(1 for r in db.list_all() if r["counterparty_id"] == "5"))
check("search by number", rows(c.get("/admin/requests?q=000000009").get_data(as_text=True)) == 1)
n_pay = sum(1 for r in db.list_all() if date(2026, 10, 1) <= r["pay_date"] <= date(2026, 10, 31))
check("pay-date range", rows(c.get("/admin/requests?date_field=pay&from=2026-10-01&to=2026-10-31").get_data(as_text=True)) == n_pay)
n_cr = sum(1 for r in db.list_all() if r["created_at"].date() <= date(2026, 9, 10))
check("created-date range", rows(c.get("/admin/requests?date_field=created&to=2026-09-10").get_data(as_text=True)) == n_cr)

r = c.get("/requests/5")
t = r.get_data(as_text=True)
check("admin opens any card read-only", r.status_code == 200 and "Рішення —" not in t and 'name="action" value="ok"' not in t)
before = db.get_request(5)["status"]
c.post("/requests/5/action", data={"action": "approve"})
check("admin cannot approve", db.get_request(5)["status"] == before)
check("admin cannot edit", c.post("/requests/7", data={"amount": "1"}).status_code == 302 and db.get_request(7)["amount"] != 1)

login("i@ftpua.com", ["g-init"])
t = c.get("/requests").get_data(as_text=True)
check("my requests sortable", 'class="stack sortable"' in t and "table-tools.js" in t)
check("initiator no Усі заявки", "/admin/requests" not in t)
print("ALL OK" if ok else "SOME FAILED")
