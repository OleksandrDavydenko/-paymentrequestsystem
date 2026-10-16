import io, os, sys, tempfile
sys.path.insert(0, os.getcwd())
tmp = tempfile.mkdtemp()
up = os.path.join(tmp, "uploads"); os.makedirs(up)
with open(os.path.join(up, "oldfile123.pdf"), "wb") as f:  # файл, що лежав на диску до Blob
    f.write(b"%PDF old")
os.environ.update(UPLOAD_DIR=up, APP_SETTINGS_FILE=os.path.join(tmp, "s.json"), USER_PREFS_FILE=os.path.join(tmp, "p.json"),
                  USER_DIRECTORY_FILE=os.path.join(tmp, "d.json"), ORG_STRUCTURE_FILE=os.path.join(tmp, "o.json"),
                  COUNTERPARTIES_PENDING_FILE=os.path.join(tmp, "cp.json"),
                  BLOB_ACCOUNT_URL="https://ftppayreqfiles.blob.core.windows.net", BLOB_CONTAINER="attachments",
                  GROUP_ADMIN="g-a", GROUP_INITIATOR="g-i", GROUP_ACC_RESIDENT="g-r", GROUP_ACC_CASH="g-c", GROUP_CFO="g-f",
                  ADMIN_EMAILS="")
from azure.core.exceptions import ResourceNotFoundError
import filestore


class Blob:
    def __init__(self, name): self.name = name


class FakeContainer:
    def __init__(self): self.blobs, self.types = {}, {}
    def upload_blob(self, name, data, overwrite=False, content_settings=None):
        self.blobs[name] = data.read() if hasattr(data, "read") else data
        self.types[name] = content_settings.content_type if content_settings else None
    def download_blob(self, name):
        if name not in self.blobs:
            raise ResourceNotFoundError("nope")
        data = self.blobs[name]
        return type("D", (), {"readall": lambda self_: data})()
    def delete_blob(self, name):
        if name not in self.blobs:
            raise ResourceNotFoundError("nope")
        del self.blobs[name]
    def list_blobs(self): return [Blob(n) for n in self.blobs]
    def get_container_properties(self): return {}


fake = FakeContainer()
filestore._container_client = fake
filestore.start_migration = lambda: None  # у тесті переносимо явно

import app as m
import user_budget; user_budget.get = lambda oid: {"direct": True, "all_departments": True, "departments": []}
import mailer, notifications
import storage as db

ok = True
def check(label, cond):
    global ok
    ok &= bool(cond)
    print(("PASS " if cond else "FAIL ") + label)

mailer.transport = lambda *a: None
notifications.role_members = lambda role: []
claims = {"name": "Author", "preferred_username": "od@ftpua.com", "oid": "od", "groups": ["g-i"]}
m.auth._auth.get_user = lambda: dict(claims)
c = m.app.test_client()

check("health: blob", c.get("/health").get_json()["files"] == "blob")
rid = next(r["id"] for r in db.list_all() if r["author_email"] == "od@ftpua.com" and r["status"] in ("draft", "rework"))
r = c.post(f"/requests/{rid}/files", data={"files": (io.BytesIO(b"%PDF-1.4 test"), "Рахунок.pdf")}, content_type="multipart/form-data")
att = db.get_request(rid)["attachments"][-1]
name = f"{att['id']}.pdf"
check("upload goes to blob, not disk", fake.blobs.get(name) == b"%PDF-1.4 test" and fake.types[name] == "application/pdf"
      and not os.path.exists(os.path.join(up, name)))
d = c.get(f"/files/{att['id']}")
check("download from blob (inline pdf)", d.status_code == 200 and d.data == b"%PDF-1.4 test" and d.mimetype == "application/pdf")
c.post(f"/files/{att['id']}/delete")
check("delete removes blob", name not in fake.blobs and not any(a["id"] == att["id"] for a in db.get_request(rid)["attachments"]))

# старий файл з диска: відкриття — з диска й заодно в Blob
_r = db.get_request(rid); _r["attachments"].append({"id": "oldfile123", "name": "old.pdf", "ext": "pdf", "size": 8,
                                         "at": db.get_request(rid)["created_at"], "user_name": "A", "user_email": "od@ftpua.com"})
db.save_request(_r)
d = c.get("/files/oldfile123")
check("old disk file served + copied to blob", d.data == b"%PDF old" and fake.blobs.get("oldfile123.pdf") == b"%PDF old"
      and os.path.exists(os.path.join(up, "oldfile123.pdf")))
with open(os.path.join(up, "another9.docx"), "wb") as f:
    f.write(b"docx")
check("migrate_local moves only missing", filestore.migrate_local() == 1 and fake.blobs["another9.docx"] == b"docx")
check("migrate again -> 0", filestore.migrate_local() == 0)
check("missing everywhere -> 404", c.get("/files/doesnotexist").status_code == 404)

# збій Blob при завантаженні — зрозуміле повідомлення, нічого не додано
orig = fake.upload_blob
fake.upload_blob = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("403 AuthorizationPermissionMismatch"))
n = len(db.get_request(rid)["attachments"])
r = c.post(f"/requests/{rid}/files", data={"files": (io.BytesIO(b"x"), "a.pdf")}, content_type="multipart/form-data",
           follow_redirects=True)
check("blob failure: not attached, message", len(db.get_request(rid)["attachments"]) == n and "не вдалося зберегти" in r.get_data(as_text=True))
fake.upload_blob = orig

print("ALL OK" if ok else "SOME FAILED")
