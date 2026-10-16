"""Автор відкликає заявку на редагування й скасовує її; адмінська зміна статусу не додає фіндиректора без
потреби; журнал сповіщень показує, чому людина отримала сповіщення."""
import os, re, sys, tempfile
sys.path.insert(0, os.getcwd())
tmp = tempfile.mkdtemp()
os.environ.update(UPLOAD_DIR=tmp, GROUP_ADMIN="g-a", GROUP_INITIATOR="g-i", GROUP_ACC_RESIDENT="g-r", GROUP_CFO="g-f",
                  ADMIN_EMAILS="")
import app as m
import user_budget; user_budget.get = lambda oid: {"direct": True, "all_departments": True, "departments": []}
import app_settings, graph, mailer, notifications, notify_log, rates
import storage as db
from workflow import RoleSet

ok = True
def check(label, cond):
    global ok
    ok &= bool(cond); print(("PASS " if cond else "FAIL ") + label)

sent = []
mailer.transport = lambda cfg, to, subject, html: sent.append((to, subject))
ROLE = {"acc_resident": ["acc@ftpua.com"], "cfo": ["cfo@ftpua.com"]}
notifications.role_members = lambda role: [{"email": e, "name": e} for e in ROLE.get(role, [])]
notifications._members_cache.clear()
for k, v in {"mail_enabled": True, "mail_sender": "noreply@ftpua.com", "smtp_host": "smtp.example.com"}.items():
    app_settings.set(k, v, "t")
app_settings.set("approval_mode", "parallel", "t")
claims = {}
def login(email, groups):
    claims.clear(); claims.update({"name": email.split("@")[0].title(), "preferred_username": email, "oid": email, "groups": groups})
m.auth._auth.get_user = lambda: dict(claims)
m.auth._auth.get_token_for_user = lambda s: {"access_token": "t", "scope": " ".join(s)}
graph.group_members = lambda t, gid: []
c = m.app.test_client()
def flush():
    mailer.wait(); out = list(sent); sent.clear(); return out
def version(rid):
    return str(db.get_request(rid)["version"])

FORM = {"pay_date": "2026-10-20", "organization_id": "1", "counterparty_id": "1", "expense_code": "01.001",
        "currency": "UAH", "amount": "5000", "payment_form": "bank"}

# ---------- паралельно: бухгалтер погодив, фіндиректор ще ні -> автор відкликає
login("author@ftpua.com", ["g-i"])
c.post("/requests/new", data={**FORM, "action": "submit"})
rid = db.last_id()
login("acc@ftpua.com", ["g-r"])
r = db.get_request(rid)
c.post(f"/requests/{rid}/action", data={"action": "approve", "payment_account_id": r.get("payment_account_id") or "1",
                                         "version": version(rid)})
check("accountant approved, cfo pending", db.get_request(rid)["status"] == "approval"
      and "acc_resident" in db.get_request(rid)["approvals"])
flush()
login("author@ftpua.com", ["g-i"])
page = c.get(f"/requests/{rid}").get_data(as_text=True)
check("author sees both buttons", "Відкликати на редагування" in page and "Скасувати заявку" in page and 'id="author-actions"' in page)
c.post(f"/requests/{rid}/author-action", data={"action": "withdraw", "comment": "Помилка в сумі", "version": version(rid)})
req = db.get_request(rid)
check("withdrawn -> draft, approvals reset", req["status"] == "draft" and req["approvals"] == {}
      and req["history"][-1]["action"] == "withdraw" and req["history"][-1]["comment"] == "Помилка в сумі")
mails = flush()
check("cfo (was waiting) and accountant (already approved) notified",
      {t for t, s in mails if "відкликано автором" in s} == {"cfo@ftpua.com", "acc@ftpua.com"})
log = [x for x in notify_log.query(request_id=rid) if x["event"] == "withdraw" and x["status"] == "sent"]
check("journal: reason shown for sent", any("чекала вашого рішення" in (x["detail"] or "") for x in log)
      and any("вже погодили" in (x["detail"] or "") for x in log))
check("editable again", "Відправити на погодження" in c.get(f"/requests/{rid}").get_data(as_text=True))
# повторна відправка — маршрут з початку
c.post(f"/requests/{rid}", data={**FORM, "amount": "4000", "action": "submit", "version": version(rid)})
req = db.get_request(rid)
check("resubmit restarts route (both approvers again)", req["status"] == "approval" and req["approvals"] == {}
      and float(req["amount"]) == 4000)
check("in queues again", rid in [x["id"] for x in db.scope_rows("queue", RoleSet({"acc_resident"}), "acc@ftpua.com")])

# ---------- скасування
c.post(f"/requests/{rid}/author-action", data={"action": "cancel", "comment": "", "version": version(rid)})
check("cancel without reason rejected", db.get_request(rid)["status"] == "approval"
      and "причину скасування" in c.get(f"/requests/{rid}").get_data(as_text=True))
flush()
c.post(f"/requests/{rid}/author-action", data={"action": "cancel", "comment": "Вже не потрібно", "version": version(rid)})
req = db.get_request(rid)
check("cancelled", req["status"] == "cancelled" and req["history"][-1]["action"] == "cancel")
check("cancel notifies those waiting", {t for t, s in flush() if "скасовано автором" in s} == {"cfo@ftpua.com", "acc@ftpua.com"})
check("not in queues", rid not in [x["id"] for x in db.scope_rows("queue", RoleSet({"acc_resident", "cfo"}), "x@ftpua.com")])
html = c.get("/requests").get_data(as_text=True)
check("my requests: shown with status 'Скасовано'", req["number"] in html and "Скасовано" in html)
page = c.get(f"/requests/{rid}").get_data(as_text=True)
check("closed: no author actions, not editable", 'id="author-actions"' not in page and "Відправити на погодження" not in page)

# ---------- чернетка: скасування без повідомлень; до оплати — не можна
c.post("/requests/new", data={**FORM, "action": "save"})
draft = db.last_id()
c.post(f"/requests/{draft}/author-action", data={"action": "withdraw", "version": version(draft)})
check("draft cannot be withdrawn", db.get_request(draft)["status"] == "draft")
c.post(f"/requests/{draft}/author-action", data={"action": "cancel", "comment": "Чернетка зайва", "version": version(draft)})
check("draft cancelled, nobody notified", db.get_request(draft)["status"] == "cancelled" and flush() == [])
paid_like = next(x for x in db.list_all() if x["status"] == "to_pay")
p = db.get_request(paid_like["id"]); p["author_email"] = "author@ftpua.com"; db.save_request(p)
pg = c.get(f"/requests/{p['id']}").get_data(as_text=True)
c.post(f"/requests/{p['id']}/author-action", data={"action": "cancel", "comment": "x", "version": version(p["id"])})
check("to_pay: no buttons, POST refused", 'id="author-actions"' not in pg and db.get_request(p["id"])["status"] == "to_pay")

# ---------- не автор не може; застаріла сторінка
c.post("/requests/new", data={**FORM, "action": "submit"})
other = db.last_id()
login("intruder@ftpua.com", ["g-i"])
r = c.post(f"/requests/{other}/author-action", data={"action": "cancel", "comment": "x"})
check("not author -> refused", r.status_code in (302, 404) and db.get_request(other)["status"] != "cancelled")
login("author@ftpua.com", ["g-i"])
old_v = version(other)
c.post(f"/requests/{other}/comment", data={"comment": "доповнення"})
c.post(f"/requests/{other}/author-action", data={"action": "withdraw", "version": old_v})
check("stale page -> not applied", db.get_request(other)["status"] != "draft")

# ---------- адмін: «На погодженні» не додає фіндиректора, якщо сума нижче порогу
app_settings.set("cfo_mode", "threshold", "t"); app_settings.set("cfo_threshold_usd", 1000, "t")
rates.to_usd = lambda amount, cur: (amount / 40 if amount is not None else None, "2026-10-15")
c.post("/requests/new", data={**FORM, "amount": "100", "action": "submit"})
small = db.last_id()
check("small request: no cfo step", db.get_request(small)["cfo_step"] is False)
login("admin@ftpua.com", ["g-a"])
flush()
c.post(f"/requests/{small}/admin-status", data={"status": "rework", "comment": "тест", "version": version(small)})
c.post(f"/requests/{small}/admin-status", data={"status": "approval", "comment": "назад", "version": version(small)})
req = db.get_request(small)
mails = flush()
check("admin -> 'На погодженні': cfo not added, not notified", req["cfo_step"] is False
      and "cfo@ftpua.com" not in {t for t, s in mails} and "acc@ftpua.com" in {t for t, s in mails})
c.post(f"/requests/{small}/admin-status", data={"status": "cfo", "comment": "до фіндиректора", "version": version(small)})
check("admin -> 'Погодження фіндиректора': cfo notified", db.get_request(small)["cfo_step"] is True
      and "cfo@ftpua.com" in {t for t, s in flush()})
print("ALL OK" if ok else "SOME FAILED")
