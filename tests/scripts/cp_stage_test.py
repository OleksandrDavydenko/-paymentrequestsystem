"""Коли новий контрагент (якого ще немає в 1С) уже має бути в 1С: до погодження бухгалтером, до «До оплати», до
«Оплачено» (як раніше) або не вимагати. Ранні етапи — щоб звірка з платежами 1С могла сама закрити заявку."""
import os, sys, tempfile
sys.path.insert(0, os.getcwd())
tmp = tempfile.mkdtemp()
os.environ.update(UPLOAD_DIR=tmp, ADMIN_EMAILS="adm@ftpua.com")
import app as m
import user_budget; user_budget.get = lambda oid: {"direct": True, "all_departments": True, "departments": []}
import app_settings, settings_log, user_roles
import counterparties as cps
import workflow as wf
import storage as db

ok = True
def check(label, cond):
    global ok
    ok &= bool(cond); print(("PASS " if cond else "FAIL ") + label)

claims = {}
def login(email):
    claims.clear(); claims.update({"name": email.split("@")[0].upper(), "preferred_username": email, "oid": email})
m.auth._auth.get_user = lambda: dict(claims) or None
c = m.app.test_client()
user_roles.set_roles("i@ftpua.com", "i@ftpua.com", "I", ["initiator"], "t")
user_roles.set_roles("res@ftpua.com", "res@ftpua.com", "RES", ["acc_resident"], "t")
user_roles.set_roles("cfo@ftpua.com", "cfo@ftpua.com", "CFO", ["cfo"], "t")
BASE = {"pay_date": "2026-11-20", "organization_id": "1", "expense_code": "01.001", "currency": "UAH", "amount": "500",
        "payment_form": "bank"}
status = lambda rid: db.get_request(rid)["status"]
act = lambda rid, action, **kw: c.post(f"/requests/{rid}/action", data={"action": action, **kw})

def new_request(name, edrpou):
    login("i@ftpua.com")
    cp = c.post("/counterparties/new", json={"resident": "1", "edrpou": edrpou, "name": name}).get_json()["item"]
    c.post("/requests/new", data={**BASE, "counterparty_id": cp["id"], "action": "submit"})
    return db.last_id(), cp

def set_stage(stage):
    login("adm@ftpua.com")
    c.post("/admin/settings", data={"tab": "payment", "pending_cp_payment": stage, "approval_mode": "sequential",
                                    "dept_head_mode": "view"})
    return app_settings.get("pending_cp_payment") == stage

check("default: until «Оплачено» (as before)", app_settings.get("pending_cp_payment") == "block")
login("adm@ftpua.com")
html = c.get("/admin/settings?tab=payment").get_data(as_text=True)
check("settings: four stages", all(f'name="pending_cp_payment" value="{v}"' in html for v in ("accountant", "to_pay", "block", "allow"))
      and "До погодження бухгалтером" in html)

# ---------- «До погодження бухгалтером»
check("switch to accountant stage, journal", set_stage("accountant")
      and any("має бути в 1С" in (e.get("label") or "") + str(e.get("new") or "") or e["key"] == "pending_cp_payment"
              for e in settings_log.query(20)))
rid, cp = new_request("ТОВ «Новий 1»", "44556677")
check("submitted with pending counterparty", status(rid) == "accountant" and cps.is_pending(cp["id"]))
login("res@ftpua.com")
html = c.get(f"/requests/{rid}").get_data(as_text=True)
flat = " ".join(html.split())
check("card: explanation, approve disabled, rework allowed", 'id="cp-approve-blocked"' in html
      and 'value="approve" class="btn btn-green" disabled>' in flat and 'value="rework" class="btn btn-orange" >' in flat)
r_ = act(rid, "approve", payment_account_id="1")
check("accountant cannot approve", status(rid) == "accountant")
c.post(f"/counterparties/{cp['id']}/link", data={"onec_id": "1", "next": f"/requests/{rid}"})
check("accountant links the counterparty to 1C", not cps.is_pending(db.get_request(rid)["counterparty_id"]))
act(rid, "approve", payment_account_id="1")
check("after link: accountant approves -> cfo", status(rid) == "cfo")

# ---------- «До переходу в «До оплати»»
check("switch to to_pay stage", set_stage("to_pay"))
rid, cp = new_request("ТОВ «Новий 2»", "12345678")
login("res@ftpua.com")
check("card: accountant may approve (cfo is next)", 'id="cp-approve-blocked"' not in c.get(f"/requests/{rid}").get_data(as_text=True))
act(rid, "approve", payment_account_id="1")
check("accountant approves -> cfo", status(rid) == "cfo")
login("cfo@ftpua.com")
check("card: cfo sees the block", 'id="cp-approve-blocked"' in c.get(f"/requests/{rid}").get_data(as_text=True))
act(rid, "approve")
check("cfo cannot move it to «До оплати»", status(rid) == "cfo")
act(rid, "rework", comment="Контрагента немає в 1С")
check("cfo may still return it", status(rid) == "rework")

# ---------- правила для паралельного режиму й без фіндиректора (чисті функції)
req = {"status": "approval", "route_mode": "parallel", "cfo_step": True, "accountant_type": "acc_resident", "approvals": {}}
check("parallel: first approval does not lead to «До оплати»", not wf.approve_leads_to_pay(req, "cfo"))
req["approvals"] = {"acc_resident": {"at": None}}
check("parallel: last approval leads to «До оплати»", wf.approve_leads_to_pay(req, "cfo") and wf.cp_stage_blocks(req, "cfo", "to_pay")
      and not wf.cp_stage_blocks(req, "cfo", "block"))
req = {"status": "accountant", "cfo_step": False, "accountant_type": "acc_resident"}
check("no cfo step: accountant approval leads to «До оплати»", wf.cp_stage_blocks(req, "acc_resident", "to_pay"))

# ---------- «До «Оплачено»» — як раніше: доходить до «До оплати», «Оплачено» заблоковано
check("switch back to block", set_stage("block"))
rid, cp = new_request("ТОВ «Новий 3»", "22334457")
login("res@ftpua.com"); act(rid, "approve", payment_account_id="1")
login("cfo@ftpua.com"); act(rid, "approve")
check("block: reaches «До оплати» with a pending counterparty", status(rid) == "to_pay")
login("res@ftpua.com"); act(rid, "pay")
check("block: «Оплачено» refused", status(rid) == "to_pay")

# ---------- звірка з 1С: підказка в налаштуваннях
app_settings.set("pay_mode_acc_resident", "auto", "t")
login("adm@ftpua.com")
html = c.get("/admin/settings?tab=reconcile").get_data(as_text=True)
check("reconcile settings warn: auto «Оплачено» while counterparty is required only before «Оплачено»",
      'callout orange cp-stage-note' in html and "краще вимагати" in html)
set_stage("to_pay")
html = c.get("/admin/settings?tab=reconcile").get_data(as_text=True)
check("reconcile settings: ok with to_pay stage", 'callout blue cp-stage-note' in html)
print("ALL OK" if ok else "SOME FAILED")
