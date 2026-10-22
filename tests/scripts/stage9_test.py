import json, os, sys, tempfile
sys.path.insert(0, os.getcwd())
tmp = tempfile.mkdtemp()
os.environ.update(UPLOAD_DIR=tmp, EXPENSE_ITEMS_CACHE=os.path.join(tmp, "exp.json"), GROUP_ADMIN="g-a",
                  GROUP_INITIATOR="g-i", GROUP_ACC_RESIDENT="g-r", ADMIN_EMAILS="")
import app as m
import user_budget; user_budget.get = lambda oid: {'direct': True, 'all_departments': True, 'departments': []}
import expense_items as ei
import storage as db

ok = True
def check(label, cond):
    global ok
    ok &= bool(cond)
    print(("PASS " if cond else "FAIL ") + label)

# ---------- 1. Фільтр груп
rows = [
    {"Код": "01", "Назва": "Логістика", "Батько": None, "Тип_вузла": "Група", "Відділ": None},
    {"Код": "01.001", "Назва": "Транспортні послуги", "Батько": "01", "Тип_вузла": "Елемент", "Відділ": "Логістика"},
    {"Код": "01.010", "Назва": "Фрахт", "Батько": "01", "Тип_вузла": "Елемент", "Відділ": None},
    {"Код": "01.002", "Назва": "Митне оформлення", "Батько": "01", "Тип_вузла": "Елемент", "Відділ": None},
    {"Код": "02", "Назва": "ІТ", "Батько": None, "Тип_вузла": "Елемент", "Відділ": None},          # група за дітьми
    {"Код": "02.001", "Назва": "Інтернет", "Батько": "02", "Тип_вузла": "Елемент", "Відділ": None},
    {"Код": "03", "Назва": "Папка без дітей", "Батько": None, "Тип_вузла": "Папка", "Відділ": None},
    {"Код": "", "Назва": "Порожній код", "Батько": None, "Тип_вузла": "Елемент", "Відділ": None},
    {"Код": "02.001", "Назва": "Дублікат", "Батько": "02", "Тип_вузла": "Елемент", "Відділ": None},
]
items = ei.extract_items(rows)
check("only elements, natural order", [i["code"] for i in items] == ["01.001", "01.002", "01.010", "02.001"])
check("item fields", set(items[0]) == {"code", "name", "departments", "kind"})

# ---------- 2. Power BI: кеш, помилка, збережена копія
# Інші довідники (контрагенти, організації…) лишаються моковими: завантажуємо їх до «підключення» Power BI
import gc, pbi_cache
for t in [o for o in gc.get_objects() if isinstance(o, pbi_cache.CachedTable)]:
    t.items()
os.environ.update(PBI_GROUP_ID="g", PBI_DATASET_ID="d", CLIENT_ID="x", CLIENT_SECRET="x", TENANT_ID="t")
calls = {"n": 0, "fail": False}
def fake_query():
    calls["n"] += 1
    if calls["fail"]:
        raise RuntimeError("Power BI повернув 403")
    return rows
ei._query_rows = fake_query
ei._state.update(items=None, loaded_at=0)
check("refresh from PBI ok", ei.refresh() and ei.status()["source"] == "powerbi" and ei.status()["count"] == 4)
n = calls["n"]; ei.get_items(); ei.get_items()
check("cached (no extra PBI calls)", calls["n"] == n)
check("copy saved to file", json.load(open(os.environ["EXPENSE_ITEMS_CACHE"], encoding="utf-8"))["items"][0]["code"] == "01.001")
calls["fail"] = True
check("refresh failure returns False", ei.refresh() is False)
check("items kept after failure", ei.status()["count"] == 4 and "403" in ei.status()["error"])
ei._state.update(items=None, loaded_at=0)  # «перезапуск» + Power BI недоступний
ei.get_items()
check("restart + PBI down -> saved copy", ei.status()["source"] == "copy" and ei.status()["count"] == 4)
calls["fail"] = False
ei.refresh()

# ---------- 3. Заявки з кодом статті
claims = {}
def login(email, groups):
    claims.clear(); claims.update({"name": email, "preferred_username": email, "oid": email, "groups": groups})
m.auth._auth.get_user = lambda: dict(claims)
c = m.app.test_client()

def admin_page():
    """Сторінки адмінки разом (користувачі + вкладки налаштувань) — для перевірок вмісту."""
    return "".join(c.get(u).get_data(as_text=True) for u in ["/admin/users"] + [
        f"/admin/settings?tab={t}" for t in ("approval", "request", "payment", "notify", "data")])
BASE = {"pay_date": "2026-10-10", "counterparty_id": "1", "organization_id": "1", "currency": "UAH",
        "amount": "100", "payment_form": "bank", "action": "save"}
login("i@ftpua.com", ["g-i"])
t = c.get("/requests/new").get_data(as_text=True)
check("card has picker + data", 'id="exp-picker"' in t and '"01.010"' in t and "expense-picker.js" in t)
r = c.post("/requests/new", data={**BASE, "expense_code": "01.010"})
rid = db.last_id()
check("saved with code", r.status_code == 302 and db.get_request(rid)["expense_code"] == "01.010")
r = c.post("/requests/new", data={**BASE, "expense_code": "99.999"})
check("unknown code rejected", r.status_code == 200 and "Такої статті немає" in r.get_data(as_text=True))
r = c.post("/requests/new", data={**BASE})
check("missing code rejected", r.status_code == 200 and "Оберіть статтю витрат" in r.get_data(as_text=True))
check("list shows code — name", "01.010 — Фрахт" in c.get("/requests").get_data(as_text=True))
check("filter by code", c.get("/requests?exp=01.010").get_data(as_text=True).count('<tr class="clickable') == 1)

# Стара стаття, якої вже немає в довіднику, приймається для заявки, де вона вже стоїть
req = db.get_request(rid); req["expense_code"] = "OLD.1"; db.save_request(req)
r = c.post(f"/requests/{rid}", data={**BASE, "expense_code": "OLD.1"})
check("legacy code of the same request accepted", r.status_code == 302)
check("legacy code shown as code", "OLD.1" in c.get(f"/requests/{rid}").get_data(as_text=True))

# ---------- 4. Адмін: статус і оновлення
check("non-admin refresh -> 403", c.post("/admin/expense-items/refresh").status_code == 403)
login("od@ftpua.com", ["g-a"])
m.auth._auth.get_token_for_user = lambda s: {"access_token": "t", "scope": " ".join(s)}
import graph
graph.group_members = lambda t, g: []
n = calls["n"]
r = c.post("/admin/expense-items/refresh")
check("admin refresh -> PBI called", r.status_code == 302 and calls["n"] == n + 1)
t = admin_page()
check("admin page shows directory status", "Довідник статей витрат" in t and "Power BI" in t)
print("ALL OK" if ok else "SOME FAILED")
login("res@ftpua.com", ["g-r"])
t = c.get(f"/requests/{rid}").get_data(as_text=True)
check("readonly card: code chip + 'немає в довіднику'", 'class="exp-readonly exp-missing' in t and '<span class="exp-code-chip">OLD.1</span>' in t)
req = db.get_request(rid); req["expense_code"] = "01.010"; db.save_request(req)
t = c.get(f"/requests/{rid}").get_data(as_text=True)
check("readonly card: code chip + name", '<span class="exp-code-chip">01.010</span>' in t and '<span class="exp-readonly-name">Фрахт</span>' in t)
print("ALL OK" if ok else "SOME FAILED")
