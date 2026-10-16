import os, sys, tempfile
sys.path.insert(0, os.getcwd())
tmp = tempfile.mkdtemp()
os.environ.update(UPLOAD_DIR=tmp, APP_SETTINGS_FILE=os.path.join(tmp, "s.json"), USER_PREFS_FILE=os.path.join(tmp, "p.json"),
                  USER_DIRECTORY_FILE=os.path.join(tmp, "d.json"), ORG_STRUCTURE_FILE=os.path.join(tmp, "o.json"),
                  COUNTERPARTIES_PENDING_FILE=os.path.join(tmp, "cp.json"),
                  GROUP_ADMIN="g-a", GROUP_INITIATOR="g-i", GROUP_ACC_RESIDENT="g-r", GROUP_ACC_CASH="g-c", GROUP_CFO="g-f",
                  GROUP_ACC_NONRESIDENT="g-n", ADMIN_EMAILS="")
import app as m
import user_budget; user_budget.get = lambda oid: {"direct": True, "all_departments": True, "departments": []}
import app_settings, mailer, notifications
import storage as db

ok = True
def check(label, cond):
    global ok
    ok &= bool(cond)
    print(("PASS " if cond else "FAIL ") + label)

sent = []
mailer.transport = lambda cfg, to, subject, html: sent.append((to, html))
notifications.role_members = lambda role: [{"email": "cash@ftpua.com", "name": "Cash"}] if role == "acc_cash" else []
for k, v in {"mail_enabled": True, "mail_sender": "noreply@ftpua.com"}.items():
    app_settings.set(k, v, "t")
claims = {}
def login(email, groups):
    claims.clear(); claims.update({"name": email.split("@")[0].title(), "preferred_username": email, "oid": email, "groups": groups})
m.auth._auth.get_user = lambda: dict(claims)
m.auth._auth.get_token_for_user = lambda s: {"access_token": "t", "scope": " ".join(s)}
c = m.app.test_client()

PAGES = {  # сторінка -> ролі
    "/requests": ["g-i"], "/requests?view=paid": ["g-i"], "/approvals": ["g-c"], "/approvals?view=accountants": ["g-f"],
    "/to-pay": ["g-c"], "/to-pay?view=paid": ["g-c"], "/to-pay?view=handover": ["g-c"], "/admin/requests": ["g-a"],
}

def org_visible():
    res = {}
    for url, groups in PAGES.items():
        login("od@ftpua.com", groups)
        html = c.get(url).get_data(as_text=True)
        res[url] = ("<th>Організація</th>" in html, 'name="org"' in html, "-noorg" in html)
    return res

base = org_visible()
check("organization mode: column + filter everywhere, no -noorg", all(v == (True, True, False) for v in base.values()))

app_settings.set("org_field_mode", "accountant", "t")
acc = org_visible()
for url, v in acc.items():
    check(f"accountant mode {url}: no column, no filter, -noorg id", v == (False, False, True))

# лист без рядка «Організація» для заявки без організації
login("author@ftpua.com", ["g-i"])
sent.clear()
c.post("/requests/new", data={"pay_date": "2026-10-20", "counterparty_id": "1", "expense_code": "01.001", "currency": "UAH",
                              "amount": "100", "payment_form": "cash", "accountant_type": "acc_cash", "action": "submit"})
mailer.wait()
html = next(h for to, h in sent if to == "cash@ftpua.com")
check("mail without organization row", ">Організація<" not in html and ">Контрагент<" in html)

app_settings.set("org_field_mode", "payer", "t")
check("payer mode: column + filter back", all(v[:2] == (True, True) for v in org_visible().values()))

print("ALL OK" if ok else "SOME FAILED")
