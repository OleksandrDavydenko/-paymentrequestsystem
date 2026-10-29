"""Звірка за сумою документа (DocumentAmount у Power BI — шапка списання), а не за сумою рядків «Счета»: рядки можуть
бути меншими (решта лишається авансом). Без колонки — як раніше, сума рядків. Коли колонка з'являється в Power BI,
копія платежів перезавантажується один раз."""
import os, sys, tempfile
sys.path.insert(0, os.getcwd())
tmp = tempfile.mkdtemp()
os.environ.update(UPLOAD_DIR=tmp, ADMIN_EMAILS="adm@ftpua.com")
import app as m  # noqa: F401
import payments
import storage as db

ok = True
def check(label, cond):
    global ok
    ok &= bool(cond); print(("PASS " if cond else "FAIL ") + label)

# Дві заявки «До оплати»: під кожну — касовий платіж із #номером, рядки «Счета» меншим за шапку (решта — аванс)
payments.sync()  # демо-платежі вже займають частину заявок — беремо вільні
taken = {rid for v in payments.store().all_links().values() for rid in v}
ids = [i for i in db.payment_candidate_ids(payments.clock.today()) if db.get_request(i)["status"] == "to_pay" and i not in taken]
a, b = (db.get_request(i) for i in ids[:2])
def doc(n, req, lines, header=None):
    rows = []
    for no, amount in enumerate(lines, 1):
        r = {"PaymentGUID": f"A{n:031d}", "LineNo": no, "DocumentKind": "Cash", "DocumentNumber": f"DA-{n}",
             "PaymentDate": payments.since().isoformat(), "PaymentTypeGUID": None, "CounterpartyGUID": None,
             "Amount": amount, "Currency": req["currency"], "Purpose": f"#{int(req['number'])} оплата", "IsPosted": True,
             "IsDeleted": False, "OperationKind": "Видача", "RowVersion": 9000 + n}
        if header is not None:
            r["DocumentAmount"] = header
        rows.append(r)
    return rows
amount_a, amount_b = float(a["amount"]), float(b["amount"])
payments.MOCK_ROWS += doc(1, a, [round(amount_a * 0.6, 2), round(amount_a * 0.2, 2)])          # без шапки: 80 %
payments.sync()
docs = payments.documents(payments.store().lines_of([f"A{1:031d}"]))
check("without DocumentAmount: amount = sum of lines -> «сума не збігається»",
      docs[f"A{1:031d}"]["amount"] == docs[f"A{1:031d}"]["lines_amount"]
      and payments.store().all_issues().get(f"A{1:031d}", {}).get("issue") == "number_amount")
check("no DocumentAmount column in Power BI -> no reload flag", not payments.store().get_state().get("doc_amount"))

# У Power BI додали колонку DocumentAmount — копія перезавантажується, звірка за шапкою
for r in payments.MOCK_ROWS:
    r.setdefault("DocumentAmount", r.get("Amount"))
for r in payments.MOCK_ROWS:
    if r["PaymentGUID"] == f"A{1:031d}":
        r["DocumentAmount"] = amount_a
payments.MOCK_ROWS += doc(2, b, [round(amount_b * 0.5, 2)], header=amount_b)                    # рядок 50 %, решта — аванс
st = payments.sync()
docs = payments.documents(payments.store().lines_of([f"A{1:031d}", f"A{2:031d}"]))
check("DocumentAmount appeared: copy reloaded once (state flag), header amount used",
      st.get("doc_amount") is True and float(docs[f"A{1:031d}"]["amount"]) == amount_a
      and float(docs[f"A{1:031d}"]["lines_amount"]) == round(amount_a * 0.8, 2))
links = payments.store().all_links()
check("lines smaller than the header (advance): request matched by #number and header amount",
      a["id"] in links.get(f"A{1:031d}", {}) and b["id"] in links.get(f"A{2:031d}", {})
      and f"A{1:031d}" not in payments.store().all_issues())
rv_before = payments.store().get_state().get("rv")
payments.sync()
check("next sync: no second reload", payments.store().get_state().get("doc_amount") is True
      and payments.store().get_state().get("rv") == rv_before)
print("ALL OK" if ok else "SOME FAILED")
