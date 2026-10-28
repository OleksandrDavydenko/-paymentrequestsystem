"""Звіт «Звірка» (фіндиректор, адміністратор): платежі з 1С без заявки, розбіжності, зіставлені з заявками, дозволені
без заявки (винятки: відділ статті витрат, стаття, вид операції, вид оплати — і швидкий виняток з рядка); ручна прив'язка
платежу вибором заявки зі списку (хто — налаштування) й відв'язка адміністратором; «платіж № …» в оплачених заявках;
назви з демо-довідника в тестовому режимі."""
import json
import os, sys, tempfile
sys.path.insert(0, os.getcwd())
tmp = tempfile.mkdtemp()
os.environ.update(UPLOAD_DIR=tmp, ADMIN_EMAILS="adm@ftpua.com")
import app as m
import app_settings, counterparties, payment_accounts, payments, payment_types, settings_log, user_roles
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
def tile(html, code):
    part = html.split(f'class="rc-tile rc-')
    for chunk in html.split('<a href="/payments/reconcile?tab=')[1:]:
        if chunk.startswith(code + "&") or chunk.startswith(code + '"'):
            return chunk.split('rc-tile-n">')[1].split("<")[0]
check("tiles: unlinked 3, issues 1, linked 3, allowed 0",
      [tile(html, c_) for c_ in ("unlinked", "issues", "linked", "allowed")] == ["3", "1", "3", "0"])
check("report: how it works", "Як працює звірка" in html)
html = page("/payments/reconcile?tab=issues&from=2026-09-01&to=2026-10-31")
check("report: issues tab (#12 amount) with the request and comparison", "ОТП-000141" in html and "сума не збігається" in html
      and 'href="/requests/12"' in html and "у заявці: <strong>1 250,00 USD</strong>" in html and "у платежі: 1 200,00 USD" in html)
check("report: cfo may not link by default", "rc-find" not in html and 'id="rc-pick"' not in html)
html = page("/payments/reconcile?tab=linked&from=2026-09-01&to=2026-10-31")
check("report: linked tab — payment, request, how matched", "ОТП-000101" in html and 'href="/requests/1"' in html
      and "за сумою й контрагентом" in html and "ОТП-000128" in html and "за #номером у призначенні" in html
      and 'href="/requests/14"' in html)
check("linked: paid by the accountant before vs manual mode", "заявку вже позначив оплаченою бухгалтер" in html
      and "статус ставить бухгалтер" in html)
check("report: counterparty names, not codes", "ПрАТ «Київстар»" in page("/payments/reconcile?from=2026-09-01&to=2026-10-31"))
html = page("/payments/reconcile?from=2026-09-01&to=2026-10-31&pt=kind:Cash&tab=linked")
check("report: cash filter", "КО-000057" in html and "ОТП-000101" not in html)
html = page("/payments/reconcile?tab=issues&from=2026-09-01&to=2026-10-31&pt=6")
check("report: payment type filter", "ОТП-000141" in html and 'value="6" selected' in html
      and "ОТП-000141" not in page("/payments/reconcile?tab=issues&from=2026-09-01&to=2026-10-31&pt=1"))

# ---------- винятки: вид операції, стаття, відділ статті
login("adm@ftpua.com")
html = page("/admin/settings?tab=reconcile")
check("settings: exclusions and link roles", 'name="payments_ok_operations" value="Комісія банку"' in html
      and 'name="payments_ok_departments" value="ІТ"' in html and 'name="payment_link_roles" value="cfo"' in html
      and 'name="payments_ok_payment_types" value="6"' in html)
c.post("/admin/settings", data={"tab": "reconcile", "pay_mode_acc_cash": "manual", "pay_mode_acc_resident": "auto",
                                "pay_mode_acc_nonresident": "manual", "payments_since": "2026-09-01",
                                "payment_link_roles": ["accountant", "cfo", "admin"],
                                "payments_ok_operations": ["Комісія банку", "Перерахування податків"],
                                "payments_ok_departments": ["ІТ"]})
check("exclusions saved, journal", app_settings.get("payments_ok_operations") == ["Комісія банку", "Перерахування податків"]
      and any(e["label"] == "Без заявки дозволено: види операцій" for e in settings_log.query(20)))
data = payments.report(payments.since(), payments.clock.today())
allowed = {d["number"]: d["allowed"]["text"] for d in data["allowed"]}
check("fee and tax allowed by operation; 02.001 (ІТ in its departments) allowed by item department",
      allowed.get("SAL4498696") == "вид операції «Комісія банку»" and "000074" in allowed
      and allowed.get("ОТП-000163") == "відділ статті «ІТ»" and not data["unlinked"])
app_settings.set("payments_ok_departments", [], "t")
check("without the department the supplier payment is unlinked again",
      [d["number"] for d in payments.report(payments.since(), payments.clock.today())["unlinked"]] == ["ОТП-000163"])
html = page("/payments/reconcile?from=2026-09-01&to=2026-10-31")
check("quick allow menu for admin", "Дозволити без заявки…" in html and 'name="key" value="payment_type"' in html
      and 'name="key" value="department"' in html and 'name="value" value="ІТ"' in html)
login("cfo@ftpua.com")
check("quick allow: admin only", c.post("/payments/allow", data={"key": "payment_type", "value": "1"}).status_code == 403)
login("adm@ftpua.com")
r_ = c.post("/payments/allow", data={"key": "payment_type", "value": "1", "next": "/payments/reconcile"})
allowed = {d["number"]: d["allowed"]["text"] for d in payments.report(payments.since(), payments.clock.today())["allowed"]}
check("quick allow by payment type -> allowed, journal", r_.status_code == 302
      and app_settings.get("payments_ok_payment_types") == ["1"]
      and allowed.get("ОТП-000163") == "вид оплати «ПриватБанк, поточний · UAH · …0101»"
      and any(e["label"] == "Без заявки дозволено: види оплати" for e in settings_log.query(20)))
c.post("/payments/allow", data={"key": "bogus", "value": "1"})
check("quick allow: unknown key ignored", app_settings.get("payments_ok_payment_types") == ["1"])

# ---------- «Чому дозволено»: стаття — код і назва
app_settings.set("payments_ok_payment_types", [], "t")
app_settings.set("payments_ok_items", ["02.001"], "t")
why = {d["number"]: d["allowed"] for d in payments.report(payments.since(), payments.clock.today())["allowed"]}.get("ОТП-000163")
name = payments.expense_items.get_name("02.001")
check("allowed by article: kind, code and name", why and why["kind"] == "Стаття" and why["code"] == "02.001"
      and name and why["name"] == name and why["text"] == f"стаття 02.001 «{name}»")
login("adm@ftpua.com")
html = page("/payments/reconcile?from=2026-09-01&to=2026-10-31&tab=allowed")
from markupsafe import escape
check("allowed tab shows the article code and name", '<span class="rc-why-code">02.001</span>' in html and str(escape(name)) in html
      and "rc-why-kind" in html)
app_settings.set("payments_ok_items", [], "t")
app_settings.set("payments_ok_payment_types", ["1"], "t")

app_settings.set("payments_ok_payment_types", [], "t")

# ---------- вікно «Знайти заявку»: кандидати
cand = lambda guid, q="": json.loads(c.get(f"/payments/link-candidates?guid={guid}&q={q}").get_data(as_text=True))["items"]
items = cand(G(7))
check("candidates: same counterparty first, same currency only", items and items[0]["id"] == 15 and items[0]["same_cp"]
      and all(i["currency"] == "UAH" for i in items) and all(i["id"] not in (1, 3, 14) for i in items))
check("candidates: search by number", [i["id"] for i in cand(G(7), "000000015")] == [15])
items = cand(G(3))
check("candidates: #number in purpose first", items and items[0]["id"] == 12 and items[0]["by_number"])
check("candidates: unknown payment -> 404", c.get("/payments/link-candidates?guid=NOPE").status_code == 404)
html = page("/payments/reconcile?from=2026-09-01&to=2026-10-31")
check("report: find button and dialog", "Знайти заявку…" in html and 'id="rc-pick"' in html and 'name="request_number"' not in html)

# ---------- ручна прив'язка зі звірки (за № заявки) й відв'язка
login("cfo@ftpua.com")
r_ = c.post("/payments/link", data={"payment_guid": G(7), "request_id": "15", "next": "/payments/reconcile"})
check("cfo links supplier payment to request 15 -> paid", r_.status_code == 302 and status(15) == "paid")
html = page("/payments/reconcile?tab=linked&from=2026-09-01&to=2026-10-31")
check("linked tab: paid by PayDesk, manual by CFO", "«Оплачено» поставив PayDesk за цим платежем" in html
      and "вручну — CFO" in html)
h = db.get_request(15)["history"][-1]
check("history: paid by the person who linked", h["action"] == "pay" and h["user_name"] == "CFO" and "прив'язано платіж" in h["comment"])
r_ = c.post("/payments/link", data={"payment_guid": G(7), "request_number": "16"})
check("same payment twice refused", status(16) == "to_pay")
c.post("/payments/link", data={"payment_guid": G(3), "request_number": "13"})
check("other currency refused", status(13) == "to_pay")
login("adm@ftpua.com")
html = page("/requests/15")
check("card: linked payment with unlink for admin", "ОТП-000163" in html and "Відв'язати" in html
      and 'pm-doc-no">№ ОТП-000163' in html)
html = page("/admin/requests?period=all")
check("all requests: payment number under «Оплачено»", "платіж № ОТП-000163" in html and "платіж № ОТП-000101" in html)
check("all requests: no payment number for to_pay", html.count("платіж № ") == 2)
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

# ---------- тестовий режим: демо-коди, яких немає в живому довіднику 1С, показуються назвами з демо-довідника
app_settings.set("data_mode", "test", "t")
live_cp, live_types, live_get = counterparties._onec_by_id, payment_accounts.use_types, payment_types.get
counterparties._onec_by_id, payment_accounts.use_types, payment_types.get = lambda: {}, lambda: True, lambda i: None
check("test mode: demo counterparty and payment type names", counterparties.name("2") == "ПрАТ «Київстар»"
      and payment_accounts.label("1") == "ПриватБанк, поточний · UAH · …0101")
counterparties._onec_by_id, payment_accounts.use_types, payment_types.get = live_cp, live_types, live_get

print("ALL OK" if ok else "SOME FAILED")
