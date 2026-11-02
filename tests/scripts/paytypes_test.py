import os, sys, tempfile
sys.path.insert(0, os.getcwd())
tmp = tempfile.mkdtemp()
os.environ.update(UPLOAD_DIR=tmp, APP_SETTINGS_FILE=os.path.join(tmp, "s.json"), USER_PREFS_FILE=os.path.join(tmp, "p.json"),
                  USER_DIRECTORY_FILE=os.path.join(tmp, "d.json"), ORG_STRUCTURE_FILE=os.path.join(tmp, "o.json"),
                  COUNTERPARTIES_PENDING_FILE=os.path.join(tmp, "cp.json"),
                  GROUP_ADMIN="g-a", GROUP_INITIATOR="g-i", GROUP_ACC_RESIDENT="g-r", GROUP_CFO="g-f", ADMIN_EMAILS="")
import app as m, storage as db, payment_accounts as pa, payment_types as pt
import user_budget; user_budget.get = lambda oid: {"direct": True, "all_departments": True, "departments": []}
ok = True
def check(label, cond):
    global ok; ok &= bool(cond); print(("PASS " if cond else "FAIL ") + label)
pa.use_types = lambda: True  # як із підключеним Power BI (дані — мокові таблиці PaymentTypes / UserPaymentTypes)
claims = {}
def login(email, groups, oid=None):
    claims.clear(); claims.update({"name": email.split("@")[0].title(), "preferred_username": email, "oid": oid or email, "groups": groups})
m.auth._auth.get_user = lambda: dict(claims)
m.auth._auth.get_token_for_user = lambda s: {"access_token": "t", "scope": " ".join(s)}
import user_roles; user_roles.set_roles("irina", "i.chagan@ftpua.com", "Iryna Chagan", ["acc_resident"], "t")
c = m.app.test_client()

def admin_page():
    """Сторінки адмінки разом (користувачі + вкладки налаштувань) — для перевірок вмісту."""
    return "".join(c.get(u).get_data(as_text=True) for u in ["/admin/users"] + [
        f"/admin/settings?tab={t}" for t in ("approval", "request", "payment", "notify", "data")])
BASE = {"pay_date": "2026-10-20", "counterparty_id": "1", "organization_id": "1", "expense_code": "01.001",
        "currency": "UAH", "amount": "500", "payment_form": "bank"}

# Користувач 1С — лише з 1С (пошта → співробітник → його користувач 1С); у картці тільки показується
login("od@ftpua.com", ["g-a"])
page = c.get("/admin/users/irina").get_data(as_text=True)
onec_box = page.split('id="uc-onec-section"')[1].split("</section>")[0]
check("card: not linked in 1C — the reason, nothing to choose", "Не зв'язано в 1С" in onec_box
      and "Співробітник <strong>Чаган Ірина</strong> у 1С є" in onec_box and "<select" not in onec_box)
user_roles.set_roles("ini-x", "ini-x@ftpua.com", "Ini X", ["initiator", "cfo"], "t")
page = c.get("/admin/users/ini-x").get_data(as_text=True)
check("not an accountant: 1C section dimmed with hint", 'class="card uc-section is-disabled" id="uc-onec-section"' in page
      and "Потрібно лише бухгалтерам" in page)
check("no employee with this email: says what to fill in 1C", "У 1С немає співробітника з поштою" in page)
import re
check("list filter: accountant without 1C user", re.search(r'data-flags="[^"]*acc-no1c', admin_page()))
login("i@ftpua.com", ["g-i"]); c.post("/requests/new", data={**BASE, "action": "submit"}); rid = db.last_id()

login("i.chagan@ftpua.com", ["g-r"], "irina")
page = c.get(f"/requests/{rid}").get_data(as_text=True)
check("not linked: no options, hint shown", "У 1С вас не зв&#39;язано з користувачем 1С" in page)
c.post(f"/requests/{rid}/action", data={"action": "approve"})
check("not linked: approval allowed without account", db.get_request(rid)["status"] == "cfo")
# повернемо; у 1С в картці користувача Irina.Chagan вказали співробітника — зв'язок з'являється сам
login("od@ftpua.com", ["g-a"])
_r = db.get_request(rid); _r["status"] = "accountant"; db.save_request(_r)
for a in pt._access.items():
    if a["user"] == "U-IRINA":
        a["employee"] = "EMP-IRINA"
page = c.get("/admin/users/irina").get_data(as_text=True)
check("card: link from 1C with payment types", "✓ З 1С" in page and "Irina.Chagan" in page
      and "Види оплати в 1С (3)" in page and "співробітник Чаган Ірина" in page)
check("list shows 1C link", "1С: Irina.Chagan" in admin_page())
login("i.chagan@ftpua.com", ["g-r"], "irina")
opts = [a["name"] for a in pa.options_for(db.get_request(rid), {"email": "i.chagan@ftpua.com", "oid": "irina"},
                                          frozenset({"acc_resident"}))]
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
