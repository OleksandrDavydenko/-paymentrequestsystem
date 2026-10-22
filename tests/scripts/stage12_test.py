import os, sys, tempfile
sys.path.insert(0, os.getcwd())
tmp = tempfile.mkdtemp()
os.environ.update(UPLOAD_DIR=tmp, APP_SETTINGS_FILE=os.path.join(tmp, "s.json"), USER_PREFS_FILE=os.path.join(tmp, "p.json"),
                  GROUP_ADMIN="g-a", GROUP_INITIATOR="g-i", GROUP_ACC_RESIDENT="g-r", GROUP_ACC_CASH="g-c",
                  GROUP_CFO="g-f", ADMIN_EMAILS="")
import app as m
import user_budget; user_budget.get = lambda oid: {"direct": True, "all_departments": True, "departments": []}
import mailer, notifications, user_prefs, app_settings
import storage as db

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
    mailer.wait(); out = list(sent); sent.clear(); return out
to = lambda msgs: sorted(x["to"] for x in msgs)
BASE = {"pay_date": "2026-10-20", "counterparty_id": "1", "organization_id": "1", "expense_code": "01.001",
        "currency": "UAH", "amount": "500", "payment_form": "bank", "action": "submit"}

# ---- вимкнено за замовчуванням
login("i@ftpua.com", ["g-i"])
c.post("/requests/new", data=BASE); rid = db.last_id()
check("mail disabled by default -> nothing sent", flush() == [])

# ---- адмін налаштовує
check("non-admin /admin/mail -> 403", c.post("/admin/mail", data={"mail_enabled": "1"}).status_code == 403)
login("od@ftpua.com", ["g-a"])
c.post("/admin/mail", data={"mail_enabled": "1", "mail_sender": "", "smtp_host": "smtp.gmail.com", "smtp_port": "587"})
check("cannot enable without sender", app_settings.get("mail_enabled") is False)
c.post("/admin/mail", data={"mail_enabled": "1", "mail_sender": "bad", "smtp_host": "smtp.gmail.com", "smtp_port": "587"})
check("invalid sender rejected", app_settings.get("mail_sender") == "")
SMTP = {"smtp_host": "smtp.gmail.com", "smtp_port": "587", "smtp_security": "starttls", "smtp_username": ""}
c.post("/admin/mail", data={"mail_enabled": "1", "mail_sender": "noreply@ftpua.com", "mail_sender_name": "PRS",
                            "smtp_password": "abcd efgh ijkl mnop", **SMTP})
import json
raw = json.load(open(os.environ["APP_SETTINGS_FILE"], encoding="utf-8"))["smtp_password"]["value"]
check("password stored encrypted", raw and "abcd" not in raw)
check("password decrypts in config", notifications.smtp_config()["password"] == "abcd efgh ijkl mnop"
      and notifications.smtp_config()["username"] == "noreply@ftpua.com")
page = admin_page()
check("password never rendered", "abcd efgh" not in page and "Збережено зашифрованим" in page)
c.post("/admin/mail", data={"mail_enabled": "1", "mail_sender": "noreply@ftpua.com", "mail_sender_name": "PRS", **SMTP})
check("empty password field keeps saved", notifications.smtp_config()["password"] == "abcd efgh ijkl mnop")
c.post("/admin/mail", data={"mail_enabled": "1", "mail_sender": "noreply@ftpua.com", **{**SMTP, "smtp_port": "abc"}})
check("bad port rejected", app_settings.get("smtp_port") == 587)
check("enabled with sender", app_settings.get("mail_enabled") is True and app_settings.get("mail_sender") == "noreply@ftpua.com")
r = c.post("/admin/mail/test", follow_redirects=True)
m1 = flush()
check("test mail to admin from sender", len(m1) == 1 and m1[0]["to"] == "od@ftpua.com" and m1[0]["from"] == "noreply@ftpua.com"
      and "надіслано" in r.get_data(as_text=True))
fail["on"] = True
r = c.post("/admin/mail/test", follow_redirects=True)
check("test mail error shown", "не надіслано" in r.get_data(as_text=True) and "535" in r.get_data(as_text=True))
fail["on"] = False
check("admin page shows mail block + log", "Email-сповіщення" in admin_page())

# ---- послідовно: submit -> бухгалтер
login("i@ftpua.com", ["g-i"])
c.post("/requests/new", data=BASE); rid = db.last_id()
msgs = flush()
check("submit -> resident accountant notified", to(msgs) == ["res@ftpua.com"] and "чекає вашого рішення" in msgs[0]["subject"])
check("mail has number + link + amount", db.get_request(rid)["number"] in msgs[0]["html"] and f"/requests/{rid}" in msgs[0]["html"] and "500,00" in msgs[0]["html"])

login("res@ftpua.com", ["g-r"])
c.post(f"/requests/{rid}/action", data={"action": "approve"})
msgs = flush()
check("approve -> author + cfo group", to(msgs) == ["cfo2@ftpua.com", "cfo@ftpua.com", "i@ftpua.com"])
check("author subject 'погоджено'", any(x["to"] == "i@ftpua.com" and "погоджено" in x["subject"] for x in msgs))

login("cfo@ftpua.com", ["g-f"])
c.post(f"/requests/{rid}/comment", data={"comment": "<script>alert(1)</script> Уточніть суму"})
msgs = flush()
check("comment -> author only (participant pref off by default, not to self)", to(msgs) == ["i@ftpua.com"])
check("comment escaped", "<script>" not in msgs[0]["html"] and "&lt;script&gt;" in msgs[0]["html"])

c.post(f"/requests/{rid}/action", data={"action": "approve"})
msgs = flush()
check("cfo approve -> author + accountant for payment", to(msgs) == ["i@ftpua.com", "res@ftpua.com"]
      and any("чекає оплати" in x["subject"] for x in msgs))

# ---- налаштування користувача
login("i@ftpua.com", ["g-i"])
t = c.get("/settings/notifications").get_data(as_text=True)
check("settings page shows email + author events only", "i@ftpua.com" in t and "Мою заявку оплачено" in t and "Нова заявка чекає" not in t)
c.post("/settings/notifications", data={"enabled": "1", "events": ["my_rework", "my_comment"]})
check("prefs saved", user_prefs.wants("i@ftpua.com", "my_comment") and not user_prefs.wants("i@ftpua.com", "my_paid"))
login("res@ftpua.com", ["g-r"])
c.post(f"/to-pay/pay", data={"ids": [str(rid)]})
check("paid but author disabled my_paid -> no mail", flush() == [])

# Учасник-погоджувач вмикає коментарі
c.post("/settings/notifications", data={"enabled": "1", "events": ["task_new", "comment_participant"]})
login("i@ftpua.com", ["g-i"])
c.post(f"/requests/{rid}/comment", data={"comment": "Дякую"})
check("participant gets comment when opted in (cfo did not opt in)", to(flush()) == ["res@ftpua.com"])

# Вимкнути все
c.post("/settings/notifications", data={})
check("master off keeps previous events", user_prefs.get("i@ftpua.com")["enabled"] is False
      and user_prefs.get("i@ftpua.com")["events"]["my_comment"] is True)
login("cfo@ftpua.com", ["g-f"])
c.post(f"/requests/{rid}/comment", data={"comment": "ще питання"})
check("author with mail off gets nothing", "i@ftpua.com" not in to(flush()))

# ---- паралельно
login("od@ftpua.com", ["g-a"])
c.post("/admin/settings", data={"approval_mode": "parallel"})
login("i2@ftpua.com", ["g-i"])
c.post("/requests/new", data={**BASE, "payment_form": "cash", "cash_receiver": "contractor"}); pid = db.last_id()
check("parallel submit -> cashier + both cfo", to(flush()) == ["cash@ftpua.com", "cfo2@ftpua.com", "cfo@ftpua.com"])
login("cash@ftpua.com", ["g-c"])
c.post(f"/requests/{pid}/action", data={"action": "approve"})
check("partial parallel approve -> only author, no new task mails", to(flush()) == ["i2@ftpua.com"])
login("cfo@ftpua.com", ["g-f"])
c.post(f"/requests/{pid}/action", data={"action": "rework", "comment": "Додайте акт"})
msgs = flush()
check("rework -> author with comment", to(msgs) == ["i2@ftpua.com"] and "Додайте акт" in msgs[0]["html"])

# admin status change
login("od@ftpua.com", ["g-a"])
c.post(f"/requests/{pid}/admin-status", data={"status": "cfo", "comment": "виправлення"})
check("admin status -> author + cfo group", to(flush()) == ["cfo2@ftpua.com", "cfo@ftpua.com", "i2@ftpua.com"])

# Розсилку вимкнено адміном
c.post("/admin/mail", data={"mail_sender": "noreply@ftpua.com", **SMTP})
login("i2@ftpua.com", ["g-i"])
c.post(f"/requests/{pid}/comment", data={"comment": "x"})
check("admin disabled -> nothing", flush() == [])
check("settings page warns when disabled", "вимкнув адміністратор" in c.get("/settings/notifications").get_data(as_text=True))

# Помилка Graph не ламає дію
login("od@ftpua.com", ["g-a"]); c.post("/admin/mail", data={"mail_enabled": "1", "mail_sender": "noreply@ftpua.com", **SMTP})
fail["on"] = True
login("cfo@ftpua.com", ["g-f"])
r = c.post(f"/requests/{pid}/comment", data={"comment": "y"})
mailer.wait()
check("graph failure: action ok, error logged", r.status_code == 302 and db.get_request(pid)["history"][-1]["comment"] == "y"
      and not mailer.LOG[0]["ok"])
login("od@ftpua.com", ["g-a"]); c.post("/admin/mail", data={"mail_enabled": "1", "mail_sender": "noreply@ftpua.com", "smtp_password_clear": "1", **SMTP})
check("password can be cleared", notifications.smtp_config()["password"] is None)
# Реальний SMTP-клієнт: помилка з'єднання пояснюється зрозуміло
err = None
try:
    mailer.send_now({"host": "127.0.0.1", "port": 1, "security": "starttls", "sender": "a@b.c"}, "x@y.z", "t", "<b>t</b>")
except mailer.MailError as e:
    err = str(e)
check("connection error explained", err and "Не вдалося з'єднатися" in err)
print("ALL OK" if ok else "SOME FAILED")
# --- показ/копіювання пароля
login("od@ftpua.com", ["g-a"])
c.post("/admin/mail", data={"mail_enabled": "1", "mail_sender": "noreply@ftpua.com", "smtp_password": "qwer tyui opas dfgh", **SMTP})
page = admin_page()
check("password not embedded in page", "qwer tyui" not in page and 'id="pass-show"' in page)
r = c.post("/admin/mail/password")
check("reveal endpoint returns password, no-store", r.json["password"] == "qwer tyui opas dfgh" and r.headers["Cache-Control"] == "no-store")
login("i@ftpua.com", ["g-i"])
check("non-admin cannot reveal", c.post("/admin/mail/password").status_code == 403)
e = mailer._explain(__import__("smtplib").SMTPSenderRefused(530, b"5.7.0 Authentication Required", "a@b.c"), "smtp.gmail.com", False)
check("530 explained as missing password", "пароль не задано" in e)
print("ALL OK" if ok else "SOME FAILED")
