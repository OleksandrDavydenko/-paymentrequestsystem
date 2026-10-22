import os, sys, tempfile
sys.path.insert(0, os.getcwd())
tmp = tempfile.mkdtemp()
os.environ.update(UPLOAD_DIR=tmp, APP_SETTINGS_FILE=os.path.join(tmp, "s.json"), USER_PREFS_FILE=os.path.join(tmp, "p.json"),
                  USER_DIRECTORY_FILE=os.path.join(tmp, "d.json"), ORG_STRUCTURE_FILE=os.path.join(tmp, "o.json"),
                  COUNTERPARTIES_PENDING_FILE=os.path.join(tmp, "cp.json"), USER_ONEC_FILE=os.path.join(tmp, "u.json"),
                  GROUP_ADMIN="g-a", GROUP_INITIATOR="g-i", GROUP_ACC_RESIDENT="g-r", GROUP_CFO="g-f", ADMIN_EMAILS="")
import app as m, storage as db, payment_accounts as pa, payment_types as pt, user_onec
import user_budget; user_budget.get = lambda oid: {"direct": True, "all_departments": True, "departments": []}
import graph
ok = True
def check(label, cond):
    global ok; ok &= bool(cond); print(("PASS " if cond else "FAIL ") + label)
pa.use_types = lambda: True  # як із підключеним Power BI (дані — мокові таблиці PaymentTypes / UserPaymentTypes)
claims = {}
def login(email, groups, oid=None):
    claims.clear(); claims.update({"name": email.split("@")[0].title(), "preferred_username": email, "oid": oid or email, "groups": groups})
m.auth._auth.get_user = lambda: dict(claims)
m.auth._auth.get_token_for_user = lambda s: {"access_token": "t", "scope": " ".join(s)}
graph.group_members = lambda t, g: [{"id": "irina", "name": "Iryna Chagan", "email": "i.chagan@ftpua.com"}] if g == "g-r" else []
c = m.app.test_client()

def admin_page():
    """Сторінки адмінки разом (користувачі + вкладки налаштувань) — для перевірок вмісту."""
    return "".join(c.get(u).get_data(as_text=True) for u in ["/admin/users"] + [
        f"/admin/settings?tab={t}" for t in ("approval", "request", "payment", "notify", "data")])
BASE = {"pay_date": "2026-10-20", "counterparty_id": "1", "organization_id": "1", "expense_code": "01.001",
        "currency": "UAH", "amount": "500", "payment_form": "bank"}

check("suggest by name", pt.suggest_user("Iryna Chagan")["name"] == "Irina.Chagan")
login("od@ftpua.com", ["g-a"])
page = c.get("/admin/users/irina").get_data(as_text=True)
check("card: 1C section with suggestion", "Користувач 1С" in page and "Підібрано за схожістю" in page and 'value="U-IRINA" selected' in page)
login("i@ftpua.com", ["g-i"]); c.post("/requests/new", data={**BASE, "action": "submit"}); rid = db.last_id()

login("i.chagan@ftpua.com", ["g-r"], "irina")
page = c.get(f"/requests/{rid}").get_data(as_text=True)
check("not linked: no options, hint shown", "ще не зв&#39;язано з користувачем 1С" in page)
c.post(f"/requests/{rid}/action", data={"action": "approve"})
check("not linked: approval allowed without account", db.get_request(rid)["status"] == "cfo")
# повернемо і зв'яжемо
login("od@ftpua.com", ["g-a"])
_r = db.get_request(rid); _r["status"] = "accountant"; db.save_request(_r)
c.post("/admin/users/irina", data={"roles": ["acc_resident"], "onec_user": "U-IRINA", "mode": "none"})
check("link saved", user_onec.guid_for("irina") == "U-IRINA")
check("list shows 1C link", "1С: Irina.Chagan" in admin_page())
login("i.chagan@ftpua.com", ["g-r"], "irina")
opts = [a["name"] for a in pa.options_for(db.get_request(rid), {"oid": "irina"}, frozenset({"acc_resident"}))]
check("accountant sees only her 1C payment types", opts == ["КАРТА", "НАЛ", "ПУМБ ФТП, ТОВ"])
c.post(f"/requests/{rid}/action", data={"action": "approve"})
check("linked: approval needs payment type", db.get_request(rid)["status"] == "accountant")
c.post(f"/requests/{rid}/action", data={"action": "approve", "payment_account_id": "PT-KASA"})
check("type without access rejected", db.get_request(rid)["status"] == "accountant")
c.post(f"/requests/{rid}/action", data={"action": "approve", "payment_account_id": "PT-NAL"})
check("approve with own type", db.get_request(rid)["status"] == "cfo" and db.get_request(rid)["payment_account_id"] == "PT-NAL")
login("cfo@ftpua.com", ["g-f"])
check("cfo sees all types", len(pa.options_for(db.get_request(rid), {"oid": "cfo"}, frozenset({"cfo"}))) == len(pt.types()))
c.post(f"/requests/{rid}/payment-account", data={"payment_account_id": "PT-KASA"})
check("cfo can choose any", db.get_request(rid)["payment_account_id"] == "PT-KASA")
check("label from 1C", pa.label("PT-KASA") == "КАСА")
login("i@ftpua.com", ["g-i"])
c.get("/requests")
check("initiator still doesn't see it", "КАСА" not in c.get(f"/requests/{rid}").get_data(as_text=True))
print("ALL OK" if ok else "SOME FAILED")
