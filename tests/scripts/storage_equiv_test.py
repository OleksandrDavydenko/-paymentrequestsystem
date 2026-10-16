"""Сховище в базі = сховище в пам'яті: ті самі заявки дають ті самі списки (склад, порядок, легкі рядки,
кількості) для всіх списків і багатьох комбінацій ролей, відділів і користувачів."""
import itertools, os, random, sys, tempfile
from copy import deepcopy
from datetime import date, timedelta
sys.path.insert(0, os.getcwd())
tmp = tempfile.mkdtemp()
os.environ.update(UPLOAD_DIR=tmp, DATABASE_URL="sqlite:///" + os.path.join(tmp, "eq.db").replace("\\", "/"))
import storage
import storage.memory as mem
import storage.sql as dbs
from storage import scopes
from workflow import RoleSet

ok = True
def check(label, cond):
    global ok
    ok &= bool(cond); print(("PASS " if cond else "FAIL ") + label)

# ---------- набір заявок: демо + варіації (інші автори, відділи, форми оплати, учасники)
rng = random.Random(7)
people = ["od@ftpua.com", "i.petrenko@ftpua.com", "O.Bondar@ftpua.com", "a.melnyk@ftpua.com", "x@ftpua.com"]
deps = ["BA", "LOG", "FIN", None]
base = [deepcopy(r) for r in sorted(storage._REQUESTS.values(), key=lambda r: r["id"])]
variants = []
for i in range(120):
    r = deepcopy(rng.choice(base))
    r["author_email"] = rng.choice(people)
    r["department"] = rng.choice(deps)
    if rng.random() < 0.3:
        r["payment_form"] = "cash" if r["payment_form"] == "bank" else "bank"
    if rng.random() < 0.3:
        r["participants"] = [{"email": rng.choice(people), "name": "P", "added_by": "x", "added_at": r["created_at"]}]
    shift = timedelta(days=rng.randint(-40, 40), minutes=i)
    r["created_at"] += shift
    for h in r["history"]:
        h["at"] += shift
    variants.append(r)
dataset = base + variants

mem._REQUESTS.clear()
id_map = {}  # id у пам'яті -> id у базі
for n, r in enumerate(dataset, start=1):
    m = deepcopy(r); m["id"] = n; m["number"] = f"{n:09d}"; m.pop("version", None)
    mem._REQUESTS[n] = m
    s = deepcopy(m); s.pop("id"); s.pop("number")
    id_map[n] = dbs.save_request(s)
check("dataset saved to db", dbs.last_id() == len(dataset) and len(id_map) == len(dataset))

# ---------- комбінації ролей
ROLE_SETS = [set(c) for k in (1, 2) for c in itertools.combinations(
    ["initiator", "acc_cash", "acc_resident", "acc_nonresident", "cfo", "dept_head", "admin"], k)]
DEP_SETS = [((), ()), (("BA",), ()), ((), ("LOG",)), (("BA", "FIN"), ("LOG",))]
users = [""] + people + ["nobody@ftpua.com"]

def ids(rows):
    return [r["id"] for r in rows]

mismatch = []
compared = 0
FIELDS = [k for k in storage.derive.light(mem._REQUESTS[1]) if k not in ("id", "number")]
for scope in sorted(scopes.SCOPES):
    for roles_, (heads, views), email in itertools.product(ROLE_SETS, DEP_SETS, users):
        if heads and "dept_head" not in roles_:
            continue
        roles = RoleSet(roles_, head_departments=heads, view_departments=views)
        a = mem.scope_rows(scope, roles, email)
        b = dbs.scope_rows(scope, roles, email)
        compared += 1
        if [id_map[i] for i in ids(a)] != ids(b):
            mismatch.append((scope, sorted(roles_), heads, views, email, "ids"))
            continue
        for x, y in zip(a, b):
            for f in FIELDS + (["sort_at"] if scope == "processed" else []):
                if x.get(f) != y.get(f):
                    mismatch.append((scope, sorted(roles_), email, f, x.get(f), y.get(f)))
                    break
        if mem.count(scope, roles, email) != dbs.count(scope, roles, email):
            mismatch.append((scope, sorted(roles_), email, "count"))
check(f"all lists identical ({compared} combinations)", not mismatch)
for x in mismatch[:10]:
    print("   ", x)

# ---------- підказка періоду для бази не змінює результат після точного фільтра
d_from, d_to = date(2026, 9, 15), date(2026, 10, 5)
period_ok = True
for field, key in (("created", "created_at"), ("pay", "pay_date"), ("paid", "paid_at")):
    for scope in ("all", "paid", "author"):
        roles = RoleSet({"cfo", "admin"})
        val = lambda r: (r[key].date() if hasattr(r[key], "date") and key != "pay_date" else r[key]) if r[key] else None
        exact = [r["id"] for r in dbs.scope_rows(scope, roles, "od@ftpua.com")
                 if val(r) and d_from <= val(r) <= d_to]
        hinted = [r["id"] for r in dbs.scope_rows(scope, roles, "od@ftpua.com", period=(field, d_from, d_to))
                  if val(r) and d_from <= val(r) <= d_to]
        period_ok &= exact == hinted and set(hinted) <= {r["id"] for r in dbs.scope_rows(scope, roles, "od@ftpua.com")}
check("period hint in SQL is exact", period_ok)

# «Опрацьовані» за датою рішення
decided_ok = True
for roles_ in ({"acc_resident"}, {"cfo"}, {"acc_cash", "dept_head"}):
    roles = RoleSet(roles_, head_departments=("BA",))
    want = [id_map[r["id"]] for r in mem.scope_rows("processed", roles, "") if r["sort_at"].date() >= d_from]
    got = [r["id"] for r in dbs.scope_rows("processed", roles, "", period=("decided", d_from, None))]
    decided_ok &= want == got
check("processed: decision-date window in SQL is exact", decided_ok)

# ---------- reindex не змінює результат, а після зміни похідних колонок — відновлює
import sqlalchemy as sa, db_sql
with db_sql.engine().begin() as c:
    c.execute(db_sql.requests_t.update().values(channel=None, accountant_role=None, pay_org=None))
roles = RoleSet({"acc_resident"})
broken = dbs.scope_rows("paid", roles, "")
dbs.reindex()
fixed = dbs.scope_rows("paid", roles, "")
check("reindex restores derived columns", [id_map[i] for i in ids(mem.scope_rows("paid", roles, ""))] == ids(fixed)
      and ids(broken) != ids(fixed))
print("ALL OK" if ok else "SOME FAILED")
