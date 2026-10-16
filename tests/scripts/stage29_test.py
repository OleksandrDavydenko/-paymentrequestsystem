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
import app_settings, mailer, notifications, notify_log, user_prefs, graph
import storage as db

ok = True
def check(label, cond):
    global ok
    ok &= bool(cond); print(("PASS " if cond else "FAIL ") + label)

mailer.transport = lambda cfg, to, subject, html: None
ROLE = {"acc_resident": [], "cfo": ["cfo@ftpua.com"]}   # у групі бухгалтерів резидентів — нікого
notifications.role_members = lambda role: [{"email": e, "name": e} for e in ROLE.get(role, [])]
for k, v in {"mail_enabled": True, "mail_sender": "noreply@ftpua.com"}.items():
    app_settings.set(k, v, "t")
app_settings.set("approval_mode", "parallel", "t")
claims = {}
def login(email, groups):
    claims.clear(); claims.update({"name": email.split("@")[0].title(), "preferred_username": email, "oid": email, "groups": groups})
m.auth._auth.get_user = lambda: dict(claims)
m.auth._auth.get_token_for_user = lambda s: {"access_token": "t", "scope": " ".join(s)}
graph.group_members = lambda t, gid: []
c = m.app.test_client()
H = {"X-Api-Key": "k"}

login("author@ftpua.com", ["g-i"])
c.post("/requests/new", data={"pay_date": "2026-10-20", "organization_id": "1", "counterparty_id": "1", "expense_code": "01.001",
                              "currency": "UAH", "amount": "100", "payment_form": "bank", "action": "submit"})
rid = db.last_id()
mailer.wait()
rows = notify_log.query(request_id=rid)
by = {(r["channel"], r["recipient"]): r for r in rows}
check("cfo got email (parallel)", by.get(("email", "cfo@ftpua.com"), {}).get("status") == "sent")
check("cfo telegram queued", by.get(("telegram", "cfo@ftpua.com"), {}).get("status") == "queued")
check("empty accountant group logged", any(r["status"] == "skipped" and "Бухгалтер (безготівка, резидент)" in r["recipient"] for r in rows))
check("rows carry request number + event", all(r["request_number"] == db.get_request(rid)["number"] and r["event"] == "submit" for r in rows))

# telegram ack оновлює запис
msg = c.get("/api/telegram/outbox", headers=H).get_json()["messages"][0]
c.post("/api/telegram/ack", headers=H, json={"results": [{"id": msg["id"], "status": "sent"}]})
check("telegram ack -> sent in log", next(r for r in notify_log.query(request_id=rid) if r["channel"] == "telegram")["status"] == "sent")

# власна дія й вимкнена подія
user_prefs.save("author@ftpua.com", True, {**user_prefs.get("author@ftpua.com")["events"], "my_comment": False})
login("cfo@ftpua.com", ["g-f"])
c.post(f"/requests/{rid}/comment", data={"comment": "питання"})
mailer.wait()
rows = [r for r in notify_log.query(request_id=rid) if r["event"] == "comment"]
check("author skipped: event disabled", any(r["recipient"] == "author@ftpua.com" and r["status"] == "skipped" and "вимкнено" in r["detail"] for r in rows))
login("author@ftpua.com", ["g-i"])
c.post(f"/requests/{rid}/comment", data={"comment": "відповідь"})
mailer.wait()
rows = [r for r in notify_log.query(request_id=rid) if r["event"] == "comment"]
check("author's own comment: skipped as own action", any(r["recipient"] == "author@ftpua.com" and r["status"] == "skipped"
      and "дію виконали ви самі" in r["detail"] for r in rows))

# сторінка журналу
login("admin@ftpua.com", ["g-a"])
page = c.get("/admin/notifications").get_data(as_text=True)
check("journal page", "Журнал сповіщень" in page and "cfo@ftpua.com" in page and "надіслано" in page)
check("filter channel", "author@ftpua.com" not in c.get("/admin/notifications?channel=email&q=cfo").get_data(as_text=True).split("<tbody>")[1])
check("filter skipped", "не надсилалось" in c.get("/admin/notifications?status=skipped").get_data(as_text=True))
check("admin users: link to journal", "📜 Журнал сповіщень" in c.get("/admin/users").get_data(as_text=True))
login("author@ftpua.com", ["g-i"])
check("non-admin 403", c.get("/admin/notifications").status_code == 403)

# розсилку вимкнено
app_settings.set("mail_enabled", False, "t")
c.post(f"/requests/{rid}/comment", data={"comment": "ще"})
check("mail disabled logged", any("розсилку вимкнено" in (r["detail"] or "") for r in notify_log.query(request_id=rid)))
print("ALL OK" if ok else "SOME FAILED")

# ---------- регресія: режим «Фіндиректор від суми» + паралельно — погоджувачі мають отримати листи
app_settings.set("mail_enabled", True, "t")
app_settings.set("cfo_mode", "threshold", "t"); app_settings.set("cfo_threshold_usd", 10, "t")
ROLE["acc_resident"] = ["res@ftpua.com"]; notifications._members_cache.clear()
login("author@ftpua.com", ["g-i"])
c.post("/requests/new", data={"pay_date": "2026-10-20", "organization_id": "1", "counterparty_id": "1", "expense_code": "01.001",
                              "currency": "USD", "amount": "500", "payment_form": "bank", "action": "submit"})
rid2 = db.last_id()
mailer.wait()
rows = notify_log.query(request_id=rid2)
sent_to = {r["recipient"] for r in rows if r["channel"] == "email" and r["status"] == "sent"}
check("threshold note in history", db.get_request(rid2)["history"][-1]["action"] == "cfo_threshold")
check("threshold+parallel: accountant AND cfo notified", {"res@ftpua.com", "cfo@ftpua.com"} <= sent_to)
print("ALL OK" if ok else "SOME FAILED")
