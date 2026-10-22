"""Сторінка «Налаштування»: вкладки, збереження лише полів вкладки, перевірка значень, журнал змін,
експорт / імпорт з переглядом змін."""
import io, json, os, sys, tempfile
sys.path.insert(0, os.getcwd())
tmp = tempfile.mkdtemp()
os.environ.update(UPLOAD_DIR=tmp, GROUP_ADMIN="g-a", GROUP_INITIATOR="g-i", ADMIN_EMAILS="")
import app as m
import app_settings, graph, secret_box, settings_log, settings_schema

ok = True
def check(label, cond):
    global ok
    ok &= bool(cond); print(("PASS " if cond else "FAIL ") + label)

claims = {}
def login(email, groups):
    claims.clear(); claims.update({"name": email, "preferred_username": email, "oid": email, "groups": groups})
m.auth._auth.get_user = lambda: dict(claims)
m.auth._auth.get_token_for_user = lambda s: {"access_token": "t", "scope": " ".join(s)}
graph.group_members = lambda t, g: []
c = m.app.test_client()

login("i@ftpua.com", ["g-i"])
check("non-admin: 403", c.get("/admin/settings").status_code == 403
      and c.post("/admin/settings", data={"tab": "approval", "approval_mode": "parallel"}).status_code == 403)

login("admin@ftpua.com", ["g-a"])
for code, title in settings_schema.TABS:
    r = c.get(f"/admin/settings?tab={code}")
    check(f"tab '{title}' opens and is active", r.status_code == 200
          and f'href="/admin/settings?tab={code}" class="tab active"' in r.get_data(as_text=True))
html = c.get("/admin/settings").get_data(as_text=True)
check("default tab: approval, admin tabs on top", "Маршрут погодження" in html and "Користувачі й ролі" in html
      and 'name="tab" value="approval"' in html)

# ---------- вкладка зберігає лише свої поля; галочка без позначки — вимкнена
before_org = app_settings.get("org_field_mode")
c.post("/admin/settings", data={"tab": "approval", "approval_mode": "parallel", "dept_head_mode": "view",
                                "cfo_mode": "approve", "cfo_threshold_usd": "1000", "org_field_mode": "payer"})
check("approval tab saved", app_settings.get("approval_mode") == "parallel")
check("field of another tab ignored", app_settings.get("org_field_mode") == before_org)
check("checkbox absent on its tab -> off", app_settings.get("no_self_approval") is False)
c.post("/admin/settings", data={"tab": "approval", "no_self_approval": "1"})
check("checkbox on", app_settings.get("no_self_approval") is True)
c.post("/admin/settings", data={"tab": "payment", "cash_handover_remind_days": "3"})
check("other tab doesn't touch approval checkbox", app_settings.get("no_self_approval") is True
      and app_settings.get("cash_handover_remind_days") == 3)
check("payment tab: unticked boxes off", app_settings.get("cash_handover_confirm") is False
      and app_settings.get("require_payment_account") is False)

# ---------- перевірка значень: нічого не зберігається
r = c.post("/admin/settings", data={"tab": "payment", "cash_handover_remind_days": "99", "pending_cp_payment": "allow",
                                    "cash_handover_confirm": "1"}, follow_redirects=True)
check("out of range rejected, nothing saved", "від 0 до 30" in r.get_data(as_text=True)
      and app_settings.get("pending_cp_payment") == "block" and app_settings.get("cash_handover_remind_days") == 3)
r = c.post("/admin/settings", data={"tab": "approval", "approval_mode": "zzz"}, follow_redirects=True)
check("unknown option rejected", "невідомий варіант" in r.get_data(as_text=True) and app_settings.get("approval_mode") == "parallel")
r = c.post("/admin/settings", data={"tab": "approval", "cfo_mode": "threshold", "cfo_threshold_usd": "0"}, follow_redirects=True)
check("threshold 0 in threshold mode rejected", "більшу за 0" in r.get_data(as_text=True) and app_settings.get("cfo_mode") == "approve")
r = c.post("/admin/settings", data={"tab": "approval", "approval_mode": "parallel", "no_self_approval": "1"},
           follow_redirects=True)
check("no changes -> said so", "Змін немає" in r.get_data(as_text=True))

# ---------- журнал змін
log = settings_log.query()
row = next(x for x in log if x["key"] == "cash_handover_remind_days")
check("journal: who / old -> new", row["by"] == "admin@ftpua.com" and row["old_label"] == "1" and row["new_label"] == "3"
      and row["label"] == settings_schema.label("cash_handover_remind_days"))
app_settings.set("smtp_password", secret_box.encrypt("Sup3r-secret"), "admin@ftpua.com")
page = c.get("/admin/settings?tab=log").get_data(as_text=True)
check("journal page: masked password, filter", "Sup3r-secret" not in page and "••••" in page and 'name="key"' in page
      and "Автор підтверджує передачу готівки" in page)
check("journal filter by setting", all(x["key"] == "approval_mode" for x in settings_log.query(key="approval_mode")))

# ---------- експорт → зміни → імпорт (перегляд, потім застосувати)
r = c.get("/admin/settings/export")
data = json.loads(r.get_data(as_text=True))
check("export: json attachment without password", r.status_code == 200 and "attachment" in r.headers["Content-Disposition"]
      and "smtp_password" not in data["settings"] and data["settings"]["approval_mode"] == "parallel"
      and "Sup3r" not in r.get_data(as_text=True))
c.post("/admin/settings", data={"tab": "approval", "approval_mode": "sequential", "cfo_mode": "observe"})
check("changed after export", app_settings.get("approval_mode") == "sequential")
r = c.post("/admin/settings/import", data={"file": (io.BytesIO(json.dumps(data).encode()), "s.json")},
           content_type="multipart/form-data", follow_redirects=True)
html = r.get_data(as_text=True)
check("import: preview, not applied yet", "Зміни з файлу" in html and "Застосувати" in html
      and app_settings.get("approval_mode") == "sequential")
c.post("/admin/settings/import/apply", data={"action": "apply"})
check("import applied", app_settings.get("approval_mode") == "parallel" and app_settings.get("cfo_mode") == "approve")
check("import changes in journal", settings_log.query(key="approval_mode")[0]["new_label"].startswith("Паралельно"))
r = c.post("/admin/settings/import", data={"file": (io.BytesIO(b"not json"), "s.json")},
           content_type="multipart/form-data", follow_redirects=True)
check("bad file rejected", "Не вдалося прочитати файл" in r.get_data(as_text=True))
bad = {"settings": {"cash_handover_remind_days": 500}}
r = c.post("/admin/settings/import", data={"file": (io.BytesIO(json.dumps(bad).encode()), "s.json")},
           content_type="multipart/form-data", follow_redirects=True)
check("invalid value in file rejected", "від 0 до 30" in r.get_data(as_text=True)
      and "Зміни з файлу" not in r.get_data(as_text=True))
same = c.post("/admin/settings/import", data={"file": (io.BytesIO(json.dumps(data).encode()), "s.json")},
              content_type="multipart/form-data", follow_redirects=True).get_data(as_text=True)
check("same file -> no changes", "змін немає" in same)
c.post("/admin/settings", data={"tab": "approval", "approval_mode": "sequential"})
c.post("/admin/settings/import", data={"file": (io.BytesIO(json.dumps(data).encode()), "s.json")},
       content_type="multipart/form-data")
c.post("/admin/settings/import/apply", data={"action": "cancel"})
check("import cancelled -> unchanged", app_settings.get("approval_mode") == "sequential")

# ---------- старі адреси розділів
users = c.get("/admin/users").get_data(as_text=True)
check("old section anchors redirect to tabs", "'#process-settings': 'approval'" in users and "'#mail-settings': 'notify'" in users)
print("ALL OK" if ok else "SOME FAILED")
