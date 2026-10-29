"""Бухгалтер «Погоджує і оплачує» / «Лише оплачує» (картка користувача): «Лише оплачує» не бачить заявок на
погодженні (ні в меню, ні за посиланням), не отримує сповіщень про них, але отримує «До оплати», бачить і оплачує
заявки свого виду; попередження, коли в якогось виду бухгалтера ніхто не погоджує; мобільне меню «☰»."""
import os, re, subprocess, sys, tempfile
sys.path.insert(0, os.getcwd())
tmp = tempfile.mkdtemp()
os.environ.update(UPLOAD_DIR=tmp, ADMIN_EMAILS="adm@ftpua.com")
import app as m
import user_budget; user_budget.get = lambda oid: {"direct": True, "all_departments": True, "departments": []}
import app_settings, mailer, notifications, notify_log, user_roles
import storage as db
import workflow as wf

ok = True
def check(label, cond):
    global ok
    ok &= bool(cond); print(("PASS " if cond else "FAIL ") + label)

sent = []
mailer.transport = lambda cfg, to, subject, html: sent.append(to)
def flush():
    mailer.wait(); out = sorted(set(sent)); sent.clear(); return out
for k, v in {"mail_enabled": True, "mail_sender": "noreply@ftpua.com", "smtp_host": "smtp.example.com",
             "cfo_mode": "observe"}.items():
    app_settings.set(k, v, "t")

AUTHOR, BOTH, PAY, PAYINI, ADMIN = "author@ftpua.com", "both@ftpua.com", "pay@ftpua.com", "payini@ftpua.com", "adm@ftpua.com"
for email, roles in ((AUTHOR, ["initiator"]), (BOTH, ["acc_cash"]), (PAY, ["acc_cash"]), (PAYINI, ["acc_cash", "initiator"])):
    user_roles.set_roles(email, email, email.split("@")[0].title(), roles, "t")
claims = {}
def login(email):
    claims.clear(); claims.update({"name": email.split("@")[0].title(), "preferred_username": email, "oid": email})
m.auth._auth.get_user = lambda: dict(claims)
c = m.app.test_client()
page = lambda url: c.get(url).get_data(as_text=True)
BASE = {"pay_date": "2026-12-20", "organization_id": "1", "counterparty_id": "1", "expense_code": "01.001",
        "currency": "UAH", "amount": "1500", "payment_form": "cash", "action": "submit"}

# ---------- адмінка: режим у картці, зберігається, не губиться при зміні ролей
login(ADMIN)
html = page(f"/admin/users/{BOTH}")
check("card: accountant mode block, «Погоджує і оплачує» by default", 'id="uc-acc-mode"' in html
      and re.search(r'value="both"\s+checked', html) and "Лише оплачує" in html)
check("card of a non-accountant: block hidden", re.search(r'id="uc-acc-mode"\s+hidden', page(f"/admin/users/{AUTHOR}")))
for email, roles in ((PAY, ["acc_cash"]), (PAYINI, ["acc_cash", "initiator"])):
    r = c.post(f"/admin/users/{email}", data={"roles": roles, "acc_mode": "pay"}, follow_redirects=True)
check("saved «Лише оплачує», shown in the summary", user_roles.acc_mode(PAY) == "pay" and "лише оплачує" in r.get_data(as_text=True))
user_roles.set_roles(PAY, PAY, "Pay", ["acc_cash"], "t")
check("mode kept when roles are rewritten", user_roles.acc_mode(PAY) == "pay")
check("members: pays_only flag", {x["email"]: x["pays_only"] for x in user_roles.members("acc_cash")}
      == {BOTH: False, PAY: True, PAYINI: True})
users = page("/admin/users")
check("users list: «лише оплачує» chip, no warning while someone approves", users.count("лише оплачує") >= 2
      and "немає жодного, хто погоджує" not in users)

# ---------- заявка на «Перевірка бухгалтера»
login(AUTHOR)
c.post("/requests/new", data=BASE)
rid = db.last_id()
check("submitted to the cash accountant", db.get_request(rid)["status"] == "accountant")
mails = flush()
check("approval notice: only «Погоджує і оплачує»", BOTH in mails and PAY not in mails and PAYINI not in mails)
check("notification log explains the skip", any(e["recipient"] == PAY and "лише оплачуєте" in (e.get("detail") or "")
                                                for e in notify_log.query(50)))
login(PAY)
check("pay-only: no «На погодження» in the menu", "На погодження" not in page("/to-pay"))
check("pay-only: request on approval — 404", c.get(f"/requests/{rid}").status_code == 404)
r = c.get("/approvals")
check("pay-only: /approvals -> «До оплати»", r.status_code == 302 and "/to-pay" in r.headers["Location"])
check("pay-only without initiator: home is «До оплати»", "/to-pay" in c.get("/").headers.get("Location", ""))
check("pay-only: cannot approve", c.post(f"/requests/{rid}/action", data={"action": "approve"}).status_code == 404
      and db.get_request(rid)["status"] == "accountant")
login(BOTH)
check("approving accountant: in the queue", f"/requests/{rid}" in page("/approvals"))
c.post(f"/requests/{rid}/action", data={"action": "approve", "payment_account_id": "5"})
check("approved -> «До оплати»", db.get_request(rid)["status"] == "to_pay")

# ---------- «До оплати»: сповіщення всім бухгалтерам виду, «Лише оплачує» бачить і оплачує
mails = flush()
check("to-pay notice: pay-only accountants too", PAY in mails and PAYINI in mails)
login(PAY)
check("pay-only: request in «До оплати», card opens", f"/requests/{rid}" in page("/to-pay")
      and c.get(f"/requests/{rid}").status_code == 200)
c.post(f"/requests/{rid}/action", data={"action": "pay", "comment": "РКО 1"})
check("pay-only: marked paid", db.get_request(rid)["status"] == "paid")
check("pay-only: paid request still visible (own kind)", c.get(f"/requests/{rid}").status_code == 200)

# ---------- «Лише оплачує» + ініціатор: свої заявки бачить, на погодженні — лише свої
login(PAYINI)
c.post("/requests/new", data=BASE)
own = db.last_id()
login(AUTHOR)
c.post("/requests/new", data=BASE)
other = db.last_id()
login(PAYINI)
check("pay-only initiator: own request visible in «Мої заявки»", f"/requests/{own}" in page("/requests")
      and c.get(f"/requests/{own}").status_code == 200)
check("pay-only initiator: someone else's request on approval — 404", c.get(f"/requests/{other}").status_code == 404)
check("pay-only initiator: home is «Мої заявки»", "/to-pay" not in c.get("/").headers.get("Location", "/to-pay"))

# ---------- усі по готівці «Лише оплачує» — збереження проходить, попередження в картці й у списку
login(ADMIN)
r = c.post(f"/admin/users/{BOTH}", data={"roles": ["acc_cash"], "acc_mode": "pay"}, follow_redirects=True)
html = r.get_data(as_text=True)
check("all cash accountants pay-only: saved, warning in the card", user_roles.acc_mode(BOTH) == "pay"
      and "немає жодного, хто погоджує" in html and user_roles.accountants_without_approver() == ["acc_cash"])
check("warning in the users list", "немає жодного, хто погоджує" in page("/admin/users"))
c.post(f"/admin/users/{BOTH}", data={"roles": ["acc_cash"], "acc_mode": "both"})
check("back to «Погоджує і оплачує»: no warning", not user_roles.accountants_without_approver())

# ---------- мобільне меню «☰»
html = page("/admin/users")
check("menu toggle with aria-controls", 'class="nav-toggle"' in html and 'aria-controls="main-nav"' in html
      and 'id="main-nav"' in html)
bad = []
for i, js in enumerate(re.findall(r"<script(?![^>]*\bsrc=)(?![^>]*application/json)[^>]*>(.*?)</script>", html, re.S)):
    f = os.path.join(tmp, f"s{i}.js")
    with open(f, "w", encoding="utf-8") as fh:
        fh.write(js)
    try:
        r = subprocess.run(["node", "--check", f], capture_output=True, text=True)
    except FileNotFoundError:
        break  # без Node.js синтаксис не перевіряємо
    if r.returncode:
        bad.append(r.stderr[:300])
check("inline scripts are valid JS", not bad)
print("ALL OK" if ok else "SOME FAILED")
