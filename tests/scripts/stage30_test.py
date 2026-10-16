import os, re, sys, tempfile
sys.path.insert(0, os.getcwd())
tmp = tempfile.mkdtemp()
os.environ.update(UPLOAD_DIR=tmp, APP_SETTINGS_FILE=os.path.join(tmp, "s.json"), USER_PREFS_FILE=os.path.join(tmp, "p.json"),
                  USER_DIRECTORY_FILE=os.path.join(tmp, "d.json"), ORG_STRUCTURE_FILE=os.path.join(tmp, "o.json"),
                  COUNTERPARTIES_PENDING_FILE=os.path.join(tmp, "cp.json"),
                  GROUP_ADMIN="g-a", GROUP_INITIATOR="g-i", GROUP_ACC_RESIDENT="g-r", GROUP_ACC_CASH="g-c", GROUP_CFO="g-f",
                  ADMIN_EMAILS="")
import app as m
import mock_data as db
import counterparties

ok = True
def check(label, cond):
    global ok
    ok &= bool(cond); print(("PASS " if cond else "FAIL ") + label)

claims = {}
def login(email, groups):
    claims.clear(); claims.update({"name": email.split("@")[0].title(), "preferred_username": email, "oid": email, "groups": groups})
m.auth._auth.get_user = lambda: dict(claims)
c = m.app.test_client()
def numbers(html):
    body = html.split("<tbody>")[1].split("</tbody>")[0] if "<tbody>" in html else ""
    return set(re.findall(r">(\d{9})<", body))

login("od@ftpua.com", ["g-a", "g-f"])
allr = db.list_all()
html = c.get("/admin/requests").get_data(as_text=True)
check("filter bar rendered", "data-fbar" in html and 'name="q"' in html and 'class="fpop' in html and "Знайдено" in html)
check("no old selects", "Усі контрагенти" not in html and "Усі статуси" not in html)

# кілька значень одного фільтра
cps = sorted({r["counterparty_id"] for r in allr})[:2]
want = {r["number"] for r in allr if r["counterparty_id"] in cps}
got = numbers(c.get(f"/admin/requests?cp={cps[0]}&cp={cps[1]}").get_data(as_text=True))
check("multi-select counterparties", got == want and len(cps) == 2)
# статуси кількома
want = {r["number"] for r in allr if r["status"] in ("draft", "to_pay")}
check("multi-select statuses", numbers(c.get("/admin/requests?status=draft&status=to_pay").get_data(as_text=True)) == want)

# кількість біля варіантів враховує інші фільтри
html = c.get("/admin/requests?status=to_pay").get_data(as_text=True)
cp_block = html.split('data-key="cp"')[1].split("</details>")[0]
counts = dict(re.findall(r'name="cp" value="([^"]+)"[^>]*>\s*<span class="fpop-text">[^<]*</span><span class="fpop-n">(\d+)</span>', cp_block))
exp_counts = {}
for r in allr:
    if r["status"] == "to_pay":
        exp_counts[r["counterparty_id"]] = exp_counts.get(r["counterparty_id"], 0) + 1
check("facet counts respect other filters", counts and {k: int(v) for k, v in counts.items()} == exp_counts)
check("status facet shows active state + chip", 'class="fpop is-active" data-key="status"' in html and "Статус: До оплати" in html)
check("chip remove url drops only that value", 'href="/admin/requests"' in html)

# пошук: номер, ЄДРПОУ, сума
r0 = allr[0]
check("search by number", numbers(c.get(f"/admin/requests?q={r0['number']}").get_data(as_text=True)) == {r0["number"]})
cp = counterparties.get(r0["counterparty_id"])
if cp and cp.get("edrpou"):
    found = numbers(c.get(f"/admin/requests?q={cp['edrpou']}").get_data(as_text=True))
    check("search by ЄДРПОУ", r0["number"] in found and all(
        db.get_request(int(n))["counterparty_id"] == r0["counterparty_id"] for n in found))
amount = f"{r0['amount']:.2f}"
check("search by amount", r0["number"] in numbers(c.get(f"/admin/requests?q={amount}").get_data(as_text=True)))
words = c.get("/admin/requests?q=zzzz-nothing").get_data(as_text=True)
check("search no results + chip", not numbers(words) and "Пошук: «zzzz-nothing»" in words and "Знайдено 0 заявок" in words)

# «До оплати»: вид оплати й «Платимо з» як фільтри, «Не вибрано»
login("od@ftpua.com", ["g-c", "g-f"])
html = c.get("/to-pay").get_data(as_text=True)
check("to-pay facets: form/acc/payorg", 'data-key="acc"' in html and 'data-key="payorg"' in html and 'data-key="form"' in html)
to_pay = db.list_to_pay(frozenset({"cfo"}))
none_acc = {r["number"] for r in to_pay if not r.get("payment_account_id")}
check("acc=none filter", numbers(c.get("/to-pay?acc=none").get_data(as_text=True)) == none_acc)

# Мої заявки: фільтр, а «view» зберігається при скиданні
login("od@ftpua.com", ["g-i"])
html = c.get("/requests?view=paid&q=000").get_data(as_text=True)
check("reset keeps view", 'class="fchip-reset" href="/requests?view=paid"' in html)
check("paid view: no status facet", 'data-key="status"' not in html)
print("ALL OK" if ok else "SOME FAILED")
