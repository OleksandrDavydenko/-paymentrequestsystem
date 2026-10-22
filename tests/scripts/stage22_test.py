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
import app_settings, mailer, notifications
import workflow as wf
import storage as db

ok = True
def check(label, cond):
    global ok
    ok &= bool(cond)
    print(("PASS " if cond else "FAIL ") + label)

sent = []
mailer.transport = lambda cfg, to, subject, html: sent.append(to)
notifications.role_members = lambda role: [{"email": e, "name": e} for e in
                                           {"acc_cash": ["cash1@ftpua.com", "cash2@ftpua.com"],
                                            "acc_nonresident": ["non@ftpua.com"], "acc_resident": ["res@ftpua.com"]}.get(role, [])]
for k, v in {"mail_enabled": True, "mail_sender": "noreply@ftpua.com"}.items():
    app_settings.set(k, v, "t")
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
def flush():
    mailer.wait(); out = list(sent); sent.clear(); return out
INIT, RES, NON, CASH, ADMIN = ["g-i"], ["g-r"], ["g-n"], ["g-c"], ["g-a"]
AUTHOR = "author@ftpua.com"
BASE = {"pay_date": "2026-10-20", "counterparty_id": "1", "expense_code": "01.001",
        "currency": "UAH", "amount": "500", "payment_form": "bank"}
SETTINGS = {"approval_mode": "sequential", "dept_head_mode": "view", "pending_cp_payment": "block",
            "cfo_mode": "approve", "cp_onec_owner": "accountant"}

login("admin@ftpua.com", ADMIN)
html = c.post("/admin/settings", data={**SETTINGS, "org_field_mode": "accountant"}, follow_redirects=True).get_data(as_text=True)
check("accountant mode saved", app_settings.get("org_field_mode") == "accountant" and "тип бухгалтера" in html)
pay = c.get("/admin/settings?tab=payment").get_data(as_text=True)
check("accountant mode: organizations inactive", "organizations is-disabled" in pay and "Не діє в режимі «Бухгалтер»" in pay)
check("admin hint", "Ініціатор, який знає, до кого звертатися" in admin_page())

login(AUTHOR, INIT)
page = c.get("/requests/new").get_data(as_text=True)
check("form: accountant choice, no org / payer", 'name="accountant_type"' in page and 'id="organization_id"' not in page
      and 'name="payer"' not in page and "Бухгалтер нерезидентів" in page)
r = c.post("/requests/new", data={**BASE, "action": "submit"})
check("choice required", "Оберіть, який бухгалтер перевірить заявку" in r.get_data(as_text=True))

flush()
c.post("/requests/new", data={**BASE, "accountant_type": "acc_cash", "action": "submit"})
rid = db.last_id()
r = db.get_request(rid)
mails = flush()
check("bank form + cash accountant chosen -> cash accountant", r["status"] == "accountant" and wf.accountant_role(r) == "acc_cash")
check("all people of the role notified", {"cash1@ftpua.com", "cash2@ftpua.com"} <= set(mails) and "res@ftpua.com" not in mails)
login("cash2@ftpua.com", CASH)
check("any cash accountant sees it in queue", ">" + r["number"] + "<" in c.get("/approvals").get_data(as_text=True))
login("res@ftpua.com", RES)
check("resident accountant does not", ">" + r["number"] + "<" not in c.get("/approvals").get_data(as_text=True))
login("cash1@ftpua.com", CASH)
c.post(f"/requests/{rid}/action", data={"action": "approve", "payment_account_id": ""})
check("cash accountant approves -> cfo", db.get_request(rid)["status"] == "cfo")

login(AUTHOR, INIT)
c.post("/requests/new", data={**BASE, "accountant_type": "acc_nonresident", "action": "submit"})
r2 = db.get_request(db.last_id())
check("nonresident accountant + payer label", wf.accountant_role(r2) == "acc_nonresident" and m.payer_label(r2) == "Іноземна компанія")

# призначення бухгалтерів організаціям не перебиває явний вибір
app_settings.set("org_accountants", {"1": {"bank": "acc_nonresident"}}, "t")
_r = db.get_request(rid); _r["organization_id"] = "1"; db.save_request(_r)
check("explicit type wins over org assignment", wf.accountant_role(db.get_request(rid)) == "acc_cash")
app_settings.set("org_accountants", {}, "t")

# повернення в режим «Організація»: при редагуванні вибір типу зникає, діє правило
login("admin@ftpua.com", ADMIN)
c.post("/admin/settings", data={**SETTINGS, "org_field_mode": "organization"})
login(AUTHOR, INIT)
c.post("/requests/new", data={**BASE, "organization_id": "4", "accountant_type": "acc_cash", "action": "submit"})
r3 = db.get_request(db.last_id())
check("org mode ignores accountant_type", r3.get("accountant_type") is None and wf.accountant_role(r3) == "acc_nonresident")

# ---------- бухгалтер за замовчуванням
import graph, org_structure
MEMBERS = {"g-i": ["author@ftpua.com"], "g-a": ["admin@ftpua.com"]}
graph.group_members = lambda t, gid: [{"id": u, "name": u.split("@")[0].title(), "email": u} for u in MEMBERS.get(gid, [])]
graph.group_members_app = lambda gid: [{"id": u, "name": u.split("@")[0].title(), "email": u} for u in MEMBERS.get(gid, [])]
graph.get_user = lambda t, uid: {"id": uid, "name": uid.split("@")[0].title(), "email": uid}
graph.add_member = graph.remove_member = lambda *a, **k: None
login("admin@ftpua.com", ADMIN)
c.post("/admin/settings", data={**SETTINGS, "org_field_mode": "accountant"})
page = c.get("/admin/users/author@ftpua.com").get_data(as_text=True)
check("user card: accountant default select", 'name="accountant_default"' in page)
org_structure.set_department("author@ftpua.com", AUTHOR, "Author", "LOG", "t")
c.post("/admin/users/author@ftpua.com", data={"roles": ["initiator"], "department": "LOG", "accountant_default": "acc_resident"})
check("saved in card, department kept", org_structure.accountant_default_of("author@ftpua.com") == "acc_resident"
      and org_structure.department_of("author@ftpua.com") == "LOG")
login(AUTHOR, INIT)
page = c.get("/requests/new").get_data(as_text=True)
check("new request preselects card default", 'value="acc_resident" checked' in page and "За замовчуванням з вашої картки" in page)
c.post("/requests/new", data={**BASE, "accountant_type": "acc_nonresident", "action": "submit"})
check("initiator can change", wf.accountant_role(db.get_request(db.last_id())) == "acc_nonresident"
      and "accountant_hint" not in db.get_request(db.last_id()))
# без налаштування в картці — як у попередній заявці
org_structure.set_accountant_default("author@ftpua.com", AUTHOR, "Author", None, "t")
check("clearing default keeps department", org_structure.department_of("author@ftpua.com") == "LOG")
page = c.get("/requests/new").get_data(as_text=True)
check("fallback: last choice", 'value="acc_nonresident" checked' in page and "Як у вашій попередній заявці" in page)
login("other@ftpua.com", INIT)
check("new user: nothing preselected", "checked" not in c.get("/requests/new").get_data(as_text=True).split('name="accountant_type"')[1][:60])
check("non-admin cannot open card", c.post("/admin/users/author@ftpua.com", data={"accountant_default": "acc_cash"}).status_code == 403)

print("ALL OK" if ok else "SOME FAILED")
