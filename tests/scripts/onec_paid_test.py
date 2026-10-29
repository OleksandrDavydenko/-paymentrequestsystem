"""Обробка 1С створила проведену платіжку (posted: true): для бухгалтера в режимі «Автоматично» / «Обидва» заявка
одразу «Оплачено», у «Вручну» — лише відмітка. Платіжку потім видалили чи розпровели в 1С — при синхронізації зв'язок
зникає, оплачена нею заявка знову «До оплати», відмітка 1С знімається (обробка знову запропонує платіжку)."""
import itertools, os, sys, tempfile
sys.path.insert(0, os.getcwd())
tmp = tempfile.mkdtemp()
os.environ.update(UPLOAD_DIR=tmp, ADMIN_EMAILS="adm@ftpua.com")
import app as m
import app_settings, onec_api, payments
import storage as db
import workflow as wf

ok = True
def check(label, cond):
    global ok
    ok &= bool(cond); print(("PASS " if cond else "FAIL ") + label)

today = payments.clock.today()
payments.sync()  # демо-платежі вже займають частину заявок — беремо вільні
taken = {rid for v in payments.store().all_links().values() for rid in v}
free = [db.get_request(i) for i in db.payment_candidate_ids(today) if i not in taken]
a, b, d, e = [r for r in free if r["status"] == "to_pay"][:4]
G = lambda n: f"B{n:031d}"
rv = itertools.count(9100)
status = lambda req: db.get_request(req["id"])["status"]
links = lambda: payments.store().all_links()


def modes(mode):
    for role in wf.ACCOUNTANT_ROLES:
        app_settings.set(f"pay_mode_{role}", mode, "t")


def created(req, n, posted=True):
    return onec_api.mark_created(req["id"], {"doc_ref": onec_api.ref_1c(G(n)), "doc_number": f"ПП-{n}",
                                             "doc_date": today.isoformat(), "user": "Бондар", "posted": posted})


def add_doc(n, req, **kw):
    payments.MOCK_ROWS.append({
        "PaymentGUID": G(n), "LineNo": 1, "DocumentKind": "Cash", "DocumentNumber": f"ПП-{n}", "PaymentDate": today.isoformat(),
        "PaymentTypeGUID": None, "CounterpartyGUID": None, "Amount": float(req["amount"]), "Currency": req["currency"],
        "Purpose": f"#{int(req['number'])} оплата", "IsPosted": True, "IsDeleted": False, "OperationKind": "Видача",
        "RowVersion": next(rv), **kw})


def change_doc(n, **kw):
    for r in payments.MOCK_ROWS:
        if r["PaymentGUID"] == G(n):
            r.update(kw, RowVersion=next(rv))


# ---------- «Вручну»: лише відмітка; прив'язаний платіж видалили — зв'язок зникає, відмітка 1С знімається
modes("manual")
r = created(a, 1)
check("manual: posted order only marked", r["status"] == "to_pay" and r["onec_order"]["posted"] is True)
add_doc(1, a)
payments.sync()
check("manual: payment linked by #number, still «До оплати»", a["id"] in links().get(G(1), {}) and status(a) == "to_pay")
change_doc(1, IsPosted=False)
st = payments.sync()
req = db.get_request(a["id"])
check("unposted in 1C: link dropped, 1C mark removed, history", G(1) not in links() and req["status"] == "to_pay"
      and "onec_order" not in req and req["history"][-1]["action"] == "payment_removed"
      and "зв'язок із заявкою прибрано" in req["history"][-1]["comment"] and not st["unpaid_last"])
check("processing offers the request again", a["id"] in [x["id"] for x in onec_api.orders("new")])

# ---------- «Автоматично»: «Оплачено» одразу з обробки
modes("auto")
r = created(b, 2)
h = r["history"][-1]
check("auto: paid at once, history from the 1C user", r["status"] == "paid" and h["action"] == "pay"
      and h["user_name"] == "1С: Бондар" and "проведену платіжку" in h["comment"] and "ПП-2" in h["comment"])
n = len(r["history"])
r = created(b, 2)
check("same call again: idempotent", r["status"] == "paid" and len(r["history"]) == n)
check("not posted -> only the mark", created(e, 5, posted=False)["status"] == "to_pay")
add_doc(2, b)
payments.sync()
req = db.get_request(b["id"])
check("sync: payment linked to the paid request, no second «Оплачено»", b["id"] in links().get(G(2), {})
      and req["status"] == "paid" and sum(1 for x in req["history"] if x["action"] == "pay") == 1)
change_doc(2, IsDeleted=True)
st = payments.sync()
req = db.get_request(b["id"])
check("deleted in 1C: link dropped, back to «До оплати», 1C mark removed", G(2) not in links()
      and req["status"] == "to_pay" and "onec_order" not in req and req["history"][-1]["action"] == "payment_removed"
      and req["history"][-1]["to_status"] == "to_pay" and b["number"] in st["unpaid_last"])
payments.sync()
check("next sync: nothing changes", status(b) == "to_pay" and db.get_request(b["id"])["history"][-1]["action"] == "payment_removed")

# ---------- через API: у відповіді вже «Оплачено»; документ видалили ще до прив'язки
key = onec_api.new_key({"name": "t", "email": "t"})
app_settings.set("onec_api_enabled", True, "t")
c = m.app.test_client()
resp = c.post(f"/api/1c/v1/payment-orders/{d['id']}/created", headers={"X-Api-Key": key},
              json={"doc_ref": onec_api.ref_1c(G(4)), "doc_number": "ПП-4", "doc_date": today.isoformat(), "posted": True})
check("API: response says «Оплачено»", resp.status_code == 200 and resp.get_json()["item"]["status"] == "paid")
add_doc(4, d, IsPosted=False)
st = payments.sync()
req = db.get_request(d["id"])
check("processing paid, document unposted before linking -> «До оплати»", req["status"] == "to_pay"
      and "onec_order" not in req and req["history"][-1]["action"] == "payment_removed" and d["number"] in st["unpaid_last"])
print("ALL OK" if ok else "SOME FAILED")
