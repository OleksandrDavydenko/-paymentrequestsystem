import os, sys, tempfile
from decimal import Decimal
sys.path.insert(0, os.getcwd())
os.environ.update(UPLOAD_DIR=tempfile.mkdtemp(), GROUP_ADMIN="g-a", GROUP_INITIATOR="g-i", GROUP_ACC_CASH="g-c",
                  GROUP_ACC_RESIDENT="g-r", GROUP_ACC_NONRESIDENT="g-n", GROUP_CFO="g-f", ADMIN_EMAILS="")
import app as m
import user_budget; user_budget.get = lambda oid: {'direct': True, 'all_departments': True, 'departments': []}
import mock_data as db
import workflow as wf

ok = True
def check(label, cond):
    global ok
    ok &= bool(cond)
    print(("PASS " if cond else "FAIL ") + label)

ALL = ["g-a", "g-i", "g-c", "g-r", "g-n", "g-f"]
m.auth._auth.get_user = lambda: {"name": "OD", "preferred_username": "od@ftpua.com", "oid": "od", "groups": ALL}
c = m.app.test_client()
rows = lambda url: c.get(url).get_data(as_text=True).count('<tr class="clickable')
roles = {"admin", "initiator", "acc_cash", "acc_resident", "acc_nonresident", "cfo"}

sources = {
    "/requests": lambda: [r for r in db.list_requests("od@ftpua.com") if r["status"] != "paid"],  # оплачені — окрема вкладка
    "/approvals": lambda: db.list_queue(roles),
    "/to-pay": lambda: db.list_to_pay(roles),
    "/admin/requests": lambda: db.list_all(),
}
for url, src in sources.items():
    base = src()
    check(f"{url}: no filter = {len(base)}", rows(url) == len(base))
    for key, field in [("org", "organization_id"), ("cp", "counterparty_id"), ("exp", "expense_code")]:
        values = sorted({r[field] for r in base})
        for v in values[:2]:
            exp = sum(1 for r in base if r[field] == v)
            check(f"{url}?{key}={v} -> {exp}", rows(f"{url}?{key}={v}") == exp)
    if base:
        r0 = base[0]
        exp = sum(1 for r in base if r["organization_id"] == r0["organization_id"] and r["counterparty_id"] == r0["counterparty_id"])
        check(f"{url} org+cp combined -> {exp}", rows(f"{url}?org={r0['organization_id']}&cp={r0['counterparty_id']}") == exp)
    check(f"{url} unknown value -> nothing found (chip to remove)", rows(f"{url}?cp=999&org=abc") == 0)
    t = c.get(f"{url}?cp=5").get_data(as_text=True)
    check(f"{url} has filters + reset", 'name="cp"' in t and "Скинути все" in t and 'main class="wide"' in t)

# комбінація з наявними фільтрами
exp = sum(1 for r in db.list_requests("od@ftpua.com") if r["status"] == "draft" and r["counterparty_id"] == "5")
check(f"/requests status+cp -> {exp}", rows("/requests?status=draft&cp=5") == exp)
pay = [r for r in db.list_to_pay(roles) if r["counterparty_id"] == 5]
t = c.get("/to-pay?cp=5").get_data(as_text=True).replace(" ", " ")
eur = sum((r["amount"] for r in pay if r["currency"] == "EUR"), Decimal(0))
check(f"/to-pay?cp=5 EUR total {eur}", m.money(eur).replace(" ", " ") in t and "32 579,99" not in t)
exp = sum(1 for r in db.list_all() if wf.channel_of(r) == "bank_resident" and r["organization_id"] == "1")
check(f"/admin/requests form+org -> {exp}", rows("/admin/requests?form=bank_resident&org=1") == exp)
check("table-tools.js linked", "table-tools.js" in c.get("/requests").get_data(as_text=True))
print("ALL OK" if ok else "SOME FAILED")
