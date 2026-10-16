import os, sys, tempfile
sys.path.insert(0, os.getcwd())
tmp = tempfile.mkdtemp()
os.environ.update(UPLOAD_DIR=tmp, APP_SETTINGS_FILE=os.path.join(tmp, "s.json"), USER_PREFS_FILE=os.path.join(tmp, "p.json"),
                  USER_DIRECTORY_FILE=os.path.join(tmp, "d.json"), ORG_STRUCTURE_FILE=os.path.join(tmp, "o.json"),
                  COUNTERPARTIES_PENDING_FILE=os.path.join(tmp, "cp.json"),
                  GROUP_ADMIN="g-a", GROUP_INITIATOR="g-i", GROUP_ACC_RESIDENT="g-r", GROUP_ACC_CASH="g-c", GROUP_CFO="g-f",
                  ADMIN_EMAILS="")
import app as m
import user_budget; user_budget.get = lambda oid: {"direct": True, "all_departments": True, "departments": []}
import app_settings, mailer, notifications, user_directory
import mock_data as db

ok = True
def check(label, cond):
    global ok
    ok &= bool(cond)
    print(("PASS " if cond else "FAIL ") + label)

sent = []
mailer.transport = lambda cfg, to, subject, html: sent.append({"to": to, "subject": subject, "html": html})
notifications.role_members = lambda role: []
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
user_directory.remember([{"id": "w", "email": "w@ftpua.com", "name": "Вікторія"},
                         {"id": "s", "email": "s@ftpua.com", "name": "Сергій"}], active=True)

BASE = {"pay_date": "2026-10-20", "organization_id": "1", "counterparty_id": "1", "expense_code": "01.001",
        "currency": "UAH", "amount": "900", "payment_form": "bank", "participants_sent": "1"}
AUTHOR = "author@ftpua.com"
login(AUTHOR, ["g-i"])

page = c.get("/requests/new").get_data(as_text=True)
check("new request: participants block with search", 'id="participant-search"' in page and 'form="request-form"' in page)

# нова заявка з учасником — лист і історія лише після збереження
flush()
c.post("/requests/new", data={**BASE, "participants": ["w@ftpua.com"], "action": "save"})
rid = max(db._REQUESTS)
r = db._REQUESTS[rid]
mails = flush()
check("saved with participant", [p["email"] for p in r["participants"]] == ["w@ftpua.com"])
check("history: create then participant_add", [h["action"] for h in r["history"]] == ["create", "participant_add"])
check("'вас додано' mail after save", any(x["to"] == "w@ftpua.com" and "учасником" in x["subject"] for x in mails))

# помилка в полях — нічого не зберігається, набрані учасники лишаються у формі
flush()
resp = c.post(f"/requests/{rid}", data={**BASE, "amount": "0", "participants": ["w@ftpua.com", "s@ftpua.com"], "action": "save"})
html = resp.get_data(as_text=True)
check("validation error: not saved, no mail", [p["email"] for p in db._REQUESTS[rid]["participants"]] == ["w@ftpua.com"] and not flush())
check("validation error: typed participants kept in form", "s@ftpua.com" in html.split('data-state=')[1].split("data-saved")[0])

# зберегти: додати Сергія, прибрати Вікторію
c.post(f"/requests/{rid}", data={**BASE, "participants": ["s@ftpua.com"], "action": "save"})
r = db._REQUESTS[rid]
mails = flush()
check("save: add + remove", [p["email"] for p in r["participants"]] == ["s@ftpua.com"]
      and [h["action"] for h in r["history"]][-2:] == ["participant_remove", "participant_add"])
check("mail only to added", [x["to"] for x in mails if "учасником" in x["subject"]] == ["s@ftpua.com"])

# без поля participants_sent (старий клієнт) — учасників не чіпаємо
c.post(f"/requests/{rid}", data={k: v for k, v in BASE.items() if k != "participants_sent"} | {"action": "save"})
check("no participants_sent -> unchanged", [p["email"] for p in db._REQUESTS[rid]["participants"]] == ["s@ftpua.com"])

# невідомий користувач
c.post(f"/requests/{rid}", data={**BASE, "participants": ["s@ftpua.com", "ghost@ftpua.com"], "action": "save"})
check("unknown user skipped", [p["email"] for p in db._REQUESTS[rid]["participants"]] == ["s@ftpua.com"])

# у чернетці окреме «Зберегти учасників» не працює
r = c.post(f"/requests/{rid}/participants", data={"action": "set", "participants": []}, follow_redirects=True)
check("draft: set route refuses", [p["email"] for p in db._REQUESTS[rid]["participants"]] == ["s@ftpua.com"]
      and "разом із нею" in r.get_data(as_text=True))

# відправлено — форма не редагується, учасники — окремою кнопкою
c.post(f"/requests/{rid}", data={**BASE, "participants": ["s@ftpua.com"], "action": "submit"})
check("submitted", db._REQUESTS[rid]["status"] == "accountant")
page = c.get(f"/requests/{rid}").get_data(as_text=True)
check("submitted: save-participants form", 'id="participants-form"' in page and 'form="participants-form"' in page)
flush()
c.post(f"/requests/{rid}/participants", data={"action": "set", "participants": ["s@ftpua.com", "w@ftpua.com"]})
mails = flush()
check("set route: added + mail", [p["email"] for p in db._REQUESTS[rid]["participants"]] == ["s@ftpua.com", "w@ftpua.com"]
      and [x["to"] for x in mails if "учасником" in x["subject"]] == ["w@ftpua.com"])
c.post(f"/requests/{rid}/participants", data={"action": "set"})
check("set route: remove all", db._REQUESTS[rid]["participants"] == [])

# сторонній
login("other@ftpua.com", ["g-i"])
check("stranger -> 404", c.post(f"/requests/{rid}/participants", data={"action": "set", "participants": ["w@ftpua.com"]}).status_code == 404)

print("ALL OK" if ok else "SOME FAILED")
