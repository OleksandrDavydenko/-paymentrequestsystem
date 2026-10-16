"""Сховище заявок у базі (Azure SQL; у тестах — SQLite).

Як працює:
  • Заявка зберігається цілком у JSON (requests.data) + похідними колонками (storage/derive.py).
  • Збереження — одна транзакція: лічильник змін request_rev +1, заявка (UPDATE … WHERE version = очікувана —
    якщо хтось уже змінив заявку, StaleRequestError), історія лише дописується, рядки рахунків, учасники, файли.
  • Номер нової заявки видає база (request_seq) — він унікальний навіть для кількох процесів.
  • Заявки «в роботі» (derive.is_active, їх небагато) кожен процес тримає в пам'яті й оновлює за лічильником
    змін: один крихітний запит на HTTP-запит, далі — лише змінені заявки. Тому черги, «До оплати» й лічильники
    в меню не читають базу щоразу, а зміни з інших процесів видно одразу.
  • Архівні списки (усі заявки, мої, відділ, оплачені, опрацьовані) — запитом: лише легкі колонки за умовою
    списку й періодом; повні заявки — тільки для рядків сторінки (get_many).
"""
import logging
import threading
from copy import deepcopy
from datetime import datetime, time, timedelta

import sqlalchemy as sa

import clock
import db_schema as schema
import db_sql
import workflow as wf
from db_sql import files_t as F
from db_sql import history_t as H
from db_sql import lines_t as L
from db_sql import participants_t as P
from db_sql import requests_t as R
from db_sql import rev_t, seq_t

from . import derive, scopes
from .errors import StaleRequestError

logger = logging.getLogger(__name__)

# Змініть, коли змінюється derive.light / derive.columns: при запуску всі заявки перерахуються
DERIVED_VERSION = "1"

LIGHT_COLUMNS = [
    R.c.id, R.c.number, R.c.status, R.c.author_email, R.c.author_name, R.c.department, R.c.created_at,
    R.c.pay_date, R.c.paid_at, R.c.last_at, R.c.organization_id, R.c.counterparty_id, R.c.expense_code,
    R.c.payment_form, R.c.channel, R.c.accountant_type, R.c.accountant_role, R.c.currency, R.c.amount,
    R.c.payment_account_id, R.c.pay_org, R.c.note, R.c.handover_status, R.c.handover_since,
]
_IN_CHUNK = 500  # SQL Server приймає до 2100 параметрів у запиті

_ready_engine = None
_ready_lock = threading.Lock()


def _engine():
    """Двигун бази зі схемою, доведеною до опису (раз на процес)."""
    global _ready_engine
    eng = db_sql.engine()
    if _ready_engine is not eng:
        with _ready_lock:
            if _ready_engine is not eng:
                with eng.connect() as c:  # схему вже оновив db_sql.engine() (db_schema.upgrade)
                    done = schema.meta_get(c, "derived_version")
                if done != DERIVED_VERSION:
                    n = reindex(eng)
                    with eng.begin() as c:
                        schema.meta_set(c, "derived_version", DERIVED_VERSION)
                    logger.info("Похідні колонки перераховано: %s заявок", n)
                _cache.reset()
                _ready_engine = eng
    return eng


def _load(row):
    req = db_sql.loads(row.data)
    req["version"] = row.version
    return req


# ---------------------------------------------------------------- заявки «в роботі» в пам'яті процесу

class _ActiveCache:
    def __init__(self):
        self.lock = threading.Lock()
        self.reset()

    def reset(self):
        self.rev = None
        self.reqs = {}  # замінюється цілком (копія при зміні), тож читати можна без замка


_cache = _ActiveCache()


def _active():
    """Заявки в роботі {id: заявка}. Не змінювати — для змін get_request повертає копію."""
    try:
        from flask import g, has_request_context
        in_request = has_request_context()
    except ImportError:
        in_request = False
    if in_request and g.get("_storage_fresh"):
        return _cache.reqs
    eng = _engine()
    with _cache.lock:
        with eng.connect() as c:
            current = c.execute(sa.select(rev_t.c.rev).where(rev_t.c.id == 1)).scalar() or 0
            if _cache.rev is None:
                rows = c.execute(sa.select(R.c.id, R.c.data, R.c.version).where(R.c.active == sa.true())).all()
                _cache.reqs = {r.id: _load(r) for r in rows}
            elif current != _cache.rev:
                rows = c.execute(sa.select(R.c.id, R.c.data, R.c.version, R.c.active)
                                 .where(R.c.rev > _cache.rev)).all()
                reqs = dict(_cache.reqs)
                for r in rows:
                    if r.active:
                        reqs[r.id] = _load(r)
                    else:
                        reqs.pop(r.id, None)
                _cache.reqs = reqs
            _cache.rev = current
    if in_request:
        g._storage_fresh = True
    return _cache.reqs


def _cache_put(req):
    with _cache.lock:
        reqs = dict(_cache.reqs)
        if derive.is_active(req):
            reqs[req["id"]] = deepcopy(req)
        else:
            reqs.pop(req["id"], None)
        _cache.reqs = reqs


# ---------------------------------------------------------------- читання

def get_request(request_id):
    act = _active()
    if request_id in act:
        return deepcopy(act[request_id])
    with _engine().connect() as c:
        row = c.execute(sa.select(R.c.id, R.c.data, R.c.version).where(R.c.id == request_id)).first()
    return _load(row) if row else None


def get_many(ids):
    act = _active()
    out = {i: deepcopy(act[i]) for i in ids if i in act}
    rest = [i for i in ids if i not in out]
    if rest:
        with _engine().connect() as c:
            for k in range(0, len(rest), _IN_CHUNK):
                rows = c.execute(sa.select(R.c.id, R.c.data, R.c.version).where(R.c.id.in_(rest[k:k + _IN_CHUNK])))
                out.update({r.id: _load(r) for r in rows})
    return out


def last_id():
    with _engine().connect() as c:
        return c.execute(sa.select(sa.func.max(R.c.id))).scalar() or 0


# ---------------------------------------------------------------- запис

def _offset(conn):
    return int(schema.meta_get(conn, "request_id_offset") or 0)


def _values(req):
    v = derive.columns(req)
    v["amount"] = db_sql._num(v["amount"])
    return v


def _write_children(c, req):
    rid = req["id"]
    # Історія лише дописується: записи, яких ще немає в базі
    have = c.execute(sa.select(sa.func.count()).select_from(H).where(H.c.request_id == rid)).scalar() or 0
    history = req.get("history") or []
    if len(history) < have:  # не має траплятися — історія не скорочується; тримаємо копію узгодженою
        c.execute(H.delete().where(H.c.request_id == rid, H.c.seq >= len(history)))
        have = len(history)
    new = [{"request_id": rid, "seq": i, "at": h.get("at"), "user_name": h.get("user_name"),
            "user_email": h.get("user_email"), "role": h.get("role"), "action": h.get("action"),
            "from_status": h.get("from_status"), "to_status": h.get("to_status"),
            "comment": h.get("comment") or ""} for i, h in enumerate(history) if i >= have]
    if new:
        c.execute(H.insert(), new)
    c.execute(L.delete().where(L.c.request_id == rid))
    lines = [{"request_id": rid, "seq": i, "invoice": ln.get("invoice"), "amount": db_sql._num(ln.get("amount")),
              "currency": ln.get("currency"), "deal": ln.get("deal")} for i, ln in enumerate(req.get("lines") or [])]
    if lines:
        c.execute(L.insert(), lines)
    c.execute(P.delete().where(P.c.request_id == rid))
    emails = sorted({(p.get("email") or "").lower() for p in req.get("participants") or [] if p.get("email")})
    if emails:
        c.execute(P.insert(), [{"request_id": rid, "email": e} for e in emails])
    c.execute(F.delete().where(F.c.request_id == rid))
    files = sorted({a["id"] for a in req.get("attachments") or [] if a.get("id")})
    if files:
        c.execute(F.insert(), [{"file_id": f, "request_id": rid} for f in files])


def save_request(req):
    """Зберегти заявку однією транзакцією. Без 'id' — створити нову (номер видає база). Повертає id;
    у req оновлюється version. Якщо заявку вже змінили після того, як її прочитали, — StaleRequestError."""
    eng = _engine()
    data = deepcopy(req)
    expected = data.get("version") or 0
    data["version"] = expected + 1
    now = clock.now()
    try:
        with eng.begin() as c:
            c.execute(rev_t.update().where(rev_t.c.id == 1).values(rev=rev_t.c.rev + 1))
            rev = c.execute(sa.select(rev_t.c.rev).where(rev_t.c.id == 1)).scalar()
            new = not data.get("id")
            if new:
                seq = c.execute(seq_t.insert().values(created_at=now)).inserted_primary_key[0]
                data["id"] = _offset(c) + seq
                data["number"] = f"{data['id']:09d}"
            values = {**_values(data), "version": data["version"], "rev": rev, "updated_at": now,
                      "data": db_sql.dumps(data)}
            if new:
                c.execute(R.insert().values(id=data["id"], **values))
            elif c.execute(R.update().where(R.c.id == data["id"], R.c.version == expected)
                           .values(**values)).rowcount != 1:
                raise StaleRequestError()
            _write_children(c, data)
    except StaleRequestError:
        raise StaleRequestError(get_request(data["id"]))
    _cache_put(data)
    req["version"] = data["version"]
    return data["id"]


# ---------------------------------------------------------------- списки

def _light(row):
    d = dict(row._mapping)
    d["note"] = d.get("note") or ""
    return d


def _period_where(period):
    if not period:
        return []
    field, d_from, d_to = period
    if field == "pay":
        col, as_dt = R.c.pay_date, False
    elif field == "paid":
        col, as_dt = R.c.paid_at, True
    else:
        col, as_dt = R.c.created_at, True
    out = []
    if d_from:
        out.append(col >= (datetime.combine(d_from, time.min) if as_dt else d_from))
    if d_to:
        out.append(col < datetime.combine(d_to + timedelta(days=1), time.min) if as_dt else col <= d_to)
    return out


def _processed_conditions(roles):
    mine = set(roles & wf.APPROVER_ROLES)
    heads = wf.head_deps(roles)
    role_cond = H.c.role.in_(sorted(mine - {"dept_head"}))
    if "dept_head" in mine and heads:
        role_cond = sa.or_(role_cond, sa.and_(H.c.role == "dept_head", R.c.department.in_(sorted(heads))))
    return [H.c.request_id == R.c.id, H.c.action.in_(sorted(wf.DECISION_ACTIONS)), role_cond]


def _scope_where(scope, roles, email):
    e = (email or "").lower()
    if scope == "all":
        return []
    if scope == "author":
        return [R.c.author_lc == e]
    if scope == "mine":
        return [R.c.author_lc == e, R.c.status != "paid"]
    if scope == "mine_paid":
        return [R.c.author_lc == e, R.c.status == "paid"]
    if scope == "participant":
        return [R.c.id.in_(sa.select(P.c.request_id).where(P.c.email == e))]
    if scope == "department":
        deps = sorted(wf.head_deps(roles) | wf.view_deps(roles))
        return [R.c.department.in_(deps), R.c.status != "draft"] if deps else [sa.false()]
    if scope == "paid":
        if "cfo" in roles:
            return [R.c.status == "paid"]
        acc = sorted(roles & wf.ACCOUNTANT_ROLES)
        return [R.c.status == "paid", R.c.accountant_role.in_(acc)] if acc else [sa.false()]
    if scope == "processed":
        # IN (…), а не EXISTS: SQLite 3.51.0 повертає дублікати для EXISTS з IN-списками по індексу
        decided = lambda *where: sa.select(H.c.request_id).where(H.c.action.in_(sorted(wf.DECISION_ACTIONS)), *where)
        mine = set(roles & wf.APPROVER_ROLES)
        cond = R.c.id.in_(decided(H.c.role.in_(sorted(mine - {"dept_head"}))))
        heads = wf.head_deps(roles)
        if "dept_head" in mine and heads:
            cond = sa.or_(cond, sa.and_(R.c.department.in_(sorted(heads)), R.c.id.in_(decided(H.c.role == "dept_head"))))
        return [cond]
    raise ValueError(f"Невідомий список {scope}")


def scope_rows(scope, roles, email, period=None):
    """Легкі рядки списку в порядку списку. period — (поле, з, по): лише підказка базі, точний
    фільтр за періодом застосовує сторінка."""
    if scope in scopes.ACTIVE:
        return scopes.light_rows(scope, list(_active().values()), roles, email)
    cols = list(LIGHT_COLUMNS)
    where = _scope_where(scope, roles, email)
    if scope == "processed":
        decided_at = sa.select(sa.func.max(H.c.at)).where(*_processed_conditions(roles)).scalar_subquery()
        cols.append(decided_at.label("sort_at"))
        if period and period[0] == "decided" and period[1]:  # рішення, ухвалені з дати
            where.append(decided_at >= datetime.combine(period[1], time.min))
    elif period:
        where += _period_where(period)
    stmt = sa.select(*cols).where(*where)
    with _engine().connect() as c:
        rows = [_light(r) for r in c.execute(stmt)]
    return scopes.sort_rows(scope, rows)


def count(scope, roles, email):
    if scope in scopes.ACTIVE:
        return sum(1 for r in _active().values() if scopes.match(scope, r, roles, email))
    stmt = sa.select(sa.func.count()).select_from(R).where(*_scope_where(scope, roles, email))
    with _engine().connect() as c:
        return c.execute(stmt).scalar() or 0


def find_attachment(file_id):
    with _engine().connect() as c:
        rid = c.execute(sa.select(F.c.request_id).where(F.c.file_id == file_id)).scalar()
    req = get_request(rid) if rid is not None else None
    att = next((a for a in (req or {}).get("attachments", []) if a["id"] == file_id), None)
    return (req, dict(att)) if att else (None, None)


def _ids(*where):
    with _engine().connect() as c:
        return list(c.execute(sa.select(R.c.id).where(*where).order_by(R.c.id)).scalars())


def ids_with_counterparty(cp_id):
    return _ids(R.c.counterparty_id == cp_id)


def ids_with_departments(codes):
    return _ids(R.c.department.in_(sorted(codes))) if codes else []


def last_accountant_type(email):
    stmt = (sa.select(R.c.accountant_type)
            .where(R.c.author_lc == (email or "").lower(), R.c.accountant_type.in_(sorted(wf.ACCOUNTANT_CHOICES)))
            .order_by(R.c.created_at.desc(), R.c.id).limit(1))
    with _engine().connect() as c:
        return c.execute(stmt).scalar()


def first_request_with_counterparty(cp_id):
    stmt = sa.select(R.c.id).where(R.c.counterparty_id == cp_id).order_by(R.c.created_at.desc(), R.c.id).limit(1)
    with _engine().connect() as c:
        return c.execute(stmt).scalar()


def status():
    """Для /health: схема оновлена, кеш заявок у роботі працює (без персональних даних)."""
    try:
        _active()
        with _engine().connect() as c:
            derived = schema.meta_get(c, "derived_version")
        return {"storage": "ok", "active_requests": len(_cache.reqs), "derived_version": derived}
    except Exception as e:
        logger.exception("Сховище заявок у базі недоступне")
        return {"storage": "error", "error": f"storage: {e}"[:500]}


def reindex(eng=None):
    """Перерахувати похідні колонки всіх заявок (після зміни довідників чи налаштувань організацій).
    Дані заявок, версія й лічильник змін не змінюються. Повертає кількість заявок."""
    eng = eng or _engine()
    last, n = 0, 0
    while True:
        with eng.connect() as c:
            rows = c.execute(sa.select(R.c.id, R.c.data, R.c.version).where(R.c.id > last)
                             .order_by(R.c.id).limit(200)).all()
        if not rows:
            return n
        with eng.begin() as c:
            for row in rows:
                try:
                    values = _values(_load(row))
                except Exception:  # пошкоджена заявка не має зупиняти перерахунок решти
                    logger.exception("Не вдалося перерахувати колонки заявки %s", row.id)
                    continue
                c.execute(R.update().where(R.c.id == row.id).values(**values))
                n += 1
        last = rows[-1].id
