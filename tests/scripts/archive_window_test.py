"""Архівні списки за замовчуванням — за останні 12 місяців, «За весь час» — усі; посилання періоду
й «Показати ще» зберігають кілька вибраних значень фільтра."""
import os, re, sys, tempfile
from datetime import timedelta
sys.path.insert(0, os.getcwd())
tmp = tempfile.mkdtemp()
os.environ.update(UPLOAD_DIR=tmp, GROUP_ADMIN="g-a", GROUP_INITIATOR="g-i", GROUP_ACC_RESIDENT="g-r", GROUP_CFO="g-f",
                  ADMIN_EMAILS="")
import app as m
import storage as db

ok = True
def check(label, cond):
    global ok
    ok &= bool(cond); print(("PASS " if cond else "FAIL ") + label)

claims = {}
def login(email, groups):
    claims.clear(); claims.update({"name": email, "preferred_username": email, "oid": email, "groups": groups})
m.auth._auth.get_user = lambda: dict(claims)
m.auth._auth.get_token_for_user = lambda s: {"access_token": "t", "scope": " ".join(s)}
c = m.app.test_client()
def numbers(html):
    body = html.split("<tbody>")[1].split("</tbody>")[0] if "<tbody>" in html else ""
    return set(re.findall(r">(\d{9})<", body))

# стара оплачена заявка (2 роки тому)
paid = [r for r in db.list_all() if r["status"] == "paid"][0]
old = dict(paid); old.pop("id"); old.pop("number"); old.pop("version", None)
shift = timedelta(days=730)
old["created_at"] = paid["created_at"] - shift
old["pay_date"] = paid["pay_date"] - shift
old["history"] = [{**h, "at": h["at"] - shift} for h in paid["history"]]
old["handover"] = None
old_id = db.save_request(old)
old_no = db.get_request(old_id)["number"]

login("admin@ftpua.com", ["g-a"])
html = c.get("/admin/requests").get_data(as_text=True)
check("all requests: default 12 months hides old", old_no not in numbers(html) and "12 місяців" in html)
html = c.get("/admin/requests?period=all").get_data(as_text=True)
check("'За весь час' shows old", old_no in numbers(html) and '<input type="hidden" name="period" value="all">' in html)
check("facet submit keeps 'all time'", old_no in numbers(c.get("/admin/requests?period=all&status=paid").get_data(as_text=True)))
d_from = (paid["created_at"] - shift - timedelta(days=1)).date().isoformat()
html = c.get(f"/admin/requests?period=all&from={d_from}").get_data(as_text=True)
check("typed dates win over 'all'", old_no in numbers(html))

# вибраний швидкий період запам'ятовується за користувачем (для кожного списку свій), введені дати — ні
import clock
today = clock.today().isoformat()
def active(html):
    m_ = re.search(r'class="chip active[^"]*"[^>]*>([^<]+)<', html)
    return m_.group(1).strip() if m_ else ""
html = c.get("/admin/requests").get_data(as_text=True)
check("remembered: 'all time' after a new visit", old_no in numbers(html) and active(html) == "За весь час")
c.get("/admin/requests?period=today")
check("remembered: today", active(c.get("/admin/requests").get_data(as_text=True)) == "Сьогодні")
html = c.get(f"/admin/requests?from={d_from}").get_data(as_text=True)
check("typed dates: shown, not remembered", active(html) == "" and old_no in numbers(html)
      and active(c.get("/admin/requests").get_data(as_text=True)) == "Сьогодні")
html = c.get(f"/admin/requests?period=today&from={today}&to={today}&status=paid").get_data(as_text=True)
check("form submit (other filter) keeps the chip", active(html) == "Сьогодні" and 'name="period" value="today"' in html)
html = c.get(f"/admin/requests?period=today&from={d_from}&to={today}").get_data(as_text=True)
check("dates changed in the form -> own period", active(html) == "" and old_no in numbers(html))
c.get("/admin/requests?period=all")

login("res@ftpua.com", ["g-r", "g-f"])
html = c.get("/to-pay?view=paid").get_data(as_text=True)
check("paid list: default 12 months", old_no not in numbers(html) and "За весь час" in html)
check("paid list: all time", old_no in numbers(c.get("/to-pay?view=paid&period=all").get_data(as_text=True)))
check("to-pay (active) keeps 'Усі' default", ">Усі</a>" in c.get("/to-pay").get_data(as_text=True))
c.get("/to-pay?view=paid&period=month")
check("paid list: remembered per user and list", active(c.get("/to-pay?view=paid").get_data(as_text=True)) == "Цей місяць"
      and active(c.get("/to-pay").get_data(as_text=True)) == "Усі")
check("other user's choice is separate (admin chose 'all time')",
      active(c.get("/admin/requests").get_data(as_text=True)) == "12 місяців")
html = c.get("/approvals?view=processed").get_data(as_text=True)
check("processed: default 12 months", old_no not in numbers(html) and "За весь час" in html)
check("processed: all time", old_no in numbers(c.get("/approvals?view=processed&period=all").get_data(as_text=True)))

# кілька вибраних значень фільтра не губляться в посиланнях періоду
login("admin@ftpua.com", ["g-a"])
cps = sorted({r["counterparty_id"] for r in db.list_all()})[:2]
html = c.get(f"/admin/requests?cp={cps[0]}&cp={cps[1]}").get_data(as_text=True)
link = re.search(r'href="([^"]*period=month[^"]*)"', html).group(1).replace("&amp;", "&")
check("period chip keeps both selected values", f"cp={cps[0]}" in link and f"cp={cps[1]}" in link)
print("ALL OK" if ok else "SOME FAILED")
