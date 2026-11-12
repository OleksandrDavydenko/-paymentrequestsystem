"""Бюджет з 1С на вкладці «Інтеграція з 1С»: розділ видно лише з увімкненим бюджетом; інтервал оновлення
(budget_sync_minutes, типово 30 хв) — у журналі змін і в кеші; «Оновити зараз»; фонова задача не звертається до
Power BI, поки бюджет вимкнено."""
import os, re, sys, tempfile, time
sys.path.insert(0, os.getcwd())
os.environ.update(UPLOAD_DIR=tempfile.mkdtemp(), ADMIN_EMAILS="adm@ftpua.com")
import app as m
import app_settings, budget, settings_log
from web import background

ok = True
def check(label, cond):
    global ok
    ok &= bool(cond); print(("PASS " if cond else "FAIL ") + label)

claims = {"name": "Adm", "preferred_username": "adm@ftpua.com", "oid": "adm@ftpua.com"}
m.auth._auth.get_user = lambda: dict(claims)
c = m.app.test_client()
page = lambda url: c.get(url, follow_redirects=True).get_data(as_text=True)
flashes = lambda html: " | ".join(re.findall(r'class="flash[^"]*">(.*?)</', html, re.S))

calls = []
real_refresh = budget._table.refresh
def counting_refresh():
    calls.append(1)
    return real_refresh()
budget._table.refresh = counting_refresh

# ---------- бюджет вимкнено (типово): розділу немає, оновлювати нічого
html = page("/admin/settings?tab=budget_sync")
check("budget off: no «Бюджет з 1С» block on the integration tab", "Оновлювати кожні" not in html
      and 'id="budget-sync"' not in html and "Звірка з платежами 1С" in html)
html = c.post("/admin/budget/refresh", follow_redirects=True).get_data(as_text=True)
check("budget off: refresh refused", "Бюджет вимкнено" in flashes(html) and not calls)
check("budget off: background job does not touch Power BI", background.run_budget_sync() is None and not calls
      and not budget.sync_due())

# ---------- увімкнено: інтервал (типово 30 хв) і кнопка
app_settings.set("budget_enabled", True, "t")
html = page("/admin/settings?tab=budget_sync")
check("budget on: block with the interval and the button", 'id="budget-sync"' in html and "Оновити зараз" in html
      and '<option value="30" selected>30 хвилин</option>' in html)
check("default interval 30 min", budget.sync_minutes() == 30 and budget._table._ttl() == 1800)
check("budget settings link to the integration tab", "tab=budget_sync" in page("/admin/settings?tab=budget"))

html = c.post("/admin/settings", data={"tab": "budget_sync", "budget_sync_minutes": "60"}, follow_redirects=True).get_data(as_text=True)
check("interval saved: 1 hour, cache follows", app_settings.get("budget_sync_minutes") == "60" and budget._table._ttl() == 3600
      and "1 година" in flashes(html))
check("interval change is in the journal", any(r["key"] == "budget_sync_minutes" and r["new_label"] == "1 година"
                                               for r in settings_log.query(20)))
c.post("/admin/settings", data={"tab": "budget_sync", "budget_sync_minutes": "7"})
check("unknown interval rejected", app_settings.get("budget_sync_minutes") == "60")

# ---------- «Оновити зараз»
budget._fact_docs["2026-11"] = (time.time(), frozenset({"X"}))
calls.clear()
html = c.post("/admin/budget/refresh", follow_redirects=True).get_data(as_text=True)
check("refresh now: re-read, message with rows, fact documents cache reset", calls and not budget._fact_docs
      and "Бюджет оновлено з Power BI" in flashes(html))

# ---------- фонова задача: лише коли копія старша за інтервал
calls.clear()
check("fresh copy: background job waits", not budget.sync_due() and background.run_budget_sync() is None and not calls)
budget._table._state["loaded_at"] = time.time() - 3600
res = background.run_budget_sync()
check("stale copy: background job refreshes", calls and res and res["ok"] and not budget.sync_due())
print("ALL OK" if ok else "SOME FAILED")
