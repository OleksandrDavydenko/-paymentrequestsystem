import io, json, os, sys, tempfile
sys.path.insert(0, os.getcwd())
tmp = tempfile.mkdtemp()
os.environ.update(UPLOAD_DIR=tmp, APP_SETTINGS_FILE=os.path.join(tmp, "s.json"), USER_PREFS_FILE=os.path.join(tmp, "p.json"),
                  USER_DIRECTORY_FILE=os.path.join(tmp, "d.json"), ORG_STRUCTURE_FILE=os.path.join(tmp, "o.json"),
                  GROUP_ADMIN="g-a", GROUP_INITIATOR="g-i", GROUP_ACC_RESIDENT="g-r", GROUP_CFO="g-f", ADMIN_EMAILS="")
# Старий формат оргструктури (heads) — має мігрувати
json.dump({"users": {}, "heads": {"old@ftpua.com": {"email": "old@ftpua.com", "name": "Old", "departments": ["ADM", "IT"]}}},
          open(os.environ["ORG_STRUCTURE_FILE"], "w", encoding="utf-8"))
import app as m
import user_budget; user_budget.get = lambda oid: {"direct": True, "all_departments": True, "departments": []}
import mailer, notifications, app_settings, org_structure, user_directory, user_prefs
import workflow as wf
import storage as db

ok = True
def check(label, cond):
    global ok
    ok &= bool(cond)
    print(("PASS " if cond else "FAIL ") + label)

sent = []
mailer.transport = lambda cfg, to, subject, html: sent.append({"to": to, "subject": subject, "html": html})
notifications.role_members = lambda role: [{"email": e, "name": e} for e in {"acc_resident": ["res@ftpua.com"], "cfo": ["cfo@ftpua.com"]}.get(role, [])]
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
P = lambda e: {"id": e, "email": e, "name": e.split("@")[0].title()}
BASE = {"pay_date": "2026-10-20", "counterparty_id": "1", "organization_id": "1", "expense_code": "01.001",
        "currency": "UAH", "amount": "500", "payment_form": "bank"}
st = lambda rid: db.get_request(rid)["status"]
for k, v in {"mail_enabled": True, "mail_sender": "noreply@ftpua.com"}.items():
    app_settings.set(k, v, "t")

# ---------- оргструктура
check("migration: old heads -> departments", org_structure.head_of("ADM")["id"] == "old@ftpua.com"
      and org_structure.head_departments("old@ftpua.com") == ["ADM", "IT"]
      and "heads" not in json.load(open(os.environ["ORG_STRUCTURE_FILE"], encoding="utf-8")))
org_structure.set_department("i@ftpua.com", "i@ftpua.com", "I", "LOG", "t")
org_structure.set_department_staff("LOG", P("a@ftpua.com"), [], "t")
org_structure.set_department_staff("LOG", P("b@ftpua.com"), [P("dep@ftpua.com"), P("b@ftpua.com")], "t")
check("one head per department: new head replaces old", org_structure.head_of("LOG")["id"] == "b@ftpua.com"
      and org_structure.head_departments("a@ftpua.com") == [])
check("head removed from viewers automatically", [v["id"] for v in org_structure.staff("LOG")["viewers"]] == ["dep@ftpua.com"])
org_structure.set_department_staff("SAL", P("b@ftpua.com"), [], "t")
check("one person heads several departments", org_structure.head_departments("b@ftpua.com") == ["LOG", "SAL"])
check("viewer departments", org_structure.viewer_departments("dep@ftpua.com") == ["LOG"])

# ---------- керівник погоджує; зміна керівника передає заявку
app_settings.set("dept_head_mode", "approve", "t"); app_settings.set("approval_mode", "sequential", "t")
login("i@ftpua.com", ["g-i"]); c.post("/requests/new", data={**BASE, "action": "submit"}); rid = db.last_id()
check("request waits for head b", st(rid) == "dept_head")
check("head b (no Azure groups) gets the mail", "b@ftpua.com" in [x["to"] for x in flush()])
org_structure.set_department_staff("LOG", P("a@ftpua.com"), [P("dep@ftpua.com")], "t")
login("b@ftpua.com", [])
c.post(f"/requests/{rid}/action", data={"action": "approve"})
check("old head can no longer approve", st(rid) == "dept_head")
login("a@ftpua.com", [])
check("new head sees it in queue", db.get_request(rid)["number"] in c.get("/approvals").get_data(as_text=True))
c.post(f"/requests/{rid}/action", data={"action": "approve"})
check("new head approves -> accountant", st(rid) == "accountant")
flush()

# ---------- «бачить заявки відділу» (без груп Azure)
login("dep@ftpua.com", [])
check("viewer without Azure groups can log in -> /department", c.get("/").headers["Location"].endswith("/department"))
page = c.get("/department").get_data(as_text=True)
check("viewer sees department request", db.get_request(rid)["number"] in page)
check("viewer has no approvals menu", "На погодження" not in page)
card = c.get(f"/requests/{rid}")
check("viewer opens card, no decision buttons", card.status_code == 200 and 'value="approve"' not in card.get_data(as_text=True))
c.post(f"/requests/{rid}/comment", data={"comment": "Перевірив, ок"})
h = db.get_request(rid)["history"][-1]
check("viewer can comment (role dept_viewer)", h["action"] == "comment" and h["role"] == "dept_viewer")
r = c.post(f"/requests/{rid}/files", data={"files": (io.BytesIO(b"%PDF-1"), "a.pdf")}, content_type="multipart/form-data")
check("viewer cannot upload files", r.status_code == 403)
login("i@ftpua.com", ["g-i"]); c.post("/requests/new", data={**BASE, "action": "save"}); draft = db.last_id()
login("dep@ftpua.com", [])
check("viewer does not see drafts", c.get(f"/requests/{draft}").status_code == 404)
org_structure.set_department("s@ftpua.com", "s@ftpua.com", "S", "SAL", "t")
login("s@ftpua.com", ["g-i"]); c.post("/requests/new", data={**BASE, "action": "submit"}); sal = db.last_id()
login("dep@ftpua.com", [])
check("viewer does not see other departments", c.get(f"/requests/{sal}").status_code == 404)
flush()

# ---------- учасники
for e in ("w@ftpua.com", "res@ftpua.com", "i@ftpua.com", "s@ftpua.com"):
    user_directory.remember([P(e)], active=True)
user_directory.remember([P("norole@ftpua.com")], active=False)
login("i@ftpua.com", ["g-i"])
found = c.get("/users/search?q=ftpua").get_json()["users"]
check("search: only active users, not me", {"w@ftpua.com", "res@ftpua.com"} <= {u["email"] for u in found}
      and "norole@ftpua.com" not in {u["email"] for u in found} and "i@ftpua.com" not in {u["email"] for u in found})
check("search needs 2+ chars", c.get("/users/search?q=w").get_json()["users"] == [])
add = lambda rid_, email: c.post(f"/requests/{rid_}/participants", data={"action": "add", "email": email})
add(draft, "w@ftpua.com")
r = db.get_request(draft)
check("author adds participant", [p["email"] for p in r["participants"]] == ["w@ftpua.com"]
      and r["history"][-1]["action"] == "participant_add")
msgs = flush()
check("only the added participant gets 'вас додано'", [x["to"] for x in msgs] == ["w@ftpua.com"] and "вас додано учасником" in msgs[0]["subject"])
add(draft, "w@ftpua.com"); add(draft, "i@ftpua.com"); add(draft, "norole@ftpua.com"); add(draft, "ghost@x.com")
check("no duplicates, no author, no non-system users", [p["email"] for p in db.get_request(draft)["participants"]] == ["w@ftpua.com"])
check("no mails for rejected adds", flush() == [])
login("res@ftpua.com", ["g-r"])
check("non-author cannot add participants (403)", add(draft, "s@ftpua.com").status_code in (403, 404))
login("od@ftpua.com", ["g-a"])
add(draft, "s@ftpua.com")
check("admin can add participants", "s@ftpua.com" in [p["email"] for p in db.get_request(draft)["participants"]])
flush()

login("w@ftpua.com", [])  # учасник без жодної ролі не має доступу до системи взагалі
check("participant without any role: no access", c.get(f"/requests/{draft}").status_code == 403)
login("w@ftpua.com", ["g-r"])
check("participant sees draft", c.get(f"/requests/{draft}").status_code == 200)
c.post(f"/requests/{draft}/comment", data={"comment": "Додам рахунок"})
check("participant comments as participant", db.get_request(draft)["history"][-1]["role"] == "participant")
r = c.post(f"/requests/{draft}/files", data={"files": (io.BytesIO(b"%PDF-1"), "invoice.pdf")}, content_type="multipart/form-data")
check("participant uploads file", any(a["name"] == "invoice.pdf" for a in db.get_request(draft)["attachments"]))
before = db.get_request(draft)["amount"]
c.post(f"/requests/{draft}", data={**BASE, "amount": "999", "action": "save"})
check("participant cannot edit", db.get_request(draft)["amount"] == before)
c.post(f"/requests/{draft}/action", data={"action": "submit"})
check("participant cannot submit", st(draft) == "draft")
check("participant tab lists request", db.get_request(draft)["number"] in c.get("/requests?view=participant").get_data(as_text=True))
check("participant (no initiator) sees 'Мої заявки' menu", "Мої заявки" in c.get("/approvals").get_data(as_text=True))
flush()

# події автора доходять до учасників
login("i@ftpua.com", ["g-i"]); c.post(f"/requests/{draft}/action", data={"action": "submit"}); flush()
user_prefs.save("s@ftpua.com", True, {**{k: d for k, (_, _, d) in user_prefs.EVENTS.items()}, "my_rework": False})
login("a@ftpua.com", []); c.post(f"/requests/{draft}/action", data={"action": "rework", "comment": "Уточніть суму"})
to = sorted(x["to"] for x in flush())
check("participants get author events (prefs respected)", to == ["i@ftpua.com", "w@ftpua.com"])
mail_w = [x for x in sent]  # already flushed; check reason text via new event
login("i@ftpua.com", ["g-i"])
c.post(f"/requests/{draft}/participants", data={"action": "remove", "email": "w@ftpua.com"})
check("author removes participant (history)", "w@ftpua.com" not in [p["email"] for p in db.get_request(draft)["participants"]]
      and db.get_request(draft)["history"][-1]["action"] == "participant_remove")
login("w@ftpua.com", ["g-r"])
check("removed participant: still sees as accountant role (all requests)", c.get(f"/requests/{draft}").status_code == 200)
login("w2@ftpua.com", ["g-i"])
check("unrelated initiator cannot see draft", c.get(f"/requests/{draft}").status_code == 404)
flush()

# ---------- нова заявка відділу (dept_new)
app_settings.set("dept_head_mode", "view", "t")
user_prefs.save("dep@ftpua.com", True, {**{k: d for k, (_, _, d) in user_prefs.EVENTS.items()}, "dept_new": True})
login("i@ftpua.com", ["g-i"]); c.post("/requests/new", data={**BASE, "action": "submit"})
to = [x["to"] for x in flush()]
check("dept_new only for those who enabled it (viewer yes, head default off)", "dep@ftpua.com" in to and "a@ftpua.com" not in to)
login("dep@ftpua.com", [])
page = c.get("/settings/notifications").get_data(as_text=True)
check("prefs page: department group + participant group", "Заявки відділу" in page and "де я учасник" in page)

# ---------- адмінка
login("od@ftpua.com", ["g-a"])
adm = admin_page()
check("admin: departments section removed", 'id="departments"' not in adm and "viewers-dialog" not in adm)
print("ALL OK" if ok else "SOME FAILED")
