import os, sys, tempfile
sys.path.insert(0, os.getcwd())
tmp = tempfile.mkdtemp()
os.environ.update(UPLOAD_DIR=tmp, APP_SETTINGS_FILE=os.path.join(tmp, "s.json"), USER_PREFS_FILE=os.path.join(tmp, "p.json"),
                  USER_DIRECTORY_FILE=os.path.join(tmp, "d.json"), ORG_STRUCTURE_FILE=os.path.join(tmp, "o.json"),
                  COUNTERPARTIES_PENDING_FILE=os.path.join(tmp, "cp.json"), GROUP_INITIATOR="g-i", ADMIN_EMAILS="")
import app as m, storage as db
claims = {"name": "OD", "preferred_username": "od@ftpua.com", "oid": "od", "groups": ["g-i"]}
m.auth._auth.get_user = lambda: dict(claims)
c = m.app.test_client()
mine = c.get("/requests").get_data(as_text=True)
paid = c.get("/requests?view=paid").get_data(as_text=True)
n1 = db.get_request(1)["number"]  # оплачена заявка od
n9 = db.get_request(9)["number"]  # на доопрацюванні
ok = (f">{n1}<" not in mine and f">{n9}<" in mine and f">{n1}<" in paid and f">{n9}<" not in paid
      and "Оплачені" in mine and "Мої оплачені заявки" in paid and 'value="paid"' not in mine)
print("ALL OK" if ok else "FAILED")
