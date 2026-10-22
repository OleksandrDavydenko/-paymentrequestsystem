import os, sys, tempfile
sys.path.insert(0, os.getcwd())
tmp = tempfile.mkdtemp()
os.environ.update(UPLOAD_DIR=tmp, APP_SETTINGS_FILE=os.path.join(tmp, "s.json"), USER_PREFS_FILE=os.path.join(tmp, "p.json"),
                  USER_DIRECTORY_FILE=os.path.join(tmp, "d.json"), ORG_STRUCTURE_FILE=os.path.join(tmp, "o.json"),
                  COUNTERPARTIES_PENDING_FILE=os.path.join(tmp, "cp.json"), GROUP_INITIATOR="g-i", GROUP_ADMIN="g-a", ADMIN_EMAILS="")
import app as m
claims = {"name": "A", "preferred_username": "od@ftpua.com", "oid": "od", "groups": ["g-i", "g-a"]}
m.auth._auth.get_user = lambda: dict(claims)
c = m.app.test_client()
ok = True
def check(label, cond):
    global ok
    ok &= bool(cond); print(("PASS " if cond else "FAIL ") + label)
r = c.get("/requests/7/files")
check("GET files -> card #documents", r.status_code == 302 and r.headers["Location"].endswith("/requests/7#documents"))
page = c.get("/requests/7").get_data(as_text=True)
check("flash explains", "Сеанс входу оновився" in page and "файл не додано" in page)
r = c.get("/requests/7/action")
check("GET action -> card", r.status_code == 302 and r.headers["Location"].endswith("/requests/7"))
check("GET to-pay/pay -> to-pay", c.get("/to-pay/pay").headers["Location"].endswith("/to-pay"))
check("GET admin/mail -> admin", c.get("/admin/mail").headers["Location"].endswith("/admin/users"))
check("GET admin/settings -> settings page", c.get("/admin/settings").status_code == 200)
check("POST to GET-only route still 405", c.post("/health").status_code == 405)
print("ALL OK" if ok else "SOME FAILED")
