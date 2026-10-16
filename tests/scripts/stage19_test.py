import os, sys, tempfile
sys.path.insert(0, os.getcwd())
tmp = tempfile.mkdtemp()
os.environ.update(UPLOAD_DIR=tmp, APP_SETTINGS_FILE=os.path.join(tmp, "s.json"), USER_PREFS_FILE=os.path.join(tmp, "p.json"),
                  USER_DIRECTORY_FILE=os.path.join(tmp, "d.json"), ORG_STRUCTURE_FILE=os.path.join(tmp, "o.json"),
                  COUNTERPARTIES_PENDING_FILE=os.path.join(tmp, "cp.json"),
                  GROUP_ADMIN="g-a", GROUP_INITIATOR="g-i", GROUP_ACC_RESIDENT="g-r", GROUP_ACC_CASH="g-c", GROUP_CFO="g-f",
                  ADMIN_EMAILS="boss@ftpua.com")
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
mailer.transport = lambda cfg, to, subject, html: sent.append({"to": to, "subject": subject, "html": html})
notifications.role_members = lambda role: [{"email": e, "name": e} for e in
                                           {"acc_resident": ["res@ftpua.com"], "cfo": ["cfo@ftpua.com"],
                                            "admin": ["admin@ftpua.com"]}.get(role, [])]
claims = {}
def login(email, groups):
    claims.clear(); claims.update({"name": email.split("@")[0].title(), "preferred_username": email, "oid": email, "groups": groups})
m.auth._auth.get_user = lambda: dict(claims)
m.auth._auth.get_token_for_user = lambda s: {"access_token": "t", "scope": " ".join(s)}
c = m.app.test_client()
def flush():
    mailer.wait(); out = list(sent); sent.clear(); return out
for k, v in {"mail_enabled": True, "mail_sender": "noreply@ftpua.com"}.items():
    app_settings.set(k, v, "t")

BASE = {"pay_date": "2026-10-20", "organization_id": "1", "counterparty_id": "1", "expense_code": "01.001",
        "currency": "UAH", "amount": "900", "payment_form": "bank"}
INIT, RES, CFO, ADMIN = ["g-i"], ["g-r"], ["g-f"], ["g-a"]
AUTHOR = "author@ftpua.com"
SETTINGS = {"approval_mode": "sequential", "dept_head_mode": "view", "pending_cp_payment": "block",
            "cfo_mode": "approve", "cp_onec_owner": "accountant"}

def settings(**kw):
    login("admin@ftpua.com", ADMIN)
    return c.post("/admin/settings", data={**SETTINGS, **kw}, follow_redirects=True).get_data(as_text=True)

def submit(**kw):
    login(AUTHOR, INIT)
    c.post("/requests/new", data={**BASE, **kw, "action": "submit"})
    return db.last_id()

# демо-заявки на етапі фіндиректора — до оплати, щоб черга фіндиректора була порожня
for r in db.list_all():
    if r["status"] in ("cfo", "approval"):
        r["status"] = "to_pay"
        db.save_request(r)

# ---------- адмінка
login("admin@ftpua.com", ADMIN)
page = c.get("/admin/users").get_data(as_text=True)
check("admin: cfo block + cp owner block", "Лише спостерігає" in page and "Хто заводить нового контрагента в 1С" in page)

# заявка, відправлена ДО перемикання (фіндиректор погоджує)
old = submit()
check("approve mode: cfo_step stored", db.get_request(old)["cfo_step"] is True)

html = settings(cfo_mode="observe")
check("observe saved + flash", app_settings.get("cfo_mode") == "observe" and "фіндиректор спостерігає" in html)
# радіокнопки порядку неактивні й не надсилаються — режим лишається попереднім
html = settings(cfo_mode="observe", approval_mode="")
check("disabled approval_mode not posted -> keeps value", app_settings.get("approval_mode") == "sequential")
check("admin page: approval order disabled", 'id="cfo-off-note" ' in c.get("/admin/users").get_data(as_text=True)
      and "disabled" in c.get("/admin/users").get_data(as_text=True).split('name="approval_mode"')[1][:120])

# ---------- маршрут без фіндиректора
flush()
rid = submit()
req = db.get_request(rid)
mails = flush()
check("observe: cfo_step False, at accountant", req["cfo_step"] is False and req["status"] == "accountant")
check("observe: no task mail to cfo on submit", not any(x["to"] == "cfo@ftpua.com" for x in mails))
login(AUTHOR, INIT)
page = c.get(f"/requests/{rid}").get_data(as_text=True)
route = page.split('<ol class="route')[1].split("</ol>")[0]
check("route bar without Фіндиректор", "Фіндиректор" not in route and "Оплата" in route)
login("res@ftpua.com", RES)
c.post(f"/requests/{rid}/action", data={"action": "approve", "payment_account_id": "1"})
req = db.get_request(rid)
mails = flush()
check("accountant approve -> to_pay", req["status"] == "to_pay" and req["history"][-1]["to_status"] == "to_pay")
check("no mail to cfo after accountant approve", not any(x["to"] == "cfo@ftpua.com" for x in mails))

# паралельний режим + спостерігач = лише бухгалтер
app_settings.set("approval_mode", "parallel", "t")
rid2 = submit()
check("parallel + observe -> sequential, accountant only", db.get_request(rid2)["route_mode"] == "sequential"
      and db.get_request(rid2)["status"] == "accountant")
app_settings.set("approval_mode", "sequential", "t")

# ---------- фіндиректор: що бачить
login("cfo@ftpua.com", CFO)
nav = c.get("/to-pay").get_data(as_text=True)
check("cfo: 'На погодження' (oversight) + 'Усі заявки'", "На погодження" in nav.split("</nav>")[0] and "/admin/requests" in nav)
r = c.get("/admin/requests")
check("cfo: all requests 200 + lists request", r.status_code == 200 and db.get_request(rid2)["number"] in r.get_data(as_text=True))
check("cfo: admin pages still forbidden", c.get("/admin/users").status_code == 403)
page = c.get(f"/requests/{rid2}").get_data(as_text=True)
check("cfo: no approve, only intervention", "Втручання фіндиректора" in page and 'value="approve"' not in page)
check("cfo home -> all requests", c.get("/").headers.get("Location", "").endswith("/admin/requests"))

# ---------- старa заявка з кроком фіндиректора
login("res@ftpua.com", RES)
c.post(f"/requests/{old}/action", data={"action": "approve", "payment_account_id": "1"})
check("old request still goes to cfo", db.get_request(old)["status"] == "cfo")
login("cfo@ftpua.com", CFO)
nav = c.get("/to-pay").get_data(as_text=True)
check("cfo has queue while old request waits", "На погодження" in nav.split("</nav>")[0])
c.post(f"/requests/{old}/action", data={"action": "approve"})
check("cfo approves old -> to_pay", db.get_request(old)["status"] == "to_pay")

# ---------- назад: погоджує
settings(cfo_mode="approve")
rid3 = submit()
login("res@ftpua.com", RES)
c.post(f"/requests/{rid3}/action", data={"action": "approve", "payment_account_id": "1"})
check("approve mode again -> cfo", db.get_request(rid3)["status"] == "cfo")

# ---------- адмін ставить статус «Фіндиректор» для заявки без кроку
login("admin@ftpua.com", ADMIN)
c.post(f"/requests/{rid2}/admin-status", data={"status": "cfo", "comment": "потрібен фіндиректор"})
check("admin -> cfo sets cfo_step", db.get_request(rid2)["cfo_step"] is True)

# ---------- хто заводить контрагентів: адміністратор
html = settings(cp_onec_owner="admin")
check("cp owner admin saved", app_settings.get("cp_onec_owner") == "admin" and "адміністратор" in html)
login(AUTHOR, INIT)
new = c.post("/counterparties/new", json={"resident": "1", "edrpou": "44556677", "name": "ТОВ «Новий партнер»"}).get_json()["item"]
flush()
rid4 = submit(counterparty_id=new["id"])
mails = flush()
check("admins notified (group + ADMIN_EMAILS)", {"admin@ftpua.com", "boss@ftpua.com"} <= {x["to"] for x in mails if "завести в 1С" in x["subject"]})
login("res@ftpua.com", RES)
page = c.get(f"/requests/{rid4}").get_data(as_text=True)
check("accountant: no link block, sees note", 'class="card cp-link"' not in page and "заводить і прив'язує адміністратор" in page.replace("&#39;", "'"))
check("accountant: check -> 403", c.post(f"/counterparties/{new['id']}/check").status_code == 403)
check("accountant: link -> 403", c.post(f"/counterparties/{new['id']}/link", data={"onec_id": "6"}).status_code == 403)
login("admin@ftpua.com", ADMIN)
page = c.get(f"/requests/{rid4}").get_data(as_text=True)
check("admin: link block", 'class="card cp-link"' in page)
adm = c.get("/admin/users").get_data(as_text=True)
check("admin settings: pending list links to request", f"/requests/{rid4}#cp-link" in adm)
c.post(f"/counterparties/{new['id']}/link", data={"onec_id": "6", "next": f"/requests/{rid4}"})
check("admin linked", db.get_request(rid4)["counterparty_id"] == "6")
check("prefs page: admin group", "Адміністрування" in c.get("/settings/notifications").get_data(as_text=True))

# ---------- бухгалтер (як раніше)
settings(cp_onec_owner="accountant")
login(AUTHOR, INIT)
new2 = c.post("/counterparties/new", json={"resident": "0", "edrpou": "", "name": "Another Partner GmbH"}).get_json()
new2 = (new2.get("item") or new2.get("duplicate"))
flush()
rid5 = submit(counterparty_id=new2["id"])
mails = flush()
check("accountant mode: no admin mail", not any("завести в 1С" in x["subject"] for x in mails))
login("res@ftpua.com", RES)
page = c.get(f"/requests/{rid5}").get_data(as_text=True)
check("accountant mode: accountant sees link block", 'class="card cp-link"' in page)

print("ALL OK" if ok else "SOME FAILED")
