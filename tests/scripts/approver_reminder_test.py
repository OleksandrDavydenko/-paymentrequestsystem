"""Нагадування погоджувачам: заявка чекає рішення довше N днів, повтор кожні N днів, не більше M разів,
новий етап — з нуля, копія фіндиректору, особисте налаштування, тестовий режим; максимум нагадувань про готівку."""
import os, sys, tempfile
from datetime import timedelta
sys.path.insert(0, os.getcwd())
tmp = tempfile.mkdtemp()
os.environ.update(UPLOAD_DIR=tmp, GROUP_ADMIN="g-a", GROUP_INITIATOR="g-i", GROUP_ACC_RESIDENT="g-r", GROUP_CFO="g-f",
                  ADMIN_EMAILS="")
import app as m
import user_budget; user_budget.get = lambda oid: {"direct": True, "all_departments": True, "departments": []}
import app_settings, clock, mailer, notifications, user_prefs
import storage as db
import workflow as wf

ok = True
def check(label, cond):
    global ok
    ok &= bool(cond); print(("PASS " if cond else "FAIL ") + label)

sent = []
mailer.transport = lambda cfg, to, subject, html: sent.append((to, subject))
ROLE = {"acc_resident": ["acc@ftpua.com"], "cfo": ["cfo@ftpua.com"]}
notifications.role_members = lambda role: [{"email": e, "name": e} for e in ROLE.get(role, [])]
notifications._members_cache.clear()
for k, v in {"mail_enabled": True, "mail_sender": "noreply@ftpua.com", "smtp_host": "smtp.example.com",
             "approval_mode": "sequential"}.items():
    app_settings.set(k, v, "t")
claims = {}
def login(email, groups):
    claims.clear(); claims.update({"name": email, "preferred_username": email, "oid": email, "groups": groups})
m.auth._auth.get_user = lambda: dict(claims)
c = m.app.test_client()
def flush():
    mailer.wait(); out = list(sent); sent.clear(); return out

# Демо-заявки теж чекають рішення — перевіряємо лише заявки цього тесту
for r_ in db.list_requests_full("awaiting_decision"):
    r_["status"] = "rework"; db.save_request(r_)

login("author@ftpua.com", ["g-i"])
c.post("/requests/new", data={"pay_date": "2026-12-20", "organization_id": "1", "counterparty_id": "1",
                              "expense_code": "01.001", "currency": "UAH", "amount": "500", "payment_form": "bank",
                              "action": "submit"})
rid = db.last_id()
req = db.get_request(rid)
check("waits for accountant", req["status"] == "accountant")
flush()
today = clock.today()
day = lambda n: today + timedelta(days=n)

check("off by default", m.run_approver_reminders(day(10)) == [] and flush() == [])
app_settings.set("approver_remind_days", 2, "t"); app_settings.set("approver_remind_max", 2, "t")
check("too early (1 day)", m.run_approver_reminders(day(1)) == [] and flush() == [])
check("after 2 days -> reminder", m.run_approver_reminders(day(2)) == [req["number"]])
mails = flush()
check("to the accountant only", [t for t, s in mails] == ["acc@ftpua.com"] and "чекає вашого рішення 2 дн." in mails[0][1])
check("not again the next day", m.run_approver_reminders(day(3)) == [] and flush() == [])
check("again after another 2 days", m.run_approver_reminders(day(4)) == [req["number"]] and len(flush()) == 1)
check("max 2 reminders", m.run_approver_reminders(day(8)) == [] and flush() == [])

# ---------- новий етап — з нуля; копія фіндиректору лише коли чекає бухгалтера
login("acc@ftpua.com", ["g-r"])
c.post(f"/requests/{rid}/action", data={"action": "approve", "payment_account_id": "1",
                                         "version": str(db.get_request(rid)["version"])})
check("now waits for cfo", db.get_request(rid)["status"] == "cfo")
flush()
app_settings.set("approver_remind_cfo_copy", True, "t")
check("new stage: counter reset", m.run_approver_reminders(day(3)) == [req["number"]])
check("cfo reminded (no copy: it's his own stage)", [t for t, s in flush()] == ["cfo@ftpua.com"])

login("author@ftpua.com", ["g-i"])
c.post("/requests/new", data={"pay_date": "2026-12-20", "organization_id": "1", "counterparty_id": "1",
                              "expense_code": "01.001", "currency": "UAH", "amount": "700", "payment_form": "bank",
                              "action": "submit"})
r2 = db.last_id()
flush()
m.run_approver_reminders(day(3))
check("waiting accountant + copy on -> accountant and cfo", {t for t, s in flush()} >= {"acc@ftpua.com", "cfo@ftpua.com"})

# ---------- особисте налаштування й тестовий режим
user_prefs.save("acc@ftpua.com", True, {**user_prefs.get("acc@ftpua.com")["events"], "task_reminder": False})
app_settings.set("approver_remind_max", 5, "t")
m.run_approver_reminders(day(6))
check("accountant switched reminders off -> not sent to him", "acc@ftpua.com" not in {t for t, s in flush()})
real = notifications.test_mode
notifications.test_mode = lambda: True
check("test mode -> nothing", m.run_approver_reminders(day(20)) == [] and flush() == [])
notifications.test_mode = real

# ---------- готівка: максимум нагадувань з налаштування
app_settings.set("handover_max_reminders", 2, "t")
check("handover max from settings", wf.handover_max() == 2)
h = {"status": "paid", "handover": {"status": "pending", "since": today, "reminders": 2, "last_reminder": None}}
check("handover: 2 of 2 -> no more", wf.handover_reminder_due(h, 1, day(5)) is False)
h["handover"]["reminders"] = 1
check("handover: 1 of 2 -> due", wf.handover_reminder_due(h, 1, day(5)) is True)

# ---------- сторінка налаштувань
login("admin@ftpua.com", ["g-a"])
page = c.get("/admin/settings?tab=notify").get_data(as_text=True)
check("notify tab: reminder settings", 'name="approver_remind_days"' in page and 'name="approver_remind_cfo_copy"' in page)
c.post("/admin/settings", data={"tab": "notify", "approver_remind_days": "4", "approver_remind_max": "3"})
check("saved; checkbox off; mail settings untouched", app_settings.get("approver_remind_days") == 4
      and app_settings.get("approver_remind_cfo_copy") is False and app_settings.get("mail_enabled") is True)
print("ALL OK" if ok else "SOME FAILED")
