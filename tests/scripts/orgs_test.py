import os, sys, tempfile
sys.path.insert(0, os.getcwd())
tmp = tempfile.mkdtemp()
os.environ.update(UPLOAD_DIR=tmp, APP_SETTINGS_FILE=os.path.join(tmp, "s.json"), USER_PREFS_FILE=os.path.join(tmp, "p.json"),
                  USER_DIRECTORY_FILE=os.path.join(tmp, "d.json"), ORG_STRUCTURE_FILE=os.path.join(tmp, "o.json"),
                  COUNTERPARTIES_PENDING_FILE=os.path.join(tmp, "cp.json"), GROUP_ADMIN="g-a", GROUP_INITIATOR="g-i", GROUP_ACC_CASH="g-c", GROUP_ACC_RESIDENT="g-r", ADMIN_EMAILS="")
import app as m, storage as db, organizations as orgs, app_settings
import user_budget; user_budget.get = lambda oid: {"direct": True, "all_departments": True, "departments": []}
import graph; graph.group_members = lambda t, g: []
ok = True
def check(label, cond):
    global ok; ok &= bool(cond); print(("PASS " if cond else "FAIL ") + label)
claims = {}
def login(email, groups):
    claims.clear(); claims.update({"name": "U", "preferred_username": email, "oid": email, "groups": groups})
m.auth._auth.get_user = lambda: dict(claims)
m.auth._auth.get_token_for_user = lambda s: {"access_token": "t", "scope": " ".join(s)}
c = m.app.test_client()

def admin_page():
    """Сторінки адмінки разом (користувачі + вкладки налаштувань) — для перевірок вмісту."""
    return "".join(c.get(u).get_data(as_text=True) for u in ["/admin/users"] + [
        f"/admin/settings?tab={t}" for t in ("approval", "request", "payment", "notify", "data")])
BASE = {"pay_date": "2026-10-20", "counterparty_id": "1", "expense_code": "01.001", "currency": "UAH", "amount": "10", "payment_form": "bank"}
check("default: all allowed", set(orgs.options()) == {"1", "2", "3", "4"})
login("i@ftpua.com", ["g-i"])
check("non-admin 403", c.post("/admin/organizations", data={"allowed": ["1"]}).status_code == 403)
login("od@ftpua.com", ["g-a"])
page = admin_page()
assert "organizations is-disabled" not in page, "organization mode: block must be active"
check("admin section listed", 'id="organizations"' in page and "дозволено 4 з 4" in page)
r = c.post("/admin/organizations", data={}, follow_redirects=True)
check("cannot disable all", "хоча б одну" in r.get_data(as_text=True) and app_settings.get("disabled_organizations") == [])
c.post("/admin/organizations", data={"allowed": ["1", "2", "4"]})
check("org 3 disabled", app_settings.get("disabled_organizations") == ["3"] and "3" not in orgs.options() and "3" in orgs.filter_options())
login("i@ftpua.com", ["g-i"])
page = c.get("/requests/new").get_data(as_text=True)
check("form: disabled org not offered", 'value="3"' not in page.split('id="organization_id"')[1].split("</select>")[0])
r = c.post("/requests/new", data={**BASE, "organization_id": "3", "action": "save"})
check("form: disabled org rejected", r.status_code == 200 and "Оберіть організацію" in r.get_data(as_text=True))
rid = 6  # мокова заявка od з організацією 3 (ФОП) — у od
req = db.get_request(rid)
check("existing request keeps disabled org on display", req["organization_id"] == "3")
login("od@ftpua.com", ["g-i"])
check("filters still list disabled org (used in own requests)", 'name="org" value="3"' in c.get("/requests").get_data(as_text=True))
# ---------- призначення бухгалтерів організаціям
import workflow as wf
login("od@ftpua.com", ["g-a"])
c.post("/admin/organizations", data={"allowed": ["1", "2", "3", "4"], "acc_bank_3": "acc_cash", "acc_cash_3": "acc_cash", "acc_bank_1": "bogus"})
check("assignment saved (bogus ignored)", app_settings.get("org_accountants") == {"3": {"bank": "acc_cash", "cash": "acc_cash"}})
check("ФОП bank payments -> cash accountant", wf.accountant_role({"organization_id": "3", "payment_form": "bank"}) == "acc_cash")
check("others by rule", wf.accountant_role({"organization_id": "1", "payment_form": "bank"}) == "acc_resident"
      and wf.accountant_role({"organization_id": "4", "payment_form": "bank"}) == "acc_nonresident")
check("payment form display unchanged", wf.channel_of({"organization_id": "3", "payment_form": "bank"}) == "bank_resident")
login("i@ftpua.com", ["g-i"])
page = c.get("/requests/new").get_data(as_text=True)
check("form hint knows assignment", '"3": {"bank": "Бухгалтер (готівка)"' in page or "ORG_ACCOUNTANTS" in page)
c.post("/requests/new", data={**BASE, "organization_id": "1", "action": "submit"})
rid = db.last_id()
login("od@ftpua.com", ["g-a"])
c.post("/admin/organizations", data={"allowed": ["1", "2", "4"], "acc_bank_1": "acc_cash"})
login("cash@ftpua.com", ["g-c"])
check("in-flight request moves to newly assigned accountant", db.get_request(rid)["number"] in c.get("/approvals").get_data(as_text=True))
login("res@ftpua.com", ["g-r"])
check("old accountant no longer sees it", db.get_request(rid)["number"] not in c.get("/approvals").get_data(as_text=True))
login("od@ftpua.com", ["g-a"])
page = admin_page()
assert "organizations is-disabled" not in page, "organization mode: block must be active"
check("admin table shows selects", 'name="acc_bank_1"' in page and "Автоматично" in page)
print("ALL OK" if ok else "SOME FAILED")
