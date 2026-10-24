"""«Налаштування → Дизайн»: кольорова тема всієї програми (<html data-theme>), мініатюри й «Спробувати»."""
import os, sys, tempfile
sys.path.insert(0, os.getcwd())
tmp = tempfile.mkdtemp()
os.environ.update(UPLOAD_DIR=tmp, ADMIN_EMAILS="adm@ftpua.com")
import app as m
import app_settings, settings_log, user_roles

ok = True
def check(label, cond):
    global ok
    ok &= bool(cond); print(("PASS " if cond else "FAIL ") + label)

claims = {}
def login(email):
    claims.clear(); claims.update({"name": email.split("@")[0].upper(), "preferred_username": email, "oid": email})
m.auth._auth.get_user = lambda: dict(claims) or None
c = m.app.test_client()
page = lambda url: c.get(url).get_data(as_text=True)

user_roles.set_roles("a@ftpua.com", "a@ftpua.com", "A", ["initiator"], "t")

# ---------- за замовчуванням — синя
login("a@ftpua.com")
html = page("/requests/new")
check("default theme: blue", 'data-theme="blue"' in html and 'content="#0f6cbd"' in html and 'class="logo-doc"' in html)

# ---------- вкладка «Дизайн»
login("adm@ftpua.com")
html = page("/admin/settings?tab=design")
check("design tab: four themes with thumbnails and try buttons", html.count('name="color_theme"') == 4
      and html.count("theme-thumb theme-scope") == 4 and html.count('class="btn btn-sm theme-try"') == 4
      and 'value="blue" checked' in html and ">Дизайн<" in html)
c.post("/admin/settings", data={"tab": "design", "color_theme": "dark"})
check("theme saved, journal", app_settings.get("color_theme") == "dark"
      and any(e["label"] == "Кольорова тема" and e["new_label"] == "Темна" for e in settings_log.query(20)))
c.post("/admin/settings", data={"tab": "design", "color_theme": "neon"})
check("unknown theme refused", app_settings.get("color_theme") == "dark")

login("a@ftpua.com")
html = page("/requests/new")
check("everyone gets the theme", 'data-theme="dark"' in html and 'content="#161b22"' in html)
check("lists too", 'data-theme="dark"' in page("/requests"))
check("non-admin cannot change", c.post("/admin/settings", data={"tab": "design", "color_theme": "blue"}).status_code in (302, 403)
      and app_settings.get("color_theme") == "dark")

css = open("static/style.css", encoding="utf-8").read()
check("css: themes and thumbnail scope", all(f'html[data-theme="{t}"], .theme-scope[data-theme="{t}"]' in css
                                             for t in ("dark", "emerald", "graphite")) and ":root, .theme-scope {" in css)
print("ALL OK" if ok else "SOME FAILED")
