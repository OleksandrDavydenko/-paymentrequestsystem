"""Навантажувальна перевірка (вручну, не в CI): N синтетичних заявок у SQLite і час ключових сторінок.

    .venv\\Scripts\\python tests\\load\\measure.py 100000

Заявки за ~3 роки, 60 авторів, 6 відділів; майже всі оплачені або відхилені, кілька сотень — у роботі.
Записуються пачками напряму в таблиці (як storage/sql.save_request, але швидше).
"""
import os
import random
import sys
import tempfile
import time
from copy import deepcopy
from datetime import timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
N = int(sys.argv[1]) if len(sys.argv) > 1 else 100_000
tmp = tempfile.mkdtemp()
os.environ.update(DATABASE_URL="sqlite:///" + os.path.join(tmp, "load.db").replace("\\", "/"), UPLOAD_DIR=tmp,
                  CLIENT_ID="x", TENANT_ID="x", CLIENT_SECRET="x", FLASK_SECRET_KEY="x",
                  REDIRECT_URI="http://localhost/getAToken", GROUP_ADMIN="g-a", GROUP_INITIATOR="g-i",
                  GROUP_ACC_RESIDENT="g-r", GROUP_ACC_CASH="g-c", GROUP_CFO="g-f", ADMIN_EMAILS="",
                  APP_SETTINGS_FILE=os.path.join(tmp, "s.json"), USER_PREFS_FILE=os.path.join(tmp, "p.json"),
                  ORG_STRUCTURE_FILE=os.path.join(tmp, "o.json"), USER_DIRECTORY_FILE=os.path.join(tmp, "d.json"))
from flask_wtf.csrf import CSRFProtect  # noqa: E402
CSRFProtect.protect = lambda self: None

import sqlalchemy as sa  # noqa: E402

import app as m  # noqa: E402
import db_sql  # noqa: E402
import storage.demo as demo  # noqa: E402
import storage.sql as dbs  # noqa: E402
from storage import derive  # noqa: E402

rng = random.Random(42)
authors = [f"user{i:02d}@ftpua.com" for i in range(60)]
deps = ["BA", "LOG", "FIN", "IT", "HR", "SALES"]
templates = sorted(demo._REQUESTS.values(), key=lambda r: r["id"])
closed = [r for r in templates if r["status"] in ("paid", "rejected") and not r.get("handover")]
active = [r for r in templates if r["status"] in derive.ACTIVE_STATUSES]

print(f"Створюю {N} заявок…", flush=True)
t0 = time.time()
eng = dbs._engine()
R, H = db_sql.requests_t, db_sql.history_t
batch_r, batch_h = [], []
for i in range(1, N + 1):
    src = rng.choice(active if rng.random() < 300 / N else closed)
    r = deepcopy(src)
    r["id"], r["number"], r["version"] = i, f"{i:09d}", 1
    r["author_email"] = rng.choice(authors)
    r["author_name"] = r["author_email"].split("@")[0]
    r["department"] = rng.choice(deps)
    shift = timedelta(days=-rng.randint(0, 1100), minutes=rng.randint(0, 1440))
    r["created_at"] += shift
    r["pay_date"] += shift
    for h in r["history"]:
        h["at"] += shift
    cols = derive.columns(r)
    cols["amount"] = db_sql._num(cols["amount"])
    batch_r.append({"id": i, **cols, "version": 1, "rev": 0, "updated_at": r["created_at"], "data": db_sql.dumps(r)})
    batch_h += [{"request_id": i, "seq": k, "at": h["at"], "user_name": h["user_name"], "user_email": h["user_email"],
                 "role": h["role"], "action": h["action"], "from_status": h["from_status"],
                 "to_status": h["to_status"], "comment": h["comment"]} for k, h in enumerate(r["history"])]
    if len(batch_r) >= 2000 or i == N:
        with eng.begin() as c:
            c.execute(R.insert(), batch_r)
            c.execute(H.insert(), batch_h)
        batch_r, batch_h = [], []
with eng.begin() as c:
    c.execute(db_sql.meta_t.update().where(db_sql.meta_t.c.key == "request_id_offset").values(value=str(N)))
    c.execute(sa.text("ANALYZE"))
dbs._cache.reset()
print(f"  {time.time() - t0:.0f} с; у роботі: {sum(1 for r in batch_r)}", flush=True)

claims = {}
m.auth._auth.get_user = lambda: dict(claims)
m.auth._auth.get_token_for_user = lambda s: {"access_token": "t", "scope": " ".join(s)}
import graph  # noqa: E402
graph.group_members = lambda t, g: []
c = m.app.test_client()


def login(email, groups):
    claims.clear()
    claims.update({"name": email, "preferred_username": email, "oid": email, "groups": groups})


PAGES = [
    ("Адмін: Усі заявки", "admin@ftpua.com", ["g-a"], "/admin/requests"),
    ("Адмін: Усі заявки, цей місяць", "admin@ftpua.com", ["g-a"], "/admin/requests?period=month"),
    ("Адмін: пошук за номером", "admin@ftpua.com", ["g-a"], f"/admin/requests?q={N // 2:09d}"),
    ("Адмін: фільтр статус+відділ", "admin@ftpua.com", ["g-a"], "/admin/requests?status=paid&dep=LOG"),
    ("Бухгалтер: На погодження", "acc@ftpua.com", ["g-r"], "/approvals"),
    ("Бухгалтер: Опрацьовані", "acc@ftpua.com", ["g-r"], "/approvals?view=processed"),
    ("Бухгалтер: До оплати", "acc@ftpua.com", ["g-r"], "/to-pay"),
    ("Бухгалтер: Оплачені", "acc@ftpua.com", ["g-r"], "/to-pay?view=paid"),
    ("Бухгалтер: Оплачені, цей місяць", "acc@ftpua.com", ["g-r"], "/to-pay?view=paid&period=month"),
    ("Автор: Мої заявки", authors[0], ["g-i"], "/requests"),
    ("Автор: Оплачені", authors[0], ["g-i"], "/requests?view=paid"),
    ("Картка заявки", "admin@ftpua.com", ["g-a"], f"/requests/{N // 3}"),
]
print(f"\n{'Сторінка':40} {'перший':>8} {'далі':>8}  статус")
for name, email, groups, url in PAGES:
    login(email, groups)
    t = time.time()
    r = c.get(url)
    first = time.time() - t
    times = []
    for _ in range(3):
        t = time.time()
        c.get(url)
        times.append(time.time() - t)
    print(f"{name:40} {first * 1000:7.0f}мс {min(times) * 1000:7.0f}мс  {r.status_code}", flush=True)
