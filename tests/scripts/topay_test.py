import os, re, sys, tempfile
sys.path.insert(0, os.getcwd())
os.environ.update(UPLOAD_DIR=tempfile.mkdtemp(), GROUP_INITIATOR="g-init", GROUP_ACC_CASH="g-cash",
                  GROUP_ACC_RESIDENT="g-res", GROUP_ACC_NONRESIDENT="g-nonres", GROUP_CFO="g-cfo", ADMIN_EMAILS="")
import app as m
import user_budget; user_budget.get = lambda oid: {'direct': True, 'all_departments': True, 'departments': []}
import mock_data as db

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
norm = lambda t: t.replace(" ", " ")

login("i@ftpua.com", ["g-init"])
check("initiator has no to-pay", c.get("/to-pay").status_code == 403)
check("initiator nav has no To-pay", "/to-pay" not in c.get("/requests").get_data(as_text=True))

login("cfo@ftpua.com", ["g-cfo"])
t = norm(c.get("/to-pay").get_data(as_text=True))
check("cfo sees all 6", t.count('name="ids"') == 0 and t.count("<tr class=\"clickable") == 6)
check("cfo UAH total 32 579,99", "32 579,99" in t)
check("cfo USD total 4 950,00", "4 950,00" in t)
check("cfo EUR total 5 150,00", "5 150,00" in t)
check("cfo sees overdue badge", "прострочено" in t)
check("cfo form filter present", 'name="form"' in t)
t = norm(c.get("/to-pay?form=bank_nonresident").get_data(as_text=True))
check("filter nonresident -> 2 rows", t.count("<tr class=\"clickable") == 2 and "32 579,99" not in t)
check("nav count", 'До оплати <span class="count">6</span>' in c.get("/approvals").get_data(as_text=True).replace("\n", " ").replace("        ", ""))

login("res@ftpua.com", ["g-res"])
t = norm(c.get("/to-pay").get_data(as_text=True))
check("resident sees 3 with checkboxes", t.count("<tr class=\"clickable") == 3 and t.count('name="ids"') == 3)
check("approvals no longer lists to_pay", "/requests/3\"" not in c.get("/approvals").get_data(as_text=True))
r = c.post("/to-pay/pay", data={"ids": ["3", "12", "13"], "comment": "ПД №77"})
check("bulk pay: 3 and 12 paid", db.get_request(3)["status"] == "paid" and db.get_request(12)["status"] == "paid")
check("bulk pay: 13 (nonresident) untouched", db.get_request(13)["status"] == "to_pay")
check("pay comment in history", db.get_request(3)["history"][-1]["comment"] == "ПД №77")
check("redirect back to to-pay", r.headers["Location"].startswith("/to-pay"))

login("cfo@ftpua.com", ["g-cfo"])
check("cfo cannot bulk pay", c.post("/to-pay/pay", data={"ids": ["13"]}).status_code == 403)

login("cash@ftpua.com", ["g-cash"])
r = c.post("/requests/14/action", data={"action": "pay"})
check("single pay from card -> to-pay page", db.get_request(14)["status"] == "paid" and r.headers["Location"] == "/to-pay")
print("ALL OK" if ok else "SOME FAILED")
