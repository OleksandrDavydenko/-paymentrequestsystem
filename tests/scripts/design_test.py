"""«Налаштування → Дизайн»: кольорова тема всієї програми (<html data-theme>), мініатюри й «Спробувати»;
дизайн картки заявки (класична, майстер, чек-лист, перегляд погоджувачем) і приклад картки в адмінці."""
import os, sys, tempfile
sys.path.insert(0, os.getcwd())
tmp = tempfile.mkdtemp()
os.environ.update(UPLOAD_DIR=tmp, ADMIN_EMAILS="adm@ftpua.com")
import app as m
import app_settings, org_structure, settings_log, user_budget, user_roles
import storage as db

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

# ---------- дизайн картки заявки
user_roles.set_roles("r@ftpua.com", "r@ftpua.com", "R", ["acc_resident"], "t")
user_budget.save_settings("a@ftpua.com", "a@ftpua.com", "A", True, True, [], updated_by="t")
org_structure.set_department("a@ftpua.com", "a@ftpua.com", "A", "LOG", "t")
app_settings.set("contractor_expense_codes", ["01.002"], "t")
BASE = {"pay_date": "2026-12-20", "organization_id": "1", "counterparty_id": "1", "currency": "UAH", "amount": "12450",
        "payment_form": "bank", "payment_kind_sent": "1", "payment_kind": "contractor", "expense_code": "01.002",
        "lines-0-invoice": "НП-1", "lines-0-amount": "12450", "lines-0-currency": "UAH"}
login("a@ftpua.com")
html = page("/requests/new")
check("default design: classic", 'class="req-layout"' in html and 'data-design=' not in html and "request-steps.js" not in html)
pos = [html.find(f'id="block-{b}"') for b in ("start", "who", "lines", "amount", "payer", "note")]
check("classic order: department & kind, who, invoices, amount, payer, note", -1 not in pos and pos == sorted(pos)
      and 'id="block-start" hidden' not in html and ">Що оплачуємо</h2>" in html and 'name="department"' not in html
      and 'id="meta-dept">' in html and '<span class="uc-num req-num"></span>' in html)
user_roles.set_roles("b@ftpua.com", "b@ftpua.com", "B", ["initiator"], "t")
org_structure.set_department("b@ftpua.com", "b@ftpua.com", "B", "BA", "t")
login("b@ftpua.com")
check("one department without direct costs: first block skipped", 'id="block-start" hidden' in page("/requests/new"))
org_structure.set_request_departments("a@ftpua.com", "a@ftpua.com", "A", ["SAL"], "t")
login("a@ftpua.com")
html = page("/requests/new")
check("several departments: department first, own preselected", ">Відділ і що оплачуємо</h2>" in html
      and html.find('name="department"') < html.find('name="payment_kind"') < html.find('id="cp-input"')
      and 'value="LOG" selected' in html)

login("adm@ftpua.com")
html = page("/admin/settings?tab=design")
check("design tab: three card designs with thumbnails and examples", html.count('name="card_design"') == 3
      and html.count('class="card-thumb ct-') == 3 and 'value="classic" checked' in html
      and "/admin/settings/design/preview?design=wizard&amp;mode=view" in html)
c.post("/admin/settings", data={"tab": "design", "card_design": "wizard", "color_theme": "dark"})
check("design saved, journal", app_settings.get("card_design") == "wizard"
      and any(e["label"] == "Дизайн картки заявки" and e["new_label"] == "Покроковий майстер" for e in settings_log.query(20)))

login("a@ftpua.com")
html = page("/requests/new")
check("wizard: steps, review, scripts", 'data-design="wizard"' in html and 'id="wiz-steps"' in html
      and 'data-step="review"' in html and 'data-step="who"' in html and "request-steps.js" in html
      and "request-summary.js" in html and 'id="sum-steps"' in html and 'data-start=""' in html
      and 'value="submit"' in html and 'class="req-layout"' not in html)
r_ = c.post("/requests/new", data={**BASE, "amount": "", "action": "submit"})
check("wizard: server error opens its step", 'data-start="amount"' in r_.get_data(as_text=True))
r_ = c.post("/requests/new", data={k: v for k, v in BASE.items() if k != "payment_kind"} | {"action": "save"})
check("wizard: missing kind opens the first step", 'data-start="start"' in r_.get_data(as_text=True))
check("wizard: department and kind in the first step", 'data-step="start" data-title="Відділ і що оплачуємо"' in html
      and 'data-goto="start"' in html)
r_ = c.post("/requests/new", data={**BASE, "action": "save", "step": "lines"})
rid = db.last_id()
check("wizard: «Записати» keeps the step", r_.status_code == 302 and "step=lines" in r_.headers["Location"]
      and 'data-start="lines"' in page(r_.headers["Location"]))
check("wizard: saved draft opens on review", 'data-start="review"' in page(f"/requests/{rid}"))
c.post(f"/requests/{rid}", data={**BASE, "action": "submit", "version": str(db.get_request(rid).get("version") or 0)})
check("submitted", db.get_request(rid)["status"] != "draft")
login("r@ftpua.com")
html = page(f"/requests/{rid}")
check("not editable: read view, no form", 'class="card req-view"' in html and 'id="request-form"' not in html
      and "НП-1" in html and "450,00" in html and "request-steps.js" not in html and "Рішення" in html)

app_settings.set("card_design", "checklist", "t")
login("a@ftpua.com")
html = page("/requests/new")
check("checklist: sections and bottom bar", 'data-design="checklist"' in html and html.count('class="cl-head"') >= 4
      and 'class="cl-bar"' in html and 'data-optional="1"' in html and "request-steps.js" in html)

# ---------- приклад картки в адмінці
login("adm@ftpua.com")
n = db.last_id()
ok_all = True
for design in ("classic", "wizard", "checklist"):
    for mode in ("edit", "view"):
        r_ = c.get(f"/admin/settings/design/preview?design={design}&mode={mode}&theme=emerald")
        h = r_.get_data(as_text=True)
        good = (r_.status_code == 200 and 'id="preview-bar"' in h and 'data-theme="emerald"' in h
                and ("req-view" in h if mode == "view" and design != "classic" else True)
                and (f'data-design="{design}"' in h if mode == "edit" and design != "classic" else True))
        if not good:
            print("  preview problem:", design, mode, r_.status_code)
        ok_all &= good
check("preview: every design, mode and theme", ok_all)
check("preview saves nothing, settings unchanged", db.last_id() == n and app_settings.get("card_design") == "checklist"
      and app_settings.get("color_theme") == "dark")
login("a@ftpua.com")
check("preview: admin only", c.get("/admin/settings/design/preview").status_code in (302, 403))
app_settings.set("card_design", "classic", "t")
print("ALL OK" if ok else "SOME FAILED")
