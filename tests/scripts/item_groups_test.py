"""Групи статей (item_groups.py): діють лише збережені; «Знайти групи за назвою» лише пропонує (ролі: позапланова —
ознака 1С «Не розподіляти» або «поза план» у назві, пряма — «(прямий)», решта — з розподілом), неоднозначні, «не
пропонувати», старі пари; ознака з таблиці Power BI; рядок групи у вкладці «Бюджет» з розгортанням на варіанти."""
import os, sys, tempfile
sys.path.insert(0, os.getcwd())
tmp = tempfile.mkdtemp()
os.environ.update(UPLOAD_DIR=tmp, ADMIN_EMAILS="adm@ftpua.com")
import app as m
import app_settings, budget, clock, expense_items, item_groups, user_roles

ok = True
def check(label, cond):
    global ok
    ok &= bool(cond); print(("PASS " if cond else "FAIL ") + label)

# ---------- ознака «Не розподіляти» з таблиці Expense_Item
rows = [{"Код": "A1", "Назва": "Меблі", "Відділ": "Адміністрація", "Тип_вузла": "Елемент", "Батько": None},
        {"Код": "A2", "Назва": "Меблі (прямий)", "Тип_вузла": "Елемент", "Батько": None},
        {"Код": "A3", "Назва": "Меблі (без плану)", "Тип_вузла": "Елемент", "Батько": None, "БазаРозподілу": "Не розподіляти"},
        {"Код": "B1", "Назва": "Таксі", "Тип_вузла": "Елемент", "Батько": None},
        {"Код": "B2", "Назва": "Таксі (прямий)", "Тип_вузла": "Елемент", "Батько": None},
        {"Код": "B3", "Назва": "Таксі (офіс)", "Тип_вузла": "Елемент", "Батько": None},
        {"Код": "K1", "Назва": "Корпоративи", "Тип_вузла": "Елемент", "Батько": None},
        {"Код": "K2", "Назва": "Корпоративи (поза план)", "Тип_вузла": "Елемент", "Батько": None},
        {"Код": "R1", "Назва": "Ремонт і сервіс", "Тип_вузла": "Елемент", "Батько": None},
        {"Код": "R2", "Назва": "Ремонт і сервіс (прямий)", "Тип_вузла": "Елемент", "Батько": None},
        {"Код": "R3", "Назва": "Ремонт і сервіс (позапланові)", "Тип_вузла": "Елемент", "Батько": None},
        {"Код": "S1", "Назва": "Оренда Склад (3800)", "Тип_вузла": "Елемент", "Батько": None},
        {"Код": "S2", "Назва": "Оренда Склад (9200)", "Тип_вузла": "Елемент", "Батько": None},
        {"Код": "C1", "Назва": "Зв'язок", "Тип_вузла": "Елемент", "Батько": None}]
items = expense_items.extract_items(rows)
by = {i["code"]: i for i in items}
check("extract: unplanned from the 1C base column", by["A3"]["unplanned"] and not by["A1"]["unplanned"]
      and not by["A2"]["unplanned"])
check("base name without trailing brackets", item_groups.base_name(" Меблі  (прямий) (2) ") == "меблі"
      and item_groups.base_name("(прямий)") == "(прямий)")
check("unplanned by name", item_groups.guess_role({"name": "Корпоративи (поза план)"}) == "unplanned"
      and item_groups.guess_role({"name": "Ремонт і сервіс (позапланові)"}) == "unplanned"
      and item_groups.guess_role({"name": "Ремонт і сервіс (прямий)"}) == "direct")

real = expense_items.get_items
expense_items.get_items = lambda: items
check("found groups do not work until added", item_groups.groups() == [] and item_groups.group_of("A2") is None)
found, ambiguous = item_groups.suggest()
check("suggest: variants by name, unplanned by flag or name",
      [{k: g[k] for k in item_groups.ROLES} for g in found]
      == [{"allocated": "K1", "direct": "", "unplanned": "K2"}, {"allocated": "A1", "direct": "A2", "unplanned": "A3"},
          {"allocated": "R1", "direct": "R2", "unplanned": "R3"}])
check("suggest: different items with the same base -> ambiguous", sorted(a["base"] for a in ambiguous)
      == ["оренда склад", "таксі"] and "з розподілом — 2 статті" in ambiguous[0]["why"])

# ---------- адмінка: «Знайти групи» → «Додати вибрані» / «Не пропонувати»
user_roles.set_roles("adm@ftpua.com", "adm@ftpua.com", "Adm", ["admin"], "t")
m.auth._auth.get_user = lambda: {"name": "adm", "preferred_username": "adm@ftpua.com", "oid": "adm@ftpua.com"}
m.app.config["WTF_CSRF_ENABLED"] = False
c = m.app.test_client()
html = c.get("/admin/settings?tab=split", follow_redirects=True).get_data(as_text=True)
check("admin: no search until the button", "Знайти групи за назвою" in html and 'id="ig-found"' not in html)
html = c.get("/admin/settings?tab=split&find=1", follow_redirects=True).get_data(as_text=True)
check("admin: found groups listed", 'id="ig-found"' in html and 'value="ремонт і сервіс"' in html
      and "Не вдалося розібрати: 2" in html)
html = c.post("/admin/settings", data={"tab": "split", "found_sent": "1", "found": ["ремонт і сервіс", "меблі"]},
              follow_redirects=True).get_data(as_text=True)
check("admin: selected found groups added", app_settings.get("item_groups") == [
    {"allocated": "R1", "direct": "R2", "unplanned": "R3"}, {"allocated": "A1", "direct": "A2", "unplanned": "A3"}]
      and item_groups.group_of("R3")["allocated"] == "R1" and 'id="ig-found"' in html)
c.post("/admin/settings", data={"tab": "split", "found_sent": "1", "no_suggest": "корпоративи"})
check("admin: «do not suggest»", app_settings.get("item_groups_skip") == ["корпоративи"]
      and item_groups.suggest()[0] == [] and len(app_settings.get("item_groups")) == 2)
c.post("/admin/settings", data={"tab": "split", "item_groups_sent": "1", "group_allocated": ["R1", "A1"],
                                "group_direct": ["R2", "A2"], "group_unplanned": ["R3", "A3"], "group_unskip": "корпоративи"})
check("admin: table save keeps groups, unskip returns the suggestion", app_settings.get("item_groups_skip") == []
      and [g["base"] for g in item_groups.suggest()[0]] == ["корпоративи"])
c.post("/admin/settings", data={"tab": "split", "item_groups_sent": "1", "group_allocated": ["R1", "B1"],
                                "group_direct": ["R2", "R2"], "group_unplanned": ["R3", ""]})
check("admin: item in two groups rejected", len(app_settings.get("item_groups")) == 2)
# ---------- «Додати групу вручну» і «Зібрати групу» з «Не вдалося розібрати»
html = c.get("/admin/settings?tab=split", follow_redirects=True).get_data(as_text=True)
check("admin: manual add card", 'id="ig-add"' in html and 'name="add_direct"' in html and "Додати групу вручну" in html)
html = c.post("/admin/settings", data={"tab": "split", "add_group_sent": "1", "add_allocated": "S1 — Оренда Склад (3800)",
                                       "add_direct": "", "add_unplanned": "оренда склад (9200)"},
              follow_redirects=True).get_data(as_text=True)
check("admin: manual group added (code or name)", app_settings.get("item_groups")[-1]
      == {"allocated": "S1", "direct": "", "unplanned": "S2"} and len(app_settings.get("item_groups")) == 3)
html = c.post("/admin/settings", data={"tab": "split", "add_group_sent": "1", "add_allocated": "C1", "add_direct": "R2"},
              follow_redirects=True).get_data(as_text=True)
check("admin: manual add — item already in a group rejected, input kept", len(app_settings.get("item_groups")) == 3
      and "указана двічі" in html and 'value="R2"' in html)
html = c.post("/admin/settings", data={"tab": "split", "add_group_sent": "1", "add_allocated": "C1"},
              follow_redirects=True).get_data(as_text=True)
check("admin: manual add — one variant rejected", len(app_settings.get("item_groups")) == 3 and "два варіанти" in html)
c.post("/admin/settings", data={"tab": "split", "add_group_sent": "1", "add_allocated": "C1", "add_direct": "A3"})
check("admin: manual add — 1C unplanned cannot be direct", len(app_settings.get("item_groups")) == 3)
app_settings.set("item_groups", [], "t")
html = c.get("/admin/settings?tab=split&find=1", follow_redirects=True).get_data(as_text=True)
check("admin: ambiguous list is information only", 'id="ig-ambiguous"' in html and 'name="amb_role"' not in html
      and "оренда склад" in html)
check("admin: empty role of a suggestion is an input", 'name="f_direct" list="split-items"' in html)
# «корпоративи» (K1 + K2 позапланова): дописати пряму вручну — S1 (будь-яка стаття з довідника для перевірки)
sug = [g["base"] for g in item_groups.suggest()[0]]
def found_form(direct_for_korp):
    data = {"tab": "split", "found_sent": "1", "found": "корпоративи", "f_base": [], "f_allocated": [], "f_direct": [],
            "f_unplanned": []}
    for g in item_groups.suggest()[0]:
        data["f_base"].append(g["base"])
        for r in item_groups.ROLES:
            data[f"f_{r}"].append(g[r] or (direct_for_korp if g["base"] == "корпоративи" and r == "direct" else ""))
    return c.post("/admin/settings", data=data, follow_redirects=True).get_data(as_text=True)
app_settings.set("item_groups", [{"allocated": "R1", "direct": "R2", "unplanned": "R3"}], "t")
html = found_form("R2")
check("admin: written-in item already in a group rejected", len(app_settings.get("item_groups")) == 1
      and "указана двічі" in html)
found_form("S1 — Оренда Склад (3800)")
check("admin: suggestion added with the written-in item", app_settings.get("item_groups")[-1]
      == {"allocated": "K1", "direct": "S1", "unplanned": "K2"})
c.post("/admin/settings", data={"tab": "split", "item_groups_sent": "1"})
check("admin: removing all rows from the table clears groups", app_settings.get("item_groups") == [])
c.post("/admin/settings", data={"tab": "split", "item_groups_sent": "1", "group_allocated": ["R1", "A1"],
                                "group_direct": ["R2", "A2"], "group_unplanned": ["R3", "A3"]})
app_settings.set("item_groups", [], "t")
app_settings.set("split_pairs", [{"direct": "B2", "allocated": "B1"}], "t")
check("old pairs act as groups", item_groups.group_of("B2")["allocated"] == "B1")
check("describe for the journal", item_groups.describe({"allocated": "A1", "direct": "A2", "unplanned": "A3"})
      == "Меблі: з розподілом A1 / пряма A2 / позапланова A3")
app_settings.set("split_pairs", [], "t")
expense_items.get_items = real

# ---------- вкладка «Бюджет»: варіанти статті — одним рядком з розгортанням (мок: 05.002 + 05.012 у відділі ІТ)
app_settings.set("budget_enabled", True, "t")
rep = budget.report(None, "IT", clock.today().year, list(range(1, 13)))
check("budget: no family row before the group is added", not any(r.get("family") for d in rep["departments"]
                                                                   for g in d["groups"] for r in g["rows"]))
app_settings.set("item_groups", [{"allocated": "05.002", "direct": "05.012", "unplanned": "05.022"}], "t")
rep = budget.report(None, "IT", clock.today().year, list(range(1, 13)))
rows_ = [r for d in rep["departments"] for g in d["groups"] for r in g["rows"]]
fam = next((r for r in rows_ if r.get("family")), None)
check("budget: family row with both variants", fam and fam["name"] == "Ремонт оргтехніки"
      and [c["code"] for c in fam["children"]] == ["05.002", "05.012"]
      and round(fam["plan"], 2) == round(sum(c["plan"] for c in fam["children"]), 2))
check("budget: variants are not separate top rows", not any(r.get("code") in ("05.002", "05.012") for r in rows_)
      and rep["count"] == len([r for r in rows_ if not r.get("family")]) + 2)
html = c.get("/budget?dep=IT", follow_redirects=True).get_data(as_text=True)
check("budget page: family row and its variants", 'data-fam-head="1"' in html and "bg-fam-child" in html
      and "варіантів: 2" in html)
html = c.get("/budget?dep=IT&view=months", follow_redirects=True).get_data(as_text=True)
check("budget page, months: family row", 'data-fam-head="1"' in html)

print("ALL OK" if ok else "SOME FAILED")
