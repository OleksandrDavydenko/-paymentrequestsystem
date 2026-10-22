import os, sys, tempfile
from datetime import timedelta
sys.path.insert(0, os.getcwd())
tmp = tempfile.mkdtemp()
os.environ.update(UPLOAD_DIR=tmp, APP_SETTINGS_FILE=os.path.join(tmp, "s.json"), USER_PREFS_FILE=os.path.join(tmp, "p.json"),
                  USER_DIRECTORY_FILE=os.path.join(tmp, "d.json"), ORG_STRUCTURE_FILE=os.path.join(tmp, "o.json"),
                  COUNTERPARTIES_PENDING_FILE=os.path.join(tmp, "cp.json"), TELEGRAM_LINKED_FILE=os.path.join(tmp, "tg.json"),
                  GROUP_ADMIN="g-a", GROUP_INITIATOR="g-i", GROUP_ACC_RESIDENT="g-r", GROUP_CFO="g-f", ADMIN_EMAILS="")
os.environ.pop("TELEGRAM_BOT_KEY", None)
import app as m
import user_budget; user_budget.get = lambda oid: {"direct": True, "all_departments": True, "departments": []}
import app_settings, mailer, notifications, telegram_notify, user_prefs, clock
import storage as db

ok = True
def check(label, cond):
    global ok
    ok &= bool(cond); print(("PASS " if cond else "FAIL ") + label)

sent = []
mailer.transport = lambda cfg, to, subject, html: sent.append((to, subject))
notifications.role_members = lambda role: [{"email": "res@ftpua.com", "name": "Res"}] if role == "acc_resident" else []
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
H = {"X-Api-Key": "s3cret-key"}

# ---------- вимкнено
check("disabled: api 404", c.get("/api/telegram/outbox", headers=H).status_code == 404)
login("res@ftpua.com", ["g-r"])
check("disabled: no channel choice", 'name="channel"' not in c.get("/settings/notifications").get_data(as_text=True))
check("health: telegram off", c.get("/health").get_json()["telegram"] == "off")

os.environ["TELEGRAM_BOT_KEY"] = "s3cret-key"
check("wrong key 403", c.get("/api/telegram/outbox", headers={"X-Api-Key": "nope"}).status_code == 403
      and c.get("/api/telegram/outbox").status_code == 403)
check("empty outbox", c.get("/api/telegram/outbox", headers=H).get_json() == {"messages": []})

# ---------- вибір каналу
page = c.get("/settings/notifications").get_data(as_text=True)
check("settings: channel choice, linked unknown", 'name="channel"' in page and "ще не передав список" in page)
c.post("/settings/notifications", data={"enabled": "1", "events": ["task_new"], "channel": "telegram"})
check("channel saved", user_prefs.channel("res@ftpua.com") == "telegram")

def submit():
    login("author@ftpua.com", ["g-i"])
    c.post("/requests/new", data={"pay_date": "2026-10-20", "organization_id": "1", "counterparty_id": "1",
                                  "expense_code": "01.001", "currency": "UAH", "amount": "1234.5", "payment_form": "bank",
                                  "action": "submit"})
    return db.last_id()

flush()
rid = submit()
mails = flush()
check("telegram-only: no email to accountant", not any(to == "res@ftpua.com" for to, _ in mails))
msgs = c.get("/api/telegram/outbox", headers=H).get_json()["messages"]
check("outbox has message with link", len(msgs) == 1 and msgs[0]["email"] == "res@ftpua.com"
      and f"/requests/{rid}" in msgs[0]["url"] and "<b>Заявка № " in msgs[0]["text"] and "1 234,50 UAH" in msgs[0]["text"])
check("taken -> not given again", c.get("/api/telegram/outbox", headers=H).get_json()["messages"] == [])
# не підтвердили 5 хв — віддається знову
real_now = clock.now
clock.now = lambda: real_now() + timedelta(minutes=6)
again = c.get("/api/telegram/outbox", headers=H).get_json()["messages"]
clock.now = real_now
check("stale taken -> given again", [x["id"] for x in again] == [msgs[0]["id"]])
r = c.post("/api/telegram/ack", headers=H, json={"results": [{"id": msgs[0]["id"], "status": "sent"}]})
check("ack sent", r.get_json()["accepted"] == 1 and any("Telegram → res@ftpua.com" == e["to"] for e in mailer.LOG))
check("ack twice ignored", c.post("/api/telegram/ack", headers=H, json={"results": [{"id": msgs[0]["id"], "status": "sent"}]}).get_json()["accepted"] == 0)

# ---------- не знайдено в боті -> запасний лист
login("res@ftpua.com", ["g-r"])
c.post(f"/requests/{rid}/comment", data={"comment": "x"})  # подія для автора (канал email) — не заважає
login("author@ftpua.com", ["g-i"])
rid2 = submit()
flush()
m2 = c.get("/api/telegram/outbox", headers=H).get_json()["messages"]
res_msg = next(x for x in m2 if x["email"] == "res@ftpua.com")
c.post("/api/telegram/ack", headers=H, json={"results": [{"id": res_msg["id"], "status": "not_found"}]})
mails = flush()
check("not_found -> fallback email", any(to == "res@ftpua.com" and "чекає" in subj for to, subj in mails))

# ---------- both
user_prefs.set_channel("res@ftpua.com", "both")
rid3 = submit()
mails = flush()
m3 = [x for x in c.get("/api/telegram/outbox", headers=H).get_json()["messages"] if x["email"] == "res@ftpua.com"]
check("both: email + telegram", any(to == "res@ftpua.com" for to, _ in mails) and len(m3) == 1)
c.post("/api/telegram/ack", headers=H, json={"results": [{"id": m3[0]["id"], "status": "blocked"}]})
check("both + blocked: no extra fallback email", not any(to == "res@ftpua.com" for to, _ in flush()))

# ---------- список від бота
r = c.post("/api/telegram/linked", headers=H, json={"emails": ["RES@ftpua.com", "iz@ftpua.com", "bad"]})
check("linked stored", r.get_json()["linked"] == 2 and telegram_notify.is_linked("res@ftpua.com") is True
      and telegram_notify.is_linked("nobody@ftpua.com") is False)
login("res@ftpua.com", ["g-r"])
check("settings: found in bot", "Вас знайдено" in c.get("/settings/notifications").get_data(as_text=True))
# людини немає в боті + «лише Telegram» -> одразу листом, у чергу не ставимо
user_prefs.set_channel("res@ftpua.com", "telegram")
c.post("/api/telegram/linked", headers=H, json={"emails": ["iz@ftpua.com"]})
submit()
mails = flush()
check("known not linked: straight to email", any(to == "res@ftpua.com" for to, _ in mails)
      and not [x for x in c.get("/api/telegram/outbox", headers=H).get_json()["messages"] if x["email"] == "res@ftpua.com"])

# ---------- тестовий режим
app_settings.set("data_mode", "test", "t")
import db_sql
was = app_settings.get("mail_enabled")

def muted_with_db():
    real = db_sql.configured
    db_sql.configured = lambda: True  # тестовий режим діє лише з базою — імітуємо
    try:
        return notifications.muted_by_test_mode()
    finally:
        db_sql.configured = real

app_settings.set("mail_enabled", False, "t")  # перехід у тестовий режим знімає «Розсилка увімкнена»
check("test mode muted", muted_with_db())
app_settings.set("mail_enabled", True, "t")
check("test mode + mailing ticked: not muted ([ТЕСТ])", not muted_with_db())
app_settings.set("data_mode", "work", "t")
app_settings.set("mail_enabled", was, "t")

# ---------- адмінка
login("admin@ftpua.com", ["g-a"])
page = admin_page()
check("admin: telegram block", "Telegram-бот" in page and "Підключено" in page)
c.post("/admin/telegram/test")
m4 = [x for x in c.get("/api/telegram/outbox", headers=H).get_json()["messages"] if x["email"] == "admin@ftpua.com"]
check("admin test message queued", len(m4) == 1 and "Тестове" in m4[0]["text"])
h = c.get("/health").get_json()
check("health: telegram on + last poll", h["telegram"] == "on" and h["telegram_last_poll"])

print("ALL OK" if ok else "SOME FAILED")
