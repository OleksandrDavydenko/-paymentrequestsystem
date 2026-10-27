"""Платежі з 1С (таблиця Power BI «Payments»): копія в PayDesk, синхронізація змін і зіставлення із заявками.

Рядок таблиці — рядок розшифровки документа списання (банк або каса). PayDesk тримає копію (SQL-таблиці payments,
payment_links, payment_issues; у тестовому режимі — у пам'яті), перше завантаження — по місяцях від «Звіряти
платежі з», далі лише змінені документи (RowVersion), раз на добу — перевірка видалених за 90 днів.
Правила зіставлення — payment_match.py; тут — що робити далі: прив'язати платіж і, якщо для бухгалтера заявки
ввімкнено автоматичне «Оплачено» («Налаштування → Звірка з 1С»), позначити заявку оплаченою.
"""
import json
import logging
import os
import threading
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

import sqlalchemy as sa

import app_settings
import clock
import counterparties
import currencies
import db_sql
import employees
import expense_items
import notifications
import payment_match
import pbi_cache
import storage as db
import workflow as wf

logger = logging.getLogger(__name__)

PAGE = 5000                 # рядків за один запит змін (з запасом до ліміту Power BI в 1 млн значень)
KEYS_WINDOW_DAYS = 90       # за скільки днів раз на добу перевіряти, чи не зникли платежі з 1С
MATCH_WINDOW_DAYS = 120     # платежі за скільки днів зіставляються з заявками
SYNC_EVERY = timedelta(minutes=10)
PAY_MODES = ("manual", "auto", "both")
DEFAULT_SINCE = date(2026, 9, 1)


def table():
    return os.environ.get("PBI_PAYMENTS_TABLE") or "Payments"


# ---------------------------------------------------------------- режими «Оплачено» за видом бухгалтера

def pay_mode(role):
    """«manual» — «Оплачено» ставить бухгалтер, «auto» — лише звірка з платежами 1С, «both» — і те, й те."""
    mode = app_settings.get(f"pay_mode_{role}") if role in wf.ACCOUNTANT_ROLES else None
    return mode if mode in PAY_MODES else "manual"


def manual_pay_allowed(req):
    """Чи може бухгалтер сам натиснути «Оплачено» (режим «Автоматично» — ні). req — заявка або легкий рядок списку."""
    return pay_mode(wf.accountant_role(req) if "history" in req else req.get("accountant_role")) != "auto"


def auto_pay(req):
    return pay_mode(wf.accountant_role(req)) in ("auto", "both")


def since():
    """З якої дати звіряти платежі («Налаштування → Звірка з 1С»)."""
    raw = app_settings.get("payments_since")
    try:
        return date.fromisoformat(str(raw)) if raw else DEFAULT_SINCE
    except ValueError:
        return DEFAULT_SINCE


# ---------------------------------------------------------------- джерело: Power BI або демо-платежі

def _live():
    """Робоча база й підключений Power BI — платежі з 1С; інакше (тестовий режим, локально) — демо-платежі."""
    return db.work_mode() and bool(os.environ.get("PBI_DATASET_ID") and os.environ.get("PBI_GROUP_ID"))


def _dax_date(d):
    return f"DATE({d.year}, {d.month}, {d.day})"


def dax(what):
    """Текст DAX-запиту: місяць (перше завантаження), зміни після RowVersion або ключі для перевірки видалених."""
    t = table()
    col = lambda name: f"'{t}'[{name}]"  # noqa: E731
    if what["kind"] == "month":
        return (f"EVALUATE FILTER('{t}', {col('PaymentDate')} >= {_dax_date(what['start'])} && "
                f"{col('PaymentDate')} < {_dax_date(what['end'])})")
    if what["kind"] == "changes":
        return (f"EVALUATE TOPN({PAGE}, FILTER('{t}', {col('RowVersion')} > {int(what['rv'])} && "
                f"{col('PaymentDate')} >= {_dax_date(what['since'])}), {col('RowVersion')}, ASC)")
    return (f"EVALUATE SELECTCOLUMNS(FILTER('{t}', {col('PaymentDate')} >= {_dax_date(what['since'])}), "
            f"\"PaymentGUID\", {col('PaymentGUID')}, \"LineNo\", {col('LineNo')})")


MOCK_ROWS = [dict(r) for r in db.demo.PAYMENTS]  # демо-платежі й тестові сценарії: рядки у форматі Power BI


def _mock(what):
    rows = []
    for r in MOCK_ROWS:
        d = _date_of(r.get("PaymentDate"))
        if what["kind"] == "month" and not (d and what["start"] <= d < what["end"]):
            continue
        if what["kind"] == "changes" and not (int(r.get("RowVersion") or 0) > what["rv"] and d and d >= what["since"]):
            continue
        if what["kind"] == "keys" and not (d and d >= what["since"]):
            continue
        rows.append(dict(r))
    if what["kind"] == "changes":
        rows.sort(key=lambda r: int(r.get("RowVersion") or 0))
    return rows


def _fetch(what):
    return expense_items.query_dax(dax(what)) if _live() else _mock(what)


# ---------------------------------------------------------------- рядок Power BI → рядок копії

def _guid(value):
    value = str(value or "").strip()
    return value if value.strip("0") and value.upper() not in ("NONE", "NULL") else None


def _dec(value):
    try:
        return Decimal(str(value)).quantize(Decimal("0.01")) if value not in (None, "") else None
    except InvalidOperation:
        return None


def _date_of(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


def _currency(r):
    code = currencies.code_by_guid(r.get("CurrencyGUID")) if r.get("CurrencyGUID") else None
    raw = str(r.get("Currency") or r.get("CurrencyGUID") or "").strip().upper()
    return code or (raw if len(raw) == 3 else None)


def parse(r):
    guid = _guid(r.get("PaymentGUID"))
    if not guid:
        return None
    return {"payment_guid": guid.upper(), "line_no": int(r.get("LineNo") or 1),
            "kind": str(r.get("DocumentKind") or "Bank").strip() or "Bank",
            "doc_number": str(r.get("DocumentNumber") or "").strip()[:50], "pay_date": _date_of(r.get("PaymentDate")),
            "payment_type": _guid(r.get("PaymentTypeGUID")), "counterparty": _guid(r.get("CounterpartyGUID")),
            "amount": _dec(r.get("Amount")), "currency": _currency(r),
            "item_code": str(r.get("ExpenseItemCode") or "").strip() or None, "department": _guid(r.get("DepartmentGUID")),
            "purpose": str(r.get("Purpose") or "").strip(),
            "posted": pbi_cache.truthy(r.get("IsPosted")) if r.get("IsPosted") is not None else True,
            "deleted": pbi_cache.truthy(r.get("IsDeleted")),
            "operation": str(r.get("OperationKind") or "").strip() or None, "recipient": _guid(r.get("RecipientEmployeeGUID")),
            "row_version": int(r.get("RowVersion") or 0), "invoice": _guid(r.get("InvoiceGUID"))}


# ---------------------------------------------------------------- сховище копії

class _MemoryStore:
    """Тестовий режим і локальний запуск без бази."""

    def __init__(self):
        self.lines, self.links, self.issues, self.state = {}, {}, {}, {}

    def replace_docs(self, lines):
        guids = {ln["payment_guid"] for ln in lines}
        for key in [k for k in self.lines if k[0] in guids]:
            del self.lines[key]
        now = clock.now()
        for ln in lines:
            self.lines[(ln["payment_guid"], ln["line_no"])] = {**ln, "removed": False, "synced_at": now}

    def keys_since(self, d):
        return {k for k, ln in self.lines.items() if ln["pay_date"] and ln["pay_date"] >= d and not ln["removed"]}

    def mark_removed(self, keys):
        for k in keys:
            self.lines[k]["removed"] = True

    def lines_since(self, d):
        return [dict(ln) for ln in self.lines.values() if ln["pay_date"] and ln["pay_date"] >= d]

    def lines_of(self, guids):
        return [dict(ln) for k, ln in self.lines.items() if k[0] in guids]

    def all_links(self):
        return {g: dict(v) for g, v in self.links.items()}

    def add_link(self, guid, request_id, match, by):
        self.links.setdefault(guid, {})[request_id] = {"match": match, "at": clock.now(), "by_name": by}

    def remove_link(self, guid, request_id):
        self.links.get(guid, {}).pop(request_id, None)
        if not self.links.get(guid):
            self.links.pop(guid, None)

    def replace_issues(self, issues):
        now = clock.now()
        self.issues = {g: {"issue": i, "detail": d, "at": now} for g, (i, d) in issues.items()}

    def all_issues(self):
        return dict(self.issues)

    def count(self):
        return sum(1 for ln in self.lines.values() if not ln["removed"])

    def get_state(self):
        return dict(self.state)

    def set_state(self, state):
        self.state = dict(state)


class _SqlStore:
    """Робочий режим: таблиці payments, payment_links, payment_issues; стан — у schema_meta."""
    P, LK, IS = db_sql.payments_t, db_sql.payment_links_t, db_sql.payment_issues_t
    COLS = [c.name for c in db_sql.payments_t.columns]

    def _eng(self):
        return db_sql.engine()

    @staticmethod
    def _chunks(items, n=500):
        items = list(items)
        for i in range(0, len(items), n):
            yield items[i:i + n]

    def replace_docs(self, lines):
        if not lines:
            return
        now = clock.now()
        rows = [{**ln, "removed": False, "synced_at": now} for ln in lines]
        with self._eng().begin() as c:
            for part in self._chunks({ln["payment_guid"] for ln in lines}):
                c.execute(self.P.delete().where(self.P.c.payment_guid.in_(part)))
            for part in self._chunks(rows):
                c.execute(self.P.insert(), part)

    def keys_since(self, d):
        with self._eng().connect() as c:
            return {(r[0], r[1]) for r in c.execute(sa.select(self.P.c.payment_guid, self.P.c.line_no)
                                                    .where(self.P.c.pay_date >= d, self.P.c.removed == sa.false()))}

    def mark_removed(self, keys):
        by_guid = {}
        for g, n in keys:
            by_guid.setdefault(g, []).append(n)
        with self._eng().begin() as c:
            for g, nums in by_guid.items():
                c.execute(self.P.update().where(self.P.c.payment_guid == g, self.P.c.line_no.in_(nums)).values(removed=True))

    def _select(self, where):
        with self._eng().connect() as c:
            return [dict(r._mapping) for r in c.execute(sa.select(self.P).where(where))]

    def lines_since(self, d):
        return self._select(self.P.c.pay_date >= d)

    def lines_of(self, guids):
        out = []
        for part in self._chunks(guids):
            out += self._select(self.P.c.payment_guid.in_(part))
        return out

    def all_links(self):
        out = {}
        with self._eng().connect() as c:
            for r in c.execute(sa.select(self.LK)):
                out.setdefault(r.payment_guid, {})[r.request_id] = {"match": r.match, "at": r.at, "by_name": r.by_name}
        return out

    def add_link(self, guid, request_id, match, by):
        with self._eng().begin() as c:
            c.execute(self.LK.delete().where(self.LK.c.payment_guid == guid, self.LK.c.request_id == request_id))
            c.execute(self.LK.insert().values(payment_guid=guid, request_id=request_id, match=match, at=clock.now(),
                                              by_name=(by or "")[:200]))

    def remove_link(self, guid, request_id):
        with self._eng().begin() as c:
            c.execute(self.LK.delete().where(self.LK.c.payment_guid == guid, self.LK.c.request_id == request_id))

    def replace_issues(self, issues):
        now = clock.now()
        with self._eng().begin() as c:
            c.execute(self.IS.delete())
            rows = [{"payment_guid": g, "issue": i, "detail": d, "at": now} for g, (i, d) in issues.items()]
            for part in self._chunks(rows):
                c.execute(self.IS.insert(), part)

    def all_issues(self):
        with self._eng().connect() as c:
            return {r.payment_guid: {"issue": r.issue, "detail": r.detail, "at": r.at} for r in c.execute(sa.select(self.IS))}

    def count(self):
        with self._eng().connect() as c:
            return c.execute(sa.select(sa.func.count()).select_from(self.P).where(self.P.c.removed == sa.false())).scalar()

    def get_state(self):
        import db_schema
        with self._eng().connect() as c:
            raw = db_schema.meta_get(c, "payments_state")
        try:
            return json.loads(raw) if raw else {}
        except ValueError:
            return {}

    def set_state(self, state):
        import db_schema
        with self._eng().begin() as c:
            text = json.dumps(state, ensure_ascii=False)
            if len(text) > 1000:  # колонка schema_meta — до 1000 символів
                text = json.dumps({**state, "error": (state.get("error") or "")[:150], "paid_last": []}, ensure_ascii=False)
            db_schema.meta_set(c, "payments_state", text)


_memory = _MemoryStore()
_sql = _SqlStore()


def store():
    return _sql if db.work_mode() else _memory


# ---------------------------------------------------------------- документи з рядків

def documents(lines):
    """{GUID документа: документ} — рядки розшифровки зібрано в документ (сума, контрагенти, статті…)."""
    docs = {}
    for ln in sorted(lines, key=lambda x: (x["payment_guid"], x["line_no"])):
        d = docs.setdefault(ln["payment_guid"], {
            "guid": ln["payment_guid"], "kind": ln["kind"], "number": ln["doc_number"], "date": ln["pay_date"],
            "payment_type": ln["payment_type"], "currency": ln["currency"], "purpose": ln["purpose"] or "",
            "recipient": ln["recipient"], "operation": ln["operation"], "posted": ln["posted"], "deleted": ln["deleted"],
            "amount": Decimal(0), "counterparties": [], "items": [], "departments": [], "invoices": [], "lines": 0,
            "removed_lines": 0})
        if ln.get("removed"):
            d["removed_lines"] += 1
            continue
        d["lines"] += 1
        d["amount"] += Decimal(ln["amount"] or 0)
        for key, value in (("counterparties", ln["counterparty"]), ("items", ln["item_code"]),
                           ("departments", ln["department"]), ("invoices", ln["invoice"])):
            if value and value not in d[key]:
                d[key].append(value)
    for d in docs.values():
        d["amount"] = d["amount"].quantize(Decimal("0.01"))
        d["active"] = bool(d["posted"] and not d["deleted"] and d["lines"])
    return docs


def label(doc):
    """«банк, № 25816106 від 17.10.2026, 12 450,00 UAH» — для історії заявки й листів."""
    kind = {"Cash": "каса", "Bank": "банк"}.get(doc["kind"], "платіж")
    amount = f"{doc['amount']:,.2f}".replace(",", " ").replace(".", ",")
    when = doc["date"].strftime("%d.%m.%Y") if doc.get("date") else ""
    return f"{kind}, № {doc['number']} від {when}, {amount} {doc['currency'] or ''}".strip()


# ---------------------------------------------------------------- зіставлення й наслідки

def _context():
    cache_cp, emails = {}, None

    def edrpou(cp_id):
        if cp_id not in cache_cp:
            c = counterparties.get(cp_id)
            cache_cp[cp_id] = (c or {}).get("edrpou") or None
        return cache_cp[cp_id]

    def employee_email(guid):
        nonlocal emails
        if emails is None:
            emails = {(e.get("guid") or "").upper(): e.get("email") for e in employees.all_employees()}
        return emails.get((guid or "").upper())

    def submitted(req):
        first = next((h for h in req.get("history", []) if h.get("action") == "submit"), None)
        return first["at"].date() if first and first.get("at") else None

    def paid_on(req):
        e = wf.paid_entry(req) if req.get("status") == "paid" else None
        return e["at"].date() if e and e.get("at") else None

    return {"edrpou": edrpou, "employee_email": employee_email, "pending": counterparties.is_pending,
            "submitted": submitted, "paid_on": paid_on}


def _pay(req_id, doc, user=None):
    """Позначити заявку оплаченою за платежем (кілька спроб, якщо заявку саме змінюють). Повертає заявку або None."""
    for _ in range(3):
        req = db.get_request(req_id)
        if not req or req["status"] != "to_pay":
            return None
        try:
            wf.mark_paid_by_payment(req, label(doc), doc.get("payment_type"), bool(app_settings.get("cash_handover_confirm")),
                                    user=user)
            db.save_request(req)
        except db.StaleRequestError:
            continue
        except wf.WorkflowError:
            return None
        notifications.notify("pay", req, user or wf.PAYMENT_USER)
        return req
    return None


def run_matching():
    """Зіставити неприв'язані платежі з заявками; для «авто»/«обидва» — «Оплачено». Повертає підсумок."""
    s, today, start = store(), clock.today(), since()
    window = max(start, today - timedelta(days=MATCH_WINDOW_DAYS))
    docs = documents(s.lines_since(window))
    links = s.all_links()
    taken = {rid for v in links.values() for rid in v}
    candidate_ids = db.payment_candidate_ids(start - timedelta(days=10))
    cands = {i: r for i, r in db.get_many([i for i in candidate_ids if i not in taken]).items()}
    paid = []
    # Платіж уже прив'язали (напр. у режимі «Вручну»), а тепер для бухгалтера заявки — «Автоматично» / «Обидва»
    active_links = {rid: g for g, v in links.items() if docs.get(g, {}).get("active") for rid in v}
    for rid, req in db.get_many([i for i in candidate_ids if i in active_links]).items():
        if req["status"] == "to_pay" and auto_pay(req) and not counterparties.is_pending(req.get("counterparty_id")):
            if _pay(rid, docs[active_links[rid]]):
                paid.append(req["number"])
    wanted = {n for d in docs.values() if d["active"] and d["guid"] not in links for n in payment_match.numbers_in(d["purpose"])}
    found = db.ids_by_numbers(wanted)
    full = db.get_many([i for i in found.values() if i not in cands])
    by_number = {n: cands.get(i) or full.get(i) for n, i in found.items() if (cands.get(i) or full.get(i))}
    ctx = _context()
    issues, linked = {}, 0
    for doc in sorted(docs.values(), key=lambda d: (d["date"] or date.min, d["guid"])):
        if doc["guid"] in links:
            if not doc["active"]:
                issues[doc["guid"]] = ("removed", "")
            continue
        if not doc["active"]:
            continue
        res = payment_match.match(doc, cands, by_number, ctx)
        if res.get("issue"):
            issues[doc["guid"]] = (res["issue"], res.get("detail") or "")
            continue
        if not res["requests"]:
            continue
        linked += 1
        for rid in res["requests"]:
            s.add_link(doc["guid"], rid, res["match"], wf.PAYMENT_USER["name"])
            req = cands.pop(rid)
            if req["status"] == "to_pay" and auto_pay(req) and not counterparties.is_pending(req.get("counterparty_id")):
                if _pay(rid, doc):
                    paid.append(req["number"])
    s.replace_issues(issues)
    return {"linked": linked, "paid": paid, "issues": len(issues)}


# ---------------------------------------------------------------- синхронізація

_lock = threading.Lock()


def _months(start, end):
    first = date(start.year, start.month, 1)
    while first <= end:
        nxt = date(first.year + (first.month == 12), first.month % 12 + 1, 1)
        yield max(first, start), nxt
        first = nxt


def sync(check_keys=False):
    """Забрати з 1С нові й змінені платежі, (раз на добу) прибрати зниклі, зіставити з заявками.
    Повертає стан синхронізації (для адмінки); помилка Power BI записується в стан, а не кидається."""
    if not _lock.acquire(blocking=False):
        return {**status(), "busy": True}
    s, now, start = store(), clock.now(), since()
    state = s.get_state()
    try:
        if state.get("since") != start.isoformat():
            state = {"since": start.isoformat()}  # змінили дату початку — перезавантажити (прив'язки лишаються)
        loaded = 0
        if not state.get("loaded"):
            for a, b in _months(start, now.date()):
                lines = [ln for ln in (parse(r) for r in _fetch({"kind": "month", "start": a, "end": b})) if ln]
                s.replace_docs(lines)
                loaded += len(lines)
                state["rv"] = max([state.get("rv") or 0] + [ln["row_version"] for ln in lines])
            state["loaded"] = True
        else:
            while True:
                rows = _fetch({"kind": "changes", "rv": state.get("rv") or 0, "since": start})
                lines = [ln for ln in (parse(r) for r in rows) if ln]
                s.replace_docs(lines)
                loaded += len(lines)
                state["rv"] = max([state.get("rv") or 0] + [ln["row_version"] for ln in lines])
                if len(rows) < PAGE:
                    break
        last_keys = state.get("keys_at")
        if check_keys or not last_keys or now - datetime.fromisoformat(last_keys) > timedelta(hours=23):
            window = max(start, now.date() - timedelta(days=KEYS_WINDOW_DAYS))
            present = {(str(r.get("PaymentGUID") or "").strip().upper(), int(r.get("LineNo") or 1))
                       for r in _fetch({"kind": "keys", "since": window})}
            gone = s.keys_since(window) - present
            s.mark_removed(gone)
            state.update(keys_at=now.isoformat(), removed_last=len(gone))
        result = run_matching()
        state.update(synced_at=now.isoformat(), error=None, loaded_last=loaded, rows=s.count(),
                     linked_last=result["linked"], paid_last=result["paid"][-20:], issues=result["issues"])
    except Exception as e:  # Power BI недоступний, немає таблиці тощо — показати в адмінці, спробувати пізніше
        logger.exception("Синхронізація платежів з 1С")
        state.update(error=str(e)[:400], failed_at=now.isoformat())
    finally:
        try:
            s.set_state(state)
        finally:
            _lock.release()
    return state


def status():
    """Стан для адмінки: коли синхронізовано, скільки рядків, остання помилка."""
    try:
        return {**store().get_state(), "table": table(), "live": _live()}
    except Exception as e:
        return {"error": str(e)[:400], "table": table(), "live": _live()}


def sync_due():
    """Чи пора синхронізувати (минуло SYNC_EVERY з останньої спроби) — для фону й відкриття сторінок."""
    st = status()
    last = st.get("synced_at") or st.get("failed_at")
    try:
        return not last or clock.now() - datetime.fromisoformat(last) >= SYNC_EVERY
    except ValueError:
        return True


def links_of_request(request_id):
    """Платежі, прив'язані до заявки: [{"guid", "match", "by_name", "at", документ…}] — для картки заявки."""
    s = store()
    guids = [g for g, v in s.all_links().items() if request_id in v]
    if not guids:
        return []
    docs = documents(s.lines_of(guids))
    links = s.all_links()
    return [{**docs[g], **links[g][request_id]} for g in guids if g in docs]
