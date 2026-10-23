"""Співробітники з 1С: додати за правилом (ролі, відділ, керівник відділу, статті свого відділу, прямі —
профітцентрам, користувач 1С бухгалтеру), розбіжності з 1С, звільнені; відділи для заявок у картці й поле
«Відділ» у заявці (керівник вибраного відділу погоджує)."""
import os, sys, tempfile
sys.path.insert(0, os.getcwd())
tmp = tempfile.mkdtemp()
os.environ.update(UPLOAD_DIR=tmp, ADMIN_EMAILS="admin@ftpua.com")
import app as m
import app_settings, departments, employees, expense_items, onec_sync, org_structure, payment_types, user_budget
import user_onec, user_roles
import storage as db

ok = True
def check(label, cond):
    global ok
    ok &= bool(cond); print(("PASS " if cond else "FAIL ") + label)

claims = {}
def login(email, name=None):
    claims.clear(); claims.update({"name": name or email, "preferred_username": email, "oid": "oid-" + email})
m.auth._auth.get_user = lambda: dict(claims) or None
c = m.app.test_client()
page = lambda url: c.get(url).get_data(as_text=True)

# ---------- довідники з 1С (мокові)
check("department by GUID, profit centers", departments.code_by_guid("dep-log") == "LOG"
      and departments.is_profit_center("SAL") and not departments.is_profit_center("BA"))
check("employee: department by GUID, head flag, XXX stripped", employees.by_email("M.Koval@ftpua.com")["department"] == "SAL"
      and employees.by_email("m.koval@ftpua.com")["head"] and employees.by_email("sn@ftpua.com")["name"] == "Науменко Сергій")
check("expense departments for a department (by GUID)", expense_items.departments_for("LOG") == ["Логістика"]
      and expense_items.departments_for("FIN") == [])
check("1C user by employee", payment_types.onec_user_for_email("o.bondar@ftpua.com")["guid"] == "U-OLENA"
      and payment_types.onec_user_for_email("i.chagan@ftpua.com") is None)

# ---------- хто вже є
user_roles.set_roles("oid-ip", "i.petrenko@ftpua.com", "Ірина Петренко", ["initiator"], "t")
org_structure.set_department("oid-ip", "i.petrenko@ftpua.com", "Ірина Петренко", "FIN", "t")  # у 1С — LOG
user_roles.set_roles("oid-ob", "o.bondar@ftpua.com", "Олена Бондар", ["acc_cash"], "t")       # без користувача 1С
user_roles.set_roles("oid-sn", "sn@ftpua.com", "Сергій Науменко", ["initiator"], "t")          # звільнений у 1С

login("i.petrenko@ftpua.com")
check("non-admin: 403", c.get("/admin/users/onec").status_code == 403)
login("admin@ftpua.com", "Адмін")
html = page("/admin/users")
check("users page links to 1C employees", "/admin/users/onec" in html)
data = onec_sync.overview()
new = {e["email"] for e in data["new"]}
check("new: active with email, not in the system", {"od@ftpua.com", "m.koval@ftpua.com", "a.melnyk@ftpua.com",
                                                     "i.chagan@ftpua.com"} <= new
      and not new & {"i.petrenko@ftpua.com", "sn@ftpua.com", "o.bondar@ftpua.com", ""})
koval = next(e for e in data["new"] if e["email"] == "m.koval@ftpua.com")
check("rule preview: profit center -> direct + own department", koval["budget"] == {
    "direct": True, "all_departments": False, "departments": ["Продажі"]})
html = page("/admin/users/onec")
check("page: three lists and rule", "Ще не в системі" in html and "Уже в системі" in html
      and "Звільнені в 1С" in html and 'name="new_user_roles"' in html and "Керівник відділу" in html)

# ---------- правило й додавання
c.post("/admin/users/onec", data={"action": "rule", "new_user_roles": ["initiator"], "new_user_budget_own": "1"})
check("rule saved", app_settings.get("new_user_roles") == ["initiator"] and app_settings.get("new_user_budget_own") is True)
c.post("/admin/users/onec", data={"action": "rule", "new_user_roles": ["admin"]})
check("admin role not allowed in rule", app_settings.get("new_user_roles") == ["initiator"])
r = c.post("/admin/users/onec", data={"action": "add", "emails": ["m.koval@ftpua.com", "od@ftpua.com", "sn@ftpua.com"]},
           follow_redirects=True)
check("added two (dismissed skipped)", "Додано співробітників: 2" in r.get_data(as_text=True))
rk = user_roles.find_by_email("m.koval@ftpua.com")
check("roles by rule, name from 1C", rk["roles"] == {"initiator"} and rk["name"] == "Коваль Марія")
check("department and head from 1C", org_structure.department_of(rk["id"]) == "SAL"
      and org_structure.head_of("SAL")["id"] == rk["id"])
b = user_budget.get(rk["id"])
check("profit center: direct + own department items", b["direct"] and b["departments"] == ["Продажі"])
od = user_roles.find_by_email("od@ftpua.com")
bo = user_budget.get(od["id"])
check("not a profit center: only own department items", not bo["direct"] and bo["departments"] == ["БАА"]
      and org_structure.head_of("BA")["id"] == od["id"])

# ---------- розбіжності з 1С
data = onec_sync.overview()
ex = {p["email"]: p for p in data["existing"]}
check("mismatch: department", ex["i.petrenko@ftpua.com"]["issues"] == ["dept"])
check("mismatch: accountant without 1C user (and no department)", ex["o.bondar@ftpua.com"]["issues"] == ["dept", "onec"])
check("added people have no issues", not ex["m.koval@ftpua.com"]["issues"])
c.post("/admin/users/onec", data={"action": "fix", "ids": ["oid-ip", "oid-ob"]})
check("fixed from 1C", org_structure.department_of("oid-ip") == "LOG" and user_onec.guid_for("oid-ob") == "U-OLENA")
c.post("/admin/users/onec", data={"action": "apply_rule", "ids": ["oid-ip"]})
bi = user_budget.get("oid-ip")
check("rule applied to existing: adds, keeps roles", bi["direct"] and bi["departments"] == ["Логістика"]
      and user_roles.get("oid-ip")["roles"] == {"initiator"})
user_budget.save_settings("oid-ip", "i.petrenko@ftpua.com", "Ірина Петренко", False, False, ["Логістика"], updated_by="t")
check("direct items can be removed by hand afterwards", not user_budget.get("oid-ip")["direct"])

# ---------- звільнені
check("dismissed listed", [p["email"] for p in onec_sync.overview()["dismissed"]] == ["sn@ftpua.com"])
c.post("/admin/users/onec", data={"action": "close", "ids": ["oid-sn"]})
check("access closed", user_roles.get("oid-sn")["roles"] == set() and not onec_sync.overview()["dismissed"])

# ---------- картка: відділ із 1С, відділи для заявок, точний користувач 1С
html = page(f"/admin/users/{od['id']}")
check("card: request departments column", 'name="request_deps"' in html and "Подає заявки" in html)
org_structure.set_department(od["id"], "od@ftpua.com", od["name"], "IT", "t")
check("card: 1C department hint", "У 1С: <strong>Відділ бізнес-аналізу</strong>" in page(f"/admin/users/{od['id']}"))
org_structure.set_department(od["id"], "od@ftpua.com", od["name"], "BA", "t")
c.post(f"/admin/users/{od['id']}", data={"roles": ["initiator"], "department": "BA", "dept_sent": "1",
                                         "request_deps": ["LOG", "BA"], "head_deps": ["BA"], "mode": "selected",
                                         "departments": ["БАА"]})
check("request departments saved (main first)", org_structure.request_departments_of(od["id"]) == ["BA", "LOG"])
user_roles.set_roles("oid-ob", "o.bondar@ftpua.com", "Олена Бондар", ["acc_cash"], "t")
user_onec.set_link("oid-ob", "o.bondar@ftpua.com", "", "", "t")
html = page("/admin/users/oid-ob")
check("card: exact 1C user via employee", "Знайдено в 1С за співробітником" in html and 'value="U-OLENA" selected' in html)

# ---------- поле «Відділ» у заявці
app_settings.set("dept_head_mode", "approve", "t")
org_structure.set_department_staff("LOG", {"id": "oid-ip", "email": "i.petrenko@ftpua.com", "name": "Ірина Петренко"}, [], "t")
BASE = {"pay_date": "2026-12-20", "organization_id": "1", "counterparty_id": "1", "expense_code": "",
        "currency": "UAH", "amount": "100", "payment_form": "bank"}
login("od@ftpua.com", "Олександр")
html = page("/requests/new")
check("two departments: select with own departments", 'name="department"' in html and 'value="LOG"' in html
      and 'value="FIN"' not in html.split('name="department"')[1].split("</select>")[0])
items = user_budget.allowed_items(user_budget.get(od["id"]), expense_items.get_items())
BASE["expense_code"] = items[0]["code"]
c.post("/requests/new", data={**BASE, "department": "FIN", "action": "save"})
rid = db.last_id()
check("foreign department from the form ignored", db.get_request(rid)["department"] == "BA")
c.post(f"/requests/{rid}", data={**BASE, "department": "LOG", "action": "submit",
                                 "version": str(db.get_request(rid)["version"])})
req = db.get_request(rid)
check("chosen department saved, history", req["department"] == "LOG"
      and any(h["action"] == "department" and "Логістика" in h["comment"] for h in req["history"]))
check("head of the chosen department approves", req["status"] == "dept_head")
check("my requests: department filter shown", "Відділ" in page("/requests"))
login("i.petrenko@ftpua.com")
check("LOG head sees and can approve", req["number"] in page("/department") and 'value="approve"' in page(f"/requests/{rid}"))

login("m.koval@ftpua.com")
html = page("/requests/new")
check("one department: no select", 'name="department"' not in html and 'value="Відділ продажу" readonly' in html)
print("ALL OK" if ok else "SOME FAILED")
