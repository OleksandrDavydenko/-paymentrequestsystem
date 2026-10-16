"""Захист форм (CSRF) і cookie сеансу. Запускається з увімкненою перевіркою токена (--csrf)."""
import glob, os, re, sys, tempfile
sys.path.insert(0, os.getcwd())
tmp = tempfile.mkdtemp()
os.environ.update(UPLOAD_DIR=tmp, GROUP_ADMIN="g-a", GROUP_INITIATOR="g-i", GROUP_ACC_RESIDENT="g-r", ADMIN_EMAILS="",
                  TELEGRAM_BOT_KEY="k")
import app as m
import mock_data as db
import user_budget; user_budget.get = lambda oid: {"direct": True, "all_departments": True, "departments": []}

ok = True
def check(label, cond):
    global ok
    ok &= bool(cond); print(("PASS " if cond else "FAIL ") + label)

claims = {}
def login(email, groups):
    claims.clear(); claims.update({"name": email.split("@")[0].title(), "preferred_username": email, "oid": email, "groups": groups})
m.auth._auth.get_user = lambda: dict(claims)
c = m.app.test_client()

FORM = {"pay_date": "2026-10-20", "organization_id": "1", "counterparty_id": "1", "expense_code": "01.001",
        "currency": "UAH", "amount": "100", "payment_form": "bank", "action": "submit"}

login("author@ftpua.com", ["g-i"])
page = c.get("/requests/new").get_data(as_text=True)
meta = re.search(r'<meta name="csrf-token" content="([^"]+)"', page)
field = re.search(r'name="csrf_token" value="([^"]+)"', page)
check("token in meta tag and in form", meta and field)
token = field.group(1) if field else ""

before = len(db._REQUESTS)
r = c.post("/requests/new", data=FORM)
check("POST without token -> back to the form, nothing saved",
      r.status_code == 302 and r.headers["Location"].endswith("/requests/new") and len(db._REQUESTS) == before)
check("explained to the user", "Сторінка застаріла" in c.get("/requests/new").get_data(as_text=True))
r = c.post("/requests/new", data={**FORM, "csrf_token": "forged"})
check("forged token rejected", r.status_code == 302 and len(db._REQUESTS) == before)
r = c.post("/requests/new", data={**FORM, "csrf_token": token})
check("POST with token -> saved", len(db._REQUESTS) == before + 1)
rid = max(db._REQUESTS)

r = c.post(f"/requests/{rid}/comment", data={"text": "x"})
check("action on a request without token -> back to its card",
      r.status_code == 302 and r.headers["Location"].endswith(f"/requests/{rid}"))

# fetch-запити: токен у заголовку, помилка — JSON
hdr = {"X-Requested-With": "fetch"}
r = c.post("/counterparties/new", json={"resident": "1", "name": "Тест"}, headers=hdr)
check("fetch without token -> 400 JSON", r.status_code == 400 and "error" in r.get_json())
r = c.post("/counterparties/new", json={"resident": "1", "name": "Тест"}, headers={**hdr, "X-CSRFToken": token})
check("fetch with X-CSRFToken header passes", r.status_code != 400 or "error" not in (r.get_json() or {}))

# Telegram-бот: без сеансу і токена, лише ключ
r = c.post("/api/telegram/ack", headers={"X-Api-Key": "k"}, json={"results": []})
check("bot API works without CSRF token", r.status_code == 200)
r = c.post("/api/telegram/linked", headers={"X-Api-Key": "k"}, json={"emails": []})
check("bot linked works without CSRF token", r.status_code == 200)

# Cookie сеансу
check("session cookie SameSite=Lax + HttpOnly",
      m.app.config["SESSION_COOKIE_SAMESITE"] == "Lax" and m.app.config["SESSION_COOKIE_HTTPONLY"])
cookies = [h for k, h in c.get("/requests").headers.items() if k == "Set-Cookie"] or \
          [h for k, h in m.app.test_client().get("/requests").headers.items() if k == "Set-Cookie"]
check("Set-Cookie carries SameSite=Lax", not cookies or all("SameSite=Lax" in h for h in cookies))

# Кожна POST-форма в шаблонах містить токен одразу після відкриття
missing = []
for p in glob.glob("templates/**/*.html", recursive=True):
    s = open(p, encoding="utf-8").read()
    for mt in re.finditer(r"<form\b[^>]*?method=\"post\"", s, re.I):
        rest = s[mt.end():]
        if not re.match(r'[^>]*>\s*<input type="hidden" name="csrf_token" value="\{\{ csrf_token\(\) \}\}">', rest):
            missing.append(f"{p}:{s[:mt.start()].count(chr(10)) + 1}")
check("every POST form has csrf_token" + (f" (missing: {missing})" if missing else ""), not missing)
print("ALL OK" if ok else "SOME FAILED")
