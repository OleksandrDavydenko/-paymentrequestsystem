import os, sys, tempfile
sys.path.insert(0, os.getcwd())
tmp = tempfile.mkdtemp()
os.environ.update(UPLOAD_DIR=tmp, APP_SETTINGS_FILE=os.path.join(tmp, "s.json"), USER_PREFS_FILE=os.path.join(tmp, "p.json"),
                  USER_DIRECTORY_FILE=os.path.join(tmp, "d.json"), ORG_STRUCTURE_FILE=os.path.join(tmp, "o.json"),
                  USER_BUDGET_FILE=os.path.join(tmp, "b.json"),
                  GROUP_ADMIN="g-a", GROUP_INITIATOR="g-i", GROUP_ACC_RESIDENT="g-r", GROUP_CFO="g-f", ADMIN_EMAILS="")
import app as m
import org_structure, user_budget, expense_items
import graph

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
MEMBERS = {"g-a": ["od"], "g-i": ["od", "ira"], "g-r": [], "g-f": []}
PEOPLE = {"od": ("Oleksandr", "od@ftpua.com"), "ira": ("Ірина", "ira@ftpua.com"), "new": ("Новий", "new@ftpua.com"),
          "boss": ("Бос", "boss@ftpua.com")}
graph.group_members = lambda t, gid: [{"id": u, "name": PEOPLE[u][0], "email": PEOPLE[u][1]} for u in MEMBERS.get(gid, [])]
graph.get_user = lambda t, uid: ({"id": uid, "name": PEOPLE[uid][0], "email": PEOPLE[uid][1]} if uid in PEOPLE
                                 else (_ for _ in ()).throw(graph.GraphError("404")))
calls = []
def add_member(t, gid, uid): calls.append(("add", gid, uid)); MEMBERS[gid].append(uid)
def remove_member(t, gid, uid): calls.append(("remove", gid, uid)); MEMBERS[gid].remove(uid)
graph.add_member, graph.remove_member = add_member, remove_member
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
      ["Ролі в системі", "Відділ співробітника", "Керівництво й перегляд відділів", "Статті бюджету", "Сповіщення"])
      and "Перевіряє й оплачує" in page and "Це не те саме, що відділи в статтях бюджету" in page)
check("card: current role checked", 'value="initiator" checked' in page)
page = c.get("/admin/users/new").get_data(as_text=True)
check("new user card: banner", "Новий користувач" in page)
r = c.get("/admin/users/ghost")
check("unknown user -> back to list", r.status_code == 302 and r.headers["Location"].endswith("/admin/users"))

# Збереження: ролі
calls.clear()
c.post("/admin/users/new", data={"roles": ["initiator", "acc_resident"], "department": "LOG", "mode": "none"})
check("new user: roles added in Azure", ("add", "g-i", "new") in calls and ("add", "g-r", "new") in calls)
check("new user: department saved", org_structure.department_of("new") == "LOG")
calls.clear()
c.post("/admin/users/new", data={"roles": ["initiator"], "department": "LOG", "mode": "none"})
check("role removed", calls == [("remove", "g-r", "new")])
calls.clear()
c.post("/admin/users/od", data={"roles": ["initiator"], "mode": "none"})
check("own admin role can't be removed", ("remove", "g-a", "od") not in calls and "od" in MEMBERS["g-a"])

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

# Помилка читання ролей — нічого не змінюємо
orig = graph.group_members
graph.group_members = lambda t, gid: (_ for _ in ()).throw(graph.GraphError("403 denied"))
calls.clear()
r = c.post("/admin/users/ira", data={"roles": [], "mode": "none"}, follow_redirects=True)
check("graph read error -> nothing saved", calls == [] and "зміни не збережено" in r.get_data(as_text=True)
      and user_budget.get("ira")["direct"])
graph.group_members = orig
print("ALL OK" if ok else "SOME FAILED")
