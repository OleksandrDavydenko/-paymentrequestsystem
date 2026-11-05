"""«Хто з бухгалтерів бачить заявки до оплати» (pay_access.py): «Лише за роллю» — як було; «За роллю і видом оплати
з 1С» — бухгалтер бачить і оплачує лише заявки своїх видів оплати (і заявки без виду), у «До оплати», «Оплачених»,
лічильнику, картці, масовій оплаті й листах; контроль «нікому оплатити» — список в адмінці, позначка в «До оплати»
фіндиректора, лист адміністраторам раз на випадок; без даних 1С — запасний режим за роллю."""
import os, re, sys, tempfile
sys.path.insert(0, os.getcwd())
os.environ.update(UPLOAD_DIR=tempfile.mkdtemp(), ADMIN_EMAILS="adm@ftpua.com")
import app as m
import user_budget; user_budget.get = lambda oid: {"direct": True, "all_departments": True, "departments": []}
import app_settings, mailer, notify_log, pay_access, payments, request_numbers, user_roles
import payment_accounts as pa, payment_types as pt
import storage as db
import workflow as wf
from web import background

ok = True
def check(label, cond):
    global ok
    ok &= bool(cond); print(("PASS " if cond else "FAIL ") + label)

sent = []
mailer.transport = lambda cfg, to, subject, html: sent.append((to, subject))
def flush():
    mailer.wait(); out = list(sent); sent.clear(); return out
for k, v in {"mail_enabled": True, "mail_sender": "noreply@ftpua.com", "smtp_host": "smtp.example.com",
             "cfo_threshold_usd": 1, "payment_link_roles": ["accountant"]}.items():
    app_settings.set(k, v, "t")

pa.use_types = lambda: True  # як із підключеним Power BI (мокові PaymentTypes / UserPaymentTypes)
for a in pt._access.items():
    if a["user"] == "U-IRINA":
        a["employee"] = "EMP-IRINA"  # Irina.Chagan: НАЛ, КАРТА, ПУМБ; Olena.Bondar: КАСА, NLB

AUTHOR, IRINA, OLENA, CFO, ADMIN = "author@ftpua.com", "i.chagan@ftpua.com", "o.bondar@ftpua.com", "cfo@ftpua.com", "adm@ftpua.com"
for email, roles in ((AUTHOR, ["initiator"]), (IRINA, ["acc_resident"]), (OLENA, ["acc_resident"]), (CFO, ["cfo"])):
    user_roles.set_roles(email, email, email.split("@")[0].title(), roles, "t")
claims = {}
def login(email):
    claims.clear(); claims.update({"name": email.split("@")[0].title(), "preferred_username": email, "oid": email})
m.auth._auth.get_user = lambda: dict(claims)
c = m.app.test_client()
page = lambda url: c.get(url, follow_redirects=True).get_data(as_text=True)
BASE = {"pay_date": "2026-12-20", "organization_id": "1", "counterparty_id": "1", "expense_code": "01.001",
        "currency": "UAH", "amount": "100000", "payment_form": "bank", "action": "submit"}

def to_pay(approver, account):
    """Заявка резидента: автор → бухгалтер (вибирає вид оплати) → фіндиректор → «До оплати»."""
    login(AUTHOR); c.post("/requests/new", data=BASE); rid = db.last_id()
    login(approver); c.post(f"/requests/{rid}/action", data={"action": "approve", "payment_account_id": account})
    login(CFO); c.post(f"/requests/{rid}/action", data={"action": "approve"})
    return rid

def listed(email, view=""):
    login(email)
    html = page("/to-pay" + (f"?view={view}" if view else ""))
    return {int(x) for x in re.findall(r'data-href="/requests/(\d+)"', html)}, html

KASA = to_pay(OLENA, "PT-KASA")
NAL = to_pay(IRINA, "PT-NAL")
app_settings.set("require_payment_account", False, "t")
EMPTY = to_pay(IRINA, "")
app_settings.set("require_payment_account", True, "t")
check("setup: three resident requests to pay", all(db.get_request(i)["status"] == "to_pay" for i in (KASA, NAL, EMPTY))
      and db.get_request(KASA)["payment_account_id"] == "PT-KASA" and not db.get_request(EMPTY).get("payment_account_id"))

# ---------- «Лише за роллю» (типово) — як було
check("default mode: by role", app_settings.get("pay_visibility") == "role" and not pay_access.strict())
mine, _ = listed(IRINA)
check("by role: Irina sees all three", {KASA, NAL, EMPTY} <= mine)
flush()
to_pay(OLENA, "PT-KASA")
mails = [to for to, s in flush() if "чекає оплати" in s]
check("by role: «чекає оплати» to both accountants", IRINA in mails and OLENA in mails)

# ---------- «За роллю і видом оплати з 1С»
login(ADMIN)
r = c.post("/admin/settings", data={"tab": "payment", "pay_visibility": "role_type", "require_payment_account": "1",
                                    "cash_handover_confirm": "1"}, follow_redirects=True)
check("switch saved from the settings tab", app_settings.get("pay_visibility") == "role_type" and pay_access.strict()
      and "Заявки до оплати бухгалтер бачить: за роллю і видом оплати з 1с" in r.get_data(as_text=True))

mine, html = listed(IRINA)
check("Irina: own type and no type, not KASA", NAL in mine and EMPTY in mine and KASA not in mine)
count = re.search(r'До оплати <span class="count">(\d+)</span>', html)
check("menu counter = the list", count and int(count.group(1)) == len(mine))
mine, _ = listed(OLENA)
check("Olena: KASA and no type, not NAL", KASA in mine and EMPTY in mine and NAL not in mine)
mine, _ = listed(CFO)
check("cfo sees all", {KASA, NAL, EMPTY} <= mine)

login(IRINA)
card = page(f"/requests/{KASA}")
check("card of someone else's type: note, no «Оплачено»", 'id="pay-type-note"' in card and 'value="pay"' not in card)
check("own type: «Оплачено» button", 'value="pay"' in page(f"/requests/{NAL}"))
c.post(f"/requests/{KASA}/action", data={"action": "pay"})
check("pay from the card refused", db.get_request(KASA)["status"] == "to_pay")
r = c.post("/to-pay/pay", data={"ids": [str(KASA)]}, follow_redirects=True)
check("bulk pay refused with the reason", db.get_request(KASA)["status"] == "to_pay"
      and "не ваша форма чи вид оплати" in r.get_data(as_text=True))
r = c.post(f"/requests/{KASA}/payment-account", data={"payment_account_id": "PT-NAL"}, follow_redirects=True)
check("cannot take it over by changing the type", db.get_request(KASA)["payment_account_id"] == "PT-KASA"
      and "Цю заявку оплачує інший бухгалтер" in r.get_data(as_text=True))
ir_roles = wf.RoleSet({"acc_resident"}, pay_types=pay_access.allowed_types(IRINA))
check("linking a 1C payment: only own type", not payments.can_link(db.get_request(KASA), ir_roles)
      and payments.can_link(db.get_request(NAL), ir_roles))

flush()
KASA2 = to_pay(OLENA, "PT-KASA")
mails = [to for to, s in flush() if "чекає оплати" in s]
check("«чекає оплати» only to the accountant of the type", OLENA in mails and IRINA not in mails)
check("notification log explains the skip", any(e["recipient"] == IRINA and "вам у 1С не призначено" in (e.get("detail") or "")
                                                for e in notify_log.query(50)))

login(OLENA)
c.post(f"/requests/{KASA}/action", data={"action": "pay", "comment": "ПД 5"})
check("Olena pays her request", db.get_request(KASA)["status"] == "paid")
paid_ol, _ = listed(OLENA, "paid")
paid_ir, _ = listed(IRINA, "paid")
check("«Оплачені»: only own types", KASA in paid_ol and KASA not in paid_ir)

# ---------- контроль «нікому оплатити»
check("nobody to pay: none of ours yet", not {r["id"] for r, _, _ in pay_access.orphans()} & {NAL, EMPTY, KASA2})
flush()
background.run_pay_orphans()  # демо-заявки з мокових рахунків теж «нікому» — лише запам'ятовуються
flush()
user_roles.set_roles(IRINA, IRINA, "Irina", ["initiator"], "t")  # Ірина більше не бухгалтер
found = {r["id"]: why for r, why, _ in pay_access.orphans()}
check("NAL now has nobody, with the reason", NAL in found and "НАЛ" in found[NAL] and "Бух. резидент" in found[NAL]
      and "без цього виду" in found[NAL] and EMPTY not in found and KASA2 not in found)
background.run_pay_orphans()
admin_mails = [s for to, s in flush() if to == ADMIN]
check("hourly check: one letter to the admin", len([s for s in admin_mails if "нікому з бухгалтерів оплатити" in s]) == 1
      and any(request_numbers.label(db.get_request(NAL)["number"]) in s for s in admin_mails))
background.run_pay_orphans()
check("second run: nothing new", not [s for to, s in flush() if to == ADMIN])
login(ADMIN)
settings = page("/admin/settings?tab=payment")
check("settings: list «Зараз нікому оплатити»", 'id="pay-orphans"' in settings and f"/requests/{NAL}" in settings)
_, html = listed(CFO)
row = html.split(f'data-href="/requests/{NAL}"')[1].split("</tr>")[0]
check("cfo: badge in «До оплати» and the summary", "нікому оплатити" in row and 'id="pay-orphans"' in html)
login(CFO)
check("cfo: red note in the card", 'id="pay-orphan"' in page(f"/requests/{NAL}"))

login(CFO)
c.post(f"/requests/{KASA2}/payment-account", data={"payment_account_id": "PT-CARD"})
check("type changed to one nobody has: admin told at once",
      any(to == ADMIN and "нікому з бухгалтерів оплатити" in s for to, s in flush()))

user_roles.set_roles(IRINA, IRINA, "Irina", ["acc_resident"], "t")
background.run_pay_orphans()
check("someone can pay again: case forgotten", str(NAL) not in pay_access._load() and not flush())
user_roles.set_roles(IRINA, IRINA, "Irina", ["initiator"], "t")
background.run_pay_orphans()
check("nobody again: a new letter", any(to == ADMIN and "нікому з бухгалтерів оплатити" in s for to, s in flush()))
user_roles.set_roles(IRINA, IRINA, "Irina", ["acc_resident"], "t")

# ---------- «Лише за роллю»: нікому — коли в ролі немає людей; попередній розрахунок
app_settings.set("pay_visibility", "role", "t")
check("by role: empty role is «nobody»", any("«Бух. нерезидент» немає жодного" in why for _, why, _ in pay_access.orphans()))
user_roles.set_roles(IRINA, IRINA, "Irina", ["initiator"], "t")
login(ADMIN)
settings = page("/admin/settings?tab=payment")
preview = settings.split('id="pay-preview"')[1].split("</div>")[0] if 'id="pay-preview"' in settings else ""
check("preview: what turning it on would hide", f"/requests/{NAL}" in preview)
user_roles.set_roles(IRINA, IRINA, "Irina", ["acc_resident"], "t")

# ---------- без даних 1С — запасний режим за роллю
app_settings.set("pay_visibility", "role_type", "t")
pa.use_types = lambda: False
check("no 1C types: strict rule off", not pay_access.strict() and pay_access.pay_types_for({"acc_resident"}, IRINA) is None)
login(ADMIN)
check("settings: warning", "поки не діє, бухгалтери бачать заявки за роллю" in page("/admin/settings?tab=payment"))
mine, _ = listed(OLENA)
check("fallback: Olena sees NAL again", NAL in mine)
print("ALL OK" if ok else "SOME FAILED")
