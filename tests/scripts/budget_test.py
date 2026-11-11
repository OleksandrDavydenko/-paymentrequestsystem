"""Бюджет: вкладка «Бюджет» (хто бачить і чий бюджет, звіт, розшифровка), залишок статті в заявці (план − факт 1С −
заявки в дорозі; Owner статті; оплачені, які вже у факті, — не вдруге) і заявка понад залишок: лише попередження,
обов'язково фіндиректор (навіть коли він лише спостерігає) або заборона відправки."""
import os, sys, tempfile
from decimal import Decimal
sys.path.insert(0, os.getcwd())
tmp = tempfile.mkdtemp()
os.environ.update(UPLOAD_DIR=tmp, ADMIN_EMAILS="adm@ftpua.com")
import app as m
import user_budget; user_budget.get = lambda oid: {"direct": True, "all_departments": True, "departments": []}
import app_settings, budget, budget_access, clock, org_structure, payments, rates, user_roles
import storage as db
import workflow as wf

ok = True
def check(label, cond):
    global ok
    ok &= bool(cond); print(("PASS " if cond else "FAIL ") + label)

claims = {}
def login(email):
    claims.clear(); claims.update({"name": email.split("@")[0], "preferred_username": email, "oid": email})
m.auth._auth.get_user = lambda: dict(claims) or None
c = m.app.test_client()
page = lambda url: c.get(url, follow_redirects=True).get_data(as_text=True)
nav = lambda html: html.split('id="main-nav">')[1].split("</nav>")[0] if 'id="main-nav">' in html else ""

# ---------- фіксовані дані бюджету (як таблиця BudgetPlanFact) за поточний рік
today = clock.today()
Y, M = today.year, today.month
MONTH = f"{Y:04d}-{M:02d}"
def row(dep, code, plan, fact, month=MONTH, owner=None, name=None, group="Офіс"):
    return {"Month": month + "-01T00:00:00", "DepartmentGUID": dep, "DepartmentName": dep,
            "ExpenseItemGUID": "I-" + code, "ExpenseItemCode": code, "ExpenseItemName": name or code,
            "ExpenseGroup": group, "OwnerDepartmentGUID": owner or "0" * 32, "PlanUSD": plan, "FactUSD": fact}
RAW = [row("DEP-BA", "07.001", 1000, 400), row("DEP-IT", "02.004", 500, 100, owner="DEP-IT"),
       row("DEP-LOG", "01.001", 2000, 2500, group="Логістика"), row("0" * 32, "", 0, 50)]
other = f"{Y:04d}-{(M % 12) + 1:02d}" if M < 12 else f"{Y:04d}-01"
RAW.append(row("DEP-BA", "07.001", 1000, 0, month=other))
FIXED = budget.parse(RAW)
budget.rows = lambda: FIXED

# ---------- користувачі
for uid, roles in (("ini@ftpua.com", ["initiator"]), ("cfo@ftpua.com", ["cfo"]), ("head@ftpua.com", ["initiator"]),
                   ("author@ftpua.com", ["initiator"])):
    user_roles.set_roles(uid, uid, uid.split("@")[0].title(), roles, "t")
org_structure.set_department_staff("BA", {"id": "head@ftpua.com", "email": "head@ftpua.com", "name": "Head"}, [], "t")
org_structure.set_department("author@ftpua.com", "author@ftpua.com", "Author", "BA", "t")

# ---------- хто бачить вкладку й чий бюджет
login("ini@ftpua.com")
check("initiator: no «Бюджет» in the menu, page closed",
      "Бюджет" not in nav(page("/requests")) and c.get("/budget").status_code == 403)
login("cfo@ftpua.com")
html = page("/budget?p=month&v=table&dep=*")
check("cfo: menu + all departments incl. «Інше»", "Бюджет" in nav(html) and c.get("/budget").status_code == 200
      and budget.OTHER_NAME in html and "01.001" in html and "07.001" in html)
login("head@ftpua.com")
html = page("/budget")
check("head: own department only", c.get("/budget").status_code == 200 and "07.001" in html and "01.001" not in html
      and "02.004" not in html)
check("head: foreign department and its details -> 403", c.get("/budget?dep=LOG").status_code == 403
      and c.get(f"/budget/details?dep=LOG&item=01.001&y={Y}&m={M}").status_code == 403
      and c.get(f"/budget/details?dep=-&item=&y={Y}&m={M}").status_code == 403)
r = c.get(f"/budget/details?dep=BA&item=07.001&y={Y}&m={M}")
check("head: details of own fact", r.status_code == 200 and r.json["error"] is None and abs(r.json["total"] - 400) < 0.01)

# винятки з картки користувача: ініціатору відкрито вкладку й бюджет Логістики
login("adm@ftpua.com")
html = c.post("/admin/users/ini@ftpua.com", data={"roles": ["initiator"], "mode": "none", "sections_sent": "1",
                                                   "section_budget": "1", "budget_access_sent": "1",
                                                   "budget_access_mode": "list", "budget_access_deps": ["LOG"]},
              follow_redirects=True).get_data(as_text=True)
check("card: budget access saved + journal", budget_access.get("ini@ftpua.com") == {"mode": "list", "departments": ["LOG"]}
      and "бюджет — вибрані: LOG" in html)
login("ini@ftpua.com")
html = page("/budget")
check("initiator with exception: LOG only", "Бюджет" in nav(html) and "01.001" in html and "07.001" not in html
      and c.get("/budget?dep=BA").status_code == 403)
check("access: all for cfo/admin, own + list for others",
      budget_access.departments_for("cfo@ftpua.com", {"cfo"}) is None
      and budget_access.departments_for("ini@ftpua.com", wf.RoleSet({"initiator"}, head_departments={"SAL"})) == {"LOG", "SAL"})

# ---------- звіт
rep = budget.report(None, "*", Y, [M])
check("report: totals and over-plan count", abs(rep["total"]["plan"] - 3500) < 0.01 and abs(rep["total"]["fact"] - 3050) < 0.01
      and rep["over"] == 2 and len(rep["departments"]) == 4 and rep["departments"][-1]["key"] == budget.OTHER_KEY)
rep = budget.report(frozenset({"BA"}), "*", Y, budget.period_months("year", Y, M, 1), "months")
check("report: months view of one department", rep["count"] == 1
      and abs(rep["departments"][0]["groups"][0]["rows"][0]["plan"] - 2000) < 0.01)

# ---------- залишок статті
info = budget.remaining("BA", "07.001", today)
check("remaining: plan - fact, nothing in progress", info and info["plan"] == Decimal("1000.00")
      and info["fact"] == Decimal("400.00") and info["pending"] == 0 and info["remaining"] == Decimal("600.00"))
check("remaining: item not in the department budget -> none", budget.remaining("BA", "01.001") is None
      and budget.remaining("BA", "") is None)
check("remaining: item with Owner -> owner's budget", budget.remaining("BA", "02.004")["dept"] == "IT")

USD = rates.latest()["USD"][0]
uah = lambda usd: str((Decimal(usd) * USD).quantize(Decimal("0.01")))

login("author@ftpua.com")
r = c.get(f"/budget/remaining?department=BA&item=07.001&amount={uah(100)}&currency=UAH&pay_date={today.isoformat()}")
check("hint endpoint: in budget, within remaining", r.json["enabled"] and r.json["in_budget"] and not r.json["over"]
      and r.json["fmt"]["remaining"] == "600,00" and r.json["fmt"]["amount_usd"] == "100,00")
check("hint endpoint: item not in budget", c.get("/budget/remaining?department=BA&item=01.001").json["in_budget"] is False)

def submit(amount_usd):
    login("author@ftpua.com")
    before = db.last_id()
    html = c.post("/requests/new", data={"pay_date": today.isoformat(), "organization_id": "1", "counterparty_id": "1",
                                         "expense_code": "07.001", "currency": "UAH", "amount": uah(amount_usd),
                                         "payment_form": "bank", "action": "submit"},
                  follow_redirects=True).get_data(as_text=True)
    return (db.get_request(db.last_id()) if db.last_id() != before else None), html

note = lambda r, kind="cfo_threshold": next((h["comment"] for h in r["history"] if h["action"] == kind), "")

# режим «обов'язково фіндиректор» (типовий), фіндиректор лише спостерігає
app_settings.set("cfo_mode", "observe", "t")
check("default over mode is cfo", app_settings.get("budget_over_mode") == "cfo")
req1, _ = submit(100)
check("within budget: no cfo, snapshot saved", req1 and req1["status"] != "draft" and req1["cfo_step"] is False
      and req1["budget_check"]["over"] is False and req1["budget_check"]["remaining"] == "600.00")
info = budget.remaining("BA", "07.001", today)
check("request in progress counted", info["pending"] == Decimal("100.00") and info["pending_count"] == 1
      and info["remaining"] == Decimal("500.00"))
req2, _ = submit(1000)
check("over budget, mode cfo: cfo approves even in observe mode", req2["cfo_step"] is True
      and "бюджету статті 07.001" in note(req2) and "на 500,00 USD" in note(req2) and req2["budget_check"]["over"])
check("over budget: no duplicate note", note(req2, "budget_over") == "")
login("cfo@ftpua.com")
html = page(f"/requests/{req2['id']}")
check("card shows the snapshot to approvers", "на момент відправки" in html and "понад залишок бюджету статті на" in html)

# режим «лише попередження»
app_settings.set("budget_over_mode", "warn", "t")
req3, _ = submit(50)
check("over budget, mode warn: no cfo, note in history", req3["cfo_step"] is False
      and "понад залишок бюджету статті 07.001" in note(req3, "budget_over"))
check("history note is a system note", wf.last_action(req3)["action"] == "submit")

# режим «не дозволяти»
app_settings.set("budget_over_mode", "block", "t")
req4, html = submit(50)
check("over budget, mode block: not submitted", req4 is None and "понад залишок бюджету статті 07.001" in html)
login("author@ftpua.com")
pv = c.get(f"/requests/route-preview?department=BA&expense_code=07.001&amount={uah(50)}&currency=UAH"
           f"&pay_date={today.isoformat()}&organization_id=1&counterparty_id=1&payment_form=bank").json
check("route preview lists the block as a requirement", any("бюджету" in t for t in pv["todo"]))

# оплачена заявка, платіж якої вже потрапив у розподіл 1С, — не вдруге
app_settings.set("budget_over_mode", "cfo", "t")
paid = db.get_request(req1["id"])
paid["status"] = "paid"
wf.add_history(paid, "pay", {"name": "Acc", "email": "acc@ftpua.com"}, "acc_resident", "", "to_pay", "paid")
db.save_request(paid)
before = budget.remaining("BA", "07.001", today)
real_links, real_docs = payments.store().links_for_requests, budget.in_fact_docs
payments.store().links_for_requests = lambda ids: {req1["id"]: {"abcdef01"}} if req1["id"] in ids else {}
budget.in_fact_docs = lambda month: frozenset({"ABCDEF01"})
after = budget.remaining("BA", "07.001", today)
payments.store().links_for_requests, budget.in_fact_docs = real_links, real_docs
check("paid request: counted until its payment is in the 1C fact", before["pending"] == Decimal("1150.00")
      and after["pending"] == Decimal("1050.00"))

# ---------- налаштування
login("adm@ftpua.com")
html = c.post("/admin/settings", data={"tab": "budget", "budget_over_mode": "warn"}, follow_redirects=True).get_data(as_text=True)
check("settings: saved from the form", app_settings.get("budget_over_mode") == "warn" and app_settings.get("budget_hint") is False)
login("author@ftpua.com")
check("hint off -> endpoint disabled", c.get("/budget/remaining?department=BA&item=07.001").json == {"enabled": False})
html = page("/requests/new")
check("hint off -> no hint block in the form", 'id="budget-hint"' not in html)
app_settings.set("budget_hint", True, "t")
check("hint on -> hint block in the form", 'id="budget-hint"' in page("/requests/new"))

print("ALL OK" if ok else "SOME FAILED")
