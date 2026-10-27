"""Звірка з платежами 1С: копія платежів (перше завантаження, зміни, видалені), «#номер» і реквізити, режими
«Оплачено» за видом бухгалтера (вручну / автоматично / обидва), підказка «#номер» у картці, «Синхронізувати зараз»."""
import os, sys, tempfile
from datetime import date, datetime
from decimal import Decimal
sys.path.insert(0, os.getcwd())
tmp = tempfile.mkdtemp()
os.environ.update(UPLOAD_DIR=tmp, ADMIN_EMAILS="adm@ftpua.com")
import app as m
import app_settings, payment_match, payments, settings_log, user_roles
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
page = lambda url: c.get(url).get_data(as_text=True)
status = lambda rid: db.get_request(rid)["status"]
user_roles.set_roles("res@ftpua.com", "res@ftpua.com", "RES", ["acc_resident"], "t")
user_roles.set_roles("cash@ftpua.com", "cash@ftpua.com", "CASH", ["acc_cash"], "t")

# ---------- правила зіставлення (чисті функції)
ctx = {"edrpou": lambda cp: {"A": "111", "B": "111", "C": "222"}.get(cp), "employee_email": lambda g: "auth@x.com" if g == "E1" else None,
       "pending": lambda cp: cp == "P", "submitted": lambda r: date(2026, 10, 1), "paid_on": lambda r: None}
doc = lambda **kw: {"kind": "Bank", "date": date(2026, 10, 10), "amount": Decimal("100.00"), "currency": "UAH",
                    "counterparties": ["A"], "payment_type": "T1", "recipient": None, "purpose": "", **kw}
req = lambda id_, **kw: {"id": id_, "number": f"{id_:09d}", "status": "to_pay", "currency": "UAH", "amount": Decimal("100.00"),
                         "counterparty_id": "A", "author_email": "auth@x.com", "pay_date": date(2026, 10, 9), **kw}
check("numbers from purpose", payment_match.numbers_in("оплата #5, # 12 і #0007") == ["000000005", "000000007", "000000012"])
r5 = req(5)
check("#5 exact", payment_match.match(doc(purpose="рах. 1, #5"), {5: r5}, {"000000005": r5}, ctx)["requests"] == [5])
check("#5 other amount -> issue", payment_match.match(doc(purpose="#5", amount=Decimal("90")), {5: r5}, {"000000005": r5}, ctx)["issue"]
      == "number_amount")
check("#5 other counterparty -> issue (bank)", payment_match.match(doc(purpose="#5", counterparties=["C"]), {5: r5},
                                                                   {"000000005": r5}, ctx)["issue"] == "number_counterparty")
check("#5 cash: counterparty not checked", payment_match.match(doc(kind="Cash", purpose="#5", counterparties=["C"]), {5: r5},
                                                              {"000000005": r5}, ctx)["requests"] == [5])
r6 = req(6, amount=Decimal("50.00"))
check("#5 #6 group", payment_match.match(doc(purpose="#5 #6", amount=Decimal("150")), {5: r5, 6: r6},
                                         {"000000005": r5, "000000006": r6}, ctx)["requests"] == [5, 6])
check("#7 unknown -> issue", payment_match.match(doc(purpose="#7"), {5: r5}, {}, ctx)["issue"] == "number_status")
check("attributes: same EDRPOU, one candidate", payment_match.match(doc(counterparties=["B"]), {5: r5}, {}, ctx)["requests"] == [5])
check("attributes: two candidates -> ambiguous", payment_match.match(doc(), {5: r5, 8: req(8)}, {}, ctx)["issue"] == "ambiguous")
check("attributes: other payment type in request -> none", payment_match.match(doc(), {5: req(5, payment_account_id="T2")}, {},
                                                                              ctx)["requests"] == [])
check("attributes: cash recipient is the author", payment_match.match(doc(kind="Cash", counterparties=[], recipient="E1"), {5: r5},
                                                                      {}, ctx)["requests"] == [5])
check("attributes: too late after pay date -> none", payment_match.match(doc(date=date(2026, 12, 30)), {5: r5}, {}, ctx)["requests"] == [])

# ---------- синхронізація демо-платежів (режим «Вручну» — лише прив'язка)
check("DAX: changes after RowVersion, by date", "'Payments'[RowVersion] > 1007" in payments.dax(
    {"kind": "changes", "rv": 1007, "since": date(2026, 9, 1)}) and "TOPN(5000" in payments.dax(
    {"kind": "changes", "rv": 1, "since": date(2026, 9, 1)}))
st = payments.sync()
links = payments.store().all_links()
linked = {rid: v["match"] for g, x in links.items() for rid, v in x.items()}
check("first load: rows, links", not st.get("error") and st["rows"] == 7 and linked == {1: "auto", 3: "number", 14: "auto"})
check("manual mode: statuses unchanged", status(3) == "to_pay" and status(14) == "to_pay" and status(1) == "paid")
issues = payments.store().all_issues()
check("#12 with other amount -> issue", issues.get("D" + "0" * 30 + "3", {}).get("issue") == "number_amount" and status(12) == "to_pay")
payments.sync()
check("re-sync: no duplicates", payments.status()["rows"] == 7 and len(payments.store().all_links()) == 3)

login("res@ftpua.com")
html = page("/requests/15")
check("card: #number hint for the accountant", 'class="pm-marker">#15<' in html and 'value="pay"' in html)
check("card: linked payment shown", "платіж з 1С" in page("/requests/3") and "ОТП-000128" in page("/requests/3"))

# ---------- режим «Автоматично» для бухгалтера резидентів
app_settings.set("pay_mode_acc_resident", "auto", "t")
html = page("/requests/15")
check("auto: no «Оплачено» button, hint says automatic", 'value="pay"' not in html and "Кнопки «Оплачено» немає" in html)
c.post("/requests/15/action", data={"action": "pay"})
c.post("/to-pay/pay", data={"ids": "15"})
check("auto: manual pay refused (card and bulk)", status(15) == "to_pay")
check("to-pay: row locked with the #number", "у призначенні вкажіть #15" in page("/to-pay"))
payments.sync()
check("auto: already linked #3 becomes paid", status(3) == "paid")
h = db.get_request(3)["history"][-1]
check("history: paid by 1C with the payment", h["action"] == "pay" and h["user_name"] == "1С (платіж)" and "ОТП-000128" in h["comment"])
payments.MOCK_ROWS.append({**payments.MOCK_ROWS[-1], "PaymentGUID": "D" + "0" * 29 + "20", "DocumentNumber": "ОТП-000170",
                           "PaymentDate": "2026-10-20", "PaymentTypeGUID": "1", "CounterpartyGUID": "2", "Amount": 6180.00,
                           "Purpose": "Оплата за рах. 31 згідно заявки #15", "RowVersion": 2000})
st = payments.sync()
check("new payment #15 -> paid automatically", status(15) == "paid" and "000000015" in st["paid_last"] and st["rows"] == 8)
check("cash accountant still manual", status(14) == "to_pay")

# ---------- видалений у 1С платіж
payments.MOCK_ROWS[:] = [r for r in payments.MOCK_ROWS if r["DocumentNumber"] != "ОТП-000170"]
payments.sync(check_keys=True)
check("removed in 1C -> issue on the linked payment", payments.store().all_issues().get("D" + "0" * 29 + "20", {}).get("issue")
      == "removed" and payments.status()["rows"] == 7)

# ---------- адмінка
login("adm@ftpua.com")
html = page("/admin/settings?tab=reconcile")
check("reconcile tab: modes for 3 accountants, sync state", html.count('name="pay_mode_acc_') == 9 and "Синхронізувати зараз" in html
      and 'name="payments_since"' in html)
c.post("/admin/settings", data={"tab": "reconcile", "pay_mode_acc_cash": "both", "pay_mode_acc_resident": "auto",
                                "pay_mode_acc_nonresident": "manual", "payments_since": "2026-09-01"})
check("modes saved, journal", app_settings.get("pay_mode_acc_cash") == "both"
      and any(e["label"] == "«Оплачено»: бухгалтер по готівці" for e in settings_log.query(20)))
c.post("/admin/settings", data={"tab": "reconcile", "payments_since": "вчора"})
check("bad date refused", app_settings.get("payments_since") == "2026-09-01")
r_ = c.post("/admin/payments/sync")
check("sync now: cash in «both» -> #14 paid", r_.status_code == 302 and status(14) == "paid")
login("res@ftpua.com")
check("sync now: admin only", c.post("/admin/payments/sync").status_code in (302, 403) and "/login" not in (c.post("/admin/payments/sync").headers.get("Location") or "x"))
print("ALL OK" if ok else "SOME FAILED")
