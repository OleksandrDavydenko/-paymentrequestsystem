import os, sys, tempfile
sys.path.insert(0, os.getcwd())
tmp = tempfile.mkdtemp()
os.environ.update(UPLOAD_DIR=tmp, APP_SETTINGS_FILE=os.path.join(tmp, "s.json"), USER_PREFS_FILE=os.path.join(tmp, "p.json"),
                  USER_DIRECTORY_FILE=os.path.join(tmp, "d.json"), ORG_STRUCTURE_FILE=os.path.join(tmp, "o.json"),
                  USER_BUDGET_FILE=os.path.join(tmp, "b.json"),
                  GROUP_ADMIN="g-a", GROUP_INITIATOR="g-i", GROUP_ACC_RESIDENT="g-r", GROUP_CFO="g-f", ADMIN_EMAILS="")
import app as m
import org_structure, user_budget, user_directory, user_roles, expense_items

ok = True
def check(label, cond):
    global ok
    ok &= bool(cond)
    print(("PASS " if cond else "FAIL ") + label)

claims = {}
def login(email, groups, oid=None):
    claims.clear(); claims.update({"name": email.split("@")[0].title(), "preferred_username": email, "oid": oid or email, "groups": groups})
m.auth._auth.get_user = lambda: dict(claims)
m.auth._auth.get_token_for_user = lambda s: {"access_token": "t", "scope": " ".join(s)}
# Хто вже входив у систему (довідник), але ще без ролей
user_directory.remember([{"id": "new", "email": "new@ftpua.com", "name": "Новий"},
                         {"id": "boss", "email": "boss@ftpua.com", "name": "Бос"}], active=False)
roles = lambda uid: (user_roles.get(uid) or {}).get("roles")
c = m.app.test_client()

def admin_page():
    """Сторінки адмінки разом (користувачі + вкладки налаштувань) — для перевірок вмісту."""
    return "".join(c.get(u).get_data(as_text=True) for u in ["/admin/users"] + [
        f"/admin/settings?tab={t}" for t in ("approval", "request", "payment", "notify", "data")])

login("ira@ftpua.com", ["g-i"], "ira")
check("non-admin: card 403", c.get("/admin/users/ira").status_code == 403)

login("od@ftpua.com", ["g-a", "g-i"], "od")
lst = admin_page()
check("list links to cards", "/admin/users/ira" in lst and "Відкрити" in lst and 'name="roles"' not in lst)
page = c.get("/admin/users/ira").get_data(as_text=True)
check("card: 4 numbered sections with explanations", all(t in page for t in
      ["Ролі в системі", "Відділ співробітника", "Відділи: заявки, керівництво й перегляд", "Статті бюджету", "Сповіщення"])
      and "Перевіряє й оплачує" in page and "Це не те саме, що відділи в статтях бюджету" in page)
check("card: current role checked", 'value="initiator" checked' in page)
page = c.get("/admin/users/new").get_data(as_text=True)
check("new user card: banner", "Новий користувач" in page)
r = c.get("/admin/users/ghost")
check("unknown user -> back to list", r.status_code == 302 and r.headers["Location"].endswith("/admin/users"))

# Збереження: ролі
c.post("/admin/users/new", data={"roles": ["initiator", "acc_resident"], "department": "LOG", "mode": "none"})
check("new user: roles saved in the system", roles("new") == {"initiator", "acc_resident"}
      and user_roles.get("new")["email"] == "new@ftpua.com")
check("new user: department saved", org_structure.department_of("new") == "LOG")
c.post("/admin/users/new", data={"roles": ["initiator"], "department": "LOG", "mode": "none"})
check("role removed", roles("new") == {"initiator"})
c.post("/admin/users/od", data={"roles": ["initiator"], "mode": "none"})
check("own admin role can't be removed", "admin" in roles("od"))

# Відділи: очолює / бачить
org_structure.set_department_staff("SAL", {"id": "boss", "email": "boss@ftpua.com", "name": "Бос"}, [], "t")
r = c.post("/admin/users/ira", data={"roles": ["initiator"], "head_deps": ["SAL", "IT"], "view_deps": ["LOG", "IT"], "mode": "none"},
           follow_redirects=True)
check("ira heads SAL+IT (replaces boss in SAL)", org_structure.head_departments("ira") == ["IT", "SAL"]
      and org_structure.head_departments("boss") == [])
check("flash mentions replaced head", "замінено керівника Бос" in r.get_data(as_text=True))
check("views LOG; head dept not duplicated in views", org_structure.viewer_departments("ira") == ["LOG"])
c.post("/admin/users/ira", data={"roles": ["initiator"], "head_deps": ["IT"], "view_deps": [], "mode": "none"})
check("unchecking head/views clears them", org_structure.head_departments("ira") == ["IT"]
      and org_structure.viewer_departments("ira") == [] and org_structure.head_of("SAL") is None)

# Статті бюджету
dep = expense_items.get_departments()[0][0]
c.post("/admin/users/ira", data={"roles": ["initiator"], "head_deps": ["IT"], "direct": "1", "mode": "selected", "departments": [dep, "Невідомий"]})
b = user_budget.get("ira")
check("budget saved from card (unknown dropped)", b["direct"] and b["departments"] == [dep] and not b["all_departments"])
r = c.post("/admin/users/ira", data={"roles": ["initiator"], "head_deps": ["IT"], "direct": "1", "mode": "selected", "departments": [dep]},
           follow_redirects=True)
check("no changes -> 'Змін немає'", "Змін немає" in r.get_data(as_text=True))

print("ALL OK" if ok else "SOME FAILED")
