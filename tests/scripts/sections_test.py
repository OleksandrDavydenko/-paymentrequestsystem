"""Доступ до розділів-звітів («Усі заявки», «Звірка»): типово — за роллю, як і було; у картці користувача — винятки
(дозволити / заборонити), лише перегляд: дії й далі за ролями; меню й доступ до сторінки — одна перевірка."""
import os, re, sys, tempfile
sys.path.insert(0, os.getcwd())
tmp = tempfile.mkdtemp()
os.environ.update(UPLOAD_DIR=tmp, ADMIN_EMAILS="adm@ftpua.com")
import app as m
import storage as db
import user_roles

ok = True
def check(label, cond):
    global ok
    ok &= bool(cond); print(("PASS " if cond else "FAIL ") + label)

claims = {}
def login(email):
    claims.clear(); claims.update({"name": email.split("@")[0], "preferred_username": email, "oid": email})
m.auth._auth.get_user = lambda: dict(claims) or None
c = m.app.test_client()
page = lambda url: c.get(url, follow_redirects=True).get_data(as_text=True)
nav = lambda html: html.split('id="main-nav">')[1].split("</nav>")[0] if 'id="main-nav">' in html else ""
status = lambda url: c.get(url).status_code
def card(uid, roles, **sections):
    """Зберегти картку користувача з потрібними ролями й доступом до розділів (як адміністратор)."""
    login("adm@ftpua.com")
    data = {"roles": roles, "mode": "none", "sections_sent": "1"}
    data.update({f"section_{k}": v for k, v in sections.items()})
    return c.post(f"/admin/users/{uid}", data=data, follow_redirects=True).get_data(as_text=True)

for uid, roles in (("ini@ftpua.com", ["initiator"]), ("cfo@ftpua.com", ["cfo"]), ("res@ftpua.com", ["acc_resident"])):
    user_roles.set_roles(uid, uid, uid.split("@")[0].title(), roles, "t")

# ---------- типово — як було
for who in ("ini@ftpua.com", "res@ftpua.com"):
    login(who)
    menu = nav(page("/"))
    check(f"{who}: reports not in the menu, pages closed", menu and "Усі заявки" not in menu and "Звірка" not in menu
          and status("/admin/requests") == 403 and status("/payments/reconcile") == 403)
for who in ("cfo@ftpua.com", "adm@ftpua.com"):
    login(who)
    menu = nav(page("/"))
    check(f"{who}: both reports by role", "Усі заявки" in menu and "Звірка" in menu
          and status("/admin/requests") == 200 and status("/payments/reconcile") == 200)

# ---------- картка: пункт «Доступ до розділів», типово — за роллю
login("adm@ftpua.com")
html = page("/admin/users/ini@ftpua.com")
check("card: section access with the role default", "Доступ до розділів" in html and 'name="section_reconcile"' in html
      and "За роллю (зараз: не бачить)" in html and 'name="section_approvals"' not in html)
check("card of cfo: default 'sees'", "За роллю (зараз: бачить)" in page("/admin/users/cfo@ftpua.com"))

# ---------- ініціатору відкрили «Звірку»
html = card("ini@ftpua.com", ["initiator"], reconcile="1", all_requests="")
check("saved: only the exception, shown in the message", user_roles.section_overrides("ini@ftpua.com") == {"reconcile": True}
      and "доступ до розділів — + Звірка" in html)
users = page("/admin/users")
check("users list: mark and filter flag", "Розділи: + Звірка" in users and re.search(r'data-flags="[^"]*sections"', users))
login("ini@ftpua.com")
menu = nav(page("/"))
check("initiator: «Звірка» in the menu, page opens", "Звірка" in menu and "Усі заявки" not in menu
      and status("/payments/reconcile") == 200)
html = page("/payments/reconcile")
check("view only: no link / allow buttons", "rc-find" not in html and 'id="rc-pick"' not in html)
other = next(r for r in db.list_all() if r["author_email"].lower() != "ini@ftpua.com" and r["status"] != "draft")
check("request cards open (view)", status(f"/requests/{other['id']}") == 200)
check("linking a payment is still refused", c.post("/payments/link", data={"payment_guid": "X", "request_id": str(other["id"])})
      .status_code == 403)
r = c.get("/admin/requests")
check("other report stays closed, with a clear reason", r.status_code == 403
      and "Розділ <strong>«Усі заявки»</strong> вам не відкрито" in r.get_data(as_text=True))

# ---------- фіндиректору заборонили «Усі заявки»
card("cfo@ftpua.com", ["cfo"], all_requests="0")
login("cfo@ftpua.com")
menu = nav(page("/"))
check("cfo: «Усі заявки» hidden and closed, «Звірка» stays", "Усі заявки" not in menu and "Звірка" in menu
      and status("/admin/requests") == 403 and status("/payments/reconcile") == 200)
check("cfo home is not the closed report", "/admin/requests" not in (c.get("/").headers.get("Location") or ""))

# ---------- зміна ролі: типове значення змінюється, виняток лишається
user_roles.set_roles("ini@ftpua.com", "ini@ftpua.com", "Ini", ["initiator", "cfo"], "t")
login("ini@ftpua.com")
check("role change keeps the exception, default follows the role",
      user_roles.section_overrides("ini@ftpua.com") == {"reconcile": True} and "Усі заявки" in nav(page("/")))
user_roles.set_roles("ini@ftpua.com", "ini@ftpua.com", "Ini", ["initiator"], "t")

# ---------- робочі й невідомі розділи з форми не беруться; «За роллю» прибирає виняток
card("ini@ftpua.com", ["initiator"], reconcile="1", approvals="1", to_pay="1", bogus="1")
check("work / unknown sections ignored", user_roles.section_overrides("ini@ftpua.com") == {"reconcile": True})
html = card("ini@ftpua.com", ["initiator"], reconcile="")
check("'by role' removes the exception", user_roles.section_overrides("ini@ftpua.com") == {}
      and "доступ до розділів — усе за роллю" in html)
login("ini@ftpua.com")
check("closed again", status("/payments/reconcile") == 403)

# ---------- без ролей, лише відкритий розділ
user_roles.set_roles("guest@ftpua.com", "guest@ftpua.com", "Guest", [], "t")
user_roles.set_section_overrides("guest@ftpua.com", {"reconcile": True}, "t")
login("guest@ftpua.com")
check("no roles, only a report: home goes there", (c.get("/").headers.get("Location") or "").endswith("/payments/reconcile")
      and status("/payments/reconcile") == 200 and status("/admin/users") == 403)
print("ALL OK" if ok else "SOME FAILED")
