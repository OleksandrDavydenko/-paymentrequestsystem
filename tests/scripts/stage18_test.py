import os, sys, tempfile
from datetime import timedelta
sys.path.insert(0, os.getcwd())
tmp = tempfile.mkdtemp()
os.environ.update(UPLOAD_DIR=tmp, APP_SETTINGS_FILE=os.path.join(tmp, "s.json"), USER_PREFS_FILE=os.path.join(tmp, "p.json"),
                  USER_DIRECTORY_FILE=os.path.join(tmp, "d.json"), ORG_STRUCTURE_FILE=os.path.join(tmp, "o.json"),
                  COUNTERPARTIES_PENDING_FILE=os.path.join(tmp, "cp.json"),
                  GROUP_ADMIN="g-a", GROUP_INITIATOR="g-i", GROUP_ACC_RESIDENT="g-r", GROUP_ACC_CASH="g-c", GROUP_CFO="g-f",
                  ADMIN_EMAILS="")
import app as m
import user_budget; user_budget.get = lambda oid: {"direct": True, "all_departments": True, "departments": []}
import app_settings, mailer, notifications, user_prefs, clock
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
                                           {"acc_cash": ["cash@ftpua.com"], "acc_resident": ["res@ftpua.com"],
                                            "cfo": ["cfo@ftpua.com"]}.get(role, [])]
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
for k, v in {"mail_enabled": True, "mail_sender": "noreply@ftpua.com"}.items():
    app_settings.set(k, v, "t")
today = clock.today()

BASE = {"pay_date": "2026-10-20", "organization_id": "1", "counterparty_id": "1", "expense_code": "01.001",
        "currency": "UAH", "amount": "1500", "payment_form": "cash"}
INIT, CASH, RES, CFO, ADMIN = ["g-i"], ["g-c"], ["g-r"], ["g-f"], ["g-a"]
AUTHOR = "author@ftpua.com"

# ---------- форма: вибору «хто отримує» немає
login(AUTHOR, INIT)
page = c.get("/requests/new").get_data(as_text=True)
check("no cash receiver choice in form", "cash_receiver" not in page and "Хто отримує готівку" not in page)

def new_cash(form="cash", author=AUTHOR):
    login(author, INIT)
    c.post("/requests/new", data={**BASE, "payment_form": form, "action": "submit"})
    return db.last_id()

def to_paid(rid):
    login("cash@ftpua.com", CASH)
    c.post(f"/requests/{rid}/action", data={"action": "approve", "payment_account_id": "5"})
    login("cfo@ftpua.com", CFO)
    c.post(f"/requests/{rid}/action", data={"action": "approve"})
    login("cash@ftpua.com", CASH)
    c.post(f"/requests/{rid}/action", data={"action": "pay", "comment": "РКО 12"})
    return db.get_request(rid)

rid = new_cash()
check("cash request submitted", db.get_request(rid)["status"] == "accountant")
login("cash@ftpua.com", CASH)
c.post(f"/requests/{rid}/action", data={"action": "approve", "payment_account_id": "5"})
login("cfo@ftpua.com", CFO)
c.post(f"/requests/{rid}/action", data={"action": "approve"})
login("cash@ftpua.com", CASH)
check("pay panel explains handover", "після «Оплачено» він" in c.get(f"/requests/{rid}").get_data(as_text=True))
c.post(f"/requests/{rid}/action", data={"action": "pay", "comment": "РКО 12"})
flush()
req = db.get_request(rid)
check("pay -> handover pending (every cash request)", req["status"] == "paid" and wf.needs_handover(req) and req["handover"]["since"] == today)
# повторно — для перевірки листа автору
_r = db.get_request(rid); _r["status"] = "to_pay"; _r.pop("handover"); db.save_request(_r)
flush()
req = to_paid(rid)
check("pay -> handover pending", req["status"] == "paid" and wf.needs_handover(req) and req["handover"]["since"] == today)
mails = flush()
am = [x for x in mails if x["to"] == AUTHOR]
check("author pay mail asks to confirm", am and "підтвердіть передачу" in am[-1]["subject"] and "Підтвердити передачу" in am[-1]["html"]
      and "#handover" in am[-1]["html"])

page = c.get(f"/requests/{rid}").get_data(as_text=True)
check("pay callout shown before? (paid now) — accountant sees confirm-for-author", "Підтвердити за автора" in page)

login(AUTHOR, INIT)
page = c.get(f"/requests/{rid}").get_data(as_text=True)
check("author card: handover block + badge", 'id="handover"' in page and "Готівку передано" in page and "передачу готівки не підтверджено" in page)
lst = c.get("/requests?view=paid").get_data(as_text=True)
check("my paid tab: counter + badge", "⏳ 1" in lst and "підтвердіть передачу" in lst)

# ---------- вкладка бухгалтера
login("cash@ftpua.com", CASH)
tp = c.get("/to-pay").get_data(as_text=True)
check("cash accountant: tab with count", "Готівка не підтверджена" in tp and "view=handover" in tp)
hv = c.get("/to-pay?view=handover")
hp = hv.get_data(as_text=True)
check("handover view lists request + by-author", hv.status_code == 200 and db.get_request(rid)["number"] in hp and "У кого скільки готівки" in hp)
check("demo pending (№20) listed, confirmed (№21) not", "000000020" in hp and "000000021" not in hp)
login("cfo@ftpua.com", CFO)
check("cfo: no tab, 403", "view=handover" not in c.get("/to-pay").get_data(as_text=True) and c.get("/to-pay?view=handover").status_code == 403)
login("res@ftpua.com", RES)
check("resident accountant: no tab, 403", "view=handover" not in c.get("/to-pay").get_data(as_text=True) and c.get("/to-pay?view=handover").status_code == 403)

# ---------- підтвердження
login("other@ftpua.com", INIT)
check("stranger -> 404", c.post(f"/requests/{rid}/handover", data={"date": today.isoformat()}).status_code == 404)
login("cfo@ftpua.com", CFO)
check("cfo cannot confirm -> 403", c.post(f"/requests/{rid}/handover", data={"date": today.isoformat()}).status_code == 403)
login(AUTHOR, INIT)
c.post(f"/requests/{rid}/handover", data={"date": (today - timedelta(days=1)).isoformat()})
check("date before payout rejected", wf.needs_handover(db.get_request(rid)))
r = c.post(f"/requests/{rid}/handover", data={"date": (today + timedelta(days=1)).isoformat()}, follow_redirects=True)
check("future date rejected", wf.needs_handover(db.get_request(rid)) and "в майбутньому" in r.get_data(as_text=True))
user_prefs.save("cash@ftpua.com", True, {**user_prefs.get("cash@ftpua.com")["events"], "handover_done": True})
flush()
c.post(f"/requests/{rid}/handover", data={"date": today.isoformat(), "comment": "Передав на об'єкті"})
req = db.get_request(rid)
h = req["history"][-1]
check("author confirmed", wf.handover_done(req) and req["handover"]["date"] == today and not req["handover"]["on_behalf"]
      and h["action"] == "handover" and h["role"] == "initiator" and "Передав на об'єкті" in h["comment"])
mails = flush()
check("accountant mail (pref on), status still paid", any(x["to"] == "cash@ftpua.com" and "передано" in x["subject"] for x in mails)
      and req["status"] == "paid")
check("author confirmed himself -> no 'за вас' mail", not any("за вас" in x["subject"] for x in mails))
lst = c.get("/requests?view=paid").get_data(as_text=True)
check("badge gone after confirm", "⏳ 1" not in lst)
page = c.get(f"/requests/{rid}").get_data(as_text=True)
check("card shows done", "готівку передано підряднику" in page and "Скасувати підтвердження" not in page)

# ---------- адмін скасовує
login("admin@ftpua.com", ADMIN)
page = c.get(f"/requests/{rid}").get_data(as_text=True)
check("admin sees undo", "Скасувати підтвердження" in page)
c.post(f"/requests/{rid}/handover", data={"action": "undo", "comment": ""})
check("undo without reason rejected", wf.handover_done(db.get_request(rid)))
login(AUTHOR, INIT)
check("non-admin undo -> 403", c.post(f"/requests/{rid}/handover", data={"action": "undo", "comment": "x"}).status_code == 403)
login("admin@ftpua.com", ADMIN)
c.post(f"/requests/{rid}/handover", data={"action": "undo", "comment": "Помилково натиснули"})
check("admin undo -> pending again", wf.needs_handover(db.get_request(rid)) and db.get_request(rid)["history"][-1]["action"] == "handover_undo")

# ---------- бухгалтер за автора, з вкладки
login("cash@ftpua.com", CASH)
flush()
r = c.post(f"/requests/{rid}/handover", data={"date": today.isoformat(), "next": "/to-pay?view=handover"})
req = db.get_request(rid)
mails = flush()
am = [x for x in mails if x["to"] == AUTHOR]
check("author notified when confirmed on behalf", am and "за вас" in am[0]["subject"]
      and "Самому підтверджувати вже не потрібно" in am[0]["html"])
check("accountant confirms on behalf, back to tab", wf.handover_done(req) and req["handover"]["on_behalf"]
      and req["history"][-1]["role"] == "acc_cash" and "за автора" in req["history"][-1]["comment"]
      and r.headers["Location"].endswith("/to-pay?view=handover"))
r = c.post(f"/requests/{rid}/handover", data={"date": today.isoformat(), "next": "//evil.com"})
check("open redirect blocked", "evil.com" not in r.headers.get("Location", ""))

# ---------- безготівка
rid2 = new_cash("bank")
login("res@ftpua.com", RES)
c.post(f"/requests/{rid2}/action", data={"action": "approve", "payment_account_id": "1"})
login("cfo@ftpua.com", CFO)
c.post(f"/requests/{rid2}/action", data={"action": "approve"})
login("res@ftpua.com", RES)
c.post(f"/requests/{rid2}/action", data={"action": "pay"})
req2 = db.get_request(rid2)
check("bank paid -> no handover", req2["status"] == "paid" and "handover" not in req2)
rid3 = new_cash()
req3 = to_paid(rid3)
check("second cash -> handover", wf.needs_handover(req3))

# ---------- адмін змінює статус
login("admin@ftpua.com", ADMIN)
c.post(f"/requests/{rid3}/admin-status", data={"status": "to_pay", "comment": "помилка"})
check("admin: paid -> to_pay clears handover", "handover" not in db.get_request(rid3))
c.post(f"/requests/{rid3}/admin-status", data={"status": "paid", "comment": "оплачено"})
check("admin: -> paid starts handover", wf.needs_handover(db.get_request(rid3)))

# ---------- нагадування
app_settings.set("cash_handover_remind_days", 1, "t")
for r_ in db.list_all():
    if wf.needs_handover(r_):
        r_["handover"]["since"] = today - timedelta(days=2)
        r_["handover"]["reminders"], r_["handover"]["last_reminder"] = 0, None
        db.save_request(r_)
# автор вимкнув усі листи — нагадування все одно приходять
user_prefs.save(AUTHOR, False, user_prefs.get(AUTHOR)["events"])
flush()
done = m.run_handover_reminders()
mails = flush()
check("reminder sent to author only", db.get_request(rid3)["number"] in done
      and any(x["to"] == AUTHOR and "нагадування" in x["subject"] for x in mails)
      and all(x["to"] in (AUTHOR, "od@ftpua.com", "i.petrenko@ftpua.com") for x in mails))
check("reminder mail: days + link", any("2 дн." in x["html"] and "#handover" in x["html"] for x in mails if x["to"] == AUTHOR))
check("counter + date stored", db.get_request(rid3)["handover"]["reminders"] == 1 and db.get_request(rid3)["handover"]["last_reminder"] == today)
check("not again same day", m.run_handover_reminders() == [])
for i in range(1, 10):
    m.run_handover_reminders(today + timedelta(days=i))
check("max 5 reminders", db.get_request(rid3)["handover"]["reminders"] == wf.HANDOVER_MAX_REMINDERS)
_r = db.get_request(rid3); _r["handover"].update(reminders=0, last_reminder=None); db.save_request(_r)
app_settings.set("cash_handover_remind_days", 0, "t")
check("remind_days=0 -> none", m.run_handover_reminders() == [])
app_settings.set("cash_handover_remind_days", 1, "t")
app_settings.set("mail_enabled", False, "t")
check("mail off -> not counted", m.run_handover_reminders() == [] and db.get_request(rid3)["handover"]["reminders"] == 0)
app_settings.set("mail_enabled", True, "t")
flush()

# ---------- налаштування в адмінці
login("admin@ftpua.com", ADMIN)
page = admin_page()
check("admin settings block", "Готівка через автора заявки" in page and 'name="cash_handover_remind_days"' in page)
c.post("/admin/settings", data={"approval_mode": "sequential", "dept_head_mode": "view", "pending_cp_payment": "block",
                                "cash_handover_sent": "1", "cash_handover_confirm": "1", "cash_handover_remind_days": "3"})
check("remind days saved", app_settings.get("cash_handover_remind_days") == 3 and app_settings.get("cash_handover_confirm"))
c.post("/admin/settings", data={"approval_mode": "sequential", "dept_head_mode": "view", "pending_cp_payment": "block",
                                "cash_handover_sent": "1", "cash_handover_remind_days": "3"})
check("feature switched off", app_settings.get("cash_handover_confirm") is False)

# ---------- вимкнено
login(AUTHOR, INIT)
page = c.get("/requests/new").get_data(as_text=True)
check("off: no receiver field", 'id="cash-receiver-field"' not in page)
c.post("/requests/new", data={**BASE, "action": "submit"})
rid4 = db.last_id()
check("off: cash request without receiver accepted", db.get_request(rid4)["status"] == "accountant")
req4 = to_paid(rid4)
check("off: no handover", "handover" not in req4)
login("cash@ftpua.com", CASH)
check("off: no tab", "view=handover" not in c.get("/to-pay").get_data(as_text=True))
check("off: no reminders", m.run_handover_reminders(today + timedelta(days=30)) == [])

# ---------- налаштування сповіщень бухгалтера
app_settings.set("cash_handover_confirm", True, "t")
check("prefs page: cash group for accountant", "Готівка через автора заявки" in c.get("/settings/notifications").get_data(as_text=True))
login(AUTHOR, INIT)
check("prefs page: no cash group for initiator", "Готівка через автора заявки" not in c.get("/settings/notifications").get_data(as_text=True))

print("ALL OK" if ok else "SOME FAILED")
