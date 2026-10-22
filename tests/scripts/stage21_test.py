import os, sys, tempfile
sys.path.insert(0, os.getcwd())
tmp = tempfile.mkdtemp()
os.environ.update(UPLOAD_DIR=tmp, APP_SETTINGS_FILE=os.path.join(tmp, "s.json"), USER_PREFS_FILE=os.path.join(tmp, "p.json"),
                  USER_DIRECTORY_FILE=os.path.join(tmp, "d.json"), ORG_STRUCTURE_FILE=os.path.join(tmp, "o.json"),
                  COUNTERPARTIES_PENDING_FILE=os.path.join(tmp, "cp.json"),
                  GROUP_ADMIN="g-a", GROUP_INITIATOR="g-i", GROUP_ACC_RESIDENT="g-r", GROUP_ACC_CASH="g-c", GROUP_CFO="g-f",
                  GROUP_ACC_NONRESIDENT="g-n", ADMIN_EMAILS="")
import app as m
import user_budget; user_budget.get = lambda oid: {"direct": True, "all_departments": True, "departments": []}
import app_settings, mailer, notifications, organizations
import workflow as wf
import storage as db

ok = True
def check(label, cond):
    global ok
    ok &= bool(cond)
    print(("PASS " if cond else "FAIL ") + label)

mailer.transport = lambda *a: None
notifications.role_members = lambda role: []
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
INIT, RES, NON, CASH, CFO, ADMIN = ["g-i"], ["g-r"], ["g-n"], ["g-c"], ["g-f"], ["g-a"]
AUTHOR = "author@ftpua.com"
BASE = {"pay_date": "2026-10-20", "counterparty_id": "1", "expense_code": "01.001",
        "currency": "UAH", "amount": "500", "payment_form": "bank"}
SETTINGS = {"approval_mode": "sequential", "dept_head_mode": "view", "pending_cp_payment": "block",
            "cfo_mode": "approve", "cp_onec_owner": "accountant"}
# мокові організації: 1–3 — Україна, 4 — Litbia (нерезидент)

# ---------- режим «Організація» (як раніше)
login(AUTHOR, INIT)
page = c.get("/requests/new").get_data(as_text=True)
check("org mode: organization select", 'id="organization_id"' in page and 'name="payer"' not in page)

# ---------- адмін вмикає «Платник»
login("admin@ftpua.com", ADMIN)
html = c.post("/admin/settings", data={**SETTINGS, "org_field_mode": "payer"}, follow_redirects=True).get_data(as_text=True)
check("payer mode saved", app_settings.get("org_field_mode") == "payer" and "лише платника" in html)
check("admin block", "Організація в заявці" in admin_page())

login(AUTHOR, INIT)
page = c.get("/requests/new").get_data(as_text=True)
check("payer mode: payer radios, no org select", 'name="payer"' in page and 'id="organization_id"' not in page
      and "Іноземна компанія" in page)
r = c.post("/requests/new", data={**BASE, "action": "submit"})
check("payer required", r.status_code == 200 and "Вкажіть платника" in r.get_data(as_text=True))

def submit(payer, form="bank", org=None):
    login(AUTHOR, INIT)
    data = {**BASE, "payer": payer, "payment_form": form, "action": "submit"}
    if org:
        data["organization_id"] = org  # ініціатор не може задати організацію в цьому режимі
    c.post("/requests/new", data=data)
    return db.last_id()

rn = submit("nonresident", org="1")
r = db.get_request(rn)
check("foreign + bank -> nonresident accountant, org ignored", r["status"] == "accountant" and r["payer"] == "nonresident"
      and r.get("organization_id") is None and wf.accountant_role(r) == "acc_nonresident")
rr = submit("resident")
check("ukrainian + bank -> resident accountant", wf.accountant_role(db.get_request(rr)) == "acc_resident")
rc = submit("nonresident", form="cash")
check("cash -> cash accountant", wf.accountant_role(db.get_request(rc)) == "acc_cash")
lst = c.get("/requests").get_data(as_text=True)
check("list: payer label", "Іноземна компанія" in lst and "вкаже бухгалтер" not in lst)

# ---------- бухгалтер погоджує без організації
login("non@ftpua.com", NON)
page = c.get(f"/requests/{rn}").get_data(as_text=True)
panel = page.split('class="card decision"')[1].split("</form>")[0]
check("decision panel: no organization select", 'name="organization_id"' not in panel and 'id="organization"' not in page)
c.post(f"/requests/{rn}/action", data={"action": "approve"})
check("accountant approves without org -> cfo", db.get_request(rn)["status"] == "cfo" and not db.get_request(rn).get("organization_id"))
login("cfo@ftpua.com", CFO)
c.post(f"/requests/{rn}/action", data={"action": "approve"})
login("non@ftpua.com", NON)
c.post(f"/requests/{rn}/action", data={"action": "pay"})
check("paid without org", db.get_request(rn)["status"] == "paid")
check("to-pay/paid list shows payer", "Іноземна компанія" in c.get("/to-pay?view=paid").get_data(as_text=True))

# ---------- стара заявка з організацією, редагування в режимі «Платник»
_r = db.get_request(rr); _r["organization_id"] = "1"; db.save_request(_r)
login("res@ftpua.com", RES)
c.post(f"/requests/{rr}/action", data={"action": "rework", "comment": "уточніть"})
login(AUTHOR, INIT)
page = c.get(f"/requests/{rr}").get_data(as_text=True)
check("rework: payer preselected from org", 'value="resident" checked' in page and "Організація: ТОВ" in page)
c.post(f"/requests/{rr}", data={**BASE, "payer": "resident", "action": "save"})
check("same payer -> org kept", db.get_request(rr)["organization_id"] == "1")
c.post(f"/requests/{rr}", data={**BASE, "payer": "nonresident", "action": "save"})
r = db.get_request(rr)
check("other payer -> org reset with history", r.get("organization_id") is None and r["payer"] == "nonresident"
      and any("скинуто: змінено платника" in h["comment"] for h in r["history"]))
check("routing follows new payer", wf.accountant_role(r) == "acc_nonresident")

# ---------- назад у режим «Організація»
login("admin@ftpua.com", ADMIN)
c.post("/admin/settings", data={**SETTINGS, "org_field_mode": "organization"})
login(AUTHOR, INIT)
check("org mode again: select back", 'id="organization_id"' in c.get("/requests/new").get_data(as_text=True))
c.post("/requests/new", data={**BASE, "organization_id": "4", "action": "submit"})
check("org mode: routing by org", wf.accountant_role(db.get_request(db.last_id())) == "acc_nonresident")

print("ALL OK" if ok else "SOME FAILED")
