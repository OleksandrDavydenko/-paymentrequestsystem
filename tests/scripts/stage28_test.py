import os, sys, tempfile
sys.path.insert(0, os.getcwd())
tmp = tempfile.mkdtemp()
os.environ.update(UPLOAD_DIR=tmp, APP_SETTINGS_FILE=os.path.join(tmp, "s.json"), USER_PREFS_FILE=os.path.join(tmp, "p.json"),
                  USER_DIRECTORY_FILE=os.path.join(tmp, "d.json"), ORG_STRUCTURE_FILE=os.path.join(tmp, "o.json"),
                  COUNTERPARTIES_PENDING_FILE=os.path.join(tmp, "cp.json"), TELEGRAM_LINKED_FILE=os.path.join(tmp, "tg.json"),
                  GROUP_ADMIN="g-a", GROUP_INITIATOR="g-i", GROUP_ACC_RESIDENT="g-r", GROUP_CFO="g-f", ADMIN_EMAILS="",
                  TELEGRAM_BOT_KEY="k")
import app as m
import user_budget; user_budget.get = lambda oid: {"direct": True, "all_departments": True, "departments": []}
import app_settings, mailer, notifications, telegram_notify, user_prefs, graph
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
MEMBERS = {"g-r": ["res@ftpua.com"], "g-a": ["admin@ftpua.com"]}
graph.group_members = lambda t, gid: [{"id": u, "name": u.split("@")[0].title(), "email": u} for u in MEMBERS.get(gid, [])]
graph.get_user = lambda t, uid: {"id": uid, "name": uid.split("@")[0].title(), "email": uid}
graph.add_member = graph.remove_member = lambda *a, **k: None
c = m.app.test_client()
H = {"X-Api-Key": "k"}
def flush():
    mailer.wait(); out = list(sent); sent.clear(); return out
def outbox():
    return c.get("/api/telegram/outbox", headers=H).get_json()["messages"]
def submit():
    login("author@ftpua.com", ["g-i"])
    c.post("/requests/new", data={"pay_date": "2026-10-20", "organization_id": "1", "counterparty_id": "1", "expense_code": "01.001",
                                  "currency": "UAH", "amount": "100", "payment_form": "bank", "action": "submit"})

# ---------- за замовчуванням «Пошта і Telegram»
check("default channel both", user_prefs.channel("res@ftpua.com") == "both")
login("res@ftpua.com", ["g-r"])
check("settings: both preselected", 'value="both" checked' in c.get("/settings/notifications").get_data(as_text=True))
user_prefs.set_channel("email-only@ftpua.com", "email")
check("explicit email kept", user_prefs.channel("email-only@ftpua.com") == "email")
flush()
submit()
mails = flush()
q = [x for x in outbox() if x["email"] == "res@ftpua.com"]
check("default: email + telegram (bot list not received yet)", any(to == "res@ftpua.com" for to, _ in mails) and len(q) == 1)

# ---------- ручна пошта для бота
login("admin@ftpua.com", ["g-a"])
page = c.get("/admin/users/res@ftpua.com").get_data(as_text=True)
check("card: telegram section", 'id="uc-telegram"' in page and 'name="telegram_email"' in page and "автоматично" in page)
c.post("/admin/users/res@ftpua.com", data={"roles": ["acc_resident"], "telegram_email": "не пошта"}, follow_redirects=True)
check("bad email rejected", user_prefs.get("res@ftpua.com")["telegram_email"] == "")
c.post("/admin/users/res@ftpua.com", data={"roles": ["acc_resident"], "telegram_email": "Res.Personal@Gmail.com"})
check("manual email saved (lowercase)", user_prefs.telegram_lookup("res@ftpua.com") == "res.personal@gmail.com")
submit()
flush()
q = [x for x in outbox() if x["email"] == "res.personal@gmail.com"]
check("bot gets manual email", len(q) == 1)
c.post("/api/telegram/ack", headers=H, json={"results": [{"id": q[0]["id"], "status": "not_found"}]})
check("both channel: no extra fallback", not any(to == "res@ftpua.com" for to, _ in flush()))
user_prefs.set_channel("res@ftpua.com", "telegram")
submit()
flush()
q = [x for x in outbox() if x["email"] == "res.personal@gmail.com"]
c.post("/api/telegram/ack", headers=H, json={"results": [{"id": q[0]["id"], "status": "not_found"}]})
check("telegram-only + not found: fallback mail to WORK address", any(to == "res@ftpua.com" for to, _ in flush()))

# ---------- статус «знайдено» за ручною поштою
c.post("/api/telegram/linked", headers=H, json={"emails": ["res.personal@gmail.com"]})
login("admin@ftpua.com", ["g-a"])
page = c.get("/admin/users/res@ftpua.com").get_data(as_text=True)
check("card: found via manual email", "Знайдено в боті" in page and "вказаною вручну: res.personal@gmail.com" in page)
lst = c.get("/admin/users").get_data(as_text=True)
check("users list: telegram mark (manual)", "✈ Telegram (вручну: res.personal@gmail.com)" in lst)
login("res@ftpua.com", ["g-r"])
check("settings: found", "Вас знайдено" in c.get("/settings/notifications").get_data(as_text=True))

# ---------- очищення → автоматично
login("admin@ftpua.com", ["g-a"])
c.post("/admin/users/res@ftpua.com", data={"roles": ["acc_resident"], "telegram_email": ""})
check("cleared -> automatic", user_prefs.telegram_lookup("res@ftpua.com") == "res@ftpua.com")
page = c.get("/admin/users/res@ftpua.com").get_data(as_text=True)
check("card: not found automatically -> hint", "Бот не знає цю людину" in page and "робочу пошту res@ftpua.com" in page)
c.post("/admin/users/res@ftpua.com", data={"roles": ["acc_resident"], "telegram_email": "res@ftpua.com"})
check("manual == work email stored as automatic", user_prefs.get("res@ftpua.com")["telegram_email"] == "")

login("res@ftpua.com", ["g-r"])
check("non-admin cannot save card", c.post("/admin/users/res@ftpua.com", data={"telegram_email": "x@y.com"}).status_code == 403)
print("ALL OK" if ok else "SOME FAILED")
