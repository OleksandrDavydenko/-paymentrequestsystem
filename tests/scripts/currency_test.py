import os, sys, tempfile
sys.path.insert(0, os.getcwd())
tmp = tempfile.mkdtemp()
os.environ.update(UPLOAD_DIR=tmp, APP_SETTINGS_FILE=os.path.join(tmp, "s.json"), USER_PREFS_FILE=os.path.join(tmp, "p.json"),
                  USER_DIRECTORY_FILE=os.path.join(tmp, "d.json"), ORG_STRUCTURE_FILE=os.path.join(tmp, "o.json"),
                  COUNTERPARTIES_PENDING_FILE=os.path.join(tmp, "cp.json"),
                  GROUP_ADMIN="g-a", GROUP_INITIATOR="g-i", GROUP_ACC_RESIDENT="g-r", GROUP_ACC_CASH="g-c", GROUP_CFO="g-f",
                  ADMIN_EMAILS="")
import app as m
import user_budget; user_budget.get = lambda oid: {"direct": True, "all_departments": True, "departments": []}
import currencies, mailer, notifications
import storage as db

ok = True
def check(label, cond):
    global ok
    ok &= bool(cond)
    print(("PASS " if cond else "FAIL ") + label)

ROWS = [  # як у таблиці Currencies з 1С
    {"IsDeleted": True, "Code": "643", "Name": "XXXRUB", "FullName": "Російський рубль", "GUID": "1"},
    {"IsDeleted": False, "Code": "985", "Name": "PLN", "FullName": "Польський злотий", "GUID": "2"},
    {"IsDeleted": False, "Code": "980", "Name": "UAH", "FullName": "Гривня", "GUID": "3"},
    {"IsDeleted": False, "Code": "840", "Name": "USD", "FullName": "Долар США", "GUID": "4"},
    {"IsDeleted": False, "Code": "978", "Name": "EUR", "FullName": "Євро", "GUID": "5"},
    {"IsDeleted": False, "Code": "203", "Name": "CZK", "FullName": "Чеська крона", "GUID": "6"},
    {"IsDeleted": "false", "Code": "", "Name": "tusd", "FullName": "Тезер", "GUID": "7"},
]
parsed = currencies.parse(ROWS)
check("order: UAH, USD, EUR first, then alphabetical", [c["name"] for c in parsed] == ["UAH", "USD", "EUR", "CZK", "PLN", "TUSD", "XXXRUB"])
currencies._table.items = lambda: parsed
check("codes() without deleted", currencies.codes() == ["UAH", "USD", "EUR", "CZK", "PLN", "TUSD"])

mailer.transport = lambda *a: None
notifications.role_members = lambda role: []
claims = {"name": "A", "preferred_username": "a@ftpua.com", "oid": "a", "groups": ["g-i"]}
m.auth._auth.get_user = lambda: dict(claims)
c = m.app.test_client()
page = c.get("/requests/new").get_data(as_text=True)
check("form lists 1C currencies with full name", '<option value="CZK"' in page and 'title="Чеська крона"' in page and "XXXRUB" not in page)
BASE = {"pay_date": "2026-10-20", "organization_id": "1", "counterparty_id": "1", "expense_code": "01.001",
        "amount": "100", "payment_form": "bank", "action": "save",
        "lines-0-invoice": "R-1", "lines-0-amount": "100", "lines-0-currency": "PLN"}
c.post("/requests/new", data={**BASE, "currency": "PLN"})
rid = db.last_id()
check("PLN accepted (request + line)", db.get_request(rid)["currency"] == "PLN" and db.get_request(rid)["lines"][0]["currency"] == "PLN")
r = c.post("/requests/new", data={**BASE, "currency": "XXXRUB"})
check("deleted currency rejected", r.status_code == 200 and "Оберіть валюту" in r.get_data(as_text=True))
# валюту прибрали з довідника — стара заявка зберігається з нею й показує її
currencies._table.items = lambda: [x for x in parsed if x["name"] != "PLN"]
c.post(f"/requests/{rid}", data={**BASE, "currency": "PLN"})
page = c.get(f"/requests/{rid}").get_data(as_text=True)
check("old currency kept on existing request", db.get_request(rid)["currency"] == "PLN" and "PLN (немає в довіднику)" in page)
check("totals order uses 1C order", m._currency_order("UAH") < m._currency_order("CZK") < m._currency_order("PLN"))

print("ALL OK" if ok else "SOME FAILED")
