import os, sys, tempfile
sys.path.insert(0, os.getcwd())
tmp = tempfile.mkdtemp()
os.environ.update(UPLOAD_DIR=tmp, APP_SETTINGS_FILE=os.path.join(tmp, "s.json"), USER_PREFS_FILE=os.path.join(tmp, "p.json"),
                  USER_DIRECTORY_FILE=os.path.join(tmp, "d.json"), GROUP_CFO="g-f", GROUP_ACC_CASH="g-c", ADMIN_EMAILS="")
import app as m, graph, mailer, user_directory
# Як із дозволом додатку: лише ID, без пошти
graph.group_members_app = lambda gid: [{"id": "oid-sa", "name": None, "email": None}, {"id": "oid-x", "name": None, "email": None}]
res = m._role_members("cfo")
assert res == [] and "Невідома пошта" in mailer.LOG[0]["subject"], (res, mailer.LOG[0])
user_directory.remember([{"id": "oid-sa", "email": "sa@ftpua.com", "name": "Stupa"}])
mailer.LOG.clear()
res = m._role_members("cfo")
assert [r["email"] for r in res] == ["sa@ftpua.com"], res
assert "1 з учасників" in mailer.LOG[0]["error"]
user_directory.reload(); assert user_directory.lookup("oid-sa")["email"] == "sa@ftpua.com"
print("directory ok")
