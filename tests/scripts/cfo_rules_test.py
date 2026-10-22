"""Правила фіндиректора: за типом бухгалтера (завжди / від суми / ніколи), валюта порогу, новий контрагент,
статті зі списку; пояснення в історії; правила без збереженої таблиці — з порогу (як до правил)."""
import os, sys, tempfile
sys.path.insert(0, os.getcwd())
tmp = tempfile.mkdtemp()
os.environ.update(UPLOAD_DIR=tmp, GROUP_ADMIN="g-a", GROUP_INITIATOR="g-i", ADMIN_EMAILS="")
import app as m
import user_budget; user_budget.get = lambda oid: {"direct": True, "all_departments": True, "departments": []}
import app_settings, cfo_rules, counterparties, settings_log
import storage as db

ok = True
def check(label, cond):
    global ok
    ok &= bool(cond); print(("PASS " if cond else "FAIL ") + label)

claims = {}
def login(email, groups):
    claims.clear(); claims.update({"name": email, "preferred_username": email, "oid": email, "groups": groups})
m.auth._auth.get_user = lambda: dict(claims)
m.auth._auth.get_token_for_user = lambda s: {"access_token": "t", "scope": " ".join(s)}
c = m.app.test_client()

RULES = {"tab": "approval", "cfo_mode": "threshold", "approval_mode": "sequential", "dept_head_mode": "view",
         "cfo_rules_sent": "1", "cfo_rule_acc_cash": "always", "cfo_limit_acc_cash": "1000",
         "cfo_rule_acc_resident": "threshold", "cfo_limit_acc_resident": "1000",
         "cfo_rule_acc_nonresident": "never", "cfo_limit_acc_nonresident": "1000", "cfo_currency": "USD"}

def save(**kw):
    login("admin@ftpua.com", ["g-a"])
    return c.post("/admin/settings", data={**RULES, **kw}, follow_redirects=True).get_data(as_text=True)

def submit(amount, form="bank", org="1", cur="UAH"):
    login("author@ftpua.com", ["g-i"])
    c.post("/requests/new", data={"pay_date": "2026-11-20", "organization_id": org, "counterparty_id": "1",
                                  "expense_code": "01.001", "currency": cur, "amount": amount, "payment_form": form,
                                  "action": "submit"})
    r = db.get_request(db.last_id())
    note = next((h["comment"] for h in r["history"] if h["action"] == "cfo_threshold"), "")
    return r["cfo_step"], note

# ---------- без збереженої таблиці — з порогу, як до правил
app_settings.set("cfo_mode", "threshold", "t"); app_settings.set("cfo_threshold_usd", 500, "t")
check("legacy: rows from threshold", all(r == {"mode": "threshold", "limit": 500} for r in cfo_rules.rows().values())
      and cfo_rules.short() == "від 500 USD")

# ---------- таблиця правил
html = save()
check("rules saved + route flash", app_settings.get("cfo_rules")["acc_cash"]["mode"] == "always"
      and "Фіндиректор (за правилами)" in html and "готівка — завжди" in html)
step, note = submit("100", form="cash")
check("cash, small amount -> cfo always", step is True and "бухгалтер по готівці — завжди" in note)
step, note = submit("1000")
check("resident below 1 000 USD -> no cfo", step is False and "менше за поріг 1 000 USD" in note)
step, note = submit("100000")
check("resident above -> cfo", step is True and "не менше за поріг 1 000 USD" in note and "курс 1С" in note)
step, note = submit("900000", org="4")
check("nonresident 'never' -> no cfo", step is False and "без фіндиректора" in note)

# ---------- валюта порогу EUR
save(cfo_currency="EUR")
step, note = submit("45000")   # ≈ 894 EUR
check("EUR threshold: below", step is False and "EUR" in note and "менше за поріг 1 000 EUR" in note)
step, note = submit("60000")   # ≈ 1 192 EUR
check("EUR threshold: above", step is True and "не менше за поріг 1 000 EUR" in note)

# ---------- додаткові умови
save(cfo_new_counterparty="1")
real = counterparties.is_pending
counterparties.is_pending = lambda cp: True
step, note = submit("900000", org="4")
check("new counterparty -> cfo even if 'never'", step is True and "контрагента ще немає в 1С" in note)
counterparties.is_pending = real
save(cfo_expense_codes=["01.001"])
step, note = submit("100", org="4")
check("expense code in list -> cfo", step is True and "01.001" in note and "у списку статей" in note)
check("route summary: за правилами", cfo_rules.short() == "за правилами")

# ---------- перевірка форми й журнал
before = app_settings.get("cfo_rules")
html = save(cfo_limit_acc_resident="0")
check("threshold 0 rejected, nothing saved", "більшу за 0" in html and app_settings.get("cfo_rules") == before)
row = next(x for x in settings_log.query(key="cfo_rules"))
check("journal: readable rules", "готівка — завжди" in row["new_label"] and "нерезиденти — ніколи" in row["new_label"])
page = c.get("/admin/settings?tab=approval").get_data(as_text=True)
check("approval tab: rules table + codes list", 'name="cfo_rule_acc_cash"' in page and 'name="cfo_expense_codes"' in page
      and "Валюта порогу" in page)

# ---------- режими «завжди» / «спостерігає» не залежать від таблиці
save(cfo_mode="approve")
step, note = submit("100", org="4")
check("mode 'approve' -> cfo regardless of rules", step is True and note == "")
save(cfo_mode="observe")
step, note = submit("900000", form="cash")
check("mode 'observe' -> never cfo", step is False)
print("ALL OK" if ok else "SOME FAILED")
