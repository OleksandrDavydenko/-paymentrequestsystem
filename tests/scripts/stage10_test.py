import json, os, sys, tempfile, threading, time
sys.path.insert(0, os.getcwd())
tmp = tempfile.mkdtemp()
os.environ.update(UPLOAD_DIR=tmp, USER_BUDGET_FILE=os.path.join(tmp, "budget.json"),
                  EXPENSE_ITEMS_CACHE=os.path.join(tmp, "exp.json"),
                  GROUP_ADMIN="g-a", GROUP_INITIATOR="g-i", ADMIN_EMAILS="")
import app as m
import expense_items as ei
import user_budget as ub
import storage as db

ok = True
def check(label, cond):
    global ok
    ok &= bool(cond)
    print(("PASS " if cond else "FAIL ") + label)

# ---------- extract_items: відділи
rows = [
    {"Код": "G", "Назва": "Група", "Батько": None, "Тип_вузла": "Група", "Відділ": None},
    {"Код": "A1", "Назва": "Пряма стаття", "Батько": "G", "Тип_вузла": "Елемент", "Відділ": None},
    {"Код": "B1", "Назва": "Розподіл", "Батько": "G", "Тип_вузла": "Елемент", "Відділ": "БАА"},
    {"Код": "B1", "Назва": "Розподіл", "Батько": "G", "Тип_вузла": "Елемент", "Відділ": "ІТ"},
    {"Код": "B2", "Назва": "Тільки ІТ", "Батько": "G", "Тип_вузла": "Елемент", "Відділ": "ІТ"},
    {"Код": "B3", "Назва": "Порожній відділ", "Батько": "G", "Тип_вузла": "Елемент", "Відділ": ""},
]
items = ei.extract_items(rows)
by = {i["code"]: i for i in items}
check("group excluded", "G" not in by)
check("departments merged for one code", by["B1"]["departments"] == ["БАА", "ІТ"] and by["B1"]["kind"] == "allocated")
check("direct = no department", by["A1"]["kind"] == "direct" and by["B3"]["kind"] == "direct")

# ---------- allowed rules
codes = lambda s: [i["code"] for i in ub.allowed_items(s, items)]
check("no settings -> nothing", codes(None) == [])
check("direct only", codes({"direct": True}) == ["A1", "B3"])
check("dept BAA only", codes({"departments": ["БАА"]}) == ["B1"])
check("direct + IT", codes({"direct": True, "departments": ["ІТ"]}) == ["A1", "B1", "B2", "B3"])
check("all departments without direct", codes({"all_departments": True}) == ["B1", "B2"])
check("summary", ub.summary({"direct": True, "departments": ["БАА"]}) == [("Прямі", "direct"), ("БАА", "dep")])

# ---------- фонове оновлення
os.environ.update(PBI_GROUP_ID="g", PBI_DATASET_ID="d")
# Інші довідники з 1С у цьому тесті — з мокових рядків (не ходимо в Power BI з фіктивним датасетом)
import counterparties as _cps, organizations as _orgs, departments as _deps
_mock_tables = {
    "Counterparties": _cps.MOCK_ONEC,
    "Organizations": [{"Code": o["code"], "Name": o["name"], "Country": o["country"], "IsDeleted": False} for o in _orgs.MOCK],
    "Departments": [{"Код_відділу": k, "Назва_відділу": v} for k, v in _deps.MOCK_DEPARTMENTS.items()],
}
_real_qt = ei.query_table
ei.query_table = lambda table: _mock_tables[table] if table in _mock_tables else _real_qt(table)
calls = {"n": 0}
gate = threading.Event()
def slow_query():
    calls["n"] += 1
    gate.wait(5)
    return rows
ei._query_rows = slow_query
ei._state.update(items=[{"code": "OLD", "name": "Стара", "departments": [], "kind": "direct"}], loaded_at=0, source="powerbi")
t0 = time.time()
first = ei.get_items(); second = ei.get_items()
check("stale cache returned immediately", time.time() - t0 < 0.5 and first[0]["code"] == "OLD")
check("only one background refresh", calls["n"] == 1)
gate.set()
for _ in range(50):
    if not ei._state["refreshing"]:
        break
    time.sleep(0.05)
check("background refresh updated items", [i["code"] for i in ei.get_items()] == ["A1", "B1", "B2", "B3"])
# Перезапуск: є копія у файлі -> віддається одразу, без очікування
ei._state.update(items=None, loaded_at=0)
gate.clear()
t0 = time.time()
got = ei.get_items()
check("restart serves saved copy instantly", time.time() - t0 < 0.5 and got and ei._state["source"] == "copy")
gate.set()
time.sleep(0.3)

# ---------- адмінка й картка
claims = {}
def login(email, groups, oid):
    claims.clear(); claims.update({"name": email.split("@")[0], "preferred_username": email, "oid": oid, "groups": groups})
m.auth._auth.get_user = lambda: dict(claims)
m.auth._auth.get_token_for_user = lambda s: {"access_token": "t", "scope": " ".join(s)}
import graph
graph.group_members = lambda t, g: [{"id": "u-ira", "name": "Ірина", "email": "ira@ftpua.com"}] if g == "g-i" else []
c = m.app.test_client()

def admin_page():
    """Сторінки адмінки разом (користувачі + вкладки налаштувань) — для перевірок вмісту."""
    return "".join(c.get(u).get_data(as_text=True) for u in ["/admin/users"] + [
        f"/admin/settings?tab={t}" for t in ("approval", "request", "payment", "notify", "data")])
BASE = {"pay_date": "2026-10-10", "counterparty_id": "1", "organization_id": "1", "currency": "UAH",
        "amount": "100", "payment_form": "bank", "action": "save"}

login("ira@ftpua.com", ["g-i"], "u-ira")
t = c.get("/requests/new").get_data(as_text=True)
check("no budget -> notice, no picker", "не призначено статті бюджету" in t and 'id="exp-picker"' not in t)
r = c.post("/requests/new", data={**BASE, "expense_code": "A1"})
check("no budget -> cannot save", r.status_code == 200 and "не входить у ваш бюджет" in r.get_data(as_text=True))
r = c.post("/requests/new", data={**BASE})
check("no budget, empty code -> message", "не призначено статті бюджету" in r.get_data(as_text=True))

check("non-admin cannot set budget", c.post("/admin/users/u-ira", data={"mode": "all"}).status_code == 403)
login("od@ftpua.com", ["g-a"], "u-od")
t = admin_page()
check("admin table shows 'Не налаштовано'", "Не налаштовано" in t and "/admin/users/u-ira" in t)
r = c.post("/admin/users/u-ira", data={"roles": ["initiator"], "direct": "1", "mode": "selected",
                                       "departments": ["БАА", "Невідомий"]})
check("admin saved budget", r.status_code == 302)
saved = json.load(open(os.environ["USER_BUDGET_FILE"], encoding="utf-8"))["u-ira"]
check("file: direct + BAA, unknown dropped, audit", saved["direct"] and saved["departments"] == ["БАА"] and saved["updated_by"] == "od@ftpua.com")
t = admin_page()
check("chips in table", "bchip-direct" in t and ">БАА<" in t)
ub.reload()
check("reload from file keeps settings", ub.get("u-ira")["departments"] == ["БАА"])

login("ira@ftpua.com", ["g-i"], "u-ira")
t = c.get("/requests/new").get_data(as_text=True)
data = json.loads(t.split('id="expense-items-data">')[1].split("</script>")[0])
check("card has only allowed items", sorted(i["code"] for i in data) == ["A1", "B1", "B3"])
check("kind passed to picker", {i["code"]: i["kind"] for i in data}["B1"] == "allocated")
r = c.post("/requests/new", data={**BASE, "expense_code": "B2"})
check("other department rejected", r.status_code == 200 and "не входить у ваш бюджет" in r.get_data(as_text=True))
r = c.post("/requests/new", data={**BASE, "expense_code": "B1"})
rid = db.last_id()
check("allowed item saved", r.status_code == 302 and db.get_request(rid)["expense_code"] == "B1")
# Адмін звузив бюджет — стара стаття заявки все одно приймається
ub.save_settings("u-ira", "ira@ftpua.com", "Ірина", direct=True, all_departments=False, departments=[], updated_by="x")
r = c.post(f"/requests/{rid}", data={**BASE, "expense_code": "B1"})
check("existing code kept after budget change", r.status_code == 302)
login("od@ftpua.com", ["g-a"], "u-od")
c.post("/admin/users/u-ira", data={"roles": ["initiator"], "mode": "all"})
check("mode all saved", ub.get("u-ira")["all_departments"] and not ub.get("u-ira")["direct"])
print("ALL OK" if ok else "SOME FAILED")
