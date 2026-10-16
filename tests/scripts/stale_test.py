"""Захист від одночасних змін: дія з картки, відкритої до чужої зміни, не виконується, а користувач
бачить, хто й коли змінив заявку. Номер нової заявки присвоюється при збереженні."""
import os, re, sys, tempfile
sys.path.insert(0, os.getcwd())
tmp = tempfile.mkdtemp()
os.environ.update(UPLOAD_DIR=tmp, GROUP_ADMIN="g-a", GROUP_INITIATOR="g-i", GROUP_ACC_RESIDENT="g-r", GROUP_CFO="g-f",
                  ADMIN_EMAILS="")
import app as m
import storage as db
import user_budget; user_budget.get = lambda oid: {"direct": True, "all_departments": True, "departments": []}

ok = True
def check(label, cond):
    global ok
    ok &= bool(cond); print(("PASS " if cond else "FAIL ") + label)

claims = {}
def login(email, groups, name=None):
    claims.clear(); claims.update({"name": name or email.split("@")[0].title(), "preferred_username": email,
                                   "oid": email, "groups": groups})
m.auth._auth.get_user = lambda: dict(claims)
m.auth._auth.get_token_for_user = lambda s: {"access_token": "t", "scope": " ".join(s)}
c = m.app.test_client()

FORM = {"pay_date": "2026-10-20", "organization_id": "1", "counterparty_id": "1", "expense_code": "01.001",
        "currency": "UAH", "amount": "100", "payment_form": "bank"}

login("author@ftpua.com", ["g-i"])
page = c.get("/requests/new").get_data(as_text=True)
check("new request: number assigned on save", "Нова заявка" in page and "присвоїться при збереженні" in page)
before = db.last_id()
c.post("/requests/new", data={**FORM, "action": "submit"})
rid = db.last_id()
check("number assigned", rid == before + 1 and db.get_request(rid)["number"] == f"{rid:09d}")

def version_on_page(html):
    found = set(re.findall(r'name="version" value="(\d+)"', html))
    return int(found.pop()) if len(found) == 1 else None

# бухгалтер відкрив картку…
login("res@ftpua.com", ["g-r"])
v = version_on_page(c.get(f"/requests/{rid}").get_data(as_text=True))
check("card forms carry the request version", v == db.get_request(rid)["version"])
# …тим часом адміністратор змінив статус
login("admin@ftpua.com", ["g-a"], name="Адмін Тест")
c.post(f"/requests/{rid}/admin-status", data={"status": "rework", "comment": "Перевірка", "version": str(v)})
check("admin change applied", db.get_request(rid)["status"] == "rework")
# …бухгалтер натискає «Погодити» на старій сторінці
login("res@ftpua.com", ["g-r"])
r = c.post(f"/requests/{rid}/action", data={"action": "approve", "version": str(v)})
check("stale action -> back to card", r.status_code == 302 and r.headers["Location"].endswith(f"/requests/{rid}"))
html = c.get(f"/requests/{rid}").get_data(as_text=True)
check("explains who changed it", "щойно змінили — Адмін Тест о" in html and "Дію не виконано" in html)
check("nothing changed by stale action", db.get_request(rid)["status"] == "rework")

# автор редагує зі сторінки, відкритої до зміни
login("author@ftpua.com", ["g-i"])
r = c.post(f"/requests/{rid}", data={**FORM, "amount": "999", "action": "save", "version": str(v)})
check("stale edit rejected", r.status_code == 302 and db.get_request(rid)["amount"] != 999)
# з актуальною версією — працює
v2 = version_on_page(c.get(f"/requests/{rid}").get_data(as_text=True))
r = c.post(f"/requests/{rid}", data={**FORM, "amount": "999", "action": "save", "version": str(v2)})
check("fresh edit saved", str(db.get_request(rid)["amount"]) in ("999", "999.00"))
# коментар не залежить від версії (лише дописується)
r = c.post(f"/requests/{rid}/comment", data={"comment": "Без версії"})
check("comment without version works", db.get_request(rid)["history"][-1]["comment"] == "Без версії")

# сховище: збереження застарілої копії -> StaleRequestError
a, b = db.get_request(rid), db.get_request(rid)
a["note"] = "A"; db.save_request(a)
try:
    b["note"] = "B"; db.save_request(b); raised = False
except db.StaleRequestError as e:
    raised = e.current["note"] == "A"
check("storage rejects stale copy", raised and db.get_request(rid)["note"] == "A")
print("ALL OK" if ok else "SOME FAILED")
