"""Журнал змін для людей (user_changes.py): ролі, режим бухгалтера, доступ до розділів, «Бюджет» (чий бачить) і статті
бюджету — з картки користувача й автоматично з 1С; лише справжні зміни; фільтр журналу за людиною й «Користувачі»."""
import os, sys, tempfile
sys.path.insert(0, os.getcwd())
os.environ.update(UPLOAD_DIR=tempfile.mkdtemp(), ADMIN_EMAILS="adm@ftpua.com")
import app as m
import onec_sync, settings_log, user_changes, user_roles

ok = True
def check(label, cond):
    global ok
    ok &= bool(cond); print(("PASS " if cond else "FAIL ") + label)

claims = {}
def login(email):
    claims.clear(); claims.update({"name": email.split("@")[0], "preferred_username": email, "oid": email})
m.auth._auth.get_user = lambda: dict(claims) or None
c = m.app.test_client()
page = lambda url: c.get(url, follow_redirects=True).get_data(as_text=True)

user_roles.set_roles("ira@ftpua.com", "ira@ftpua.com", "Ірина Бух", ["initiator"], "seed")
user_roles.set_roles("oleg@ftpua.com", "oleg@ftpua.com", "Олег", ["initiator"], "seed")
people = lambda: [r for r in settings_log.query(500, "user:")]
mine = lambda uid: settings_log.query(500, f"user:{uid}:")
check("seed: roles of new people are journaled", {r["key"] for r in people()}
      == {"user:ira@ftpua.com:roles", "user:oleg@ftpua.com:roles"})

# ---------- картка: ролі, режим бухгалтера, розділи, бюджет, статті — одним збереженням
login("adm@ftpua.com")
before = len(mine("ira@ftpua.com"))
card = {"roles": ["initiator", "acc_resident"], "acc_mode": "pay", "sections_sent": "1", "section_reconcile": "1",
        "budget_access_sent": "1", "budget_access_deps": ["LOG"], "mode": "selected", "direct": "1",
        "departments": ["Логістика"]}
c.post("/admin/users/ira@ftpua.com", data=card)
rows = {r["key"].rsplit(":", 1)[1]: r for r in mine("ira@ftpua.com")[:len(mine("ira@ftpua.com")) - before]}
check("card: five changes journaled", set(rows) == {"roles", "acc_mode", "sections", "budget_access", "budget_items"})
check("who and section", all(r["by"] == "adm@ftpua.com" and r["tab"] == "Користувачі" for r in rows.values()))
check("labels: person and field", rows["roles"]["label"] == "Ірина Бух (ira@ftpua.com): ролі"
      and rows["budget_access"]["label"].endswith(": бюджет — чий бачить"))
check("roles: names, was -> became", rows["roles"]["old_label"] == "Ініціатор"
      and "Бухгалтер (безготівка, резидент)" in rows["roles"]["new_label"] and "Ініціатор" in rows["roles"]["new_label"])
check("accountant mode", rows["acc_mode"]["old_label"] == "Погоджує і оплачує" and rows["acc_mode"]["new_label"] == "Лише оплачує")
check("sections", rows["sections"]["old_label"] == "усе за роллю" and rows["sections"]["new_label"] == "+ Звірка")
check("budget access: department names", rows["budget_access"]["old_label"] == "лише відділи, які очолює"
      and rows["budget_access"]["new_label"] == "свої й додані відділи: Логістика")
check("budget items", rows["budget_items"]["old_label"] == "немає статей"
      and rows["budget_items"]["new_label"] == "прямі; з розподілом — Логістика")

# ---------- те саме ще раз — нових записів немає
n = len(mine("ira@ftpua.com"))
c.post("/admin/users/ira@ftpua.com", data=card)
check("saving the same card again adds nothing", len(mine("ira@ftpua.com")) == n)

# ---------- автоматично з 1С
user_roles.set_roles("oleg@ftpua.com", "oleg@ftpua.com", "Олег", [], onec_sync.AUTO_WHO)
last = mine("oleg@ftpua.com")[0]
check("automatic change from 1C: who is «1С (автоматично)»", last["by"] == onec_sync.AUTO_WHO
      and last["old_label"] == "Ініціатор" and last["new_label"] == "без ролей")
user_roles.set_roles("oleg@ftpua.com", "oleg@ftpua.com", "Олег Новий", [], "t")
check("name change only: not journaled", mine("oleg@ftpua.com")[0]["id"] == last["id"])

# ---------- сторінка журналу
html = page("/admin/settings?tab=log&key=user:ira@ftpua.com:")
check("journal of one person: only her, with a heading", "Ірина Бух (ira@ftpua.com)" in html and "Олег" not in html
      and "Лише оплачує" in html and "усі зміни" in html)
html = page("/admin/settings?tab=log&key=user:")
check("filter «Користувачі»: everybody", "Ірина Бух" in html and "Олег" in html
      and '<option value="user:" selected' in html)
html = page("/admin/settings?tab=log")
check("all changes: people among settings", "Ірина Бух (ira@ftpua.com): ролі" in html)
check("user card links to the person's journal", "/admin/settings?tab=log&amp;key=user:ira@ftpua.com:" in page("/admin/users/ira@ftpua.com"))
check("prefix filter does not leak to similar ids", settings_log.matches("user:ira@ftpua.com:roles", "user:ira@ftpua.com:")
      and not settings_log.matches("user:ira@ftpua.com.ua:roles", "user:ira@ftpua.com:"))
print("ALL OK" if ok else "SOME FAILED")
