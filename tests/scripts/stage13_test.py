import os, sys, tempfile
sys.path.insert(0, os.getcwd())
tmp = tempfile.mkdtemp()
os.environ.update(UPLOAD_DIR=tmp, APP_SETTINGS_FILE=os.path.join(tmp, "s.json"), USER_PREFS_FILE=os.path.join(tmp, "p.json"), USER_DIRECTORY_FILE=os.path.join(tmp, "d.json"),
                  GROUP_ADMIN="g-a", GROUP_INITIATOR="g-i", GROUP_ACC_RESIDENT="g-r", GROUP_ACC_CASH="g-c",
                  GROUP_CFO="g-f", ADMIN_EMAILS="")
import app as m
import user_budget; user_budget.get = lambda oid: {"direct": True, "all_departments": True, "departments": []}
import mailer, notifications, user_prefs, app_settings
import mock_data as db

ok = True
def check(label, cond):
    global ok
    ok &= bool(cond)
    print(("PASS " if cond else "FAIL ") + label)

sent = []
fail = {"on": False}
def fake_transport(cfg, to, subject, html):
    if fail["on"]:
        raise mailer.MailError("Сервер не прийняв логін або пароль (535)")
    sent.append({"from": cfg["sender"], "to": to, "subject": subject, "html": html, "cfg": cfg})
mailer.transport = fake_transport
MEMBERS = {"acc_resident": ["res@ftpua.com"], "acc_cash": ["cash@ftpua.com"], "cfo": ["cfo@ftpua.com", "cfo2@ftpua.com"]}
notifications.role_members = lambda role: [{"email": e, "name": e} for e in MEMBERS.get(role, [])]

claims = {}
def login(email, groups):
    claims.clear(); claims.update({"name": email.split("@")[0], "preferred_username": email, "oid": email, "groups": groups})
m.auth._auth.get_user = lambda: dict(claims)
m.auth._auth.get_token_for_user = lambda s: {"access_token": "t", "scope": " ".join(s)}
import graph; graph.group_members = lambda t, g: []
c = m.app.test_client()
def flush():
    mailer.wait(); out = list(sent); sent.clear(); return out
to = lambda msgs: sorted(x["to"] for x in msgs)
import re as _re
from datetime import timedelta
BASE = {"pay_date": "2026-10-20", "counterparty_id": "1", "organization_id": "1", "expense_code": "01.001",
        "currency": "UAH", "amount": "500", "payment_form": "bank", "action": "submit"}
ADDR = "/settings/notifications/address"

login("od@ftpua.com", ["g-a"])
SMTP = {"mail_enabled": "1", "mail_sender": "noreply@ftpua.com", "smtp_host": "smtp.gmail.com", "smtp_port": "587",
        "smtp_security": "starttls", "smtp_password": "x"}
c.post("/admin/mail", data=SMTP)
check("allow_custom_email off by default", app_settings.get("allow_custom_email") is False)
page = c.get("/admin/users").get_data(as_text=True)
check("admin sections collapsible + toggle all", page.count("data-collapsible") == 5 and "sec-expand-all" in page
      and "admin-sections.js" in page and "allow_custom_email" in page)

# --- вимкнено: блоку немає, POST відхиляється
login("i@ftpua.com", ["g-i"])
check("no address block when disabled", 'id="address"' not in c.get("/settings/notifications").get_data(as_text=True))
c.post(ADDR, data={"action": "save", "target": "other", "alt_email": "me@gmail.com"}); flush()
check("POST rejected when disabled", user_prefs.get("i@ftpua.com")["alt_email"] == "")

# --- адмін вмикає
login("od@ftpua.com", ["g-a"])
c.post("/admin/mail", data={**SMTP, "allow_custom_email": "1"})
check("admin enables allow_custom_email", app_settings.get("allow_custom_email") is True)

login("i@ftpua.com", ["g-i"])
check("address block shown", 'id="address"' in c.get("/settings/notifications").get_data(as_text=True))
c.post(ADDR, data={"action": "save", "target": "other", "alt_email": "bad"})
check("invalid address rejected", user_prefs.get("i@ftpua.com")["alt_email"] == "" and flush() == [])
r = c.post(ADDR, data={"action": "save", "target": "other", "alt_email": "Me@Gmail.com"}, follow_redirects=True)
msgs = flush()
check("confirmation sent to new address", to(msgs) == ["Me@Gmail.com"] and "Підтвердіть" in msgs[0]["subject"])
link = _re.search(r'href="[^"]*(/settings/notifications/confirm/[^"]+)"', msgs[0]["html"]).group(1)
check("pending state shown", "Очікує підтвердження" in r.get_data(as_text=True))
check("before confirm -> letters to work email", user_prefs.delivery_address("i@ftpua.com", True) == "i@ftpua.com")
c.post(ADDR, data={"action": "resend"})
check("resend throttled", flush() == [])

# чужий користувач не може підтвердити
login("x@ftpua.com", ["g-i"])
r = c.get(link, follow_redirects=True)
check("other user cannot confirm", "недійсне" in r.get_data(as_text=True) and not user_prefs.get("i@ftpua.com")["alt_verified"])
login("i@ftpua.com", ["g-i"])
check("wrong token rejected", "недійсне" in c.get("/settings/notifications/confirm/abc", follow_redirects=True).get_data(as_text=True))
r = c.get(link, follow_redirects=True)
check("owner confirms", "Адресу підтверджено" in r.get_data(as_text=True) and user_prefs.get("i@ftpua.com")["alt_verified"])
check("token single-use", "недійсне" in c.get(link, follow_redirects=True).get_data(as_text=True))
check("page shows delivery to alt", "Me@Gmail.com" in c.get("/settings/notifications").get_data(as_text=True)
      and "підтверджена інша адреса" in c.get("/settings/notifications").get_data(as_text=True))

# сповіщення автору йдуть на іншу адресу
c.post("/requests/new", data=BASE); rid = max(db._REQUESTS); flush()
login("res@ftpua.com", ["g-r"])
c.post(f"/requests/{rid}/action", data={"action": "rework", "comment": "уточніть"})
msgs = flush()
check("author letter goes only to alt", "Me@Gmail.com" in to(msgs) and "i@ftpua.com" not in to(msgs))
login("od@ftpua.com", ["g-a"])
graph.group_members = lambda t, g: [{"id": "oid-i", "name": "I", "email": "i@ftpua.com"}] if g == "g-i" else []
check("admin table shows alt address", "листи на: Me@Gmail.com" in c.get("/admin/users").get_data(as_text=True))

# адмін вимкнув -> знову робоча, адреса зберігається
c.post("/admin/mail", data=SMTP)
check("disabled -> work email", user_prefs.delivery_address("i@ftpua.com", app_settings.get("allow_custom_email")) == "i@ftpua.com"
      and user_prefs.get("i@ftpua.com")["alt_email"] == "Me@Gmail.com")
check("admin table hides alt when disabled", "листи на:" not in c.get("/admin/users").get_data(as_text=True))
c.post("/admin/mail", data={**SMTP, "allow_custom_email": "1"})

# зміна адреси скидає підтвердження; протермінований токен
login("i@ftpua.com", ["g-i"])
user_prefs._load()["i@ftpua.com"]["alt_sent_at"] = (m.clock.now() - timedelta(minutes=5)).isoformat(timespec="seconds")
c.post(ADDR, data={"action": "save", "target": "other", "alt_email": "new@gmail.com"})
msgs = flush()
check("change resets verification", not user_prefs.get("i@ftpua.com")["alt_verified"] and to(msgs) == ["new@gmail.com"]
      and user_prefs.delivery_address("i@ftpua.com", True) == "i@ftpua.com")
link2 = _re.search(r'href="[^"]*(/settings/notifications/confirm/[^"]+)"', msgs[0]["html"]).group(1)
user_prefs._load()["i@ftpua.com"]["alt_sent_at"] = (m.clock.now() - timedelta(hours=49)).isoformat(timespec="seconds")
check("expired token rejected", "недійсне" in c.get(link2, follow_redirects=True).get_data(as_text=True))

# скасувати
c.post(ADDR, data={"action": "clear"})
check("clear -> work email", user_prefs.get("i@ftpua.com")["alt_email"] == "" and user_prefs.delivery_address("i@ftpua.com", True) == "i@ftpua.com")
# prefs save keeps alt
c.post(ADDR, data={"action": "save", "target": "other", "alt_email": "keep@gmail.com"}); flush()
c.post("/settings/notifications", data={"enabled": "1", "events": ["my_paid"]})
check("saving events keeps alt address", user_prefs.get("i@ftpua.com")["alt_email"] == "keep@gmail.com")
c.post(ADDR, data={"action": "save", "target": "work"})
check("choose work -> cleared", user_prefs.get("i@ftpua.com")["alt_email"] == "")

# HTML-ін'єкція в адресі листа підтвердження
user_prefs._load()["i@ftpua.com"] = {}
c.post(ADDR, data={"action": "save", "target": "other", "alt_email": "a<b>@x.com"}); msgs = flush()
check("address escaped in mail", not msgs or "a<b>" not in msgs[0]["html"])
print("ALL OK" if ok else "SOME FAILED")
