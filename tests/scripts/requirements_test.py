"""Вимоги до заявки з налаштувань (документ, дата оплати, рядки рахунків), режими дій автора,
дозволені формати й розмір файлів."""
import io, os, sys, tempfile
from datetime import date, timedelta
sys.path.insert(0, os.getcwd())
tmp = tempfile.mkdtemp()
os.environ.update(UPLOAD_DIR=tmp, GROUP_ADMIN="g-a", GROUP_INITIATOR="g-i", GROUP_ACC_RESIDENT="g-r", ADMIN_EMAILS="")
import app as m
import user_budget; user_budget.get = lambda oid: {"direct": True, "all_departments": True, "departments": []}
import app_settings, clock
import storage as db
from web.request_helpers import add_workdays

ok = True
def check(label, cond):
    global ok
    ok &= bool(cond); print(("PASS " if cond else "FAIL ") + label)

claims = {}
def login(email, groups):
    claims.clear(); claims.update({"name": email, "preferred_username": email, "oid": email, "groups": groups})
m.auth._auth.get_user = lambda: dict(claims)
m.auth._auth.get_token_for_user = lambda s: {"access_token": "t", "scope": " ".join(s)}
c = m.app.test_client()

FAR = (clock.today() + timedelta(days=40)).isoformat()
FORM = {"pay_date": FAR, "organization_id": "1", "counterparty_id": "1", "expense_code": "01.001",
        "currency": "UAH", "amount": "100000", "payment_form": "bank"}

def settings(**kw):
    login("admin@ftpua.com", ["g-a"])
    return c.post("/admin/settings", data={"tab": "request", "org_field_mode": "organization", "withdraw_mode": "before_full",
                                           "cancel_mode": "before_pay", "doc_max_mb": "10", "req_document": "no",
                                           "req_document_limit": "1000", "req_min_pay_days": "0",
                                           "doc_extensions": ["pdf", "jpg", "jpeg", "png", "doc", "docx", "xls", "xlsx"],
                                           **kw}, follow_redirects=True).get_data(as_text=True)

# ---------- робочі дні
check("workdays skip weekend", add_workdays(date(2026, 10, 23), 1) == date(2026, 10, 26)   # пт -> пн
      and add_workdays(date(2026, 10, 24), 1) == date(2026, 10, 26)                         # сб -> пн
      and add_workdays(date(2026, 10, 21), 2) == date(2026, 10, 23))

# ---------- документ обов'язковий від суми (поріг у валюті фіндиректора — USD)
settings(req_document="amount", req_document_limit="1000")
login("author@ftpua.com", ["g-i"])
r = c.post("/requests/new", data={**FORM, "action": "submit"})
html = r.get_data(as_text=True)
check("big request without document -> submit refused", r.status_code == 200 and "Перед відправкою додайте документ" in html
      and "від 1 000 USD" in html)
before = db.last_id()
c.post("/requests/new", data={**FORM, "action": "save"})
rid = db.last_id()
check("draft can be saved without it", rid == before + 1 and db.get_request(rid)["status"] == "draft")
c.post(f"/requests/{rid}/files", data={"files": (io.BytesIO(b"%PDF-1.4 test"), "invoice.pdf")}, content_type="multipart/form-data")
v = str(db.get_request(rid)["version"])
c.post(f"/requests/{rid}", data={**FORM, "action": "submit", "version": v})
check("with a document -> submitted", db.get_request(rid)["status"] not in ("draft",))
r = c.post("/requests/new", data={**FORM, "amount": "100", "action": "submit"})
check("small request -> no document needed", r.status_code == 302 and db.get_request(db.last_id())["status"] != "draft")
settings(req_document="always")
login("author@ftpua.com", ["g-i"])
r = c.post("/requests/new", data={**FORM, "amount": "100", "action": "submit"})
check("'always' -> small request refused too", r.status_code == 200 and "Перед відправкою додайте документ" in r.get_data(as_text=True))

# ---------- найраніша дата оплати й рядки рахунків
settings(req_min_pay_days="3", req_lines_required="1")
login("author@ftpua.com", ["g-i"])
soon = add_workdays(clock.today(), 1).isoformat()
import app_settings as _as
_as.set("contractor_expense_codes", ["01.001"], "t")  # рахунки обов'язкові — для «Оплата підряднику»
r = c.post("/requests/new", data={**FORM, "amount": "100", "pay_date": soon, "action": "submit",
                                  "payment_kind_sent": "1", "payment_kind": "contractor"})
html = r.get_data(as_text=True)
earliest = add_workdays(clock.today(), 3)
check("pay date too early -> error with earliest date", f"не раніше {earliest:%d.%m.%Y}" in html)
check("lines required -> error", "додайте рахунок" in html)
r = c.post("/requests/new", data={**FORM, "amount": "100", "pay_date": earliest.isoformat(), "action": "submit",
                                  "lines-0-invoice": "INV-1", "lines-0-amount": "100", "lines-0-currency": "UAH"})
check("valid date + line -> submitted", r.status_code == 302 and db.get_request(db.last_id())["status"] != "draft")
settings()

# ---------- режими дій автора
login("author@ftpua.com", ["g-i"])
c.post("/requests/new", data={**FORM, "amount": "100", "action": "submit"})
rid = db.last_id()
settings(withdraw_mode="off", cancel_mode="editable")
login("author@ftpua.com", ["g-i"])
page = c.get(f"/requests/{rid}").get_data(as_text=True)
check("withdraw off + cancel only editable -> no author block on approval", 'id="author-actions"' not in page)
c.post(f"/requests/{rid}/author-action", data={"action": "withdraw"})
check("withdraw refused when off", db.get_request(rid)["status"] != "draft")
settings(withdraw_mode="before_decision")
login("acc@ftpua.com", ["g-r"])
c.post(f"/requests/{rid}/action", data={"action": "approve", "payment_account_id": "1",
                                         "version": str(db.get_request(rid)["version"])})
login("author@ftpua.com", ["g-i"])
decided = db.get_request(rid)
c.post(f"/requests/{rid}/author-action", data={"action": "withdraw", "version": str(decided["version"])})
check("'before first decision': after accountant approved -> refused", db.get_request(rid)["status"] == decided["status"]
      and decided["status"] != "draft")
c.post("/requests/new", data={**FORM, "amount": "100", "action": "submit"})
fresh = db.last_id()
c.post(f"/requests/{fresh}/author-action", data={"action": "withdraw", "version": str(db.get_request(fresh)["version"])})
check("'before first decision': nobody decided -> withdrawn", db.get_request(fresh)["status"] == "draft")
settings(cancel_mode="off")
login("author@ftpua.com", ["g-i"])
c.post(f"/requests/{fresh}/author-action", data={"action": "cancel", "comment": "x", "version": str(db.get_request(fresh)["version"])})
check("cancel off -> refused even for draft", db.get_request(fresh)["status"] == "draft")
settings()

# ---------- документи: формати й розмір
settings(doc_extensions=["pdf"], doc_max_mb="1")
login("author@ftpua.com", ["g-i"])
page = c.get(f"/requests/{fresh}").get_data(as_text=True)
check("card shows allowed formats + size", 'accept=".pdf"' in page and "PDF — до 1 МБ кожен" in page
      and '"max_mb": 1' in page)
n = len(db.get_request(fresh)["attachments"])
c.post(f"/requests/{fresh}/files", data={"files": (io.BytesIO(b"x"), "photo.png")}, content_type="multipart/form-data")
check("png refused", len(db.get_request(fresh)["attachments"]) == n)
c.post(f"/requests/{fresh}/files", data={"files": (io.BytesIO(b"%PDF" + b"0" * (1024 * 1024 + 10)), "big.pdf")},
       content_type="multipart/form-data")
check("file over 1 MB refused", len(db.get_request(fresh)["attachments"]) == n)
c.post(f"/requests/{fresh}/files", data={"files": (io.BytesIO(b"%PDF small"), "ok.pdf")}, content_type="multipart/form-data")
check("small pdf accepted", len(db.get_request(fresh)["attachments"]) == n + 1)
html = settings(doc_extensions=[])
check("at least one format required", "хоча б один формат" in html and app_settings.get("doc_extensions") == ["pdf"])
html = settings(doc_max_mb="40")
check("max size bounded 1..25", "від 1 до 25" in html and app_settings.get("doc_max_mb") == 1)
print("ALL OK" if ok else "SOME FAILED")
