"""Святкове оформлення («Налаштування → Дизайн»): календар свят (православний Великдень, День вишиванки, новорічне
через рік), яке свято діє, «Показати зараз», <html data-holiday>, вкладка й приклад картки."""
import os, sys, tempfile
from datetime import date
sys.path.insert(0, os.getcwd())
tmp = tempfile.mkdtemp()
os.environ.update(UPLOAD_DIR=tmp, ADMIN_EMAILS="adm@ftpua.com")
import app as m
import app_settings, holidays, settings_log, user_roles

ok = True
def check(label, cond):
    global ok
    ok &= bool(cond); print(("PASS " if cond else "FAIL ") + label)

# ---------- календар
check("orthodox Easter", [holidays.orthodox_easter(y) for y in (2024, 2025, 2026, 2027)]
      == [date(2024, 5, 5), date(2025, 4, 20), date(2026, 4, 12), date(2027, 5, 2)])
check("vyshyvanka: third Thursday of May", holidays.ranges(2026)["vyshyvanka"] == (date(2026, 5, 19), date(2026, 5, 21)))
ALL = list(holidays.HOLIDAYS)
act = lambda d, en=ALL, force="auto": holidays.active(d, en, force)
check("new year crosses the year", act(date(2026, 12, 15)) == "new_year" and act(date(2027, 1, 14)) == "new_year"
      and act(date(2026, 12, 14)) is None and act(date(2027, 1, 15)) is None)
check("easter week", act(date(2026, 4, 6)) == "easter" and act(date(2026, 4, 13)) == "easter" and act(date(2026, 4, 14)) is None)
check("fixed days", act(date(2026, 8, 24)) == "independence" and act(date(2026, 7, 16)) == "accountant"
      and act(date(2026, 3, 8)) == "women_day" and act(date(2026, 2, 14)) == "valentine" and act(date(2026, 10, 31)) == "halloween")
check("only enabled holidays", act(date(2026, 10, 28), ["new_year"]) is None and act(date(2026, 10, 28), []) is None)
check("«show now» wins over the date", act(date(2026, 6, 1), [], "new_year") == "new_year")
check("ranges never overlap", all(sum(holidays._on(c, date(2026, 1, 1).fromordinal(date(2026, 1, 1).toordinal() + i)) for c in ALL) <= 1
                                  for i in range(366)))
check("next up and dates text", holidays.next_up(date(2026, 10, 24), ALL) == ("halloween", date(2026, 10, 27))
      and holidays.when("new_year", date(2026, 10, 24)) == "15 грудня 2026 – 14 січня 2027"
      and holidays.when("easter", date(2026, 10, 24)) == "26 квітня – 3 травня 2027")

# ---------- сторінки й налаштування
claims = {}
def login(email):
    claims.clear(); claims.update({"name": email.split("@")[0].upper(), "preferred_username": email, "oid": email})
m.auth._auth.get_user = lambda: dict(claims) or None
c = m.app.test_client()
page = lambda url: c.get(url).get_data(as_text=True)
user_roles.set_roles("a@ftpua.com", "a@ftpua.com", "A", ["initiator"], "t")

login("a@ftpua.com")
html = page("/requests/new")
check("off by default", "data-holiday=" not in html and 'class="card req-head"' in html and 'class="holiday-greet"' in html)

login("adm@ftpua.com")
html = page("/admin/settings?tab=design")
check("design tab: 8 holidays with thumbnails, try buttons and «show now»", html.count('name="holidays"') == 8
      and html.count('class="holiday-thumb holiday-scope"') == 8 and html.count('class="btn btn-sm holiday-try"') == 8
      and 'name="holiday_now"' in html and "Святкове оформлення" in html)
c.post("/admin/settings", data={"tab": "design", "card_design": "classic", "color_theme": "blue",
                                "holidays": ["new_year", "halloween"], "holiday_now": "independence"})
check("saved, journal", app_settings.get("holidays") == ["halloween", "new_year"] and app_settings.get("holiday_now") == "independence"
      and any(e["label"] == "Святкове оформлення за календарем" for e in settings_log.query(20)))
login("a@ftpua.com")
check("everyone sees the holiday", 'data-holiday="independence"' in page("/requests/new") and 'data-holiday="independence"' in page("/requests"))
login("adm@ftpua.com")
c.post("/admin/settings", data={"tab": "design", "card_design": "classic", "color_theme": "blue", "holiday_now": "auto"})
check("unticked + calendar: none", app_settings.get("holidays") == [] and app_settings.get("holiday_now") == "auto"
      and "data-holiday=" not in page("/requests"))
h = page("/admin/settings/design/preview?design=wizard&mode=edit&holiday=easter&theme=dark")
check("preview with a holiday", 'data-holiday="easter"' in h and 'data-theme="dark"' in h and ">Без свята</a>" in h)
check("preview without", "data-holiday=" not in page("/admin/settings/design/preview?holiday=none"))
css = open("static/style.css", encoding="utf-8").read()
check("css: every holiday has decorations, files exist", all(f'[data-holiday="{code}"]' in css for code in ALL)
      and all(os.path.exists(os.path.join("static", u.split("/static/")[1])) for u in
              {part.split(")")[0] for part in css.split("url(")[1:] if part.startswith("/static/holiday/")}))
print("ALL OK" if ok else "SOME FAILED")
