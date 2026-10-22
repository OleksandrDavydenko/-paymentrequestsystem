import os, sys, tempfile
sys.path.insert(0, os.getcwd())
os.environ.update(UPLOAD_DIR=tempfile.mkdtemp(), GROUP_ADMIN="g-admin", GROUP_INITIATOR="g-init",
                  GROUP_ACC_CASH="g-cash", GROUP_ACC_NONRESIDENT="g-nonres", GROUP_ACCOUNTANT="g-res-legacy",
                  GROUP_CFO="g-cfo", ADMIN_EMAILS="od@ftpua.com")
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
m.auth._auth.get_token_for_user = lambda s: {"access_token": "t", "scope": " ".join(s)}
c = m.app.test_client()

# Бухгалтер при погодженні вибирає «Вид оплати» — у старих тестах підставляємо перший доступний
import payment_accounts as _pa, storage as _md
_orig_post = c.post
def _post(url, data=None, **kw):
    if isinstance(data, dict) and data.get("action") == "approve" and "payment_account_id" not in data and url.endswith("/action"):
        _r = _md.get_request(int(url.split("/")[2]))
        _opts = _pa.options_for(_r) if _r else []
        if _opts:
            data = {**data, "payment_account_id": str(_opts[0]["id"])}
    return _orig_post(url, data=data, **kw)
c.post = _post


BASE = {"pay_date": "2026-10-10", "counterparty_id": "1", "organization_id": "1", "expense_code": "01.001",
        "currency": "UAH", "amount": "100", "action": "submit"}
ids = {}
login("i@ftpua.com", ["g-init"])
CASES = {"cash": ("cash", "4"), "bank_resident": ("bank", "1"), "bank_nonresident": ("bank", "4")}
for form, (pf, org) in CASES.items():
    c.post("/requests/new", data={**BASE, "payment_form": pf, "organization_id": org, "cash_receiver": "contractor"})
    ids[form] = db.last_id()
    check(f"{form}: submitted to accountant stage", db.get_request(ids[form])["status"] == "accountant")
check("old 'bank_resident' value rejected", c.post("/requests/new", data={**BASE, "payment_form": "bank_resident"}).status_code == 200)
check("cash from Litbia still goes to cashier", db.get_request(ids["cash"])["organization_id"] == "4")

acc = {"cash": ("g-cash", "acc_cash"), "bank_resident": ("g-res-legacy", "acc_resident"),
       "bank_nonresident": ("g-nonres", "acc_nonresident")}
for form, (group, role) in acc.items():
    login(f"{role}@ftpua.com", [group])
    page = c.get("/approvals").get_data(as_text=True)
    mine = f"/requests/{ids[form]}\""
    others = [f"/requests/{ids[f]}\"" for f in ids if f != form]
    check(f"{role}: sees own form, not others", mine in page and not any(o in page for o in others))
    for f in ids:
        if f != form:
            before = len(db.get_request(ids[f])["history"])
            c.post(f"/requests/{ids[f]}/action", data={"action": "approve"})
            check(f"{role} cannot approve {f}", len(db.get_request(ids[f])["history"]) == before)
    c.post(f"/requests/{ids[form]}/action", data={"action": "approve"})
    r = db.get_request(ids[form])
    check(f"{role} approves own -> cfo, history role", r["status"] == "cfo" and r["history"][-1]["role"] == role)

login("cfo@ftpua.com", ["g-cfo"])
for rid in ids.values():
    c.post(f"/requests/{rid}/action", data={"action": "approve"})
check("cfo approves all -> to_pay", all(db.get_request(i)["status"] == "to_pay" for i in ids.values()))

login("cash@ftpua.com", ["g-cash"])
c.post(f"/requests/{ids['bank_resident']}/action", data={"action": "pay"})
check("cashier cannot pay resident", db.get_request(ids["bank_resident"])["status"] == "to_pay")
c.post(f"/requests/{ids['cash']}/action", data={"action": "pay"})
check("cashier pays cash", db.get_request(ids["cash"])["status"] == "paid")

page = c.get(f"/requests/{ids['bank_nonresident']}").get_data(as_text=True)
check("route bar names nonresident accountant", "Бухгалтер (безготівка, нерезидент)" in page)

login("od@ftpua.com", [])
import user_roles; user_roles.set_roles("u1", "u@ftpua.com", "U", ["initiator"], "t")
page = c.get("/admin/users/u1").get_data(as_text=True)
check("user card has 3 accountant roles", all(x in page for x in ["Бухгалтер (готівка)", "Бухгалтер (безготівка, резидент)", "Бухгалтер (безготівка, нерезидент)"]))
print("ALL OK" if ok else "SOME FAILED")
