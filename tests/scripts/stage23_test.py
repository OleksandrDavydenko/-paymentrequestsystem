import os, sys, tempfile
from decimal import Decimal
sys.path.insert(0, os.getcwd())
tmp = tempfile.mkdtemp()
os.environ.update(UPLOAD_DIR=tmp, APP_SETTINGS_FILE=os.path.join(tmp, "s.json"), USER_PREFS_FILE=os.path.join(tmp, "p.json"),
                  USER_DIRECTORY_FILE=os.path.join(tmp, "d.json"), ORG_STRUCTURE_FILE=os.path.join(tmp, "o.json"),
                  COUNTERPARTIES_PENDING_FILE=os.path.join(tmp, "cp.json"),
                  GROUP_ADMIN="g-a", GROUP_INITIATOR="g-i", GROUP_ACC_RESIDENT="g-r", GROUP_ACC_CASH="g-c", GROUP_CFO="g-f",
                  GROUP_ACC_NONRESIDENT="g-n", ADMIN_EMAILS="")
import app as m
import user_budget; user_budget.get = lambda oid: {"direct": True, "all_departments": True, "departments": []}
import app_settings, mailer, notifications, rates, currencies
import workflow as wf
import storage as db

ok = True
def check(label, cond):
    global ok
    ok &= bool(cond)
    print(("PASS " if cond else "FAIL ") + label)

sent = []
mailer.transport = lambda cfg, to, subject, html: sent.append((to, subject))
notifications.role_members = lambda role: []
for k, v in {"mail_enabled": True, "mail_sender": "noreply@ftpua.com"}.items():
    app_settings.set(k, v, "t")
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
def flush():
    mailer.wait(); out = list(sent); sent.clear(); return out
INIT, RES, NON, CASH, CFO, ADMIN = ["g-i"], ["g-r"], ["g-n"], ["g-c"], ["g-f"], ["g-a"]
AUTHOR = "author@ftpua.com"
BASE = {"pay_date": "2026-10-20", "organization_id": "1", "counterparty_id": "1", "expense_code": "01.001",
        "currency": "UAH", "amount": "500", "payment_form": "bank"}
SETTINGS = {"approval_mode": "sequential", "dept_head_mode": "view", "pending_cp_payment": "block",
            "cp_onec_owner": "accountant"}

# ---------- курси
rows = [  # як у ExchangeRates: кілька дат, береться найсвіжіша
    {"Date": "2026-10-14T00:00:00", "GUID": "G-USD", "RateBU": 44.5},
    {"Date": "2026-10-15T00:00:00", "GUID": "G-USD", "RateBU": 44.9801},
    {"Date": "2026-10-15T00:00:00", "GUID": "G-EUR", "RateBU": 50.3453},
    {"Date": "2026-10-15T00:00:00", "GUID": "G-JPY", "RateBU": 0.28387},
    {"Date": "2026-10-15T00:00:00", "GUID": "G-UNKNOWN", "RateBU": 3},
]
orig_cur = currencies._table.items
currencies._table.items = lambda: [{"guid": "G-USD", "name": "USD"}, {"guid": "G-EUR", "name": "EUR"},
                                   {"guid": "G-JPY", "name": "JPY"}, {"guid": "", "name": "UAH"}]
parsed = rates.parse(rows)
currencies._table.items = orig_cur
check("parse: latest date per currency, code by GUID", {x["name"]: x["rate"] for x in parsed} == {"USD": "44.9801", "EUR": "50.3453", "JPY": "0.28387"}
      and all(x["date"] == "2026-10-15" for x in parsed))
check("to_usd UAH", rates.to_usd(50000, "UAH")[0] == Decimal("1111.60"))
check("to_usd EUR", rates.to_usd(950, "EUR")[0] == Decimal("1063.32"))
check("to_usd JPY (<1)", rates.to_usd(100000, "JPY")[0] == Decimal("631.10"))
check("to_usd USD itself", rates.to_usd(900, "USD")[0] == Decimal("900"))
check("no rate -> None", rates.to_usd(5, "TUSD")[0] is None)

# ---------- адмінка: режим «від суми»
login("admin@ftpua.com", ADMIN)
page = admin_page()
check("admin: rules mode + rules table + rates status", "Погоджує за правилами" in page and 'name="cfo_limit_acc_resident"' in page
      and "Курси валют" in page)
r = c.post("/admin/settings", data={**SETTINGS, "cfo_mode": "threshold", "cfo_threshold_usd": "0"}, follow_redirects=True)
check("threshold 0 rejected", app_settings.get("cfo_mode") == "approve" and "більшу за 0" in r.get_data(as_text=True))
html = c.post("/admin/settings", data={**SETTINGS, "cfo_mode": "threshold", "cfo_threshold_usd": "1000"}, follow_redirects=True).get_data(as_text=True)
check("threshold saved, route label", app_settings.get("cfo_mode") == "threshold" and app_settings.get("cfo_threshold_usd") == 1000
      and "Фіндиректор (від 1 000 USD)" in html)

def submit(**kw):
    login(AUTHOR, INIT)
    c.post("/requests/new", data={**BASE, **kw, "action": "submit"})
    return db.last_id()

def approve_acc(rid):
    login("res@ftpua.com", RES)
    c.post(f"/requests/{rid}/action", data={"action": "approve", "payment_account_id": "1"})
    return db.get_request(rid)

r_small = approve_acc(submit(currency="USD", amount="900"))
note = next(h for h in r_small["history"] if h["action"] == "cfo_threshold")
check("900 USD -> skips cfo, to_pay", r_small["status"] == "to_pay" and r_small["cfo_step"] is False
      and "менше за поріг 1 000 USD" in note["comment"])
r_uah = approve_acc(submit(currency="UAH", amount="50000"))
check("50 000 UAH (≈1 112 USD) -> cfo", r_uah["status"] == "cfo" and "не менше за поріг" in
      next(h for h in r_uah["history"] if h["action"] == "cfo_threshold")["comment"])
r_eur = approve_acc(submit(currency="EUR", amount="950"))
check("950 EUR (≈1 063 USD) -> cfo", r_eur["status"] == "cfo")
check("no extra fields stored", not any(k.startswith("usd") for k in r_eur))
real_latest = rates.latest
rates.latest = lambda: {k: v for k, v in real_latest().items() if k != "EUR"}  # курсу EUR немає
r_nor = approve_acc(submit(currency="EUR", amount="1"))
rates.latest = real_latest
check("no rate -> cfo (safe)", r_nor["status"] == "cfo" and "не вдалося перерахувати" in
      next(h for h in r_nor["history"] if h["action"] == "cfo_threshold")["comment"])

# доопрацювання зі зменшеною сумою — при повторній відправці перерахунок
login("cfo@ftpua.com", CFO)
c.post(f"/requests/{r_uah['id']}/action", data={"action": "rework", "comment": "зменшіть суму"})
login(AUTHOR, INIT)
c.post(f"/requests/{r_uah['id']}", data={**BASE, "currency": "UAH", "amount": "20000", "action": "submit"})
r2 = approve_acc(r_uah["id"])
check("resubmit with smaller sum -> no cfo", r2["status"] == "to_pay" and r2["cfo_step"] is False)

# паралельний режим: нижче порогу — лише бухгалтер
app_settings.set("approval_mode", "parallel", "t")
rp = submit(currency="USD", amount="100")
check("parallel below threshold -> sequential accountant", db.get_request(rp)["status"] == "accountant")
rp2 = submit(currency="USD", amount="5000")
check("parallel above threshold -> both", db.get_request(rp2)["status"] == "approval" and "cfo" in wf.pending_roles(db.get_request(rp2)))
app_settings.set("approval_mode", "sequential", "t")

# ---------- «У бухгалтерів» і втручання
ra = submit(currency="UAH", amount="300")                              # бухгалтер резидентів
rn = submit(currency="UAH", amount="300", organization_id="4")         # нерезидентів
rc = submit(currency="UAH", amount="300", payment_form="cash")         # по готівці
login("cfo@ftpua.com", CFO)
html = c.get("/approvals?view=accountants").get_data(as_text=True)
check("cfo tab lists all accountant types", all(f">{db.get_request(x)['number']}<" in html for x in (ra, rn, rc))
      and "Бух. нерезидент" in html and "Бух. готівка" in html)
check("cfo tab includes parallel waiting accountant", f">{db.get_request(rp2)['number']}<" in html)
login("res@ftpua.com", RES)
check("accountant: tab forbidden", c.get("/approvals?view=accountants").status_code == 403)

app_settings.set("cfo_mode", "observe", "t")
login("cfo@ftpua.com", CFO)
nav = c.get("/approvals?view=accountants").get_data(as_text=True)
check("observe: menu still there", "На погодження" in nav.split("</nav>")[0])
page = c.get(f"/requests/{rn}").get_data(as_text=True)
check("card: intervention panel, no approve", "Втручання фіндиректора" in page and 'value="rework"' in page
      and 'value="reject"' in page and 'value="approve"' not in page)
c.post(f"/requests/{rn}/action", data={"action": "approve"})
check("cfo cannot approve for accountant", db.get_request(rn)["status"] == "accountant")
c.post(f"/requests/{rn}/action", data={"action": "reject", "comment": ""})
check("reject needs comment", db.get_request(rn)["status"] == "accountant")
flush()
c.post(f"/requests/{rn}/action", data={"action": "rework", "comment": "Додайте договір"})
r = db.get_request(rn)
mails = flush()
check("cfo returns for rework (history role cfo)", r["status"] == "rework" and r["history"][-1]["role"] == "cfo")
check("author notified", any(to == AUTHOR for to, _ in mails))
c.post(f"/requests/{rc}/action", data={"action": "reject", "comment": "Не узгоджено"})
check("cfo rejects cash request at accountant", db.get_request(rc)["status"] == "rejected")
login("i2@ftpua.com", INIT)
check("initiator cannot intervene", c.post(f"/requests/{ra}/action", data={"action": "reject", "comment": "x"}).status_code in (403, 404)
      or db.get_request(ra)["status"] == "accountant")

print("ALL OK" if ok else "SOME FAILED")
