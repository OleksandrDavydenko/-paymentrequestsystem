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
import app_settings, mailer, notifications, organizations, payment_types, payment_accounts
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

# ---------- розбір
t = payment_types._parse_types([
    {"GUID": "A", "Name": "БАНК ФТП, ТОВ", "IsDeleted": False, "OrganizationsGUID": "93F0"},
    {"GUID": "B", "Name": "ГОША", "IsDeleted": False, "OrganizationsGUID": "00000000000000000000000000000000"},
    {"GUID": "C", "Name": "БЕЗ", "IsDeleted": False}])
check("payment types: org_guid, zeros -> None", {x["id"]: x["org_guid"] for x in t} == {"A": "93F0", "B": None, "C": None})
o = organizations.parse([{"Code": "1", "Name": "ФТП", "Country": "Україна", "GUID": "93F0", "IsDeleted": False}])
check("organizations: guid parsed", o[0]["guid"] == "93F0")
check("by_guid (mock)", organizations.by_guid("ORG-G4")["name"] == "Litbia" and organizations.by_guid("") is None)

# ---------- organization_of
check("mock accounts: org of account", payment_accounts.organization_of("10")["name"] == "Litbia"
      and payment_accounts.organization_of("1")["code"] == "1" and payment_accounts.organization_of(None) is None)
real_use = payment_accounts.use_types
payment_accounts.use_types = lambda: True  # режим «види оплати з 1С» (мокові типи)
check("1C types: org via OrganizationsGUID", payment_accounts.organization_of("PT-NLB")["name"] == "Litbia"
      and payment_accounts.organization_of("PT-PUMB") is None)
opts = payment_accounts.options_for({}, None, frozenset({"cfo"}))
check("options carry org name", any(x["id"] == "PT-NLB" and x["org"] == "Litbia" for x in opts))

# ---------- «До оплати»: режим «Бухгалтер», заявки без організації
app_settings.set("org_field_mode", "accountant", "t")
BASE = {"pay_date": "2026-10-20", "counterparty_id": "1", "expense_code": "01.001", "currency": "UAH",
        "amount": "700", "payment_form": "bank", "accountant_type": "acc_resident", "action": "submit"}
def to_pay_request(account):
    login("author@ftpua.com", ["g-i"])
    c.post("/requests/new", data=BASE)
    rid = db.last_id()
    login("res@ftpua.com", ["g-r"])
    c.post(f"/requests/{rid}/action", data={"action": "approve", "payment_account_id": account})
    login("cfo@ftpua.com", ["g-f"])
    c.post(f"/requests/{rid}/action", data={"action": "approve"})
    return rid
app_settings.set("require_payment_account", False, "t")
# бухгалтер резидентів у 1С-режимі бачить свої види оплати лише при зв'язку з 1С — даємо вибрати фіндиректору нижче
r1 = to_pay_request("")
login("cfo@ftpua.com", ["g-f"])
c.post(f"/requests/{r1}/payment-account", data={"payment_account_id": "PT-NLB"})
r2 = to_pay_request("")
check("setup: two requests to pay", db.get_request(r1)["status"] == "to_pay" and db.get_request(r1)["payment_account_id"] == "PT-NLB"
      and db.get_request(r2)["status"] == "to_pay")
html = c.get("/to-pay").get_data(as_text=True)
check("to-pay: 'Платимо з' column, no Організація column", "<th>Платимо з</th>" in html and "<th>Організація</th>" not in html)
row1 = html.split(f">{db.get_request(r1)['number']}<")[1].split("</tr>")[0]
row2 = html.split(f">{db.get_request(r2)['number']}<")[1].split("</tr>")[0]
check("row shows org of payment type / dash", 'data-label="Платимо з">Litbia<' in row1 and 'data-label="Платимо з">—<' in row2)
check("by-org totals", 'id="by-org"' in html and "За організаціями" in html and "Не визначено" in html)
f = c.get("/to-pay?payorg=4").get_data(as_text=True)
check("payorg filter", f">{db.get_request(r1)['number']}<" in f and f">{db.get_request(r2)['number']}<" not in f)
f = c.get("/to-pay?payorg=none").get_data(as_text=True)
check("payorg none filter", f">{db.get_request(r2)['number']}<" in f and f">{db.get_request(r1)['number']}<" not in f)
page = c.get(f"/requests/{r1}").get_data(as_text=True)
check("card: cfo sees 'Платимо з'", "Платимо з:</strong> Litbia" in page)
login("author@ftpua.com", ["g-i"])
page = c.get(f"/requests/{r1}").get_data(as_text=True)
check("initiator: no 'Платимо з' in card", "Платимо з" not in page)
check("initiator: no 'Платимо з' in lists", "Платимо з" not in c.get("/requests").get_data(as_text=True))
check("initiator cannot open to-pay", c.get("/to-pay").status_code == 403)
payment_accounts.use_types = real_use

# ---------- режим «Організація»: обидві колонки
app_settings.set("org_field_mode", "organization", "t")
login("cfo@ftpua.com", ["g-f"])
html = c.get("/to-pay").get_data(as_text=True)
check("organization mode: both columns", "<th>Платимо з</th>" in html and "<th>Організація</th>" in html)

print("ALL OK" if ok else "SOME FAILED")
