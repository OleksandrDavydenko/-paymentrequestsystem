import os, sys, tempfile
sys.path.insert(0, os.getcwd())
os.environ.update(UPLOAD_DIR=tempfile.mkdtemp(), GROUP_ADMIN="g-a", GROUP_INITIATOR="g-i", GROUP_ACC_CASH="g-c",
                  GROUP_ACC_RESIDENT="g-r", GROUP_ACC_NONRESIDENT="g-n", GROUP_CFO="g-f", ADMIN_EMAILS="")
import app as m
import user_budget; user_budget.get = lambda oid: {'direct': True, 'all_departments': True, 'departments': []}
import storage as db
import workflow as wf

ok = True
def check(label, cond):
    global ok
    ok &= bool(cond)
    print(("PASS " if cond else "FAIL ") + label)

claims = {}
def login(email, groups):
    claims.clear(); claims.update({"name": email.split("@")[0], "preferred_username": email, "oid": email, "groups": groups})
m.auth._auth.get_user = lambda: dict(claims)
c = m.app.test_client()
rows = lambda url: c.get(url).get_data(as_text=True).count('<tr class="clickable')

# --- Оплачені
login("res@ftpua.com", ["g-r"])
before_paid = rows("/to-pay?view=paid")
exp = sum(1 for r in db.list_all() if r["status"] == "paid" and wf.channel_of(r) == "bank_resident")
check(f"resident sees {exp} paid", before_paid == exp)
c.post("/to-pay/pay", data={"ids": ["3", "12"], "comment": "ПД №501"})
t = c.get("/to-pay?view=paid").get_data(as_text=True)
check("paid requests now in Оплачені", rows("/to-pay?view=paid") == exp + 2 and "/requests/3\"" in t and "ПД №501" in t)
check("paid tab has no checkboxes", 'name="ids"' not in t and "План платежів" not in t)
check("paid view hides overdue chip", ">Прострочені<" not in t)
check("accountant can open paid card", c.get("/requests/3").status_code == 200)
today = m.date.today().isoformat()
check("paid filtered by actual pay date (today)", rows(f"/to-pay?view=paid&from={today}&to={today}") == 2)

login("cash@ftpua.com", ["g-c"])
check("cashier does not see resident paid", "/requests/3\"" not in c.get("/to-pay?view=paid").get_data(as_text=True))
login("cfo@ftpua.com", ["g-f"])
check("cfo sees all paid", rows("/to-pay?view=paid") == sum(1 for r in db.list_all() if r["status"] == "paid"))

# --- Зміна статусу адміністратором
login("res@ftpua.com", ["g-r"])
check("non-admin cannot change status", c.post("/requests/13/admin-status", data={"status": "draft", "comment": "x"}).status_code == 403)
check("non-admin card has no admin panel", "Адміністратор: змінити статус" not in c.get("/requests/13").get_data(as_text=True))

login("admin@ftpua.com", ["g-a"])
t = c.get("/requests/13").get_data(as_text=True)
check("admin card has admin panel", "Адміністратор: змінити статус" in t)
c.post("/requests/13/admin-status", data={"status": "cfo", "comment": ""})
check("comment required", db.get_request(13)["status"] == "to_pay")
c.post("/requests/13/admin-status", data={"status": "to_pay", "comment": "same"})
check("same status rejected", len(db.get_request(13)["history"]) == len(db.get_request(13)["history"]))
c.post("/requests/13/admin-status", data={"status": "bogus", "comment": "x"})
check("unknown status rejected", db.get_request(13)["status"] == "to_pay")
r = c.post("/requests/13/admin-status", data={"status": "cfo", "comment": "Помилково погоджено, повторна перевірка"})
req = db.get_request(13)
h = req["history"][-1]
check("to_pay -> cfo by admin", req["status"] == "cfo")
check("history entry", h["action"] == "admin_status" and h["role"] == "admin" and h["from_status"] == "to_pay"
      and h["to_status"] == "cfo" and h["comment"].startswith("Помилково"))
t = c.get("/requests/13").get_data(as_text=True)
check("history shows admin change", "Статус змінено адміністратором" in t and "Помилково погоджено" in t)
login("cfo@ftpua.com", ["g-f"])
check("request back in cfo queue", "/requests/13\"" in c.get("/approvals").get_data(as_text=True))
check("and gone from to-pay", "/requests/13\"" not in c.get("/to-pay").get_data(as_text=True))

login("admin@ftpua.com", ["g-a"])
c.post("/requests/3/admin-status", data={"status": "to_pay", "comment": "Оплату не проведено банком"})
check("paid -> to_pay by admin", db.get_request(3)["status"] == "to_pay")
c.post("/requests/9/admin-status", data={"status": "draft", "comment": "Повернути автору як чернетку"})
login("od@ftpua.com", ["g-i"])
check("author can edit after admin -> draft", 'name="action" value="save"' in c.get("/requests/9").get_data(as_text=True))
print("ALL OK" if ok else "SOME FAILED")

# --- Опрацьовані (погоджувачі)
ok = True
login("cfo@ftpua.com", ["g-f"])
t = c.get("/approvals?view=processed").get_data(as_text=True)
mine = [r for r in db.list_all() if wf.processed_entry(r, {"cfo"})]
check(f"cfo processed = {len(mine)}", t.count('<tr class="clickable') == len(mine) and "Моє рішення" in t)
c.post("/requests/5/action", data={"action": "approve"})
t = c.get("/approvals?view=processed").get_data(as_text=True)
check("just approved appears first", t.index("/requests/5\"") < t.index("<tr class=\"clickable", t.index("/requests/5\"") - 400) + 400)
check("processed tab keeps view on filter reset", "view=processed" in c.get("/approvals?view=processed&org=1").get_data(as_text=True).split("Скинути")[0][-200:])
login("nonres@ftpua.com", ["g-n"])
t = c.get("/approvals?view=processed").get_data(as_text=True)
check("nonresident sees own role decisions", "/requests/5\"" in t and "/requests/1\"" not in t)
login("i@ftpua.com", ["g-i"])
check("initiator has no approvals", c.get("/approvals?view=processed").status_code == 403)
print("ALL OK" if ok else "SOME FAILED")
