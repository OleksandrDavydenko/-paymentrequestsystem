"""Рахунки з 1С і тип оплати: підбір лише контрагента й відділу заявки, лише неоплачені, попередження «вже в заявці»;
«Оплата підряднику» — статті відділу (за замовчуванням CR000065), «Непрямі витрати» — бюджет; обов'язкові рахунки
для відділу; рахунки вимкнено зовсім."""
import os, sys, tempfile
sys.path.insert(0, os.getcwd())
tmp = tempfile.mkdtemp()
os.environ.update(UPLOAD_DIR=tmp, ADMIN_EMAILS="adm@ftpua.com")
import app as m
import app_settings, dept_settings, invoices, org_structure, settings_log, user_budget, user_roles
import storage as db

ok = True
def check(label, cond):
    global ok
    ok &= bool(cond); print(("PASS " if cond else "FAIL ") + label)

claims = {}
def login(email):
    claims.clear(); claims.update({"name": email.split("@")[0].upper(), "preferred_username": email, "oid": email})
m.auth._auth.get_user = lambda: dict(claims) or None
c = m.app.test_client()
page = lambda url: c.get(url).get_data(as_text=True)
get = lambda url: c.get(url).get_json()

user_roles.set_roles("a@ftpua.com", "a@ftpua.com", "A", ["initiator"], "t")
user_budget.save_settings("a@ftpua.com", "a@ftpua.com", "A", True, True, [], updated_by="t")
org_structure.set_department("a@ftpua.com", "a@ftpua.com", "A", "LOG", "t")
org_structure.set_request_departments("a@ftpua.com", "a@ftpua.com", "A", ["SAL"], "t")

# ---------- запит до Power BI
q = invoices.query("CP-GUID", "DEP-GUID")
check("DAX: counterparty, department, unpaid only", "'InvoiceForPay'[CounterpartyGUID] = \"CP-GUID\"" in q
      and "[DepartmentGUID] = \"DEP-GUID\"" in q and "[UnpaidAmountInCurrency] > 0" in q)

# ---------- підбір рахунків
login("a@ftpua.com")
r = get("/requests/invoices?counterparty_id=1&department=LOG")
nums = [i["number"] for i in r["invoices"]]
check("LOG invoices of counterparty, newest first", nums == ["НП-104788", "НП-104512"] and r["department"] == "Логістика")
check("unpaid amount (partially paid)", next(i for i in r["invoices"] if i["number"] == "НП-104788")["unpaid"] == "5300.00")
check("only own department: SAL", [i["number"] for i in get("/requests/invoices?counterparty_id=4&department=SAL")["invoices"]] == ["UC-0920"])
check("foreign department ignored -> main (LOG)", [i["number"] for i in get("/requests/invoices?counterparty_id=4&department=FIN")["invoices"]] == ["UC-0915"])
check("fully paid not shown", [i["number"] for i in get("/requests/invoices?counterparty_id=5&department=LOG")["invoices"]] == ["MSK-7781203"])
check("no department in 1C -> not shown", get("/requests/invoices?counterparty_id=6&department=LOG")["invoices"] == [])
check("no counterparty -> hint", "контрагента" in get("/requests/invoices?department=LOG")["error"])

# ---------- тип оплати й статті
app_settings.set("contractor_expense_codes", ["01.002"], "t")
BASE = {"pay_date": "2026-12-20", "organization_id": "1", "counterparty_id": "1", "currency": "UAH", "amount": "12450",
        "payment_form": "bank", "payment_kind_sent": "1"}
html = page("/requests/new")
check("form: kind switch, lists for picker, lines block hidden", 'name="payment_kind"' in html and 'id="expense-lists"' in html
      and 'id="block-lines" hidden' in html)
r_ = c.post("/requests/new", data={**BASE, "expense_code": "01.001", "action": "save"})
check("kind required", "Виберіть, що оплачуємо" in r_.get_data(as_text=True))
r_ = c.post("/requests/new", data={**BASE, "payment_kind": "contractor", "expense_code": "01.001", "action": "save"})
check("contractor: budget item rejected", "не входить у статті оплати підряднику" in r_.get_data(as_text=True))
before = db.last_id()
c.post("/requests/new", data={**BASE, "payment_kind": "contractor", "expense_code": "01.002", "action": "save",
                              "lines-0-invoice": "НП-104512", "lines-0-amount": "12450", "lines-0-currency": "UAH",
                              "lines-0-deal": "Договір №15/2025"})
rid = db.last_id()
req = db.get_request(rid)
check("contractor saved with invoice line", rid != before and req["payment_kind"] == "contractor"
      and req["expense_code"] == "01.002" and req["lines"][0]["invoice"] == "НП-104512")
used = lambda url: next(i for i in get(url)["invoices"] if i["number"] == "НП-104512")["used_in"]
check("own request not counted as other", req["number"] not in used(f"/requests/invoices?counterparty_id=1&department=LOG&request_id={rid}"))
check("used in another request -> warning", req["number"] in used("/requests/invoices?counterparty_id=1&department=LOG"))
req["status"] = "rejected"; db.save_request(req)
check("rejected request not counted", req["number"] not in used("/requests/invoices?counterparty_id=1&department=LOG"))
req["status"] = "draft"; db.save_request(req)

c.post(f"/requests/{rid}", data={**BASE, "payment_kind": "indirect", "expense_code": "01.001", "action": "save",
                                 "lines-0-invoice": "НП-104512", "lines-0-amount": "12450", "lines-0-currency": "UAH",
                                 "version": str(db.get_request(rid)["version"])})
req = db.get_request(rid)
check("indirect: budget item, lines dropped, history", req["payment_kind"] == "indirect" and req["expense_code"] == "01.001"
      and req["lines"] == [] and any(h["action"] == "payment_kind" for h in req["history"]))
c.post(f"/requests/{rid}", data={**BASE, "payment_kind": "contractor", "expense_code": "01.001", "action": "save",
                                 "version": str(db.get_request(rid)["version"])})
check("switch to contractor: old budget item not kept", db.get_request(rid)["payment_kind"] == "indirect")

# ---------- налаштування відділів: статті підрядника й обов'язкові рахунки
login("adm@ftpua.com")
html = page("/admin/departments")
check("departments: contractor items and required invoices", 'name="contractor_codes-LOG"' in html
      and 'name="invoices_required-LOG"' in html and 'name="contractor_default"' in html)
c.post("/admin/departments", data={"head_mode-LOG": "default", "cfo-LOG": "default", "contractor_codes-LOG": "01.003",
                                   "invoices_required-LOG": "1", "head_mode-SAL": "default", "cfo-SAL": "default",
                                   "contractor_codes-SAL": "*", "contractor_default": "01.002,01.004"})
check("saved: LOG own list + required, SAL default", dept_settings.get("LOG")["contractor_codes"] == ["01.003"]
      and dept_settings.invoices_required("LOG") and dept_settings.contractor_codes("SAL") == ["01.002", "01.004"])
labels = [e["label"] for e in settings_log.query(50)]
check("journal", "Відділ «Логістика»: статті оплати підряднику" in labels and "Відділ «Логістика»: рахунки обов'язкові" in labels)
login("a@ftpua.com")
r_ = c.post("/requests/new", data={**BASE, "payment_kind": "contractor", "expense_code": "01.003", "action": "submit"})
check("required invoices: submit without lines refused", "додайте рахунок" in r_.get_data(as_text=True))
n = db.last_id()
c.post("/requests/new", data={**BASE, "payment_kind": "contractor", "expense_code": "01.003", "action": "save"})
check("draft without lines saved", db.last_id() == n + 1)
c.post("/requests/new", data={**BASE, "payment_kind": "contractor", "department": "SAL", "expense_code": "01.004",
                              "action": "submit", "lines-0-invoice": "UC-0920", "lines-0-amount": "980", "lines-0-currency": "USD"})
req = db.get_request(db.last_id())
check("SAL: default list, not required -> submitted", req["department"] == "SAL" and req["status"] != "draft")
pv = get("/requests/route-preview?amount=100&currency=UAH&department=LOG&payment_kind=contractor&payment_form=bank&organization_id=1")
check("summary: required invoices in todo", any("рахунок" in t for t in pv["todo"]))

# ---------- рахунки вимкнено
app_settings.set("invoices_mode", "off", "t")
html = page("/requests/new")
check("off: no kind switch, no invoices block", 'name="payment_kind"' not in html and 'id="block-lines"' not in html
      and 'id="pick-invoices"' not in html)
c.post("/requests/new", data={k: v for k, v in BASE.items() if k != "payment_kind_sent"} | {"expense_code": "01.001", "action": "save"})
req = db.get_request(db.last_id())
check("off: budget item saved", req["expense_code"] == "01.001")
print("ALL OK" if ok else "SOME FAILED")
