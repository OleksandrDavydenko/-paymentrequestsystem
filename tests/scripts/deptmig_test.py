import os, sys, tempfile, time
sys.path.insert(0, os.getcwd())
tmp = tempfile.mkdtemp()
os.environ.update(UPLOAD_DIR=tmp, APP_SETTINGS_FILE=os.path.join(tmp, "s.json"), USER_PREFS_FILE=os.path.join(tmp, "p.json"),
                  USER_DIRECTORY_FILE=os.path.join(tmp, "d.json"), ORG_STRUCTURE_FILE=os.path.join(tmp, "o.json"),
                  COUNTERPARTIES_PENDING_FILE=os.path.join(tmp, "cp.json"), GROUP_ADMIN="g-a", GROUP_INITIATOR="g-i", ADMIN_EMAILS="")
import app as m, mock_data as db, departments, org_structure
import user_budget; user_budget.get = lambda oid: {"direct": True, "all_departments": True, "departments": []}
import graph; graph.group_members = lambda t, g: []
ok = True
def check(label, cond):
    global ok; ok &= bool(cond); print(("PASS " if cond else "FAIL ") + label)
# старі (тестові) призначення
org_structure.set_department("od", "od@ftpua.com", "OD", "BA", "t")
org_structure.set_department("x", "x@ftpua.com", "X", "FIN", "t")
org_structure.set_department_staff("BA", {"id": "od", "email": "od@ftpua.com", "name": "OD"}, [], "t")
# «справжній» довідник з 1С
departments._table._state.update(items=[{"code": "000000007", "name": "Відділ бізнес-аналізу", "deleted": False},
                                        {"code": "000000018", "name": "Фінанси", "deleted": False}],
                                 loaded_at=time.time(), source="powerbi")
check("mapping by exact name", departments.legacy_mapping() == {"BA": "000000007"})
claims = {"name": "OD", "preferred_username": "od@ftpua.com", "oid": "od", "groups": ["g-a", "g-i"]}
m.auth._auth.get_user = lambda: dict(claims)
m.auth._auth.get_token_for_user = lambda s: {"access_token": "t", "scope": " ".join(s)}
c = m.app.test_client()
r = c.get("/admin/users", follow_redirects=True).get_data(as_text=True)
check("user BA -> 1C code, head moved", org_structure.department_of("od") == "000000007" and org_structure.head_departments("od") == ["000000007"])
check("FIN not matched (stays for manual fix)", org_structure.department_of("x") == "FIN")
check("mock requests remapped", all(r.get("department") != "BA" for r in db.list_all()))
check("flash shown once", "переведено на довідник 1С" in r and "переведено" not in c.get("/admin/users").get_data(as_text=True))
print("ALL OK" if ok else "SOME FAILED")
