"""Групи статей (item_groups.py): автоматично за назвою (ролі: позапланова — ознака 1С «Не розподіляти», пряма —
«(прямий)», решта — з розподілом), неоднозначні, прибрані, ручні й старі пари; ознака з таблиці Power BI; рядок групи
у вкладці «Бюджет» з розгортанням на варіанти."""
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
        {"Код": "A3", "Назва": "Меблі (позаплан)", "Тип_вузла": "Елемент", "Батько": None, "БазаРозподілу": "Не розподіляти"},
        {"Код": "B1", "Назва": "Таксі", "Тип_вузла": "Елемент", "Батько": None},
        {"Код": "B2", "Назва": "Таксі (прямий)", "Тип_вузла": "Елемент", "Батько": None},
        {"Код": "B3", "Назва": "Таксі (офіс)", "Тип_вузла": "Елемент", "Батько": None},
        {"Код": "C1", "Назва": "Зв'язок", "Тип_вузла": "Елемент", "Батько": None}]
items = expense_items.extract_items(rows)
by = {i["code"]: i for i in items}
check("extract: unplanned from the 1C base column", by["A3"]["unplanned"] and not by["A1"]["unplanned"]
      and not by["A2"]["unplanned"])
check("base name without trailing brackets", item_groups.base_name(" Меблі  (прямий) (2) ") == "меблі"
      and item_groups.base_name("(прямий)") == "(прямий)")

real = expense_items.get_items
expense_items.get_items = lambda: items
groups, ambiguous = item_groups.detect()
check("auto: three variants by name", [{k: g[k] for k in item_groups.ROLES} for g in groups]
      == [{"allocated": "A1", "direct": "A2", "unplanned": "A3"}])
check("auto: two allocated candidates -> ambiguous, not grouped", [a["base"] for a in ambiguous] == ["таксі"]
      and item_groups.group_of("B2") is None)
check("role and unplanned", item_groups.role("A2") == "direct" and item_groups.is_unplanned("A3")
      and item_groups.role("C1") is None)
check("group title: the allocated variant", item_groups.groups()[0]["name"] == "Меблі")

app_settings.set("item_groups", [{"allocated": "B1", "direct": "B2", "unplanned": ""}], "t")
check("manual group first, auto still works", [(g["direct"], g["auto"]) for g in item_groups.groups()]
      == [("B2", False), ("A2", True)])
app_settings.set("item_groups_skip", ["меблі"], "t")
check("skipped auto group gone", item_groups.group_of("A1") is None)
app_settings.set("item_groups_skip", [], "t")
app_settings.set("item_groups", [{"allocated": "A1", "direct": "", "unplanned": "A3"}], "t")
check("auto group with an item from a manual group is not used", item_groups.group_of("A2") is None
      and item_groups.group_of("A3")["allocated"] == "A1")
app_settings.set("item_groups", [], "t")
app_settings.set("split_pairs", [{"direct": "B2", "allocated": "B1"}], "t")
check("old pairs act as manual groups", item_groups.group_of("B2")["allocated"] == "B1"
      and not item_groups.group_of("B2")["auto"])
check("describe for the journal", item_groups.describe({"allocated": "A1", "direct": "A2", "unplanned": "A3"})
      == "Меблі: з розподілом A1 / пряма A2 / позапланова A3")
app_settings.set("split_pairs", [], "t")
expense_items.get_items = real

# ---------- вкладка «Бюджет»: варіанти статті — одним рядком з розгортанням (мок: 05.002 + 05.012 у відділі ІТ)
app_settings.set("budget_enabled", True, "t")
rep = budget.report(None, "IT", clock.today().year, list(range(1, 13)))
rows_ = [r for d in rep["departments"] for g in d["groups"] for r in g["rows"]]
fam = next((r for r in rows_ if r.get("family")), None)
check("budget: family row with both variants", fam and fam["name"] == "Ремонт оргтехніки"
      and [c["code"] for c in fam["children"]] == ["05.002", "05.012"]
      and round(fam["plan"], 2) == round(sum(c["plan"] for c in fam["children"]), 2))
check("budget: variants are not separate top rows", not any(r.get("code") in ("05.002", "05.012") for r in rows_)
      and rep["count"] == len([r for r in rows_ if not r.get("family")]) + 2)
user_roles.set_roles("adm@ftpua.com", "adm@ftpua.com", "Adm", ["admin"], "t")
m.auth._auth.get_user = lambda: {"name": "adm", "preferred_username": "adm@ftpua.com", "oid": "adm@ftpua.com"}
c = m.app.test_client()
html = c.get("/budget?dep=IT", follow_redirects=True).get_data(as_text=True)
check("budget page: family row and its variants", 'data-fam-head="1"' in html and "bg-fam-child" in html
      and "варіантів: 2" in html)
html = c.get("/budget?dep=IT&view=months", follow_redirects=True).get_data(as_text=True)
check("budget page, months: family row", 'data-fam-head="1"' in html)

print("ALL OK" if ok else "SOME FAILED")
