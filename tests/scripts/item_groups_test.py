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

# ---------- адмінка: один розділ — таблиця груп, «Заповнити за назвою» (JSON на сторінці), «Зберегти»
user_roles.set_roles("adm@ftpua.com", "adm@ftpua.com", "Adm", ["admin"], "t")
m.auth._auth.get_user = lambda: {"name": "adm", "preferred_username": "adm@ftpua.com", "oid": "adm@ftpua.com"}
m.app.config["WTF_CSRF_ENABLED"] = False
c = m.app.test_client()
html = c.get("/admin/settings?tab=split", follow_redirects=True).get_data(as_text=True)
check("admin: one form with the table and the fill button", html.count('id="split-settings"') == 1
      and 'id="ig-fill"' in html and 'id="ig-found"' not in html and 'id="ig-add"' not in html)
check("admin: suggestions for «fill by name» are on the page", '"suggest"' in html and "R2 — Ремонт і сервіс (прямий)" in html)
check("admin: ambiguous list is information only", "Не вдалося розібрати за назвою: 2" in html and "оренда склад" in html
      and 'name="amb_role"' not in html)

def save(rows):
    data = {"tab": "split", "item_groups_sent": "1"}
    for r in item_groups.ROLES:
        data[f"group_{r}"] = [row.get(r, "") for row in rows]
    return c.post("/admin/settings", data=data, follow_redirects=True).get_data(as_text=True)

html = save([{"allocated": "R1 — Ремонт і сервіс", "direct": "R2 — Ремонт і сервіс (прямий)", "unplanned": "R3"},
             {"allocated": "K1", "direct": "S1 — Оренда Склад (3800)", "unplanned": "корпоративи (поза план)"}, {}])
check("admin: table saved (code, «code — name» or name)", app_settings.get("item_groups") == [
    {"allocated": "R1", "direct": "R2", "unplanned": "R3"}, {"allocated": "K1", "direct": "S1", "unplanned": "K2"}])
check("admin: saved groups are not suggested again", [g["base"] for g in item_groups.suggest()[0]] == ["меблі"])
html = save([{"allocated": "R1", "direct": "R2", "unplanned": "R3"}, {"allocated": "A1", "direct": "R2"},
             {"allocated": "B1", "direct": "B2"}])
check("admin: item twice rejected, the sent table comes back", len(app_settings.get("item_groups")) == 2
      and "указана двічі" in html and 'value="B2" data-role="direct"' in html and 'value="A1" data-role="allocated"' in html)
html = c.get("/admin/settings?tab=split", follow_redirects=True).get_data(as_text=True)
check("admin: draft shown once, then saved groups again", 'value="B2" data-role' not in html and "K1 — Корпоративи" in html)
save([{"allocated": "C1"}])
check("admin: one variant rejected", len(app_settings.get("item_groups")) == 2)
save([{"allocated": "C1", "direct": "A3"}])
check("admin: 1C unplanned cannot be direct", len(app_settings.get("item_groups")) == 2)
save([])
check("admin: empty table clears groups", app_settings.get("item_groups") == [])
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
check("budget: children marked with their role", [c["role"] for c in fam["children"]] == ["allocated", "direct"])
# один варіант у відділі (05.003 + 05.013 → лише 05.003 у LOG) — теж рядок групи з одним варіантом
app_settings.set("item_groups", [{"allocated": "05.002", "direct": "05.012", "unplanned": "05.022"},
                                 {"allocated": "05.001", "direct": "05.003", "unplanned": ""}], "t")
rep = budget.report(None, "LOG", clock.today().year, list(range(1, 13)))
one = next((r for d in rep["departments"] for g in d["groups"] for r in g["rows"] if r.get("family")), None)
check("budget: one variant in the department -> group row with it", one and one["name"] == "Ремонт та обслуговування"
      and [(c["code"], c["role"]) for c in one["children"]] == [("05.003", "direct")])
# відділ «за групами статей»: варіанти з різних папок 1С — в одному рядку, у папці варіанта з розподілом
real_rows = budget.rows
def brow(code, name, group, plan):
    return {"Month": f"{clock.today().year}-01-01", "DepartmentGUID": "DEP-IT", "DepartmentName": "ІТ",
            "ExpenseItemGUID": "I-" + code, "ExpenseItemCode": code, "ExpenseItemName": name, "ExpenseGroup": group,
            "OwnerDepartmentGUID": "0" * 32, "PlanUSD": plan, "FactUSD": 0}
budget.rows = lambda: budget.parse([brow("05.002", "Ремонт оргтехніки", "Офіс", 100),
                                    brow("05.012", "Ремонт оргтехніки (прямий)", "Прямі", 40),
                                    brow("02.001", "Зв'язок та інтернет", "Прямі", 7)])
rep = budget.report(None, "IT", clock.today().year, list(range(1, 13)), grouped={"IT"})
gs = {g["name"]: g for d in rep["departments"] for g in d["groups"]}
check("budget grouped dept: variants from different folders joined in the allocated one's folder",
      gs["Офіс"]["rows"][0].get("family") and len(gs["Офіс"]["rows"][0]["children"]) == 2
      and round(gs["Офіс"]["plan"], 2) == 140 and round(gs["Прямі"]["plan"], 2) == 7)
budget.rows = real_rows
app_settings.set("item_groups", [{"allocated": "05.002", "direct": "05.012", "unplanned": "05.022"}], "t")
html = c.get("/budget?dep=IT", follow_redirects=True).get_data(as_text=True)
check("budget page: family row and its variants", 'data-fam-head="1"' in html and "bg-fam-child" in html
      and "варіантів: 2" in html and "bg-role" in html)
html = c.get("/budget?dep=IT&view=months", follow_redirects=True).get_data(as_text=True)
check("budget page, months: family row", 'data-fam-head="1"' in html)
# вимикач «Варіанти статті — одним рядком» (budget_group_variants): вимкнено — кожен варіант окремим рядком
rep = budget.report(None, "IT", clock.today().year, list(range(1, 13)), variants=False)
rows_ = [r for d in rep["departments"] for g in d["groups"] for r in g["rows"]]
check("budget: variants off -> separate rows", not any(r.get("family") for r in rows_)
      and {"05.002", "05.012"} <= {r.get("code") for r in rows_})
c.post("/admin/settings", data={"tab": "budget", "budget_enabled": "1"})
check("admin: unchecked «variants in one row» saved", app_settings.get("budget_group_variants") is False)
html = c.get("/budget?dep=IT", follow_redirects=True).get_data(as_text=True)
check("budget page: variants off -> no family row", 'data-fam-head="1"' not in html)
c.post("/admin/settings", data={"tab": "budget", "budget_enabled": "1", "budget_group_variants": "1"})
html = c.get("/budget?dep=IT", follow_redirects=True).get_data(as_text=True)
check("budget page: variants on again -> family row", app_settings.get("budget_group_variants") is True
      and 'data-fam-head="1"' in html)

print("ALL OK" if ok else "SOME FAILED")
