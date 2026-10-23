import os, sys, tempfile
sys.path.insert(0, os.getcwd())
tmp = tempfile.mkdtemp()
os.environ.update(UPLOAD_DIR=tmp, APP_SETTINGS_FILE=os.path.join(tmp, "s.json"), USER_PREFS_FILE=os.path.join(tmp, "p.json"),
                  USER_DIRECTORY_FILE=os.path.join(tmp, "d.json"), ORG_STRUCTURE_FILE=os.path.join(tmp, "o.json"),
                  GROUP_ADMIN="g-a", GROUP_INITIATOR="g-i", GROUP_ACC_RESIDENT="g-r", GROUP_ACC_CASH="g-c",
                  GROUP_CFO="g-f", ADMIN_EMAILS="")
import app as m
import user_budget; user_budget.get = lambda oid: {"direct": True, "all_departments": True, "departments": []}
import mailer, notifications, app_settings, org_structure
import workflow as wf
import storage as db

ok = True
def check(label, cond):
    global ok
    ok &= bool(cond)
    print(("PASS " if cond else "FAIL ") + label)

sent = []
mailer.transport = lambda cfg, to, subject, html: sent.append({"to": to, "subject": subject})
MEMBERS = {"acc_resident": ["res@ftpua.com"], "cfo": ["cfo@ftpua.com"], "dept_head": ["h@ftpua.com", "x@ftpua.com"]}
notifications.role_members = lambda role: [{"email": e, "name": e} for e in MEMBERS.get(role, [])]
claims = {}
def login(email, groups):
    claims.clear(); claims.update({"name": email.split("@")[0], "preferred_username": email, "oid": email, "groups": groups})
m.auth._auth.get_user = lambda: dict(claims)
m.auth._auth.get_token_for_user = lambda s: {"access_token": "t", "scope": " ".join(s)}
c = m.app.test_client()

def admin_page():
    """Сторінки адмінки разом (користувачі + вкладки налаштувань) — для перевірок вмісту."""
    return "".join(c.get(u).get_data(as_text=True) for u in ["/admin/users"] + [
        f"/admin/settings?tab={t}" for t in ("approval", "request", "payment", "notify", "data")])

# Бухгалтер при погодженні вибирає «Вид оплати» — у старих тестах підставляємо перший доступний
import payment_accounts as _pa, storage as _md
_orig_post = c.post
def _post(url, data=None, **kw):
    if isinstance(data, dict) and data.get("action") == "approve" and "payment_account_id" not in data and url.endswith("/action"):
        _r = _md.get_request(int(url.split("/")[2]))
        _opts = _pa.options_for(_r) if _r else []
        if _opts:
            data = {**data, "payment_account_id": str(_opts[0]["id"])}
    return _orig_post(url, data=data, **kw)
c.post = _post
def flush():
    mailer.wait(); out = sorted(x["to"] for x in sent); sent.clear(); return out
BASE = {"pay_date": "2026-10-20", "counterparty_id": "1", "organization_id": "1", "expense_code": "01.001",
        "currency": "UAH", "amount": "500", "payment_form": "bank", "action": "submit"}
INIT, HEAD, OTHER_HEAD, ACC, CFO = ["g-i"], [], [], ["g-r"], ["g-f"]  # керівник — з розділу «Відділи», не група
st = lambda rid: db.get_request(rid)["status"]

def settings(mode="sequential", head="view"):
    app_settings.set("approval_mode", mode, "t"); app_settings.set("dept_head_mode", head, "t")

for k, v in {"mail_enabled": True, "mail_sender": "noreply@ftpua.com"}.items():
    app_settings.set(k, v, "t")
org_structure.set_department("i@ftpua.com", "i@ftpua.com", "I", "LOG", "t")
org_structure.set_department("nohead@ftpua.com", "nohead@ftpua.com", "N", "FIN", "t")
org_structure.set_department("h@ftpua.com", "h@ftpua.com", "H", "LOG", "t")
H = {"id": "h@ftpua.com", "email": "h@ftpua.com", "name": "H"}
X = {"id": "x@ftpua.com", "email": "x@ftpua.com", "name": "X"}
org_structure.set_department_staff("LOG", H, [], "t")
org_structure.set_department_staff("SAL", X, [], "t")

def submit(author="i@ftpua.com", groups=INIT):
    login(author, groups); c.post("/requests/new", data=BASE); return db.last_id()

# Демо-заявка, що чекає керівника, лишилася б у його черзі й у режимі «лише бачить» — перевіряємо лише заявки тесту
for r_ in [x for x in db.list_all() if x["status"] == "dept_head"]:
    r_["status"] = "accountant"; db.save_request(r_)

# ---------- 1. view + sequential
settings("sequential", "view")
rid = submit()
check("view+seq: submit -> accountant", st(rid) == "accountant" and not db.get_request(rid).get("head_step"))
check("request stores author department", db.get_request(rid)["department"] == "LOG")
check("view: mail only to accountant", flush() == ["res@ftpua.com"])
login("h@ftpua.com", HEAD)
check("view: head-only home -> /department", c.get("/").headers["Location"].endswith("/department"))
check("view: head queue empty, no 'На погодження' menu", "На погодження" not in c.get("/department").get_data(as_text=True))
page = c.get("/department").get_data(as_text=True)
check("department page lists request + title", db.get_request(rid)["number"] in page and "Заявки відділу: Логістика" in page)
card = c.get(f"/requests/{rid}")
check("head can open card, no decision buttons", card.status_code == 200 and 'value="approve"' not in card.get_data(as_text=True))
check("head sees request department", 'value="Логістика" readonly' in card.get_data(as_text=True))
login("x@ftpua.com", OTHER_HEAD)
check("other dept head: 404 on card", c.get(f"/requests/{rid}").status_code == 404)
check("other dept head: not in his department list", db.get_request(rid)["number"] not in c.get("/department").get_data(as_text=True))

# ---------- 2. approve + sequential
settings("sequential", "approve")
rid = submit()
check("approve+seq: submit -> dept_head", st(rid) == "dept_head" and db.get_request(rid)["head_step"])
check("mail only to heads of LOG (not x)", flush() == ["h@ftpua.com"])
login("x@ftpua.com", OTHER_HEAD)
c.post(f"/requests/{rid}/action", data={"action": "approve"})
check("other head cannot approve", st(rid) == "dept_head")
login("h@ftpua.com", HEAD)
check("head / -> approvals when approving", c.get("/").headers["Location"].endswith("/approvals"))
check("head queue has request", db.get_request(rid)["number"] in c.get("/approvals").get_data(as_text=True))
card = c.get(f"/requests/{rid}").get_data(as_text=True)
check("route bar has head stage first", card.index("Керівник відділу") < card.index("Бухгалтер (безготівка"))
c.post(f"/requests/{rid}/action", data={"action": "approve"})
check("head approve -> accountant", st(rid) == "accountant")
check("after head: author + accountant notified", flush() == ["i@ftpua.com", "res@ftpua.com"])
check("head processed list has it", db.get_request(rid)["number"] in c.get("/approvals?view=processed").get_data(as_text=True))
login("res@ftpua.com", ACC); c.post(f"/requests/{rid}/action", data={"action": "approve"})
login("cfo@ftpua.com", CFO); c.post(f"/requests/{rid}/action", data={"action": "approve"})
check("approve+seq full route -> to_pay", st(rid) == "to_pay")
states = wf.stage_states(db.get_request(rid))
check("stage states: head done, to_pay current", states[0] == ("Керівник відділу", "done") and states[-1][1] == "current")
flush()

# ---------- 3. approve + parallel
settings("parallel", "approve")
rid = submit(); flush()
login("h@ftpua.com", HEAD); c.post(f"/requests/{rid}/action", data={"action": "approve"})
check("approve+par: head approve -> approval (parallel)", st(rid) == "approval")
got = flush(); print("GOT", got); check("parallel: accountant + cfo notified together", got == ["cfo@ftpua.com", "i@ftpua.com", "res@ftpua.com"])
states = wf.stage_states(db.get_request(rid))
check("parallel stage states", [s for _, s in states] == ["done", "current", "current", "todo"])
login("res@ftpua.com", ACC); c.post(f"/requests/{rid}/action", data={"action": "approve"})
login("cfo@ftpua.com", CFO); c.post(f"/requests/{rid}/action", data={"action": "approve"})
check("approve+par full route -> to_pay", st(rid) == "to_pay")
flush()

# ---------- 4. view + parallel
settings("parallel", "view")
rid = submit()
check("view+par: submit -> approval", st(rid) == "approval")
flush()

# ---------- 5. особливі випадки
settings("sequential", "approve")
rid = submit("h@ftpua.com", INIT + HEAD)
r = db.get_request(rid)
check("author is head: auto-approved -> accountant", r["status"] == "accountant" and r["head_step"]
      and any(h["action"] == "approve" and h["role"] == "dept_head" and "Автоматично" in h["comment"] for h in r["history"]))
check("author-head: accountant notified", "res@ftpua.com" in flush())
rid = submit("nohead@ftpua.com")
r = db.get_request(rid)
check("department without head: auto_skip -> accountant", r["status"] == "accountant" and not r["head_step"]
      and r["history"][-1]["action"] == "auto_skip" and "немає керівника" in r["history"][-1]["comment"])
check("auto_skip: accountant still notified", "res@ftpua.com" in flush())
rid = submit("nodep@ftpua.com")
r = db.get_request(rid)
check("author without department: auto_skip", r["status"] == "accountant" and "не вказано відділ" in r["history"][-1]["comment"])
flush()

# rework by head, resubmit after switching to view
rid = submit()
login("h@ftpua.com", HEAD); c.post(f"/requests/{rid}/action", data={"action": "rework", "comment": "уточніть"})
check("head rework -> rework", st(rid) == "rework")
check("rework stage marks head", wf.stage_states(db.get_request(rid))[0][1] == "returned")
settings("sequential", "view")
login("i@ftpua.com", INIT); c.post(f"/requests/{rid}/action", data={"action": "submit"})
check("resubmit uses current settings (view) -> accountant", st(rid) == "accountant" and not db.get_request(rid)["head_step"])
# in-flight request not affected by settings change
settings("sequential", "approve"); rid2 = submit(); settings("sequential", "view")
login("h@ftpua.com", HEAD); c.post(f"/requests/{rid2}/action", data={"action": "approve"})
check("settings change does not break in-flight", st(rid2) == "accountant")
flush()

# admin sets status dept_head
login("od@ftpua.com", ["g-a"])
c.post(f"/requests/{rid}/admin-status", data={"status": "dept_head", "comment": "на керівника"})
check("admin can set dept_head status", st(rid) == "dept_head" and db.get_request(rid)["head_step"])
flush()

# ---------- 6. адмінка
c.post("/admin/settings", data={"approval_mode": "parallel", "dept_head_mode": "approve"})
check("admin saves dept_head_mode", app_settings.get("dept_head_mode") == "approve" and app_settings.get("approval_mode") == "parallel")
page = admin_page()
check("route summary shown", "Керівник відділу → Бухгалтер + Фіндиректор (одночасно) → Оплата" in page)
check("departments source shown", "Відділи співробітників" in page and "Мокові дані" in page)
c.post("/admin/users/new@ftpua.com", data={"roles": ["initiator"], "department": "SAL"})
check("admin saves user department (card)", org_structure.department_of("new@ftpua.com") == "SAL")
c.post("/admin/users/new@ftpua.com", data={"roles": ["initiator"], "department": "BOGUS"})
check("unknown department rejected", org_structure.department_of("new@ftpua.com") == "SAL")
import user_directory
user_directory.remember([{"id": "new@ftpua.com", "email": "new@ftpua.com", "name": "New"}], active=True)
c.post("/admin/users/new@ftpua.com", data={"roles": ["initiator"], "department": "SAL", "head_deps": ["IT"]})
check("admin sets department head (card)", org_structure.head_departments("new@ftpua.com") == ["IT"])
check("departments section route removed", c.post("/admin/departments/save", data={"department": "IT"}).status_code == 404)
login("od@ftpua.com", ["g-a"])
page = c.get("/admin/requests?dep=LOG").get_data(as_text=True); print("DEPSEL", "Усі відділи" in page, [r["number"] for r in db.list_all() if f">{r['number']}<" in page and r.get("department") != "LOG"])
check("department filter on all requests", 'name="dep"' in page and all(
    r["department"] == "LOG" for r in db.list_all() if f">{r['number']}<" in page))
adm = admin_page()
check("no Azure column for dept head; no departments section", "Кер. відділу" not in adm and 'id="departments"' not in adm)
check("non-head cannot open /department", (login("i@ftpua.com", INIT), c.get("/department").status_code)[1] == 403)
print("ALL OK" if ok else "SOME FAILED")
