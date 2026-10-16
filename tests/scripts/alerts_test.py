"""Листи адміністраторам про помилки сайту: збій сторінки й фонової задачі, не частіше раз на годину."""
import os, sys, tempfile
sys.path.insert(0, os.getcwd())
tmp = tempfile.mkdtemp()
os.environ.update(UPLOAD_DIR=tmp, GROUP_ADMIN="g-a", GROUP_INITIATOR="g-i", ADMIN_EMAILS="boss@ftpua.com")
import app as m
import alerts, app_settings, jobs, mailer, notifications, notify_log
from web import background

ok = True
def check(label, cond):
    global ok
    ok &= bool(cond); print(("PASS " if cond else "FAIL ") + label)

sent = []
mailer.transport = lambda cfg, to, subject, html: sent.append((to, subject, html))
notifications.cached_role_members = lambda role: [{"email": "admin2@ftpua.com", "name": "A"}] if role == "admin" else []
for k, v in {"mail_sender": "noreply@ftpua.com", "smtp_host": "smtp.example.com", "mail_enabled": False}.items():
    app_settings.set(k, v, "t")

# маршрут, що падає (лише для тесту; реєструється до першого запиту)
def boom():
    raise ZeroDivisionError("ділення на нуль у тесті")
def boom2():
    raise KeyError("інша помилка")
m.app.add_url_rule("/_test/boom", "boom", boom)
m.app.add_url_rule("/_test/boom2", "boom2", boom2)

claims = {"name": "Іра", "preferred_username": "ira@ftpua.com", "oid": "ira", "groups": ["g-i"]}
m.auth._auth.get_user = lambda: dict(claims)
c = m.app.test_client()

r = c.get("/_test/boom")
mailer.wait()
check("friendly 500 page", r.status_code == 500 and "Щось пішло не так" in r.get_data(as_text=True))
check("mail to every admin even with mailing off", sorted(t for t, _, _ in sent) == ["admin2@ftpua.com", "boss@ftpua.com"])
subj, body = sent[0][1], sent[0][2]
check("subject + details (where, traceback, no form data)", subj.startswith("[Помилка сайту] ZeroDivisionError")
      and "GET /_test/boom" in body and "Traceback" in body and "ділення на нуль" in body)
check("journal records the alert", any(r_["event"] == "error_alert" for r_ in notify_log.query(limit=5)))
sent.clear()
c.get("/_test/boom"); mailer.wait()
check("same error within an hour -> no second mail", sent == [])
c.get("/_test/boom2"); mailer.wait()
check("different error -> mail", len(sent) == 2 and "KeyError" in sent[0][1])

# фонова задача
sent.clear()
def bad_job():
    raise RuntimeError("фонова задача впала")
jobs_run = jobs.run
background.jobs.run = lambda name, every, fn: bad_job() if name == "maintenance" else None
background.REMINDER_INTERVAL = 0
calls = {"n": 0}
real_sleep = background.time.sleep
def stop_after(s):
    calls["n"] += 1
    if calls["n"] > 2:
        raise SystemExit
background.time.sleep = stop_after
try:
    background._reminder_loop()
except SystemExit:
    pass
background.time.sleep = real_sleep
background.jobs.run = jobs_run
mailer.wait()
check("background job failure -> mail", sent and "RuntimeError" in sent[0][1] and "фонова задача «maintenance»" in sent[0][2])

# вимкнено в адмінці
sent.clear()
app_settings.set("error_alerts", False, "t")
alerts._recent.clear()
c.get("/_test/boom"); mailer.wait()
check("alerts off -> no mail", sent == [])
print("ALL OK" if ok else "SOME FAILED")
