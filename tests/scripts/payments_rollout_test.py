"""Звірка з 1С при поступовому підключенні відділів: дата «заявки обов'язкові з» для відділу (відділ — з документа
списання 1С) і режим «Відділи без дати»; дата «діє до» для винятків «без заявки дозволено» (форма налаштувань і
швидкий виняток зі звіту) — минулі платежі лишаються дозволеними, пізніші знову потребують заявки."""
import os, sys, tempfile
from datetime import date
sys.path.insert(0, os.getcwd())
tmp = tempfile.mkdtemp()
os.environ.update(UPLOAD_DIR=tmp, ADMIN_EMAILS="adm@ftpua.com")
import app as m
import app_settings, dept_settings, payments, settings_log

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

payments.sync()
# Платежі без заявки: відділ «Логістика» в документі (DEP-LOG — GUID демо-відділу), вид оплати «КАСА-ТЕСТ»
def pay(n, day, dep=None, ptype="PT-TEST", deps2=None):
    rows = [{"PaymentGUID": f"R{n:031d}", "LineNo": 1, "DocumentKind": "Cash", "DocumentNumber": f"RL-{n}", "PaymentDate": day,
             "PaymentTypeGUID": ptype, "CounterpartyGUID": None, "Amount": 100 + n, "Currency": "UAH", "Purpose": "без заявки",
             "IsPosted": True, "IsDeleted": False, "OperationKind": "Видача", "DepartmentGUID": dep, "RowVersion": 1}]
    if deps2:
        rows.append({**rows[0], "LineNo": 2, "DepartmentGUID": deps2})
    payments.store().replace_docs([payments.parse(r) for r in rows])
pay(1, "2026-10-20", dep="DEP-LOG")
pay(2, "2026-10-20", dep="DEP-LOG", deps2="DEP-IT")
pay(3, "2026-10-28")
pay(4, "2026-11-05")

def report():
    data = payments.report(date(2026, 9, 1), date(2026, 11, 30))
    where = {d["number"]: tab for tab in ("unlinked", "allowed") for d in data[tab]}
    why = {d["number"]: d.get("allowed") for d in data["allowed"]}
    return where, why

where, why = report()
check("by default departments without a date are connected: payment of «Логістика» is unlinked",
      where.get("RL-1") == "unlinked" and app_settings.get("payments_dept_default") == "connected")

# ---------- відділи без дати — ще не підключені
app_settings.set("payments_dept_default", "pending", "t")
where, why = report()
check("pending: payment of a department without a date is allowed, with the reason",
      where.get("RL-1") == "allowed" and why["RL-1"]["kind"] == "Відділ ще не працює через PayDesk"
      and why["RL-1"]["name"] == "Логістика" and why["RL-1"]["detail"] == "дату підключення ще не задано")
check("payment without a department in the document is reconciled as usual", where.get("RL-3") == "unlinked")

# ---------- дата підключення відділу
dept_settings.set_many({"LOG": {"requests_since": "2026-10-25"}}, "t")
where, why = report()
check("before the department's start date: allowed (requests from 25.10.2026)",
      where.get("RL-1") == "allowed" and why["RL-1"]["detail"] == "заявки обов'язкові з 25.10.2026")
dept_settings.set_many({"LOG": {"requests_since": "2026-10-15"}}, "t")
where, _ = report()
check("on/after the start date: a payment without a request is a problem again", where.get("RL-1") == "unlinked")
dept_settings.set_many({"LOG": {"requests_since": "2026-12-01"}, "IT": {"requests_since": "2026-10-01"}}, "t")
where, _ = report()
check("two departments in the document: one already works through PayDesk -> unlinked",
      where.get("RL-1") == "allowed" and where.get("RL-2") == "unlinked")
app_settings.set("payments_dept_default", "connected", "t")
where, _ = report()
check("connected by default, but the department starts later: still allowed", where.get("RL-1") == "allowed")

# ---------- сторінка «Відділи» → «Звірка з 1С»
login("adm@ftpua.com")
html = page("/admin/departments?view=reconcile")
check("departments page: reconcile tab with dates and the default mode", 'data-view="reconcile"' in html
      and 'name="requests_since-LOG" value="2026-12-01"' in html and 'name="payments_dept_default" value="pending"' in html
      and "ще ні — заявки з 01.12.2026" in html)
r = c.post("/admin/departments", data={"view": "reconcile", "head_mode-LOG": "default", "cfo-LOG": "default",
                                       "head_threshold-LOG": "", "requests_since-LOG": "2026-11-01",
                                       "payments_dept_default": "pending"})
check("save: date and mode, back to the same tab, journal", r.status_code == 302 and "view=reconcile" in r.headers["Location"]
      and dept_settings.get("LOG")["requests_since"] == "2026-11-01" and app_settings.get("payments_dept_default") == "pending"
      and any(e["label"] == "Відділ «Логістика»: заявки обов'язкові з" and e["new_label"] == "01.11.2026"
              for e in settings_log.query(20)))
r = c.post("/admin/departments", data={"view": "reconcile", "head_mode-LOG": "default", "cfo-LOG": "default",
                                       "head_threshold-LOG": "", "requests_since-LOG": "01.11.2026"})
check("bad date -> error, nothing saved", dept_settings.get("LOG")["requests_since"] == "2026-11-01")
c.post("/admin/departments", data={"view": "reconcile", "head_mode-LOG": "default", "cfo-LOG": "default",
                                   "head_threshold-LOG": "", "requests_since-LOG": ""})
check("empty date -> as for departments without a date", dept_settings.get("LOG")["requests_since"] is None)
app_settings.set("payments_dept_default", "connected", "t")

# ---------- «діє до» для винятку: швидкий виняток зі звіту
html = page("/payments/reconcile?from=2026-09-01&to=2026-11-30")
check("quick allow menu: one form with «діє до»", 'name="pick" value="payment_type::PT-TEST"' in html and 'name="until"' in html)
r = c.post("/payments/allow", data={"pick": "payment_type::PT-TEST", "until": "2026-10-31", "next": "/payments/reconcile"})
check("quick allow with a date: exclusion + «діє до»", r.status_code == 302
      and "PT-TEST" in app_settings.get("payments_ok_payment_types")
      and app_settings.get("payments_ok_until") == {"payments_ok_payment_types": {"PT-TEST": "2026-10-31"}})
where, why = report()
check("payments up to the date are allowed (with «до»), later ones are not",
      where.get("RL-3") == "allowed" and why["RL-3"]["until"] == date(2026, 10, 31) and why["RL-3"]["text"].endswith("(до 31.10.2026)")
      and where.get("RL-4") == "unlinked")
check("allowed tab shows «діє до»", "діє до 31.10.2026" in page("/payments/reconcile?tab=allowed&from=2026-09-01&to=2026-11-30"))
c.post("/payments/allow", data={"pick": "payment_type::PT-TEST", "until": "", "next": "/payments/reconcile"})
where, _ = report()
check("quick allow again without a date -> forever", where.get("RL-4") == "allowed" and app_settings.get("payments_ok_until") == {})
check("quick allow: bad date -> error, nothing changed",
      c.post("/payments/allow", data={"pick": "payment_type::PT-TEST", "until": "31.10"}).status_code == 302
      and app_settings.get("payments_ok_until") == {})

# ---------- «діє до» у формі налаштувань
html = page("/admin/settings?tab=reconcile")
check("settings: date field next to each exclusion", 'name="ok_until__payments_ok_payment_types__PT-TEST"' in html
      and 'name="ok_until_sent"' in html and "Відділи, які ще не працюють через PayDesk" in html)
base = {"tab": "reconcile", "ok_until_sent": "1", "pay_mode_acc_cash": "manual", "pay_mode_acc_resident": "manual",
        "pay_mode_acc_nonresident": "manual", "payments_since": "2026-09-01", "payment_link_roles": ["accountant", "admin"]}
c.post("/admin/settings", data={**base, "payments_ok_payment_types": ["PT-TEST"],
                                "ok_until__payments_ok_payment_types__PT-TEST": "2026-10-25",
                                "ok_until__payments_ok_items__02.001": "2026-10-01"})
check("settings: date saved only for a checked exclusion",
      app_settings.get("payments_ok_until") == {"payments_ok_payment_types": {"PT-TEST": "2026-10-25"}}
      and any(e["label"] == "Без заявки дозволено: діє до" and "PT-TEST — до 25.10.2026" in e["new_label"]
              for e in settings_log.query(20)))
where, _ = report()
check("exclusion ended on 25.10: the 28.10 payment needs a request again, history kept",
      where.get("RL-3") == "unlinked" and where.get("RL-4") == "unlinked")
c.post("/admin/settings", data={**base, "payments_ok_payment_types": ["PT-TEST"],
                                "ok_until__payments_ok_payment_types__PT-TEST": "2026-13-01"})
check("settings: bad date -> error, nothing saved",
      app_settings.get("payments_ok_until") == {"payments_ok_payment_types": {"PT-TEST": "2026-10-25"}})
c.post("/admin/settings", data={**base, "payments_ok_payment_types": ["PT-TEST"],
                                "ok_until__payments_ok_payment_types__PT-TEST": ""})
check("settings: date cleared -> exclusion forever", app_settings.get("payments_ok_until") == {}
      and report()[0].get("RL-4") == "allowed")
print("ALL OK" if ok else "SOME FAILED")
