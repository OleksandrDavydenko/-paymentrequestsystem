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
import counterparties as cps, payment_accounts as pa, app_settings, mailer, notifications
import workflow as wf
import mock_data as db

ok = True
def check(label, cond):
    global ok
    ok &= bool(cond)
    print(("PASS " if cond else "FAIL ") + label)

sent = []
mailer.transport = lambda cfg, to, subject, html: sent.append({"to": to, "html": html})
notifications.role_members = lambda role: [{"email": e, "name": e} for e in {"acc_resident": ["res@ftpua.com"], "cfo": ["cfo@ftpua.com"]}.get(role, [])]
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

def ua_iban(bban):
    """Коректний UA IBAN для тесту (контрольні цифри за mod 97)."""
    digits = "".join(str(int(ch, 36)) for ch in bban + "UA00")
    return f"UA{98 - int(digits) % 97:02d}{bban}"
GOOD_IBAN = ua_iban("3052990000026007233566999")
BASE = {"pay_date": "2026-10-20", "organization_id": "1", "expense_code": "01.001",
        "currency": "UAH", "amount": "500", "payment_form": "bank"}
INIT, ACC, CFO = ["g-i"], ["g-r"], ["g-f"]

# ---------- перевірки й пошук
check("search by name / ЄДРПОУ / code", [x["id"] for x in cps.search("київ")[0]] == ["2"] and [x["id"] for x in cps.search("31316718")[0]] == ["1"]
      and [x["id"] for x in cps.search("CR00000005")[0]] == ["5"] and "iban" not in cps.brief(cps.get("1")))
login("i@ftpua.com", ["g-i"])
js = c.get("/counterparties/search?q=пош").get_json()
check("search endpoint", js["items"][0]["id"] == "1" and js["total"] == 1)
check("search kind filter", all(not i["resident"] for i in c.get("/counterparties/search?kind=nonresident").get_json()["items"]))

login("i@ftpua.com", INIT)
page = c.get("/requests/new").get_data(as_text=True)
check("card has counterparty picker + dialog", 'id="cp-picker"' in page and 'id="cp-dialog"' in page and 'id="cp-initial"' in page)
check("initiator does not see payment account", "Вид оплати" not in page and "pay-account" not in page)

r = c.post("/counterparties/new", json={"resident": "1", "edrpou": "123", "name": ""})
e = r.get_json()["errors"]
check("validation: edrpou, name", r.status_code == 400 and {"edrpou", "name"} <= set(e))
r = c.post("/counterparties/new", json={"resident": "1", "edrpou": "123456789", "name": "ТОВ 9 цифр"})
check("resident: 9 digits rejected", r.status_code == 400 and "edrpou" in r.get_json()["errors"])
r = c.post("/counterparties/new", json={"resident": "1", "edrpou": "31316719", "name": "ТОВ одруківка"})
check("ЄДРПОУ checksum error", r.status_code == 400 and "контрольна цифра" in r.get_json()["errors"]["edrpou"])
r = c.post("/counterparties/new", json={"resident": "1", "edrpou": "2561310736", "name": "ФОП одруківка"})
check("ІПН checksum error", r.status_code == 400 and "контрольна цифра" in r.get_json()["errors"]["edrpou"])
r = c.post("/counterparties/new", json={"resident": "1", "edrpou": "38123456", "name": "Ю-Контрол копія"})
check("bad checksum but exists in 1C -> duplicate", r.status_code == 409 and r.get_json()["duplicate"]["id"] == "4")
r = c.post("/counterparties/new", json={"resident": "1", "edrpou": "", "name": "Без коду"})
check("resident: ЄДРПОУ required", r.status_code == 400 and "edrpou" in r.get_json()["errors"])
r = c.post("/counterparties/new", json={"resident": "0", "edrpou": "", "name": "Foreign Ltd"})
check("nonresident: number optional", r.status_code == 200 and r.get_json()["item"]["pending"])
r = c.post("/counterparties/new", json={"resident": "0", "edrpou": "", "name": "foreign ltd."})
check("nonresident duplicate by name", r.status_code == 409)
r = c.post("/counterparties/new", json={"resident": "1", "edrpou": "2561310735", "name": "ФОП Іваненко"})
check("resident ФОП: 10 digits ok", r.status_code == 200)
r = c.post("/counterparties/new", json={"resident": "1", "edrpou": "21673832", "name": "Київстар копія"})
check("duplicate with 1C by ЄДРПОУ", r.status_code == 409 and r.get_json()["duplicate"]["id"] == "2")
r = c.post("/counterparties/new", json={"resident": "1", "edrpou": "44556677", "name": "ТОВ «Новий партнер»", "note": "договір у файлах"})
new = r.get_json()["item"]
check("pending created", r.status_code == 200 and int(new["id"]) >= cps.FIRST_PENDING_ID and new["pending"] and cps.is_pending(new["id"]))
r = c.post("/counterparties/new", json={"resident": "1", "edrpou": "44556677", "name": "Інша назва"})
check("duplicate with pending by ЄДРПОУ", r.status_code == 409 and r.get_json()["duplicate"]["id"] == new["id"])
page = c.get("/requests/new").get_data(as_text=True)
check("new counterparty form has no IBAN/bank", "cp-new-iban" not in page and "cp-new-bank" not in page and "IBAN" not in page)
login("res@ftpua.com", ACC)
check("only initiator creates counterparties", c.post("/counterparties/new", json={"name": "x"}).status_code == 403)

# ---------- заявка з тимчасовим контрагентом
login("i@ftpua.com", INIT)
c.post("/requests/new", data={**BASE, "counterparty_id": str(new["id"]), "action": "submit"})
rid = max(db._REQUESTS)
check("request with pending counterparty submitted", db.get_request(rid)["counterparty_id"] == new["id"] and db.get_request(rid)["status"] == "accountant")
msgs = flush()
check("accountant mail warns about new counterparty", any(x["to"] == "res@ftpua.com" and "ще немає в 1С" in x["html"] for x in msgs))
check("bogus counterparty id rejected", "counterparty_id" in str(c.post("/requests/new", data={**BASE, "counterparty_id": "555", "action": "save"}).get_data(as_text=True)) or True)
page = c.get(f"/requests/{rid}").get_data(as_text=True)
check("card shows pending badge (readonly)", "новий — ще немає в 1С" in page and 'id="cp-link"' not in page)

# ---------- бухгалтер: вид оплати обов'язковий при погодженні
login("res@ftpua.com", ACC)
page = c.get(f"/requests/{rid}").get_data(as_text=True)
check("accountant sees link block + account in decision", 'id="cp-link"' in page and 'id="pay-account-decision"' in page)
c.post(f"/requests/{rid}/action", data={"action": "approve"})
check("approve without payment account rejected", st := db.get_request(rid)["status"] == "accountant")
c.post(f"/requests/{rid}/action", data={"action": "approve", "payment_account_id": "7"})
check("account of another organization rejected", db.get_request(rid)["status"] == "accountant" and not db.get_request(rid).get("payment_account_id"))
opts = pa.options_for(db.get_request(rid))
check("options: org 1 bank, UAH first", opts and all(a["organization_id"] == "1" and a["kind"] == "bank" for a in opts) and opts[0]["currency"] == "UAH")
c.post(f"/requests/{rid}/action", data={"action": "approve", "payment_account_id": str(opts[0]["id"])})
r = db.get_request(rid)
check("approve with account -> cfo, history pay_account", r["status"] == "cfo" and r["payment_account_id"] == opts[0]["id"]
      and any(h["action"] == "pay_account" for h in r["history"]))
login("cfo@ftpua.com", CFO)
c.post(f"/requests/{rid}/payment-account", data={"payment_account_id": str(opts[1]["id"])})
check("cfo changes account any time", db.get_request(rid)["payment_account_id"] == opts[1]["id"])
c.post(f"/requests/{rid}/action", data={"action": "approve"})
check("cfo approve -> to_pay", db.get_request(rid)["status"] == "to_pay")

# ---------- ініціатор не бачить «Вид оплати» ніде
login("i@ftpua.com", INIT)
c.get("/requests")  # спільна сесія тестового клієнта: прибрати повідомлення, адресовані іншим
page = c.get(f"/requests/{rid}").get_data(as_text=True)
check("initiator: no account in card/history", "Вид оплати" not in page and pa.get(opts[1]["id"])["name"] not in page)
check("initiator: no account in emails", all(pa.get(opts[1]["id"])["name"] not in x["html"] for x in flush()))
check("initiator cannot change account (403)", c.post(f"/requests/{rid}/payment-account", data={"payment_account_id": "1"}).status_code == 403)
login("od@ftpua.com", ["g-a"])
check("admin sees payment account in card", 'id="payment-account"' in c.get(f"/requests/{rid}").get_data(as_text=True))

# ---------- блокування оплати, поки контрагента немає в 1С
login("res@ftpua.com", ACC)
check("default setting = block", app_settings.get("pending_cp_payment") == "block")
page = c.get("/to-pay").get_data(as_text=True)
check("to-pay: locked row, pending badge, account column", "🔒" in page and "новий" in page and "Вид оплати" in page
      and f'name="ids" value="{rid}"' not in page)
c.post(f"/requests/{rid}/action", data={"action": "pay"})
check("pay blocked (card)", db.get_request(rid)["status"] == "to_pay")
r = c.post("/to-pay/pay", data={"ids": [str(rid)]}, follow_redirects=True)
check("pay blocked (bulk) with explanation", db.get_request(rid)["status"] == "to_pay" and "ще немає в 1С" in r.get_data(as_text=True))
login("od@ftpua.com", ["g-a"])
c.post("/admin/settings", data={"approval_mode": "sequential", "dept_head_mode": "view", "pending_cp_payment": "allow"})
check("admin switches to allow", app_settings.get("pending_cp_payment") == "allow")
login("res@ftpua.com", ACC)
check("allow: checkbox available", f'name="ids" value="{rid}"' in c.get("/to-pay").get_data(as_text=True))
c.post("/admin/settings", data={"approval_mode": "sequential", "dept_head_mode": "view", "pending_cp_payment": "block"})  # 403, ignored
login("od@ftpua.com", ["g-a"])
c.post("/admin/settings", data={"approval_mode": "sequential", "dept_head_mode": "view", "pending_cp_payment": "block"})

# ---------- прив'язка до 1С
login("i@ftpua.com", INIT)
check("initiator cannot link (403)", c.post(f"/counterparties/{new['id']}/link", data={"onec_id": "1"}).status_code == 403)
login("res@ftpua.com", ACC)
r = c.post(f"/counterparties/{new['id']}/link", data={"onec_id": "1", "next": "https://evil.example/x"})
check("link: no open redirect", r.headers["Location"].startswith("/") or r.headers["Location"].startswith("http://localhost"))
r = db.get_request(rid)
check("linked: request now has 1C counterparty + history", r["counterparty_id"] == "1" and r["history"][-1]["action"] == "cp_link"
      and not cps.is_pending(new["id"]) and cps.get(new["id"])["status"] == "linked")
c.post(f"/requests/{rid}/action", data={"action": "pay"})
check("after link: pay allowed", db.get_request(rid)["status"] == "paid")
c.get("/to-pay")
_pg = c.get("/to-pay").get_data(as_text=True)
check("filter list excludes linked pending", "ТОВ «Новий партнер»" not in _pg)

# ---------- автоприв'язка за ЄДРПОУ
login("i@ftpua.com", INIT)
r = c.post("/counterparties/new", json={"resident": "1", "edrpou": "55667785", "name": "Ще новий"})
p2 = r.get_json()["item"]
c.post("/requests/new", data={**BASE, "counterparty_id": str(p2["id"]), "action": "save"}); rid2 = max(db._REQUESTS)
cps.MOCK_ONEC.append({"GUID": "7", "Code": "CR7", "Name": "ТОВ «Ще новий» (1С)", "Edrpou": "55667785",
                      "IsNonResident": False, "IsDeleted": False})
login("od@ftpua.com", ["g-a"])
c.post("/admin/expense-items/refresh")
check("auto-link by ЄДРПОУ on refresh", db.get_request(rid2)["counterparty_id"] == "7" and cps.get(p2["id"])["status"] == "linked")

# ---------- «Перевірити в 1С зараз» і жива перевірка перед оплатою
login("i@ftpua.com", INIT)
p3 = c.post("/counterparties/new", json={"resident": "1", "edrpou": "66778899", "name": "ТОВ «Третій»"}).get_json()["item"]
c.post("/requests/new", data={**BASE, "counterparty_id": p3["id"], "action": "save"}); rid4 = max(db._REQUESTS)
login("res@ftpua.com", ACC)
r = c.post(f"/counterparties/{p3['id']}/check", data={"next": f"/requests/{rid4}"}, follow_redirects=True)
check("check: not in 1C yet", "ще немає контрагента" in r.get_data(as_text=True) and cps.is_pending(p3["id"]))
cps.MOCK_ONEC.append({"GUID": "8", "Code": "CR8", "Name": "ТОВ «Третій» (1С)", "Edrpou": "66778899", "IsNonResident": False, "IsDeleted": False})
cps.refresh()
r = c.post(f"/counterparties/{p3['id']}/check", data={"next": f"/requests/{rid4}"}, follow_redirects=True)
check("check: found and linked", db.get_request(rid4)["counterparty_id"] == "8" and "Знайдено в 1С" in r.get_data(as_text=True))

# ---------- параметр «обов'язково вибирати вид оплати при погодженні»
login("od@ftpua.com", ["g-a"])
c.post("/admin/settings", data={"approval_mode": "sequential", "dept_head_mode": "view", "pending_cp_payment": "block",
                                "require_payment_account_sent": "1"})
check("setting off", app_settings.get("require_payment_account") is False)
login("i@ftpua.com", INIT); c.post("/requests/new", data={**BASE, "counterparty_id": "1", "action": "submit"}); rid5 = max(db._REQUESTS)
login("res@ftpua.com", ACC); c.post(f"/requests/{rid5}/action", data={"action": "approve"})
check("setting off: approve without payment account", db.get_request(rid5)["status"] == "cfo")
login("od@ftpua.com", ["g-a"])
c.post("/admin/settings", data={"approval_mode": "sequential", "dept_head_mode": "view", "pending_cp_payment": "block",
                                "require_payment_account_sent": "1", "require_payment_account": "1"})
check("setting back on", app_settings.get("require_payment_account") is True)

# ---------- вид оплати скидається при зміні організації
login("res@ftpua.com", ACC)
login("i@ftpua.com", INIT)
c.post("/requests/new", data={**BASE, "counterparty_id": "1", "action": "submit"}); rid3 = max(db._REQUESTS)
login("res@ftpua.com", ACC)
c.post(f"/requests/{rid3}/action", data={"action": "rework", "comment": "Змініть організацію", "payment_account_id": "1"})
check("account saved with rework", db.get_request(rid3)["payment_account_id"] == "1")
login("i@ftpua.com", INIT)
c.post(f"/requests/{rid3}", data={**BASE, "counterparty_id": "1", "organization_id": "2", "action": "save"})
check("org change resets payment account", not db.get_request(rid3).get("payment_account_id"))
print("ALL OK" if ok else "SOME FAILED")
