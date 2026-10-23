"""Налаштування відділів: керівник погоджує / лише бачить окремо для відділу, поріг керівника, заступник погоджує
замість керівника, фіндиректор завжди для відділу; сторінка «Відділи» й журнал змін; заявки в роботі не змінюються."""
import os, sys, tempfile
sys.path.insert(0, os.getcwd())
tmp = tempfile.mkdtemp()
os.environ.update(UPLOAD_DIR=tmp, ADMIN_EMAILS="adm@ftpua.com")
import app as m
import app_settings, dept_settings, mailer, notifications, org_structure, settings_log, user_budget, user_roles
import storage as db

ok = True
def check(label, cond):
    global ok
    ok &= bool(cond); print(("PASS " if cond else "FAIL ") + label)

sent = []
mailer.transport = lambda cfg, to, subject, html: sent.append((to, subject))
for k, v in {"mail_enabled": True, "mail_sender": "noreply@ftpua.com", "smtp_host": "smtp.example.com",
             "approval_mode": "sequential", "dept_head_mode": "view", "cfo_mode": "observe"}.items():
    app_settings.set(k, v, "t")
PEOPLE = {"a@ftpua.com": ["initiator"], "h@ftpua.com": ["initiator"], "d@ftpua.com": ["initiator"],
          "r@ftpua.com": ["acc_resident"], "c@ftpua.com": ["cfo"]}
for email, roles in PEOPLE.items():
    user_roles.set_roles(email, email, email.split("@")[0].upper(), roles, "t")
    user_budget.save_settings(email, email, email, True, True, [], updated_by="t")
org_structure.set_department("a@ftpua.com", "a@ftpua.com", "A", "LOG", "t")
org_structure.set_department("d@ftpua.com", "d@ftpua.com", "D", "LOG", "t")
org_structure.set_department_staff("LOG", {"id": "h@ftpua.com", "email": "h@ftpua.com", "name": "H"},
                                   [{"id": "d@ftpua.com", "email": "d@ftpua.com", "name": "D"}], "t")
notifications._members_cache.clear()

claims = {}
def login(email):
    claims.clear(); claims.update({"name": email.split("@")[0].upper(), "preferred_username": email, "oid": email})
m.auth._auth.get_user = lambda: dict(claims) or None
c = m.app.test_client()
page = lambda url: c.get(url).get_data(as_text=True)
def flush():
    mailer.wait(); out = list(sent); sent.clear(); return out

def submit(amount, author="a@ftpua.com"):
    login(author)
    c.post("/requests/new", data={"pay_date": "2026-12-20", "organization_id": "1", "counterparty_id": "1",
                                  "expense_code": "01.001", "currency": "UAH", "amount": str(amount),
                                  "payment_form": "bank", "action": "submit"})
    return db.get_request(db.last_id())

# ---------- за замовчуванням — як у загальних налаштуваннях (керівник лише бачить)
r = submit(500)
check("default: global view -> no head step", r["status"] == "accountant" and not r["head_step"])

# ---------- сторінка «Відділи»
login("a@ftpua.com")
check("non-admin: 403", c.get("/admin/departments").status_code == 403)
login("adm@ftpua.com")
html = page("/admin/departments")
check("departments page: tab, settings per department", 'name="head_mode-LOG"' in html and 'name="deputy_approve-LOG"' in html
      and ">Відділи</a>" in html and "Логістика" in html)
c.post("/admin/departments", data={"head_mode-LOG": "approve", "head_threshold-LOG": "abc", "cfo-LOG": "always"})
check("bad threshold -> nothing saved", dept_settings.get("LOG")["head_mode"] == "default")
r_ = c.post("/admin/departments", data={"head_mode-LOG": "approve", "head_threshold-LOG": "1 000", "cfo-LOG": "always",
                                        "head_mode-SAL": "default", "cfo-SAL": "default"}, follow_redirects=True)
s = dept_settings.get("LOG")
check("saved LOG only", s == {"head_mode": "approve", "head_threshold": 1000, "deputy_approve": False, "cfo": "always"}
      and dept_settings.get("SAL") == dept_settings.DEFAULT and "Збережено: Логістика" in r_.get_data(as_text=True))
labels = [e["label"] for e in settings_log.query(50)]
check("journal: department settings", "Відділ «Логістика»: поріг керівника" in labels
      and "Відділ «Логістика»: керівник відділу" in labels)
check("route text", "Керівник (від 1 000 USD) → Бухгалтер → Фіндиректор (завжди) → Оплата" in page("/admin/departments"))

# ---------- поріг керівника й фіндиректор для відділу
r = submit(500)  # ≈ 12 USD
check("below head threshold -> skipped with note", r["status"] == "accountant"
      and any(h["action"] == "auto_skip" and "менше за поріг керівника" in h["comment"] for h in r["history"]))
check("cfo always for department (global: observe)", r["cfo_step"]
      and any("відділ «Логістика» — завжди" in h["comment"] for h in r["history"]))
flush()
big = submit(100000)  # ≈ 2 400 USD
check("above threshold -> head step", big["status"] == "dept_head")
mails = {t for t, s_ in flush()}
check("task mail to head only (no deputy setting)", "h@ftpua.com" in mails and "d@ftpua.com" not in mails)
login("d@ftpua.com")
check("viewer without setting: cannot approve", 'value="approve"' not in page(f"/requests/{big['id']}"))
login("h@ftpua.com")
check("head: in queue, can approve", big["number"] in page("/approvals") and 'value="approve"' in page(f"/requests/{big['id']}"))

# ---------- заступник погоджує
login("adm@ftpua.com")
c.post("/admin/departments", data={"head_mode-LOG": "approve", "head_threshold-LOG": "1000", "cfo-LOG": "always",
                                   "deputy_approve-LOG": "1"})
check("deputy setting saved", dept_settings.deputies_approve("LOG"))
flush()
r2 = submit(100000)
mails = {t for t, s_ in flush()}
check("task mail to head and deputy", {"h@ftpua.com", "d@ftpua.com"} <= mails)
login("d@ftpua.com")
check("deputy: in queue and can approve", r2["number"] in page("/approvals") and 'value="approve"' in page(f"/requests/{r2['id']}"))
c.post(f"/requests/{r2['id']}/action", data={"action": "approve", "version": str(db.get_request(r2["id"])["version"])})
r2 = db.get_request(r2["id"])
check("deputy approved, name in history", r2["status"] == "accountant"
      and any(h["action"] == "approve" and h["role"] == "dept_head" and h["user_email"] == "d@ftpua.com" for h in r2["history"]))
own = submit(100000, author="d@ftpua.com")
login("d@ftpua.com")
check("deputy cannot approve own request", own["status"] == "dept_head" and 'value="approve"' not in page(f"/requests/{own['id']}"))
login("h@ftpua.com")
check("head approves deputy's request", 'value="approve"' in page(f"/requests/{own['id']}"))

# ---------- зміна налаштувань не ламає заявки в роботі; відділ «лише бачить» при загальному «погоджує»
login("adm@ftpua.com")
c.post("/admin/departments", data={"head_mode-LOG": "view", "head_threshold-LOG": "", "cfo-LOG": "default"})
check("earlier request still waits for head", db.get_request(big["id"])["status"] == "dept_head")
login("h@ftpua.com")
check("head still sees it in queue", big["number"] in page("/approvals"))
app_settings.set("dept_head_mode", "approve", "t")
r3 = submit(100000)
check("department view overrides global approve", r3["status"] == "accountant" and not r3["head_step"])
check("cfo back to global (observe)", not r3["cfo_step"])
login("adm@ftpua.com")
c.post("/admin/departments", data={"head_mode-LOG": "default", "head_threshold-LOG": "", "cfo-LOG": "default"})
r4 = submit(100)
check("default follows global approve", r4["status"] == "dept_head")
print("ALL OK" if ok else "SOME FAILED")
