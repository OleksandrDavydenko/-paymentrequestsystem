import json, os, sys, tempfile
sys.path.insert(0, os.getcwd())
tmp = tempfile.mkdtemp()
os.environ.update(UPLOAD_DIR=tmp, APP_SETTINGS_FILE=os.path.join(tmp, "settings.json"), GROUP_ADMIN="g-a",
                  GROUP_INITIATOR="g-i", GROUP_ACC_CASH="g-c", GROUP_ACC_RESIDENT="g-r", GROUP_CFO="g-f", ADMIN_EMAILS="")
import app as m
import user_budget; user_budget.get = lambda oid: {"direct": True, "all_departments": True, "departments": []}
import app_settings
import mock_data as db

ok = True
def check(label, cond):
    global ok
    ok &= bool(cond)
    print(("PASS " if cond else "FAIL ") + label)

claims = {}
def login(email, groups):
    claims.clear(); claims.update({"name": email.split("@")[0], "preferred_username": email, "oid": email, "groups": groups})
m.auth._auth.get_user = lambda: dict(claims)
m.auth._auth.get_token_for_user = lambda s: {"access_token": "t", "scope": " ".join(s)}
import graph
graph.group_members = lambda t, g: []
c = m.app.test_client()

# Бухгалтер при погодженні вибирає «Вид оплати» — у старих тестах підставляємо перший доступний
import payment_accounts as _pa, mock_data as _md
_orig_post = c.post
def _post(url, data=None, **kw):
    if isinstance(data, dict) and data.get("action") == "approve" and "payment_account_id" not in data and url.endswith("/action"):
        _r = _md.get_request(int(url.split("/")[2]))
        _opts = _pa.options_for(_r) if _r else []
        if _opts:
            data = {**data, "payment_account_id": str(_opts[0]["id"])}
    return _orig_post(url, data=data, **kw)
c.post = _post
inq = lambda rid: f"/requests/{rid}\"" in c.get("/approvals").get_data(as_text=True)
BASE = {"pay_date": "2026-10-20", "counterparty_id": "1", "organization_id": "1", "expense_code": "01.001",
        "currency": "UAH", "amount": "500", "payment_form": "bank", "action": "submit"}

# --- налаштування
login("i@ftpua.com", ["g-i"])
check("non-admin settings -> 403", c.post("/admin/settings", data={"approval_mode": "parallel"}).status_code == 403)
c.post("/requests/new", data=BASE); seq_id = max(db._REQUESTS)
check("default sequential -> accountant", db.get_request(seq_id)["status"] == "accountant")

login("od@ftpua.com", ["g-a"])
c.post("/admin/settings", data={"approval_mode": "parallel"})
check("admin set parallel", app_settings.get("approval_mode") == "parallel")
check("saved to file with audit", json.load(open(os.environ["APP_SETTINGS_FILE"], encoding="utf-8"))["approval_mode"]["updated_by"] == "od@ftpua.com")
app_settings.reload()
check("survives reload", app_settings.get("approval_mode") == "parallel")
check("bogus mode rejected", c.post("/admin/settings", data={"approval_mode": "x"}).status_code == 302 and app_settings.get("approval_mode") == "parallel")
check("admin page shows block", "Налаштування процесу" in c.get("/admin/users").get_data(as_text=True))

# --- паралельно
login("i@ftpua.com", ["g-i"])
c.post("/requests/new", data=BASE); pid = max(db._REQUESTS)
r = db.get_request(pid)
check("parallel submit -> approval", r["status"] == "approval" and r["route_mode"] == "parallel")
login("cfo@ftpua.com", ["g-f"]); cfo_sees = inq(pid)
login("res@ftpua.com", ["g-r"]); res_sees = inq(pid)
login("cash@ftpua.com", ["g-c"]); cash_sees = inq(pid)
check("both cfo & resident accountant see it, cashier not", cfo_sees and res_sees and not cash_sees)
c.post(f"/requests/{pid}/action", data={"action": "approve"})
check("cashier cannot approve", db.get_request(pid)["approvals"] == {})

login("cfo@ftpua.com", ["g-f"])
c.post(f"/requests/{pid}/action", data={"action": "approve"})
r = db.get_request(pid)
check("cfo approves first -> still approval", r["status"] == "approval" and "cfo" in r["approvals"])
check("gone from cfo queue", not inq(pid))
check("in cfo processed", f"/requests/{pid}\"" in c.get("/approvals?view=processed").get_data(as_text=True))
c.post(f"/requests/{pid}/action", data={"action": "approve"})
check("cfo cannot approve twice", len([h for h in db.get_request(pid)["history"] if h["action"] == "approve"]) == 1)
t = c.get(f"/requests/{pid}").get_data(as_text=True)
check("card shows parallel note + pending", "Паралельне погодження" in t and "очікує:" in t)

login("res@ftpua.com", ["g-r"])
check("still in accountant queue", inq(pid))
c.post(f"/requests/{pid}/action", data={"action": "approve"})
check("both approved -> to_pay", db.get_request(pid)["status"] == "to_pay")

# rework resets approvals; resubmission needs both again
login("i@ftpua.com", ["g-i"])
c.post("/requests/new", data=BASE); pid2 = max(db._REQUESTS)
login("res@ftpua.com", ["g-r"]); c.post(f"/requests/{pid2}/action", data={"action": "approve"})
login("cfo@ftpua.com", ["g-f"]); c.post(f"/requests/{pid2}/action", data={"action": "rework", "comment": "Додайте акт"})
r = db.get_request(pid2)
check("rework clears approvals", r["status"] == "rework" and r["approvals"] == {})
check("route bar marks cfo returned", ("Фіндиректор", "returned") in m.wf.stage_states(r))
login("i@ftpua.com", ["g-i"]); c.post(f"/requests/{pid2}", data=BASE)
r = db.get_request(pid2)
check("resubmit -> approval, both pending", r["status"] == "approval" and m.wf.pending_roles(r) == ["acc_resident", "cfo"])

# one user with both roles approves twice (per role)
login("both@ftpua.com", ["g-r", "g-f"])
c.post(f"/requests/{pid2}/action", data={"action": "approve"})
check("first approval as accountant", db.get_request(pid2)["status"] == "approval")
c.post(f"/requests/{pid2}/action", data={"action": "approve"})
r = db.get_request(pid2)
check("second approval as cfo -> to_pay", r["status"] == "to_pay" and
      [h["role"] for h in r["history"] if h["action"] == "approve"][-2:] == ["acc_resident", "cfo"])

# sequential request submitted before switch keeps its route
login("res@ftpua.com", ["g-r"]); c.post(f"/requests/{seq_id}/action", data={"action": "approve"})
check("old sequential request -> cfo (not approval)", db.get_request(seq_id)["status"] == "cfo")

# --- коментарі
for who, groups, rid in [("i@ftpua.com", ["g-i"], seq_id), ("cfo@ftpua.com", ["g-f"], seq_id), ("od@ftpua.com", ["g-a"], 1)]:
    login(who, groups)
    before = db.get_request(rid)["status"]
    c.post(f"/requests/{rid}/comment", data={"comment": f"Питання від {who}\nдругий рядок"})
    r = db.get_request(rid); h = r["history"][-1]
    check(f"comment by {who}: status unchanged ({before})", r["status"] == before and h["action"] == "comment"
          and h["from_status"] == h["to_status"] == before and "\n" in h["comment"])
check("comment on paid request works", db.get_request(1)["status"] == "paid")
login("i@ftpua.com", ["g-i"])
n = len(db.get_request(seq_id)["history"])
c.post(f"/requests/{seq_id}/comment", data={"comment": "   "})
check("empty comment rejected", len(db.get_request(seq_id)["history"]) == n)
c.post(f"/requests/{seq_id}/comment", data={"comment": "x" * 2001})
check("too long rejected", len(db.get_request(seq_id)["history"]) == n)
check("stranger -> 404", c.post("/requests/2/comment", data={"comment": "hi"}).status_code == 404)
t = c.get(f"/requests/{seq_id}").get_data(as_text=True)
check("comment visible in history with label", "Коментар</span>" in t and "Питання від cfo@ftpua.com" in t)
print("ALL OK" if ok else "SOME FAILED")
