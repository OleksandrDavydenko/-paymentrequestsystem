"""Вхід через Google і Microsoft, ролі лише в системі: налаштування «Вхід у систему», перевірки повернення від
Google (state, nonce, домен, підтверджена пошта), зовнішні користувачі (лише ті, кого додав адміністратор),
картка користувача без Graph, режим «лише Google» із запасним входом адміністратора, вихід."""
import base64, json, os, sys, tempfile, time
from urllib.parse import parse_qs, urlparse
sys.path.insert(0, os.getcwd())
tmp = tempfile.mkdtemp()
os.environ.update(UPLOAD_DIR=tmp, ADMIN_EMAILS="boss@ftpua.com")
import app as m
for k in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_ALLOWED_DOMAINS"):
    os.environ.pop(k, None)  # після import app: локальний .env міг їх задати
import app_settings, google_login, user_directory, user_roles
import web.auth as wa

ok = True
def check(label, cond):
    global ok
    ok &= bool(cond); print(("PASS " if cond else "FAIL ") + label)

claims = {}
def ms_login(email):
    claims.clear(); claims.update({"name": email, "preferred_username": email, "oid": "oid-" + email})
def ms_logout():
    claims.clear()
m.auth._auth.get_user = lambda: dict(claims) or None
m.auth._auth.log_in = lambda **kw: {"auth_uri": "https://login.microsoftonline.com/authorize"}
user_roles.set_roles("oid-admin@ftpua.com", "admin@ftpua.com", "Адмін", ["admin", "initiator"], "t")
user_roles.set_roles("oid-ini@ftpua.com", "ini@ftpua.com", "Ініціатор", ["initiator"], "t")
user_roles.set_roles("oid-acc@ftpua.com", "", "", ["acc_resident"], "t")  # колись перенесений з Azure лише за ID
c = m.app.test_client()
page = lambda url: c.get(url).get_data(as_text=True)

# ---------- Google не налаштований: лише Microsoft, ролі однаково з системи
r = c.get("/requests")
check("not logged in -> login page with next", r.status_code == 302 and "/login?next=/requests" in r.headers["Location"])
r = c.get("/privacy")
html = r.get_data(as_text=True)
check("privacy page: public, contact, nothing about the system", r.status_code == 200 and "Політика конфіденційності" in html
      and "mailto:boss@ftpua.com" in html and not any(w in html for w in ("заяв", "Azure", "оплат", "Payment")))
html = page("/login")
check("no Google client: only Microsoft button", "Увійти через Microsoft" in html and "Увійти через Google" not in html
      and 'href="/privacy"' in html)
check("google start disabled", "/login" in c.get("/auth/google/login").headers["Location"])
ms_login("ini@ftpua.com")
check("microsoft user: role from the system", c.get("/requests").status_code == 200
      and c.get("/admin/users").status_code == 403)
ms_login("admin@ftpua.com")
html = page("/admin/settings?tab=data")
check("data tab: login methods, google not configured", 'name="login_methods"' in html and "не налаштовано" in html
      and "групи Azure не використовуються" in html)
c.post("/admin/settings", data={"tab": "data", "login_methods": "google"})
check("google only refused without Google client", app_settings.get("login_methods") == "both")

# ---------- клієнт Google налаштовано
os.environ.update(GOOGLE_CLIENT_ID="cid.apps.googleusercontent.com", GOOGLE_CLIENT_SECRET="x",
                  GOOGLE_ALLOWED_DOMAINS="ftpua.com")
check("both by default", wa.login_mode() == "both")
app_settings.set("login_methods", "microsoft", "t")  # значення з часів груп Azure
check("old 'microsoft' value -> both", wa.login_mode() == "both")
app_settings.set("login_methods", "both", "t")
check("notifications: role members from the system", [p["email"] for p in m._role_members("initiator")]
      == ["admin@ftpua.com", "ini@ftpua.com"] and m._role_members("acc_resident") == [])
ms_logout()
html = page("/login?next=/requests")
check("both: Google, external link, Microsoft", "Увійти через Google" in html and "Увійти через Microsoft" in html
      and "Увійти іншим акаунтом Google" in html and "Зовнішній" not in html)

def id_token(**over):
    claims_ = {"iss": "https://accounts.google.com", "aud": "cid.apps.googleusercontent.com", "exp": time.time() + 600,
               "email_verified": True, "sub": "g-1", **over}
    body = base64.urlsafe_b64encode(json.dumps(claims_).encode()).decode().rstrip("=")
    return "eyJhbGciOiJSUzI1NiJ9." + body + ".sig"

class Resp:
    def __init__(self, data, status=200): self.data, self.status_code = data, status
    def json(self): return self.data
posted = []
def google_flow(email, name="Г. Користувач", nxt="/requests", tamper=None, external=False, **over):
    """Пройти вхід через Google: (відповідь callback, параметри адреси Google)."""
    r = c.get("/auth/google/login?next=" + nxt + ("&external=1" if external else ""))
    q = parse_qs(urlparse(r.headers["Location"]).query)
    with c.session_transaction() as s:
        nonce = s["google_pending"]["nonce"]
    token = id_token(email=email, name=name, nonce=nonce, **over)
    def fake_post(url, data, timeout):
        posted.append(data)
        return Resp({"id_token": token})
    google_login.requests.post = fake_post
    state = tamper if tamper is not None else q["state"][0]
    return c.get(f"/auth/google/callback?code=abc&state={state}"), q

r, q = google_flow("ini@ftpua.com", tamper="wrong")
check("google url: client, hd hint, account choice", q["client_id"] == ["cid.apps.googleusercontent.com"]
      and q["hd"] == ["ftpua.com"] and q["prompt"] == ["select_account"] and q["scope"] == ["openid email profile"])
check("wrong state -> error, not logged in", "застаріла" in r.get_data(as_text=True)
      and c.get("/requests").status_code == 302)
r, _ = google_flow("ini@ftpua.com", email_verified=False)
check("unverified email -> refused", "не підтвердив" in r.get_data(as_text=True))
r, _ = google_flow("ini@ftpua.com", aud="other-client")
check("token for another client -> refused", "не для цього сайту" in r.get_data(as_text=True))
r, _ = google_flow("ini@ftpua.com", nxt="//evil.example.com")
check("foreign next ignored", r.status_code == 302 and r.headers["Location"] in ("/", "http://localhost/"))
check("code exchanged with secret and callback uri", posted[-1]["grant_type"] == "authorization_code"
      and posted[-1]["redirect_uri"].endswith("/auth/google/callback") and posted[-1]["code"] == "abc")
check("google user with role works", c.get("/requests").status_code == 200)
check("same person (Entra id) -> same user id", wa._app_user({"oid": None}, "ini@ftpua.com")[0] == "oid-ini@ftpua.com")
check("google user is not an admin", c.get("/admin/users").status_code == 403)
r, _ = google_flow("new@ftpua.com")
check("company user without roles -> no access page", c.get("/requests").status_code == 403)

# ---------- зовнішні користувачі
r, q = google_flow("bk@szitnik-team.com", external=True)
check("external link: no domain hint", "hd" not in q)
check("external not added -> refused", "не додано в систему" in r.get_data(as_text=True)
      and c.get("/requests").status_code == 302)

google_flow("admin@ftpua.com", name="Адмін")
found = c.get("/admin/users/search?q=bk@szitnik-team.com").get_json()["users"]
check("search: external email suggested", any(u["id"] == "bk@szitnik-team.com" and u.get("new")
                                               and "зовнішній" in u["email"] for u in found))
html = page("/admin/users/bk@szitnik-team.com")
check("external card: explanation + name field", 'id="uc-external"' in html and 'name="person_name"' in html)
c.post("/admin/users/bk@szitnik-team.com", data={"roles": ["initiator"], "person_name": "Б. Кравець"})
rec = user_roles.get("bk@szitnik-team.com")
check("external saved", rec and rec["roles"] == {"initiator"} and rec["name"] == "Б. Кравець")
html = page("/admin/users")
check("users list: external chip", "Зовнішній" in html and "bk@szitnik-team.com" in html)

r, _ = google_flow("bk@szitnik-team.com", name="Bohdan K", external=True)
check("added external logs in", r.status_code == 302 and c.get("/requests").status_code == 200)
check("admin-entered name kept", user_roles.get("bk@szitnik-team.com")["name"] == "Б. Кравець")
r, _ = google_flow("Bk@Szitnik-team.com", external=False)
check("external via main button works too (email case-insensitive)", c.get("/requests").status_code == 200)

google_flow("admin@ftpua.com", name="Адмін")
c.post("/admin/users/bk@szitnik-team.com", data={"roles": []})
r, _ = google_flow("bk@szitnik-team.com", external=True)
check("external without roles -> refused again", "не додано в систему" in r.get_data(as_text=True))

# ---------- адмінка: людина без пошти, нова людина
google_flow("admin@ftpua.com", name="Адмін")
html = page("/admin/users")
check("users list: warning about unknown email", "Невідома пошта в 1 користувачів" in html and "oid-acc@ftpua.com" in html)
check("no Azure leftovers", "GROUP_ADMIN" not in html and "Не налаштовано групи" not in html)
check("card: email field for ID-only person", 'name="person_email"' in page("/admin/users/oid-acc@ftpua.com"))
c.post("/admin/users/oid-acc@ftpua.com", data={"roles": ["acc_resident"], "person_email": "not-an-email"})
check("bad email refused", user_roles.get("oid-acc@ftpua.com")["email"] == "")
c.post("/admin/users/oid-acc@ftpua.com", data={"roles": ["acc_resident"], "person_email": "ini@ftpua.com"})
check("email of another user refused", user_roles.get("oid-acc@ftpua.com")["email"] == "")
c.post("/admin/users/oid-acc@ftpua.com", data={"roles": ["acc_resident"], "person_email": "Acc@ftpua.com",
                                               "person_name": "Бухгалтер"})
rec = user_roles.get("oid-acc@ftpua.com")
check("email and name saved", rec["email"] == "acc@ftpua.com" and rec["name"] == "Бухгалтер" and rec["roles"] == {"acc_resident"})
check("now gets mail", [p["email"] for p in m._role_members("acc_resident")] == ["acc@ftpua.com"])
check("search by name", any(u["id"] == "oid-acc@ftpua.com" for u in c.get("/admin/users/search?q=бухг").get_json()["users"]))
html = page("/admin/users/new@ftpua.com")
check("card of someone who logged in: name from Google", "Г. Користувач" in html and 'name="person_name"' not in html
      and "uc-azure" not in html and 'id="uc-external"' not in html)
c.post("/admin/users/new@ftpua.com", data={"roles": ["initiator"]})
check("roles saved, access at once", user_roles.get("new@ftpua.com")["roles"] == {"initiator"}
      and (google_flow("new@ftpua.com")[0], c.get("/requests").status_code)[1] == 200)
google_flow("admin@ftpua.com", name="Адмін")
c.post("/admin/users/fresh@ftpua.com", data={"roles": ["cfo"]})
google_flow("fresh@ftpua.com", name="Свіжий Співробітник")
c.get("/requests")
check("name filled from Google on first login", user_roles.get("fresh@ftpua.com")["name"] == "Свіжий Співробітник")
check("invalid card id refused", (google_flow("admin@ftpua.com", name="Адмін"),
                                  c.get("/admin/users/not-an-email").status_code)[1] == 302)
c.post("/admin/users/oid-admin@ftpua.com", data={"roles": ["initiator"]})
check("own admin role kept", "admin" in user_roles.get("oid-admin@ftpua.com")["roles"])

# ---------- лише Google: Microsoft — лише для ADMIN_EMAILS
c.post("/admin/settings", data={"tab": "data", "login_methods": "google"})
check("switched to google only", wa.login_mode() == "google")
c.get("/auth/logout")
check("logout forgets google session", c.get("/requests").status_code == 302)
html = page("/login")
check("google only: only Google buttons", "Увійти через Google" in html and "Microsoft" not in html)
check("hidden admin fallback address", "Увійти через Microsoft" in page("/login?method=microsoft"))
ms_login("ini@ftpua.com")
check("microsoft non-admin blocked", c.get("/requests").status_code == 302)
ms_login("boss@ftpua.com")
check("ADMIN_EMAILS via microsoft works", c.get("/admin/settings?tab=data").status_code == 200)

# ---------- без доменів Google вимкнено (нікого не пускаємо через Google)
os.environ["GOOGLE_ALLOWED_DOMAINS"] = ""
check("no domains -> google not configured -> microsoft", wa.login_mode() == "microsoft")
print("ALL OK" if ok else "SOME FAILED")
