import io, os, sys, tempfile
sys.path.insert(0, os.getcwd())
tmp = tempfile.mkdtemp()
os.environ.update(UPLOAD_DIR=os.path.join(tmp, "up"), APP_SETTINGS_FILE=os.path.join(tmp, "s.json"), USER_PREFS_FILE=os.path.join(tmp, "p.json"),
                  USER_DIRECTORY_FILE=os.path.join(tmp, "d.json"), ORG_STRUCTURE_FILE=os.path.join(tmp, "o.json"),
                  COUNTERPARTIES_PENDING_FILE=os.path.join(tmp, "cp.json"),
                  GROUP_ADMIN="g-a", GROUP_INITIATOR="g-i", GROUP_ACC_RESIDENT="g-r", GROUP_CFO="g-f", ADMIN_EMAILS="")
import app as m
import user_budget; user_budget.get = lambda oid: {"direct": True, "all_departments": True, "departments": []}
import app_settings, mailer, notifications
import storage as db
ok = True
def check(label, cond):
    global ok
    ok &= bool(cond); print(("PASS " if cond else "FAIL ") + label)
sent = []
mailer.transport = lambda cfg, to, subject, html: sent.append((to, html))
notifications.role_members = lambda role: []
for k, v in {"mail_enabled": True, "mail_sender": "noreply@ftpua.com"}.items():
    app_settings.set(k, v, "t")
claims = {}
def login(email, groups):
    claims.clear(); claims.update({"name": email.split("@")[0].title(), "preferred_username": email, "oid": email, "groups": groups})
m.auth._auth.get_user = lambda: dict(claims)
c = m.app.test_client()

rid = 12  # демо-заявка іншого автора (Ірина Петренко), на етапі оплати
author = db.get_request(rid)["author_email"]
login("cfo@ftpua.com", ["g-f"])
page = c.get(f"/requests/{rid}").get_data(as_text=True)
check("comment form: attach button + multipart", 'id="comment-attach"' in page and 'enctype="multipart/form-data"' in page.split('id="comment"')[1][:200])
sent.clear()
r = c.post(f"/requests/{rid}/comment", data={"comment": "Дивіться акт", "files": [(io.BytesIO(b"%PDF a"), "Акт.pdf"), (io.BytesIO(b"img"), "фото.jpg")]},
           content_type="multipart/form-data", follow_redirects=True)
req = db.get_request(rid)
h = req["history"][-1]
check("comment with 2 files", h["action"] == "comment" and h["comment"] == "Дивіться акт" and len(h["files"]) == 2)
check("files in attachments, marked from comment, no file_add entries",
      sum(1 for a in req["attachments"] if a.get("from_comment")) == 2 and req["history"][-2]["action"] != "file_add")
page = r.get_data(as_text=True)
check("history shows file links", "📎 Акт.pdf" in page and "📎 фото.jpg" in page and "з коментаря" in page)
mailer.wait()
check("author mail lists files", any(to == author and "Акт.pdf" in html for to, html in sent))
r = c.post(f"/requests/{rid}/comment", data={"comment": "", "files": [(io.BytesIO(b"x"), "лише файл.pdf")]},
           content_type="multipart/form-data")
check("file-only comment ok", db.get_request(rid)["history"][-1]["comment"] == "" and len(db.get_request(rid)["history"][-1]["files"]) == 1)
n = len(db.get_request(rid)["history"])
r = c.post(f"/requests/{rid}/comment", data={"comment": "", "files": [(io.BytesIO(b"x"), "virus.exe")]},
           content_type="multipart/form-data", follow_redirects=True)
check("bad file + no text -> nothing added, message", len(db.get_request(rid)["history"]) == n and "недопустимий тип" in r.get_data(as_text=True))
r = c.post(f"/requests/{rid}/comment", data={"comment": ""}, follow_redirects=True)
check("empty comment rejected", len(db.get_request(rid)["history"]) == n and "прикріпіть файл" in r.get_data(as_text=True))
# видалений файл — у історії «файл видалено»
fid = db.get_request(rid)["history"][-1]["files"][0]
c.post(f"/files/{fid}/delete")
check("deleted file shown as gone", "файл видалено" in c.get(f"/requests/{rid}").get_data(as_text=True))
# звичайне завантаження в «Документи» працює як раніше
login(db.get_request(7)["author_email"], ["g-i"])
c.post("/requests/7/files", data={"files": (io.BytesIO(b"%PDF"), "doc.pdf")}, content_type="multipart/form-data")
check("documents upload still adds file_add", db.get_request(7)["history"][-1]["action"] == "file_add"
      and not db.get_request(7)["attachments"][-1].get("from_comment"))
print("ALL OK" if ok else "SOME FAILED")
