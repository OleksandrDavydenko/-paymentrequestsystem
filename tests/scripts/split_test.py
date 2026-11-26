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

# ---------- групи статей в адмінці (item_groups): автоматичні за назвою, старі пари, ручні
import item_groups
AUTO = {"allocated": "05.002", "direct": "05.012", "unplanned": "05.022"}   # мок: «Ремонт оргтехніки» (+ прямий, позаплан)
check("auto group detected by name", [{k: g[k] for k in item_groups.ROLES} for g in item_groups.groups()] == [AUTO]
      and item_groups.groups()[0]["auto"] and item_groups.groups()[0]["name"] == "Ремонт оргтехніки")
login("adm@ftpua.com")
html = page("/admin/settings?tab=split")
check("settings: groups section with the auto group", 'id="split-settings"' in html and "05.012 — Ремонт оргтехніки (прямий)" in html
      and ">авто<" in html)
app_settings.set("split_pairs", [{"direct": DIRECT, "allocated": ALLOC}], "t")   # старі пари — як ручні групи
check("old pairs work as manual groups", item_groups.group_of(DIRECT)["allocated"] == ALLOC
      and expense_split.pairs() == {DIRECT: ALLOC, "05.012": "05.002"})

def save_groups(rows, **extra):
    data = {"tab": "split", "item_groups_sent": "1", **extra}
    for r in item_groups.ROLES:
        data[f"group_{r}"] = [row.get(r, "") for row in rows]
    data["group_base"] = [row.get("base", "") for row in rows]
    return c.post("/admin/settings", data=data, follow_redirects=True).get_data(as_text=True)

MANUAL = {"allocated": ALLOC, "direct": f"{DIRECT} — Обслуговування автомобілів", "unplanned": ""}
html = save_groups([MANUAL, {**AUTO, "base": "ремонт оргтехніки"}, {}])
check("settings: saved — manual group, auto stays auto, old pairs moved",
      app_settings.get("item_groups") == [{"allocated": ALLOC, "direct": DIRECT, "unplanned": ""}]
      and not app_settings.get("item_groups_skip") and app_settings.get("split_pairs") == []
      and "Ремонт та обслуговування: з розподілом 05.001 / пряма 05.003" in html)
save_groups([MANUAL])
check("settings: removed auto group is skipped", app_settings.get("item_groups_skip") == ["ремонт оргтехніки"]
      and item_groups.group_of("05.012") is None)
save_groups([MANUAL], group_unskip="ремонт оргтехніки")
check("settings: skipped auto group returned", app_settings.get("item_groups_skip") == [] and item_groups.group_of("05.012"))
before = app_settings.get("item_groups")
save_groups([MANUAL, {"allocated": ALLOC, "direct": "05.012"}])
check("settings: item in two groups rejected", app_settings.get("item_groups") == before)
save_groups([{"allocated": "99.999", "direct": DIRECT}])
check("settings: unknown item rejected", app_settings.get("item_groups") == before)
save_groups([MANUAL, {"allocated": "05.002", "direct": "05.022"}])
check("settings: unplanned (1C base) cannot be direct", app_settings.get("item_groups") == before)
save_groups([MANUAL, {"allocated": "05.002", "unplanned": "05.022", "base": "ремонт оргтехніки"}])
check("settings: edited auto group becomes manual", {"allocated": "05.002", "direct": "", "unplanned": "05.022"}
      in app_settings.get("item_groups") and not item_groups.group_of("05.012"))
save_groups([MANUAL, {**AUTO, "base": "ремонт оргтехніки"}])
check("settings: back to auto", app_settings.get("item_groups") == before and item_groups.group_of("05.022")["auto"])

# ---------- непрофітцентр: розподіл обов'язковий
req, html = submit("adm.ini@ftpua.com", 1000)
check("non-profit-center without split: not submitted, explained", req is None and "лише на профітцентр" in html)
req, html = submit("adm.ini@ftpua.com", 1000, action="save")
check("non-profit-center: draft can be saved without split", req and req["status"] == "draft")
login("adm.ini@ftpua.com")
html = page("/requests/new")
check("form: split block and data for the script", 'id="split-block"' in html and "expense-split.js" in html
      and '"groups"' in html and 'id="exp-variants"' in html
      and expense_split.form_config({})["groups"][DIRECT] == {"direct": DIRECT, "allocated": ALLOC, "unplanned": None,
                                                              "name": "Ремонт та обслуговування"})
req, html = submit("adm.ini@ftpua.com", 1000, rows=[("LOG", DIRECT, "600"), ("ADM", ALLOC, "400")])
check("split: submitted with two rows", req and req["status"] != "draft" and len(req["split"]) == 2
      and req["split"][0] == {"department": "LOG", "expense_code": DIRECT, "amount": Decimal("600.00")}
      and req["expense_code"] == DIRECT)
_, html = submit("adm.ini@ftpua.com", 1000, rows=[("LOG", DIRECT, "600"), ("FIN", DIRECT, "400")])
check("direct item on a non-profit-center row rejected", "пряму статтю можна віднести лише на профітцентр" in html)
# стаття з розподілом — лише на відділ-власник (у мокових статтях власник 05.001 — «Адміністрація», ADM)
check("owners of the allocated item", expense_split.owners(ALLOC) == ["ADM"])
_, html = submit("adm.ini@ftpua.com", 1000, rows=[("LOG", DIRECT, "600"), ("FIN", ALLOC, "400")])
check("allocated item on a non-owner rejected", "розносить на відділ-власник" in html and "Адміністрац" in html)
_, html = submit("adm.ini@ftpua.com", 1000, rows=[("LOG", ALLOC, "600"), ("ADM", ALLOC, "400")])
check("allocated item on a profit center (not owner) rejected, direct suggested",
      "розносить на відділ-власник" in html and "пряму статтю" in html)
_, html = submit("adm.ini@ftpua.com", 1000, rows=[("LOG", DIRECT, "600"), ("SAL", OTHER, "400")])
check("item outside the group rejected", "стаття має бути з групи" in html)
_, html = submit("adm.ini@ftpua.com", 1000, rows=[("LOG", DIRECT, "600"), ("LOG", ALLOC, "400")])
check("same department twice rejected", "указано двічі" in html)
r_bad, html = submit("adm.ini@ftpua.com", 1000, rows=[("LOG", DIRECT, "600"), ("ADM", ALLOC, "300")])
check("sum of rows must equal the amount", r_bad is None and "не дорівнює сумі заявки" in html)
r_draft, _ = submit("adm.ini@ftpua.com", 1000, rows=[("LOG", DIRECT, "600"), ("ADM", ALLOC, "300")], action="save")
check("incomplete split can be saved as a draft", r_draft and r_draft["status"] == "draft" and len(r_draft["split"]) == 2)
_, html = submit("adm.ini@ftpua.com", 1000, rows=[("LOG", DIRECT, "abc")])
check("non-numeric amount rejected", "не число" in html)

# ---------- профітцентр: за бажанням
req, _ = submit("log.ini@ftpua.com", 1000)
check("profit center: direct item without split", req and req["status"] != "draft" and "split" not in req)
req, _ = submit("log.ini@ftpua.com", 1000, rows=[("LOG", DIRECT, "700"), ("ADM", ALLOC, "300")])
check("profit center: optional split", req and req["status"] != "draft" and len(req["split"]) == 2)
# стаття з розподілом із пари зверху: розподіл за бажанням (частина — прямою статтею на профітцентр)
check("allocated item of a pair: applies, not required", expense_split.applies({"expense_code": ALLOC, "department": "ADM"})
      and not expense_split.required({"expense_code": ALLOC, "department": "ADM"})
      and expense_split.pair_items(ALLOC) == [DIRECT, ALLOC])
req, _ = submit("adm.ini@ftpua.com", 1000, item=ALLOC)
check("allocated item: without split as before", req and req["status"] != "draft" and "split" not in req)
req, _ = submit("adm.ini@ftpua.com", 1000, item=ALLOC, rows=[("LOG", DIRECT, "700"), ("ADM", ALLOC, "300")])
check("allocated item: optional split kept", req and req["status"] != "draft" and len(req["split"]) == 2
      and req["expense_code"] == ALLOC)
check("allocated item not in a pair: no split", not expense_split.applies({"expense_code": OTHER, "department": "ADM"}))
login("adm.ini@ftpua.com")
html = page("/requests/new")
check("form config: owners of the allocated item", expense_split.form_config({})["owners"][ALLOC] == ["ADM"])

# ---------- позапланова стаття групи — рядок на «Інше» (група «Ремонт оргтехніки» складена автоматично)
U_A, U_D, U_U = AUTO["allocated"], AUTO["direct"], AUTO["unplanned"]
check("unplanned in group: applies, optional", expense_split.applies({"expense_code": U_U, "department": "ADM"})
      and not expense_split.required({"expense_code": U_U, "department": "ADM"})
      and expense_split.pair_items(U_A) == [U_D, U_A, U_U])
req, _ = submit("adm.ini@ftpua.com", 1000, item=U_A, rows=[("LOG", U_D, "500"), ("ADM", U_A, "300"), ("-", U_U, "200")])
check("unplanned row on «Інше» accepted", req and req["status"] != "draft"
      and [r["department"] for r in req["split"]] == ["LOG", "ADM", "-"])
unplanned_req = req
req, _ = submit("adm.ini@ftpua.com", 1000, item=U_U, rows=[("LOG", U_D, "600"), ("", U_U, "400")])
check("unplanned row without department goes to «Інше»", req and req["split"][1]["department"] == "-")
_, html = submit("adm.ini@ftpua.com", 1000, item=U_A, rows=[("LOG", U_D, "600"), ("ADM", U_U, "400")])
check("unplanned on a department rejected", "кидає на «Інше»" in html)
_, html = submit("adm.ini@ftpua.com", 1000, item=U_A, rows=[("LOG", U_D, "600"), ("-", U_A, "400")])
check("only unplanned goes to «Інше»", "на «Інше» йде лише позапланова" in html)
o = onec_api.order(unplanned_req)
check("1C payload: «Інше» row without department", o["allocation"][2]["department"] is None
      and o["allocation"][2]["expense_item"]["code"] == U_U)
login("cfo@ftpua.com")
check("card: «Інше (позапланова)» row", "Інше (позапланова)" in page(f"/requests/{unplanned_req['id']}"))
check("contractor branch: no split", not expense_split.applies({"expense_code": DIRECT, "department": "ADM",
                                                                "payment_kind": "contractor"}))

# ---------- картка, 1С
split_req = next(r for r in (db.get_request(i) for i in range(1, db.last_id() + 1)) if r and r.get("split")
                 and r["author_email"] == "adm.ini@ftpua.com" and r["status"] != "draft")
login("cfo@ftpua.com")
html = page(f"/requests/{split_req['id']}")
check("card shows the split", "Розподіл між відділами" in html and "Логістика" in html)
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
FIXED = budget.parse([row("DEP-LOG", DIRECT, 100000), row("DEP-ADM", ALLOC, 100)])
budget.rows = lambda: FIXED
app_settings.set("budget_enabled", True, "t")
USD = rates.latest()["USD"][0]
uah = lambda usd: (Decimal(usd) * USD).quantize(Decimal("0.01"))
total = uah(600) + uah(400)
before_adm = budget.remaining("ADM", ALLOC, today)["pending"]
req, _ = submit("adm.ini@ftpua.com", total, rows=[("LOG", DIRECT, uah(600)), ("ADM", ALLOC, uah(400))])
snap = req["budget_check"]
check("budget snapshot per row, over on the ADM row", snap["split"] and len(snap["rows"]) == 2
      and snap["over"] and [r["over"] for r in snap["rows"]] == [False, True])
check("over-budget row -> CFO with the row in the note", req["cfo_step"] is True
      and any("05.001" in h["comment"] and "бюджету" in h["comment"] for h in req["history"] if h["action"] == "cfo_threshold"))
after_adm = budget.remaining("ADM", ALLOC, today)["pending"]
check("pending counts the split row's amount, not the whole request", after_adm - before_adm == Decimal("400.00"))
login("cfo@ftpua.com")
html = page(f"/requests/{req['id']}")
check("card: snapshot of each row", html.count("на момент відправки") == 2 and "Рядок понад залишок" in html)
app_settings.set("budget_enabled", False, "t")

# ---------- пару прибрали: збережений розподіл лишається й показується
app_settings.set("item_groups", [], "t")
check("pair removed: existing split still shown", "Розподіл між відділами" in page(f"/requests/{split_req['id']}"))
req, _ = submit("adm.ini@ftpua.com", 1000)
check("pair removed: direct item from non-profit-center submits as before", req and req["status"] != "draft")

# ---------- пряма стаття без пари («(прямий)» у назві): непрофітцентр — лише на профітцентри
import expense_items
TAXI = "06.003"
real_name, real_items = expense_items.get_name, expense_items.get_items
expense_items.get_name = lambda code: "Таксі/Розвезення персоналу (прямий)" if code == TAXI else real_name(code)
expense_items.get_items = lambda: [{**i, "name": "Таксі/Розвезення персоналу (прямий)"} if i["code"] == TAXI else i
                                   for i in real_items()]
check("direct by name, no pair", expense_split.is_direct(TAXI) and expense_split.pair_items(TAXI) == [TAXI]
      and expense_split.required({"expense_code": TAXI, "department": "ADM"})
      and not expense_split.required({"expense_code": TAXI, "department": "LOG"}))
req, html = submit("adm.ini@ftpua.com", 800, item=TAXI)
check("direct without pair, non-profit-center, no split: not submitted", req is None and "лише на профітцентр" in html)
_, html = submit("adm.ini@ftpua.com", 800, item=TAXI, rows=[("LOG", TAXI, "500"), ("ADM", TAXI, "300")])
check("direct without pair: own non-profit-center row rejected", "немає варіанта з розподілом" in html)
req, _ = submit("adm.ini@ftpua.com", 800, item=TAXI, rows=[("LOG", TAXI, "500"), ("SAL", TAXI, "300")])
check("direct without pair: split onto profit centers only — submitted", req and req["status"] != "draft"
      and [r["department"] for r in req["split"]] == ["LOG", "SAL"])
req, _ = submit("log.ini@ftpua.com", 800, item=TAXI)
check("direct without pair: profit center as before", req and req["status"] != "draft" and "split" not in req)
login("adm.ini@ftpua.com")
check("form knows direct items without a pair", f'"{TAXI}"' in page("/requests/new")
      and expense_split.form_config({})["groups"][TAXI]["allocated"] is None)
req, _ = submit("adm.ini@ftpua.com", 800, item=OTHER)
check("not direct, no pair: as before", req and req["status"] != "draft" and "split" not in req)
expense_items.get_name, expense_items.get_items = real_name, real_items

print("ALL OK" if ok else "SOME FAILED")
