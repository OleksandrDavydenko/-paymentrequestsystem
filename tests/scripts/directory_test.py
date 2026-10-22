"""Отримувачі листів ролі: записи ролей, колись перенесені з Azure лише за ID, беруть пошту й ім'я з довідника
користувачів (хто входив у систему)."""
import os, sys, tempfile
sys.path.insert(0, os.getcwd())
tmp = tempfile.mkdtemp()
os.environ.update(UPLOAD_DIR=tmp, APP_SETTINGS_FILE=os.path.join(tmp, "s.json"), USER_PREFS_FILE=os.path.join(tmp, "p.json"),
                  USER_DIRECTORY_FILE=os.path.join(tmp, "d.json"), ADMIN_EMAILS="")
import app as m, user_directory, user_roles
# Як після перенесення з Azure дозволом додатку: лише ID, без пошти й імені
user_roles.set_roles("oid-sa", "", "", ["cfo"], "t")
user_roles.set_roles("oid-x", "", "", ["cfo"], "t")
res = m._role_members("cfo")
assert res == [], res
user_directory.remember([{"id": "oid-sa", "email": "sa@ftpua.com", "name": "Stupa"}])
res = m._role_members("cfo")
assert [(r["email"], r["name"]) for r in res] == [("sa@ftpua.com", "Stupa")], res
assert user_roles.find_by_email("SA@ftpua.com")["id"] == "oid-sa"
user_directory.reload(); assert user_directory.lookup("oid-sa")["email"] == "sa@ftpua.com"
print("directory ok")
