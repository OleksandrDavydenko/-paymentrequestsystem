"""Вхід через Google і ролі в системі: налаштування «Вхід у систему», перевірки повернення від Google
(state, nonce, домен, підтверджена пошта), перенесення ролей з груп Azure, ролі з картки користувача без Graph,
режим «лише Google» із запасним входом адміністратора, вихід."""
import base64, json, os, sys, tempfile, time
from urllib.parse import parse_qs, urlparse
sys.path.insert(0, os.getcwd())
tmp = tempfile.mkdtemp()
os.environ.update(UPLOAD_DIR=tmp, GROUP_ADMIN="g-a", GROUP_INITIATOR="g-i", GROUP_ACC_RESIDENT="g-r",
                  ADMIN_EMAILS="boss@ftpua.com")
import app as m
for k in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_ALLOWED_DOMAINS"):
    os.environ.pop(k, None)  # після import app: локальний .env міг їх задати
import app_settings, google_login, graph, mailer, user_directory, user_roles
import web.auth as wa

ok = True
def check(label, cond):
    global ok
    ok &= bool(cond); print(("PASS " if cond else "FAIL ") + label)

claims = {}
def ms_login(email, groups=()):
    claims.clear(); claims.update({"name": email, "preferred_username": email, "oid": "oid-" + email, "groups": list(groups)})
def ms_logout():
    claims.clear()
m.auth._auth.get_user = lambda: dict(claims) or None
m.auth._auth.get_token_for_user = lambda s: {"access_token": "t"}
m.auth._auth.log_in = lambda **kw: {"auth_uri": "https://login.microsoftonline.com/authorize?scopes="
                                                + "+".join(kw.get("scopes") or [])}
GROUP_PEOPLE = {"g-a": [{"id": "oid-admin@ftpua.com", "name": "Адмін", "email": "admin@ftpua.com"}],
                "g-i": [{"id": "oid-ini@ftpua.com", "name": "Ініціатор", "email": "ini@ftpua.com"},
                        {"id": "oid-admin@ftpua.com", "name": "Адмін", "email": "admin@ftpua.com"}],
                "g-r": [{"id": "oid-acc@ftpua.com", "name": "Бухгалтер", "email": "acc@ftpua.com"}]}
# Токен адміністратора (Graph User.ReadBasic.All) — ім'я й пошта; токен самого додатку — лише ID учасників
graph.group_members = lambda t, g: [p if t != "app-token" else {"id": p["id"], "name": "", "email": ""}
                                    for p in GROUP_PEOPLE.get(g, [])]
mailer.app_token = lambda: "app-token"
c = m.app.test_client()
page = lambda url: c.get(url).get_data(as_text=True)

# ---------- за замовчуванням — як було: Microsoft, ролі з груп Azure
r = c.get("/requests")
check("not logged in -> login page with next", r.status_code == 302 and "/login?next=/requests" in r.headers["Location"])
html = page("/login")
check("microsoft mode: only Microsoft button", "Увійти через Microsoft" in html and "Увійти через Google" not in html)
check("google start disabled in microsoft mode", "/login" in c.get("/auth/google/login").headers["Location"])
ms_login("admin@ftpua.com", ["g-a"])
check("microsoft admin works", c.get("/admin/settings?tab=data").status_code == 200)
html = page("/admin/settings?tab=data")
check("data tab: login methods, google not configured", 'name="login_methods"' in html and "не налаштовано" in html)

c.post("/admin/settings", data={"tab": "data", "login_methods": "both"})
check("switch refused without Google client", app_settings.get("login_methods") == "microsoft"
      and not user_roles.all_users())

# ---------- клієнт Google налаштовано -> перемикання переносить ролі з Azure
os.environ.update(GOOGLE_CLIENT_ID="cid.apps.googleusercontent.com", GOOGLE_CLIENT_SECRET="x",
                  GOOGLE_ALLOWED_DOMAINS="ftpua.com")
real_import = mailer.app_token
mailer.app_token = lambda: (_ for _ in ()).throw(mailer.MailError("немає доступу"))
c.post("/admin/settings", data={"tab": "data", "login_methods": "both"})
check("graph failure -> not switched", app_settings.get("login_methods") == "microsoft")
mailer.app_token = real_import
user_directory._load().clear()  # хто входив — у довіднику; бухгалтер ще не входив
user_directory.remember([{"id": "oid-admin@ftpua.com", "name": "Адмін", "email": "admin@ftpua.com"},
                         {"id": "oid-ini@ftpua.com", "name": "Ініціатор", "email": "ini@ftpua.com"}])
r = c.post("/admin/settings", data={"tab": "data", "login_methods": "both"}, follow_redirects=True)
check("switched to both", app_settings.get("login_methods") == "both" and wa.login_mode() == "both")
check("roles imported", user_roles.get("oid-admin@ftpua.com")["roles"] == {"admin", "initiator"}
      and user_roles.get("oid-acc@ftpua.com")["roles"] == {"acc_resident"}
      and "Ролі перенесено з груп Azure в систему: 3" in r.get_data(as_text=True))
check("imported with only IDs: email and name from the directory", user_roles.get("oid-ini@ftpua.com")["email"] == "ini@ftpua.com"
      and user_roles.find_by_email("admin@ftpua.com")["id"] == "oid-admin@ftpua.com")
check("never logged in -> no email yet, no mail", user_roles.get("oid-acc@ftpua.com")["email"] == ""
      and m._role_members("acc_resident") == [])

ms_logout()
html = page("/login?next=/requests")
check("both: Google and Microsoft buttons", "Увійти через Google" in html and "Увійти через Microsoft" in html)

# ---------- вхід через Google
def id_token(**over):
    claims_ = {"iss": "https://accounts.google.com", "aud": "cid.apps.googleusercontent.com", "exp": time.time() + 600,
               "email_verified": True, "sub": "g-1", **over}
    body = base64.urlsafe_b64encode(json.dumps(claims_).encode()).decode().rstrip("=")
    return "eyJhbGciOiJSUzI1NiJ9." + body + ".sig"

class Resp:
    def __init__(self, data, status=200): self.data, self.status_code = data, status
    def json(self): return self.data
posted = []
def google_flow(email, name="Г. Користувач", nxt="/requests", tamper=None, **over):
    """Пройти вхід через Google: (відповідь callback, параметри адреси Google)."""
    r = c.get("/auth/google/login?next=" + nxt)
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
check("google url: client, hd hint, account choice", r is not None and q["client_id"] == ["cid.apps.googleusercontent.com"]
      and q["hd"] == ["ftpua.com"] and q["prompt"] == ["select_account"] and q["scope"] == ["openid email profile"])
check("wrong state -> error, not logged in", "застаріла" in r.get_data(as_text=True)
      and c.get("/requests").status_code == 302)
r, _ = google_flow("someone@gmail.com")
check("other domain -> refused", "Вхід дозволено лише робочим акаунтам (@ftpua.com)" in r.get_data(as_text=True)
      and c.get("/requests").status_code == 302)
r, _ = google_flow("ini@ftpua.com", email_verified=False)
check("unverified email -> refused", "не підтвердив" in r.get_data(as_text=True))
r, _ = google_flow("ini@ftpua.com", aud="other-client")
check("token for another client -> refused", "не для цього сайту" in r.get_data(as_text=True))
r, _ = google_flow("ini@ftpua.com", nxt="//evil.example.com")
check("foreign next ignored", r.status_code == 302 and r.headers["Location"] in ("/", "http://localhost/"))
check("code exchanged with secret and callback uri", posted[-1]["grant_type"] == "authorization_code"
      and posted[-1]["redirect_uri"].endswith("/auth/google/callback") and posted[-1]["code"] == "abc")
r = c.get("/requests")
check("google user with imported role works", r.status_code == 200)
check("same person (Entra id) -> same user id", wa._app_user_id({"oid": None}, "ini@ftpua.com")[0] == "oid-ini@ftpua.com")
check("google user is not an admin", c.get("/admin/users").status_code == 403)

r, _ = google_flow("new@ftpua.com")
check("google user without roles -> no access", c.get("/requests").status_code == 403)

# ---------- адмінка: ролі в системі, без Graph
graph.group_members = lambda t, g: (_ for _ in ()).throw(AssertionError("Graph must not be used"))
r, _ = google_flow("admin@ftpua.com", name="Адмін")
html = page("/admin/users")
check("admin via google: users from the system", "Ініціатор" in html and "Адмін" in html
      and "Не налаштовано групи" not in html)
check("users list: warning about unknown email", "Невідома пошта в 1 користувачів" in html and "oid-acc@ftpua.com" in html)
html = page("/admin/users/oid-acc@ftpua.com")
check("card: email field for ID-only person", 'name="person_email"' in html)
c.post("/admin/users/oid-acc@ftpua.com", data={"roles": ["acc_resident"], "person_email": "x@gmail.com"})
check("foreign-domain email refused", user_roles.get("oid-acc@ftpua.com")["email"] == "")
c.post("/admin/users/oid-acc@ftpua.com", data={"roles": ["acc_resident"], "person_email": "ini@ftpua.com"})
check("email of another user refused", user_roles.get("oid-acc@ftpua.com")["email"] == "")
c.post("/admin/users/oid-acc@ftpua.com", data={"roles": ["acc_resident"], "person_email": "Acc@ftpua.com",
                                               "person_name": "Бухгалтер"})
rec = user_roles.get("oid-acc@ftpua.com")
check("email and name saved", rec["email"] == "acc@ftpua.com" and rec["name"] == "Бухгалтер" and rec["roles"] == {"acc_resident"})
check("notifications: role members from the system", [p["email"] for p in m._role_members("acc_resident")] == ["acc@ftpua.com"])
found = c.get("/admin/users/search?q=new@ftpua.com").get_json()["users"]
check("search: new email suggested", any(u["id"] == "new@ftpua.com" and u.get("new") for u in found))
check("search: foreign domain not suggested",
      not any(u.get("new") for u in c.get("/admin/users/search?q=x@gmail.com").get_json()["users"]))
check("search by name", any(u["id"] == "oid-acc@ftpua.com" for u in c.get("/admin/users/search?q=бухг").get_json()["users"]))
html = page("/admin/users/new@ftpua.com")
check("card of someone who logged in: name from Google, app roles text", "Г. Користувач" in html
      and 'name="person_name"' not in html and "зберігаються в самій системі" in html and "uc-azure" not in html)
check("never seen email: name field", 'name="person_name"' in page("/admin/users/fresh@ftpua.com"))
c.post("/admin/users/fresh@ftpua.com", data={"roles": ["cfo"], "person_name": "Нова Людина"})
check("new person saved with name", user_roles.get("fresh@ftpua.com")["name"] == "Нова Людина")
check("foreign email card refused", c.get("/admin/users/x@gmail.com").status_code == 302)
c.post("/admin/users/new@ftpua.com", data={"roles": ["initiator"], "person_name": "Нова Людина"})
rec = user_roles.get("new@ftpua.com")
check("roles saved in the system", rec and rec["roles"] == {"initiator"} and rec["email"] == "new@ftpua.com")
r, _ = google_flow("new@ftpua.com")
check("new person now has access at once", c.get("/requests").status_code == 200)

google_flow("admin@ftpua.com", name="Адмін")
c.post("/admin/users/oid-admin@ftpua.com", data={"roles": ["initiator"]})
check("own admin role kept", "admin" in user_roles.get("oid-admin@ftpua.com")["roles"])
c.post("/admin/users/oid-acc@ftpua.com", data={"roles": ["acc_resident", "cfo"]})
check("role added without Graph", user_roles.get("oid-acc@ftpua.com")["roles"] == {"acc_resident", "cfo"})

# ---------- лише Google: Microsoft — лише для ADMIN_EMAILS
c.post("/admin/settings", data={"tab": "data", "login_methods": "google"})
check("switched to google only", wa.login_mode() == "google")
c.get("/auth/logout")
check("logout forgets google session", c.get("/requests").status_code == 302)
html = page("/login")
check("google only: Google button + admin fallback", "Увійти через Google" in html and "Увійти через Microsoft" not in html
      and "Вхід адміністратора через Microsoft" in html)
ms_login("acc@ftpua.com", ["g-r"])
check("microsoft non-admin blocked", c.get("/requests").status_code == 302)
ms_login("boss@ftpua.com")
check("ADMIN_EMAILS via microsoft works", c.get("/admin/settings?tab=data").status_code == 200)

# ---------- назад до Microsoft: ролі знову з Azure
graph.group_members = lambda t, g: list(GROUP_PEOPLE.get(g, []))
r = c.post("/admin/settings", data={"tab": "data", "login_methods": "microsoft"}, follow_redirects=True)
check("back to microsoft with warning", wa.login_mode() == "microsoft"
      and "ролі знову беруться з груп Azure" in r.get_data(as_text=True))
ms_login("new@ftpua.com")
check("microsoft mode: system roles ignored", c.get("/requests").status_code == 403)

# ---------- без доменів Google вимкнено (нікого не пускаємо)
app_settings.set("login_methods", "both", "t")
os.environ["GOOGLE_ALLOWED_DOMAINS"] = ""
check("no domains -> google not configured -> microsoft", wa.login_mode() == "microsoft")
print("ALL OK" if ok else "SOME FAILED")
