"""Номер заявки для людей: префікс і нулі з адмінки (лише показ), розпізнавання номера в будь-якому вигляді — у
призначенні платежу (#123, PD000123, PD-123, pd123, кирилична «Р», колишній префікс), у пошуку й у ручній прив'язці;
API для 1С віддає number_label, marker, markers і prefixes."""
import itertools, os, sys, tempfile
sys.path.insert(0, os.getcwd())
tmp = tempfile.mkdtemp()
os.environ.update(UPLOAD_DIR=tmp, ADMIN_EMAILS="adm@ftpua.com")
import app as m
import app_settings, notifications, onec_api, payment_match, payments, request_numbers as rn
import storage as db
import workflow as wf

ok = True
def check(label, cond):
    global ok
    ok &= bool(cond); print(("PASS " if cond else "FAIL ") + label)

claims = {"name": "Adm", "preferred_username": "adm@ftpua.com", "oid": "adm@ftpua.com"}
m.auth._auth.get_user = lambda: dict(claims)
c = m.app.test_client()
page = lambda url: c.get(url).get_data(as_text=True)
N = lambda *nums: [f"{n:09d}" for n in nums]

# ---------- за замовчуванням — як було
check("default: 000000123 and #123", rn.label("000000123") == "000000123" and rn.marker("000000123") == "#123"
      and rn.label(None) == "" and rn.all_prefixes() == [])
check("default: only #-numbers recognised", payment_match.numbers_in("оплата #5, # 12 і #0007, рах. 123") == N(5, 7, 12))

# ---------- адмінка: перевірка префікса
KEEP = {"doc_extensions": app_settings.get("doc_extensions"), "kind_question": "1" if app_settings.get("kind_question") else "",
        "req_lines_required": "1" if app_settings.get("req_lines_required") else ""}  # решта вкладки — як була
def save(**form):
    return c.post("/admin/settings", data={"tab": "request", **KEEP, **form}, follow_redirects=True).get_data(as_text=True)
for bad in ("P2", "ПД", "ABCDEFG", "P-D"):
    html = save(request_prefix=bad, request_digits="6")
    check(f"prefix {bad!r} refused", "до 6 латинських літер" in html and rn.prefix() == "")
save(request_prefix="pd", request_digits="6")
check("saved as PD, 6 digits", rn.prefix() == "PD" and rn.digits() == 6)
html = page("/admin/settings?tab=request")
check("settings page: block and example", 'name="request_prefix"' in html and 'value="PD"' in html and "PD000123" in html)

# ---------- показ
check("label / marker with prefix", rn.label("000000123") == "PD000123" and rn.marker(123) == "PD000123"
      and rn.label(1234567) == "PD1234567")
cases = {"#123": [123], "# 123": [123], "#000123": [123], "#PD123": [123], "#PD-000123": [123], "#pd 123": [123],
         "PD123": [123], "PD000123": [123], "PD-123": [123], "PD 123": [123], "pd123": [123], "оплата РD000123": [123],
         "PD12, PD-13 і #14": [12, 13, 14], "UPD123": [], "PD1234": [1234], "рах. 123 від 01.10": [],
         "PD1234567890": [], "CR112522": []}
bad = {t: payment_match.numbers_in(t) for t, want in cases.items() if payment_match.numbers_in(t) != N(*want)}
check("numbers_in: every form" + (f" (wrong: {bad})" if bad else ""), not bad)
check("parse_query", rn.parse_query("pd123") == N(123)[0] and rn.parse_query("#000123") == N(123)[0]
      and rn.parse_query("PD-000123") == N(123)[0] and rn.parse_query("123") is None and rn.parse_query("pd") is None)

# демо-заявка для показу й пошуку
req = db.get_request(sorted(db.payment_candidate_ids(payments.clock.today()))[0])
num, lbl = req["number"], rn.label(req["number"])
card = page(f"/requests/{req['id']}")
check("card title shows the label", f"Заявка № {lbl}" in card)
lst = page("/admin/requests?period=all")
check("list shows the label", f">{lbl}</a>" in lst)
tg = notifications.telegram_text({"req": req, "headline": "Тест", "actor": {}, "status": "", "comment": "", "event": "x"})
check("telegram text shows the label", f"Заявка № {lbl}" in tg)

# ---------- пошук
for q in (lbl, f"PD{int(num)}", f"#{int(num)}", f"pd-{int(num)}", str(int(num))):
    check(f"search {q!r} finds the request", f"/requests/{req['id']}\"" in page(f"/admin/requests?period=all&q={q.replace('#', '%23')}"))
other = f"PD{int(num) + 1000}"
check("search by another label does not find it", f"/requests/{req['id']}\"" not in page(f"/admin/requests?period=all&q={other}"))

# ---------- зміна префікса: колишній розпізнається далі
save(request_prefix="ZO", request_digits="6")
check("prefix history", rn.prefix() == "ZO" and rn.all_prefixes() == ["ZO", "PD"]
      and app_settings.get("request_prefix_history") == ["PD"])
check("old prefix still recognised", payment_match.numbers_in("PD000123 ZO-124") == N(123, 124))
check("markers: new, old #, old prefix", {"ZO000123", "#123", "PD123", "PD000123"} <= set(rn.markers(123)))
check("settings page lists old prefixes", "Колишні префікси теж розпізнаються" in page("/admin/settings?tab=request"))
save(request_prefix="PD", request_digits="6")
check("back to PD: history without duplicates", rn.all_prefixes() == ["PD", "ZO"])

# ---------- звірка: платіж із PD000123 у призначенні прив'язується й закриває заявку
for role in wf.ACCOUNTANT_ROLES:
    app_settings.set(f"pay_mode_{role}", "auto", "t")
payments.sync()
taken = {rid for v in payments.store().all_links().values() for rid in v}
free = [db.get_request(i) for i in db.payment_candidate_ids(payments.clock.today()) if i not in taken]
a, b = [r for r in free if r["status"] == "to_pay"][:2]
rv = itertools.count(9500)
def add_doc(guid, r, purpose):
    payments.MOCK_ROWS.append({
        "PaymentGUID": guid, "LineNo": 1, "DocumentKind": "Cash", "DocumentNumber": guid[-6:],
        "PaymentDate": payments.clock.today().isoformat(), "PaymentTypeGUID": None, "CounterpartyGUID": None,
        "Amount": float(r["amount"]), "Currency": r["currency"], "Purpose": purpose, "IsPosted": True, "IsDeleted": False,
        "OperationKind": "Видача", "RowVersion": next(rv)})
add_doc("C" + "0" * 30 + "1", a, f"оплата {rn.label(a['number'])}")
add_doc("C" + "0" * 30 + "2", b, f"оплата ZO-{int(b['number'])}")  # колишній префікс, з рискою, без нулів
payments.sync()
check("payment with PD000xxx -> linked and paid", db.get_request(a["id"])["status"] == "paid")
check("payment with an old prefix -> linked and paid", db.get_request(b["id"])["status"] == "paid")

# ---------- API для 1С
key = onec_api.new_key({"name": "t", "email": "t"})
app_settings.set("onec_api_enabled", True, "t")
item = c.get(f"/api/1c/v1/payment-orders/{req['id']}", headers={"X-Api-Key": key}).get_json()["item"]
check("API: number raw, number_label, marker, markers", item["number"] == num and item["number_label"] == lbl
      and item["marker"] == lbl and f"#{int(num)}" in item["markers"] and lbl in item["markers"])
check("API: purpose ends with the marker", onec_api.purpose(req).endswith(lbl))
checks = c.get("/api/1c/v1/payment-checks", headers={"X-Api-Key": key}).get_json()
check("API: payment-checks gives prefixes", checks["prefixes"] == ["PD", "ZO"])
print("ALL OK" if ok else "SOME FAILED")
