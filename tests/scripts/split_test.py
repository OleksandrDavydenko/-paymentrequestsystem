"""Розподіл прямої витрати між відділами (expense_split): пари статей в адмінці; непрофітцентр не відправить пряму статтю
без розподілу; профітцентр — за бажанням; у рядку лише стаття пари, пряма — тільки на профітцентр; сума рядків = сума
заявки; бюджет, правило фіндиректора й дані для 1С — по рядках; стаття без пари — як раніше."""
import os, sys, tempfile
from decimal import Decimal
sys.path.insert(0, os.getcwd())
tmp = tempfile.mkdtemp()
os.environ.update(UPLOAD_DIR=tmp, ADMIN_EMAILS="adm@ftpua.com")
import app as m
import user_budget; user_budget.get = lambda oid: {"direct": True, "all_departments": True, "departments": []}
import app_settings, budget, clock, departments, expense_split, onec_api, org_structure, rates, user_roles
import storage as db

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
today = clock.today()

# Мокові відділи: профітцентри LOG, CUS, SAL; ADM, FIN — ні
check("mock profit centers", {"LOG", "SAL"} <= expense_split.profit_centers() and "ADM" not in expense_split.profit_centers())
for uid, dep in (("adm.ini@ftpua.com", "ADM"), ("log.ini@ftpua.com", "LOG")):
    user_roles.set_roles(uid, uid, uid.split("@")[0].title(), ["initiator"], "t")
    org_structure.set_department(uid, uid, uid, dep, "t")
user_roles.set_roles("cfo@ftpua.com", "cfo@ftpua.com", "Cfo", ["cfo"], "t")

DIRECT, ALLOC, OTHER = "05.003", "05.001", "04.001"   # «Обслуговування автомобілів» (пряма) → «Ремонт та обслуговування»

def submit(author, amount, item=DIRECT, rows=None, action="submit", currency="UAH"):
    login(author)
    before = db.last_id()
    data = {"pay_date": today.isoformat(), "organization_id": "1", "counterparty_id": "1", "expense_code": item,
            "currency": currency, "amount": str(amount), "payment_form": "bank", "action": action}
    if rows is not None:
        data.update(split_sent="1", split_department=[r[0] for r in rows], split_item=[r[1] for r in rows],
                    split_amount=[str(r[2]) for r in rows])
    html = c.post("/requests/new", data=data, follow_redirects=True).get_data(as_text=True)
    return (db.get_request(db.last_id()) if db.last_id() != before else None), html

# ---------- без пар — як раніше
req, _ = submit("adm.ini@ftpua.com", 1000)
check("no pairs: direct item from non-profit-center submits as before", req and req["status"] != "draft" and "split" not in req)
check("no pairs: nothing applies", not expense_split.applies({"expense_code": DIRECT, "department": "ADM"}))

# ---------- пари в адмінці
login("adm@ftpua.com")
html = page("/admin/settings?tab=split")
check("settings: split section on the page", 'id="split-settings"' in html and "пар немає" in html)
html = c.post("/admin/settings", data={"tab": "split", "split_pairs_sent": "1",
                                       "split_direct": [f"{DIRECT} — Обслуговування автомобілів", ""],
                                       "split_allocated": [ALLOC, ""]}, follow_redirects=True).get_data(as_text=True)
check("settings: pair saved, journal with names", app_settings.get("split_pairs") == [{"direct": DIRECT, "allocated": ALLOC}]
      and "Обслуговування автомобілів → Ремонт та обслуговування" in html)
c.post("/admin/settings", data={"tab": "split", "split_pairs_sent": "1", "split_direct": [DIRECT, ALLOC],
                                "split_allocated": [ALLOC, "04.002"]})
check("settings: an item cannot be direct in one pair and allocated in another",
      app_settings.get("split_pairs") == [{"direct": DIRECT, "allocated": ALLOC}])
c.post("/admin/settings", data={"tab": "split", "split_pairs_sent": "1", "split_direct": ["99.999"], "split_allocated": [ALLOC]})
check("settings: unknown item rejected", app_settings.get("split_pairs") == [{"direct": DIRECT, "allocated": ALLOC}])

# ---------- непрофітцентр: розподіл обов'язковий
req, html = submit("adm.ini@ftpua.com", 1000)
check("non-profit-center without split: not submitted, explained", req is None and "лише на профітцентр" in html)
req, html = submit("adm.ini@ftpua.com", 1000, action="save")
check("non-profit-center: draft can be saved without split", req and req["status"] == "draft")
login("adm.ini@ftpua.com")
html = page("/requests/new")
check("form: split block and data for the script", 'id="split-block"' in html and "expense-split.js" in html
      and f'"{DIRECT}": "{ALLOC}"' in html)
req, html = submit("adm.ini@ftpua.com", 1000, rows=[("LOG", DIRECT, "600"), ("SAL", ALLOC, "400")])
check("split: submitted with two rows", req and req["status"] != "draft" and len(req["split"]) == 2
      and req["split"][0] == {"department": "LOG", "expense_code": DIRECT, "amount": Decimal("600.00")}
      and req["expense_code"] == DIRECT)
_, html = submit("adm.ini@ftpua.com", 1000, rows=[("LOG", DIRECT, "600"), ("FIN", DIRECT, "400")])
check("direct item on a non-profit-center row rejected", "пряму статтю можна віднести лише на профітцентр" in html)
_, html = submit("adm.ini@ftpua.com", 1000, rows=[("LOG", DIRECT, "600"), ("SAL", OTHER, "400")])
check("item outside the pair rejected", "стаття має бути з пари" in html)
_, html = submit("adm.ini@ftpua.com", 1000, rows=[("LOG", DIRECT, "600"), ("LOG", ALLOC, "400")])
check("same department twice rejected", "указано двічі" in html)
r_bad, html = submit("adm.ini@ftpua.com", 1000, rows=[("LOG", DIRECT, "600"), ("SAL", ALLOC, "300")])
check("sum of rows must equal the amount", r_bad is None and "не дорівнює сумі заявки" in html)
r_draft, _ = submit("adm.ini@ftpua.com", 1000, rows=[("LOG", DIRECT, "600"), ("SAL", ALLOC, "300")], action="save")
check("incomplete split can be saved as a draft", r_draft and r_draft["status"] == "draft" and len(r_draft["split"]) == 2)
_, html = submit("adm.ini@ftpua.com", 1000, rows=[("LOG", DIRECT, "abc")])
check("non-numeric amount rejected", "не число" in html)

# ---------- профітцентр: за бажанням
req, _ = submit("log.ini@ftpua.com", 1000)
check("profit center: direct item without split", req and req["status"] != "draft" and "split" not in req)
req, _ = submit("log.ini@ftpua.com", 1000, rows=[("LOG", DIRECT, "700"), ("ADM", ALLOC, "300")])
check("profit center: optional split", req and req["status"] != "draft" and len(req["split"]) == 2)
req, _ = submit("adm.ini@ftpua.com", 1000, item=ALLOC, rows=[("LOG", DIRECT, "1000")])
check("allocated item: no split at all (rows dropped)", req and req["status"] != "draft" and "split" not in req)
check("contractor branch: no split", not expense_split.applies({"expense_code": DIRECT, "department": "ADM",
                                                                "payment_kind": "contractor"}))

# ---------- картка, 1С
split_req = next(r for r in (db.get_request(i) for i in range(1, db.last_id() + 1)) if r and r.get("split")
                 and r["author_email"] == "adm.ini@ftpua.com" and r["status"] != "draft")
login("cfo@ftpua.com")
html = page(f"/requests/{split_req['id']}")
check("card shows the split", "Розподіл між відділами" in html and "Відділ продажу" in html)
o = onec_api.order(split_req)
check("1C payload: allocation rows", o["allocation"] and len(o["allocation"]) == 2
      and o["allocation"][1]["expense_item"]["code"] == ALLOC and o["allocation"][1]["amount"] == "400.00"
      and o["allocation"][0]["department"]["code"] == "LOG")
check("1C payload: no split -> null", onec_api.order(db.get_request(1))["allocation"] is None)

# ---------- правило фіндиректора: стаття рядка зі списку
app_settings.set("cfo_mode", "threshold", "t"); app_settings.set("cfo_expense_codes", [ALLOC], "t")
app_settings.set("cfo_rules", {r: {"mode": "never", "limit": 1000} for r in ("acc_cash", "acc_resident", "acc_nonresident")}, "t")
req, _ = submit("log.ini@ftpua.com", 1000, rows=[("LOG", DIRECT, "700"), ("ADM", ALLOC, "300")])
check("cfo list hits a split row's item", req["cfo_step"] is True
      and any(ALLOC in h["comment"] for h in req["history"] if h["action"] == "cfo_threshold"))
req, _ = submit("log.ini@ftpua.com", 1000)
check("cfo list: request without that item — no cfo", req["cfo_step"] is False)
app_settings.set("cfo_expense_codes", [], "t"); app_settings.set("cfo_mode", "observe", "t")

# ---------- бюджет по рядках
Y, M = today.year, today.month
MONTH = f"{Y:04d}-{M:02d}"
def row(dep, code, plan):
    return {"Month": MONTH + "-01", "DepartmentGUID": dep, "DepartmentName": dep, "ExpenseItemGUID": "I-" + code,
            "ExpenseItemCode": code, "ExpenseItemName": code, "ExpenseGroup": "G", "OwnerDepartmentGUID": "0" * 32,
            "PlanUSD": plan, "FactUSD": 0}
FIXED = budget.parse([row("DEP-LOG", DIRECT, 100000), row("DEP-SAL", ALLOC, 100)])
budget.rows = lambda: FIXED
app_settings.set("budget_enabled", True, "t")
USD = rates.latest()["USD"][0]
uah = lambda usd: (Decimal(usd) * USD).quantize(Decimal("0.01"))
total = uah(600) + uah(400)
before_sal = budget.remaining("SAL", ALLOC, today)["pending"]
req, _ = submit("adm.ini@ftpua.com", total, rows=[("LOG", DIRECT, uah(600)), ("SAL", ALLOC, uah(400))])
snap = req["budget_check"]
check("budget snapshot per row, over on the SAL row", snap["split"] and len(snap["rows"]) == 2
      and snap["over"] and [r["over"] for r in snap["rows"]] == [False, True])
check("over-budget row -> CFO with the row in the note", req["cfo_step"] is True
      and any("05.001" in h["comment"] and "бюджету" in h["comment"] for h in req["history"] if h["action"] == "cfo_threshold"))
after_sal = budget.remaining("SAL", ALLOC, today)["pending"]
check("pending counts the split row's amount, not the whole request", after_sal - before_sal == Decimal("400.00"))
login("cfo@ftpua.com")
html = page(f"/requests/{req['id']}")
check("card: snapshot of each row", html.count("на момент відправки") == 2 and "Рядок понад залишок" in html)
app_settings.set("budget_enabled", False, "t")

# ---------- пару прибрали: збережений розподіл лишається й показується
app_settings.set("split_pairs", [], "t")
check("pair removed: existing split still shown", "Розподіл між відділами" in page(f"/requests/{split_req['id']}"))
req, _ = submit("adm.ini@ftpua.com", 1000)
check("pair removed: direct item from non-profit-center submits as before", req and req["status"] != "draft")

print("ALL OK" if ok else "SOME FAILED")
