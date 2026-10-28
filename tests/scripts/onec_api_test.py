"""API для обробки 1С «Платіжки із заявок PayDesk»: ключ (лише хеш, показується один раз), увімкнення, IP; заявки
«До оплати» у форматі платіжки; відмітки 1С «створено» / «помилка» / «скинуто» — в історії й у картці заявки."""
import os, sys, tempfile
sys.path.insert(0, os.getcwd())
tmp = tempfile.mkdtemp()
os.environ.update(UPLOAD_DIR=tmp, ADMIN_EMAILS="adm@ftpua.com")
import app as m
import app_settings, onec_api, settings_log, user_roles
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
user_roles.set_roles("res@ftpua.com", "res@ftpua.com", "RES", ["acc_resident"], "t")

# ---------- ключ і доступ
check("off by default", c.get("/api/1c/v1/ping").status_code == 403)
login("adm@ftpua.com")
html = c.get("/admin/settings?tab=onec_api").get_data(as_text=True)
check("settings tab", "API для обробки 1С" in html and "ще не створено" in html and 'name="onec_api_enabled"' in html)
html = c.post("/admin/onec-api/key").get_data(as_text=True)
key = html.split('id="api-key-value">')[1].split("<")[0]
check("key shown once, only hash stored", key.startswith("pd1c_") and len(key) > 30
      and app_settings.get("onec_api_key_hash") == onec_api._hash(key) and key not in str(app_settings.get("onec_api_key_info")))
check("key not in the journal", all(key not in str(e) for e in settings_log.query(50)))
check("key not shown again", key not in c.get("/admin/settings?tab=onec_api").get_data(as_text=True))
login("res@ftpua.com")
check("only admin creates keys", c.post("/admin/onec-api/key").status_code in (302, 403))
H = {"X-Api-Key": key}
check("disabled -> 403 even with the key", c.get("/api/1c/v1/ping", headers=H).status_code == 403)
login("adm@ftpua.com")
c.post("/admin/settings", data={"tab": "onec_api", "onec_api_enabled": "1", "onec_api_ips": ""})
check("enabled", app_settings.get("onec_api_enabled") is True)
r = c.get("/api/1c/v1/ping", headers=H)
check("ping with the key", r.status_code == 200 and r.get_json()["ok"] and r.get_json()["api"] == 1)
check("wrong key -> 403 with text", c.get("/api/1c/v1/ping", headers={"X-Api-Key": "x"}).get_json()["error"].startswith("Невірний ключ"))
check("no CSRF needed for POST", c.post("/api/1c/v1/payment-orders/15/failed", headers=H, json={"message": "t"}).status_code == 200)
c.post("/api/1c/v1/payment-orders/15/reset", headers=H, json={})
app_settings.set("onec_api_ips", "10.1.1.1", "t")
check("IP not allowed -> 403", c.get("/api/1c/v1/ping", headers=H).status_code == 403)
app_settings.set("onec_api_ips", "127.0.0.1, 10.1.1.1", "t")
check("IP allowed", c.get("/api/1c/v1/ping", headers=H).status_code == 200)

# ---------- платіжки
items = c.get("/api/1c/v1/payment-orders", headers=H).get_json()["items"]
ids = [i["request_id"] for i in items]
check("new: requests «До оплати»", ids and all(db.get_request(i)["status"] == "to_pay" for i in ids))
o = next(i for i in items if i["request_id"] == 15)
check("order fields", o["marker"] == "#15" and o["amount"] == "6180.00" and o["currency"] == "UAH"
      and o["counterparty"]["name"] == "ПрАТ «Київстар»" and o["counterparty"]["edrpou"] == "21673832"
      and o["expense_item"]["code"] and o["purpose"].endswith("#15") and o["onec_state"] == "new"
      and o["url"].endswith("/requests/15") and o["organization"]["code"])
check("payment kind and author's 1C employee", o["payment_kind"] in ("contractor", "indirect", None)
      and o["author"]["email"] == "i.petrenko@ftpua.com" and o["author"]["employee"]["guid"] == "EMP-IP"
      and o["author"]["employee"]["name"] == "Петренко Ірина")
check("bank / cash filter", all(i["payment_form"] == "bank" for i in c.get("/api/1c/v1/payment-orders?form=bank", headers=H).get_json()["items"])
      and all(i["payment_form"] == "cash" for i in c.get("/api/1c/v1/payment-orders?form=cash", headers=H).get_json()["items"]))
check("1C ref from SQL GUID", onec_api.ref_1c("0123456789ABCDEF0123456789ABCDEF") == "89abcdef-4567-0123-0123-456789abcdef"
      and onec_api.ref_1c("2") is None)

r = c.post("/api/1c/v1/payment-orders/15/created", headers=H,
           json={"doc_ref": "11111111-2222-3333-4444-555555555555", "doc_number": "ПП-000123", "doc_date": "2026-10-27",
                 "doc_type": "ПлатежноеПоручениеИсходящее", "user": "Бондар"})
req = db.get_request(15)
check("created: mark, history", r.status_code == 200 and req["onec_order"]["doc_number"] == "ПП-000123"
      and req["history"][-1]["action"] == "onec_order" and "ПП-000123" in req["history"][-1]["comment"]
      and req["history"][-1]["user_name"] == "1С: Бондар" and req["status"] == "to_pay")
n = len(req["history"])
c.post("/api/1c/v1/payment-orders/15/created", headers=H,
       json={"doc_ref": "11111111-2222-3333-4444-555555555555", "doc_number": "ПП-000123"})
check("same document again: idempotent", len(db.get_request(15)["history"]) == n)
r = c.post("/api/1c/v1/payment-orders/15/created", headers=H, json={"doc_number": "ПП-000999"})
check("another document -> 409 without replace", r.status_code == 409 and db.get_request(15)["onec_order"]["doc_number"] == "ПП-000123")
check("failed after created -> 409", c.post("/api/1c/v1/payment-orders/15/failed", headers=H, json={"message": "x"}).status_code == 409)
check("created is not in «new»", 15 not in [i["request_id"] for i in c.get("/api/1c/v1/payment-orders", headers=H).get_json()["items"]]
      and 15 in [i["request_id"] for i in c.get("/api/1c/v1/payment-orders?state=created", headers=H).get_json()["items"]])
check("doc_number required", c.post("/api/1c/v1/payment-orders/16/created", headers=H, json={}).status_code == 400)
check("draft / unknown request -> 404", c.get("/api/1c/v1/payment-orders/99999", headers=H).status_code == 404)

c.post("/api/1c/v1/payment-orders/16/failed", headers=H, json={"message": "У контрагента немає банківського рахунку"})
check("failed: mark + history, still «new» for retry list as failed", db.get_request(16)["onec_order"]["state"] == "failed"
      and 16 in [i["request_id"] for i in c.get("/api/1c/v1/payment-orders?state=failed", headers=H).get_json()["items"]])
c.post("/api/1c/v1/payment-orders/16/created", headers=H, json={"doc_number": "ПП-000124"})
check("created after failed", db.get_request(16)["onec_order"]["state"] == "created")
c.post("/api/1c/v1/payment-orders/16/reset", headers=H, json={"reason": "документ видалено"})
check("reset: back to new, history", "onec_order" not in db.get_request(16)
      and db.get_request(16)["history"][-1]["action"] == "onec_order_reset")

# ---------- картка й «До оплати»
login("res@ftpua.com")
html = c.get("/requests/15").get_data(as_text=True)
check("card: created in 1C", 'id="onec-order"' in html and "Платіжку створено в 1С: № ПП-000123" in html)
check("to-pay list: 1C document", "1С: платіжка № ПП-000123" in c.get("/to-pay").get_data(as_text=True))
c.post("/api/1c/v1/payment-orders/16/failed", headers=H, json={"message": "Немає рахунку"})
check("card: 1C error", "1С не створила платіжку:</strong> Немає рахунку" in c.get("/requests/16").get_data(as_text=True))
html = c.get("/requests/15").get_data(as_text=True)
check("history entry visible", "Платіжку створено в 1С" in html)

# ---------- заявку повернули після створення платіжки -> «cancelled»
req = db.get_request(15); req["status"] = "rework"; db.save_request(req)
check("cancelled: order created, request no longer to pay",
      15 in [i["request_id"] for i in c.get("/api/1c/v1/payment-orders?state=cancelled", headers=H).get_json()["items"]])
check("created for a request not «До оплати» -> 409",
      c.post("/api/1c/v1/payment-orders/15/created", headers=H, json={"doc_number": "X", "replace": True}).status_code == 409)

# ---------- перевірка документів: заявки з прив'язаними платежами 1С
import payments
guid = "0123456789ABCDEF0123456789ABCDEF"
payments.store().replace_docs([payments.parse({
    "PaymentGUID": guid, "LineNo": 1, "DocumentKind": "Cash", "DocumentNumber": "CR104495", "PaymentDate": "2026-10-27",
    "Amount": 6180, "Currency": "UAH", "Purpose": "#16 оплата", "IsPosted": True, "IsDeleted": False, "RowVersion": 1})])
payments.store().add_link(guid, 16, "manual", "t")
r = c.get("/api/1c/v1/payment-checks?since=2026-10-01", headers=H)
items = r.get_json()["items"]
it = next((i for i in items if i["request_id"] == 16), None)
check("checks: request with its linked 1C document", r.status_code == 200 and it and it["payments"] == [
    {"doc_ref": "89abcdef-4567-0123-0123-456789abcdef", "guid": guid, "number": "CR104495", "kind": "Cash",
     "date": "2026-10-27", "active": True, "match": "manual"}] and it["counterparty"] and it["marker"] == "#16")
check("checks: to-pay requests without payments listed too",
      any(i["payments"] == [] and db.get_request(i["request_id"])["status"] == "to_pay" for i in items))
check("checks: bad date -> 400", c.get("/api/1c/v1/payment-checks?since=27.10.2026", headers=H).status_code == 400)
check("checks: default 30 days", c.get("/api/1c/v1/payment-checks", headers=H).get_json()["since"]
      == (onec_api.clock.today() - onec_api.timedelta(days=30)).isoformat())
check("checks: key required", c.get("/api/1c/v1/payment-checks").status_code == 403)
print("ALL OK" if ok else "SOME FAILED")
