"""Швидкість (web/performance.py): статичні файли, маніфест і favicon — без сеансу й без звернень до бази; статика з
версією в адресі кешується браузером на рік; шаблони можна скомпілювати заздалегідь, а компіляція під час запиту
видна в підсумку запиту."""
import os, re, sys, tempfile
sys.path.insert(0, os.getcwd())
os.environ.update(UPLOAD_DIR=tempfile.mkdtemp(), ADMIN_EMAILS="adm@ftpua.com")
import app as m
import user_budget; user_budget.get = lambda oid: {"direct": True, "all_departments": True, "departments": []}
import db_sql
from flask import g
from web import performance

ok = True
def check(label, cond):
    global ok
    ok &= bool(cond); print(("PASS " if cond else "FAIL ") + label)

import user_roles
user_roles.set_roles("adm@ftpua.com", "adm@ftpua.com", "Adm", ["initiator"], "t")  # «Мої заявки» й нова заявка
claims = {"name": "Adm", "preferred_username": "adm@ftpua.com", "oid": "adm@ftpua.com"}
m.auth._auth.get_user = lambda: dict(claims)
timings = []
@m.app.after_request
def _keep_timings(resp):
    timings.append(dict(g.get("timings") or {}))
    return resp
c = m.app.test_client()

# ---------- шаблони: компіляція під час запиту видна; після прогріву — немає
r = c.get("/requests/new")
check("first render: template compile time counted", r.status_code == 200 and "tpl" in timings[-1])
check("warm-up compiles every template", performance.warm_templates(m.app)
      == len(m.app.jinja_env.list_templates(filter_func=lambda n: n.endswith(".html"))))
c.get("/requests/1"); c.get("/admin/users")
check("after warm-up: no compiling during requests", "tpl" not in timings[-1] and "tpl" not in timings[-2])
check("ops log label", "компіляція шаблонів" in m.ops_log.timing_summary({"tpl": (2, 1.5)}))

# ---------- версія в адресі статики й кеш
html = c.get("/requests", follow_redirects=True).get_data(as_text=True)
css = re.search(r'href="(/static/style\.css\?v=([0-9a-f]{10}))"', html)
check("static URL carries a content version", css)
r = c.get(css.group(1))
check("versioned file: cached for a year", r.status_code == 200 and r.cache_control.max_age == performance.STATIC_MAX_AGE
      and r.cache_control.public)
r = c.get("/static/style.css")
check("without version: revalidated as before", r.status_code == 200 and r.cache_control.max_age != performance.STATIC_MAX_AGE)
check("same file, same version", performance.static_version(m.app.static_folder, "style.css") == css.group(2))

# ---------- легкі запити — без бази (сховище в базі: сеанси входу там же)
if db_sql.configured():
    import sqlalchemy as sa
    queries = []
    sa.event.listen(db_sql.engine(), "before_cursor_execute", lambda *a: queries.append(a[2]))
    c.get("/requests/new")  # сторінка з формою: сеанс із токеном форми — у базі, cookie в браузера
    check("logged-in session cookie present", c.get_cookie("session") is not None)
    queries.clear()
    for url in (css.group(1), "/static/table-tools.js", "/site.webmanifest", "/favicon.ico"):
        r = c.get(url)
        check(f"{url.split('?')[0]}: served", r.status_code == 200)
    check("static, manifest, favicon: no database queries", not queries)
    queries.clear()
    c.get("/requests")
    check("ordinary page: session from the database as before", any("sessions" in q.lower() for q in queries))
else:
    r = c.get("/site.webmanifest")
    check("manifest served (memory storage)", r.status_code == 200 and "short_name" in r.get_data(as_text=True))
print("ALL OK" if ok else "SOME FAILED")
