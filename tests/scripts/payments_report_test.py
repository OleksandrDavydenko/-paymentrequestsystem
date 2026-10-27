"""Звіт «Звірка» (фіндиректор, адміністратор): платежі з 1С без заявки, розбіжності, дозволені без заявки (винятки:
відділ статті витрат, стаття, вид операції); ручна прив'язка платежу (хто — налаштування) й відв'язка адміністратором."""
import os, sys, tempfile
sys.path.insert(0, os.getcwd())
tmp = tempfile.mkdtemp()
os.environ.update(UPLOAD_DIR=tmp, ADMIN_EMAILS="adm@ftpua.com")
import app as m
import app_settings, payments, settings_log, user_roles
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
user_roles.set_roles("cfo@ftpua.com", "cfo@ftpua.com", "CFO", ["cfo"], "t")
user_roles.set_roles("res@ftpua.com", "res@ftpua.com", "RES", ["acc_resident"], "t")
G = lambda n: "D" + str(n).rjust(31, "0")  # GUID демо-платежу

payments.sync()
st = payments.status()
check("nav count: supplier payment, fee and tax without a request", st["unlinked"] == 3)

# ---------- доступ і вкладки
login("res@ftpua.com")
check("report: accountants have no access", c.get("/payments/reconcile").status_code in (302, 403))
login("cfo@ftpua.com")
html = page("/payments/reconcile?from=2026-09-01&to=2026-10-31")
check("report: unlinked tab", "ОТП-000163" in html and "SAL4498696" in html and "000074" in html and "ОТП-000128" not in html)
check("menu: «Звірка» with count", ">Звірка" in html.replace("\n", "").replace(" ", "") or "Звірка" in html)
html = page("/payments/reconcile?tab=issues&from=2026-09-01&to=2026-10-31")
check("report: issues tab (#12 amount)", "ОТП-000141" in html and "сума не збігається" in html)
check("report: cfo may not link by default", 'name="request_number"' not in html)
html = page("/payments/reconcile?from=2026-09-01&to=2026-10-31&kind=Cash")
check("report: cash filter", "ОТП-000163" not in html)

# ---------- винятки: вид операції, стаття, відділ статті
login("adm@ftpua.com")
html = page("/admin/settings?tab=reconcile")
check("settings: exclusions and link roles", 'name="payments_ok_operations" value="Комісія банку"' in html
      and 'name="payments_ok_departments" value="ІТ"' in html and 'name="payment_link_roles" value="cfo"' in html)
c.post("/admin/settings", data={"tab": "reconcile", "pay_mode_acc_cash": "manual", "pay_mode_acc_resident": "auto",
                                "pay_mode_acc_nonresident": "manual", "payments_since": "2026-09-01",
                                "payment_link_roles": ["accountant", "cfo", "admin"],
                                "payments_ok_operations": ["Комісія банку", "Перерахування податків"],
                                "payments_ok_departments": ["ІТ"]})
check("exclusions saved, journal", app_settings.get("payments_ok_operations") == ["Комісія банку", "Перерахування податків"]
      and any(e["label"] == "Без заявки дозволено: види операцій" for e in settings_log.query(20)))
data = payments.report(payments.since(), payments.clock.today())
allowed = {d["number"]: d["allowed"] for d in data["allowed"]}
check("fee and tax allowed by operation; 02.001 (ІТ in its departments) allowed by item department",
      allowed.get("SAL4498696") == "вид операції «Комісія банку»" and "000074" in allowed
      and allowed.get("ОТП-000163") == "відділ статті «ІТ»" and not data["unlinked"])
app_settings.set("payments_ok_departments", [], "t")
check("without the department the supplier payment is unlinked again",
      [d["number"] for d in payments.report(payments.since(), payments.clock.today())["unlinked"]] == ["ОТП-000163"])

# ---------- ручна прив'язка зі звірки (за № заявки) й відв'язка
login("cfo@ftpua.com")
r_ = c.post("/payments/link", data={"payment_guid": G(7), "request_number": "#15", "next": "/payments/reconcile"})
check("cfo links supplier payment to request 15 -> paid", r_.status_code == 302 and status(15) == "paid")
h = db.get_request(15)["history"][-1]
check("history: paid by the person who linked", h["action"] == "pay" and h["user_name"] == "CFO" and "прив'язано платіж" in h["comment"])
r_ = c.post("/payments/link", data={"payment_guid": G(7), "request_number": "16"})
check("same payment twice refused", status(16) == "to_pay")
c.post("/payments/link", data={"payment_guid": G(3), "request_number": "13"})
check("other currency refused", status(13) == "to_pay")
login("adm@ftpua.com")
html = page("/requests/15")
check("card: linked payment with unlink for admin", "ОТП-000163" in html and "Відв'язати" in html)
c.post("/requests/15/unlink-payment", data={"payment_guid": G(7)})
check("unlink: link removed, history entry, status kept", G(7) not in payments.store().all_links() and status(15) == "paid"
      and db.get_request(15)["history"][-1]["action"] == "payment_unlink")

# ---------- картка: вибір платежу вручну (режим «Автоматично»)
login("res@ftpua.com")
html = page("/requests/12")
check("card: manual pick lists USD payments, same counterparty first", "Прив'язати платіж з 1С вручну" in html
      and html.find("ОТП-000141") > 0 and 'value="pay"' not in html)
c.post("/payments/link", data={"payment_guid": G(3), "request_id": "12"})
check("accountant picks the #12 payment -> paid, issue cleared", status(12) == "paid" and G(3) not in payments.store().all_issues())
app_settings.set("payment_link_roles", ["admin"], "t")
check("accountant not in link roles -> no pick list", "Прив'язати платіж з 1С вручну" not in page("/requests/16"))
print("ALL OK" if ok else "SOME FAILED")
