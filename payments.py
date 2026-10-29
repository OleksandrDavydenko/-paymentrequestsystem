"""Платежі з 1С (таблиця Power BI «Payments»): копія в PayDesk, синхронізація змін і зіставлення із заявками.

Рядок таблиці — рядок розшифровки документа списання (банк або каса). Сума для звірки — сума документа (колонка
DocumentAmount — шапка): рядки «Счета» можуть бути меншими (решта лишається авансом); без цієї колонки — сума рядків.
Коли колонка з'являється, суми документів дописуються в наявну копію одним легким запитом (без перезавантаження). PayDesk тримає копію (SQL-таблиці payments,
payment_links, payment_issues; у тестовому режимі — у пам'яті), перше завантаження — по місяцях від «Звіряти
платежі з», далі лише змінені документи (RowVersion) і перевірка зниклих (видалених зовсім) за 14 днів, раз на добу — за 90.
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
import departments
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
KEYS_RECENT_DAYS = 14       # а за скільки — при кожній синхронізації (платіжку видалили зовсім — зникає з Power BI)
MATCH_WINDOW_DAYS = 120     # платежі за скільки днів зіставляються з заявками
REPORT_DAYS = 30            # звіт «Звірка» за замовчуванням і лічильник у меню — за останні 30 днів
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


def sync_minutes():
    """Як часто синхронізувати з 1С, хвилин («Налаштування → Звірка з 1С»)."""
    return int(app_settings.get("payments_sync_minutes") or 10)


def sync_every():
    return timedelta(minutes=sync_minutes())


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
    if what["kind"] == "probe":  # один рядок — які колонки є в таблиці
        return f"EVALUATE TOPN(1, '{t}')"
    if what["kind"] == "doc_amounts":  # сума документа (шапка) для кожного документа — дописати в наявну копію
        return (f"EVALUATE DISTINCT(SELECTCOLUMNS(FILTER('{t}', {col('PaymentDate')} >= {_dax_date(what['since'])}), "
                f"\"PaymentGUID\", {col('PaymentGUID')}, \"DocumentAmount\", {col('DocumentAmount')}))")
    return (f"EVALUATE SELECTCOLUMNS(FILTER('{t}', {col('PaymentDate')} >= {_dax_date(what['since'])}), "
            f"\"PaymentGUID\", {col('PaymentGUID')}, \"LineNo\", {col('LineNo')})")


MOCK_ROWS = [dict(r) for r in db.demo.PAYMENTS]  # демо-платежі й тестові сценарії: рядки у форматі Power BI


def _mock(what):
    if what["kind"] == "probe":
        return [dict(r) for r in MOCK_ROWS[:1]]
    if what["kind"] == "doc_amounts":
        return [{"PaymentGUID": r.get("PaymentGUID"), "DocumentAmount": r.get("DocumentAmount")} for r in MOCK_ROWS
                if (_date_of(r.get("PaymentDate")) or what["since"]) >= what["since"]]
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
    return (code or (raw if len(raw) == 3 else None) or "")[:10] or None


def parse(r):
    guid = _guid(r.get("PaymentGUID"))
    if not guid:
        return None
    return {"payment_guid": guid.upper(), "line_no": int(r.get("LineNo") or 1),
            "kind": str(r.get("DocumentKind") or "Bank").strip() or "Bank",
            "doc_number": str(r.get("DocumentNumber") or "").strip()[:50], "pay_date": _date_of(r.get("PaymentDate")),
            "payment_type": _guid(r.get("PaymentTypeGUID")), "counterparty": _guid(r.get("CounterpartyGUID")),
            "amount": _dec(r.get("Amount")), "doc_amount": _dec(r.get("DocumentAmount")), "currency": _currency(r),
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

    def set_doc_amounts(self, amounts):
        for (g, _), ln in self.lines.items():
            if g in amounts:
                ln["doc_amount"] = amounts[g]

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

    def operations(self):
        return {ln["operation"] for ln in self.lines.values() if ln.get("operation")}

    def payment_types(self):
        return {ln["payment_type"] for ln in self.lines.values() if ln.get("payment_type")}

    def links_for_requests(self, ids):
        ids = set(ids)
        out = {}
        for g, v in self.links.items():
            for rid in v:
                if rid in ids:
                    out.setdefault(rid, []).append(g)
        return out

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
        """Документи замінюються порціями по 200 — короткими транзакціями, щоб сторінки, які читають платежі, не
        чекали на весь місяць завантаження."""
        if not lines:
            return
        now = clock.now()
        by_guid = {}
        for ln in lines:
            by_guid.setdefault(ln["payment_guid"], []).append({**ln, "removed": False, "synced_at": now})
        for part in self._chunks(by_guid, 200):
            with self._eng().begin() as c:
                c.execute(self.P.delete().where(self.P.c.payment_guid.in_(part)))
                c.execute(self.P.insert(), [row for g in part for row in by_guid[g]])

    def set_doc_amounts(self, amounts):
        """Дописати суму документа в наявні рядки копії — порціями по 200 документів."""
        for part in self._chunks(amounts, 200):
            with self._eng().begin() as c:
                c.execute(self.P.update().where(self.P.c.payment_guid.in_(part))
                          .values(doc_amount=sa.case({g: amounts[g] for g in part}, value=self.P.c.payment_guid)))

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

    def operations(self):
        with self._eng().connect() as c:
            return {o for o in c.execute(sa.select(self.P.c.operation).distinct()).scalars() if o}

    def payment_types(self):
        with self._eng().connect() as c:
            return {o for o in c.execute(sa.select(self.P.c.payment_type).distinct()).scalars() if o}

    def links_for_requests(self, ids):
        out = {}
        with self._eng().connect() as c:
            for part in self._chunks(sorted(set(ids))):
                for r in c.execute(sa.select(self.LK.c.request_id, self.LK.c.payment_guid).where(self.LK.c.request_id.in_(part))):
                    out.setdefault(r.request_id, []).append(r.payment_guid)
        return out

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
                text = json.dumps({**state, "error": (state.get("error") or "")[:150], "paid_last": [], "unpaid_last": []},
                                  ensure_ascii=False)
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
            "amount": Decimal(0), "doc_amount": None, "counterparties": [], "items": [], "departments": [], "invoices": [],
            "lines": 0, "removed_lines": 0})
        if ln.get("doc_amount") is not None and d["doc_amount"] is None:
            d["doc_amount"] = Decimal(ln["doc_amount"])
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
        d["lines_amount"] = d["amount"].quantize(Decimal("0.01"))  # сума рядків «Счета» (менша — решта авансом)
        d["amount"] = d["doc_amount"].quantize(Decimal("0.01")) if d["doc_amount"] is not None else d["lines_amount"]
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


def _unpay(req_id, doc, revert):
    """Платіж заявки видалили або розпровели в 1С (кілька спроб, якщо заявку саме змінюють): revert — оплачена ним
    заявка знову «До оплати»; відмітка обробки 1С про цей документ знімається (обробка знову запропонує платіжку).
    Повертає заявку, якщо її повернуто в «До оплати»."""
    import onec_api
    for _ in range(3):
        req = db.get_request(req_id)
        if not req:
            return None
        o = req.get("onec_order") or {}
        if o.get("state") == "created" and o.get("doc_ref") == onec_api.ref_1c(doc["guid"]):
            req.pop("onec_order")
        back = revert and req["status"] == "paid"
        if back:
            wf.revert_paid_by_payment(req, label(doc))
        else:
            wf.add_history(req, "payment_removed", wf.PAYMENT_USER, wf.accountant_role(req) or "acc_resident",
                           f"Платіж 1С ({label(doc)}) видалено або розпроведено — зв'язок із заявкою прибрано",
                           req["status"], req["status"])
        try:
            db.save_request(req)
        except db.StaleRequestError:
            continue
        return req if back else None
    return None


def _drop_removed(s, docs, links):
    """Прив'язаний платіж став неактивним (видалено, розпроведено, зник з 1С): зв'язок зникає (і з links), заявка без
    інших активних платежів — знову «До оплати». Повертає номери заявок, повернутих у «До оплати»."""
    back, by_req = [], {}
    for g, v in links.items():
        for rid in v:
            by_req.setdefault(rid, set()).add(g)
    for g in [g for g in links if g in docs and not docs[g]["active"]]:
        for rid in list(links.pop(g)):
            s.remove_link(g, rid)
            by_req[rid].discard(g)
            still = any(docs.get(x, {"active": True})["active"] for x in by_req[rid])
            if req := _unpay(rid, docs[g], not still):
                back.append(req["number"])
    return back


def _drop_removed_orders(docs, cands):
    """Заявку оплатила обробка 1С (проведена платіжка), а документ видалили чи розпровели ще до прив'язки: заявка
    знову «До оплати» (оновлюється в cands). Повертає номери повернутих заявок."""
    import onec_api
    removed = {ref: d for d in docs.values() if not d["active"] and (ref := onec_api.ref_1c(d["guid"]))}
    back = []
    for rid, req in list(cands.items()):
        o = req.get("onec_order") or {}
        if req["status"] == "paid" and o.get("state") == "created" and o.get("doc_ref") in removed:
            if r := _unpay(rid, removed[o["doc_ref"]], True):
                cands[rid] = r
                back.append(r["number"])
    return back


def run_matching():
    """Зіставити неприв'язані платежі з заявками; для «авто»/«обидва» — «Оплачено». Повертає підсумок."""
    s, today, start = store(), clock.today(), since()
    window = max(start, today - timedelta(days=MATCH_WINDOW_DAYS))
    docs = documents(s.lines_since(window))
    links = s.all_links()
    unpaid = _drop_removed(s, docs, links)
    taken = {rid for v in links.values() for rid in v}
    candidate_ids = db.payment_candidate_ids(start - timedelta(days=10))
    cands = {i: r for i, r in db.get_many([i for i in candidate_ids if i not in taken]).items()}
    unpaid += _drop_removed_orders(docs, cands)
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
        if doc["guid"] in links or not doc["active"]:
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
    links, ok, item_deps = s.all_links(), exclusions(), item_departments()
    recent = today - timedelta(days=REPORT_DAYS)
    unlinked = sum(1 for d in docs.values() if d["active"] and d["date"] and d["date"] >= recent and d["guid"] not in links
                   and d["guid"] not in issues and not allowed_reason(d, ok, item_deps))
    return {"linked": linked, "paid": paid, "unpaid": unpaid, "issues": len(issues), "unlinked": unlinked}


# ---------------------------------------------------------------- синхронізація

_lock = threading.Lock()


def _months(start, end):
    first = date(start.year, start.month, 1)
    while first <= end:
        nxt = date(first.year + (first.month == 12), first.month % 12 + 1, 1)
        yield max(first, start), nxt
        first = nxt


def _has_document_amount():
    rows = _fetch({"kind": "probe"})
    return bool(rows) and "DocumentAmount" in rows[0]


def _fill_doc_amounts(s, start):
    """Суми документів із Power BI — у наявну копію (кількість документів)."""
    amounts = {}
    for r in _fetch({"kind": "doc_amounts", "since": start}):
        guid, amount = _guid(r.get("PaymentGUID")), _dec(r.get("DocumentAmount"))
        if guid and amount is not None:
            amounts[guid.upper()] = amount
    s.set_doc_amounts(amounts)
    return len(amounts)


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
        if not state.get("doc_amount") and _has_document_amount():
            # у Power BI з'явилася сума документа — дописати її в уже забрані платежі одним легким запитом
            state.update(doc_amount=True, doc_amount_filled=_fill_doc_amounts(s, start))
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
        # Зниклі з 1С документи (видалені зовсім): щоразу — за останні дні, раз на добу — за 90 днів
        last_keys = state.get("keys_at")
        full = check_keys or not last_keys or now - datetime.fromisoformat(last_keys) > timedelta(hours=23)
        window = max(start, now.date() - timedelta(days=KEYS_WINDOW_DAYS if full else KEYS_RECENT_DAYS))
        present = {(str(r.get("PaymentGUID") or "").strip().upper(), int(r.get("LineNo") or 1))
                   for r in _fetch({"kind": "keys", "since": window})}
        gone = s.keys_since(window) - present
        s.mark_removed(gone)
        state.update(removed_last=len(gone))
        if full:
            state.update(keys_at=now.isoformat())
        result = run_matching()
        state.update(synced_at=now.isoformat(), error=None, loaded_last=loaded, rows=s.count(),
                     linked_last=result["linked"], paid_last=result["paid"][-20:], unpaid_last=result["unpaid"][-10:],
                     issues=result["issues"], unlinked=result["unlinked"])
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
    """Стан для адмінки: коли синхронізовано, скільки рядків, остання помилка, як часто синхронізувати."""
    try:
        return {**store().get_state(), "table": table(), "live": _live(), "every_minutes": sync_minutes()}
    except Exception as e:
        return {"error": str(e)[:400], "table": table(), "live": _live(), "every_minutes": sync_minutes()}


def sync_due():
    """Чи пора синхронізувати (минуло sync_every() з останньої спроби) — для фону й відкриття сторінок."""
    st = status()
    last = max(st.get("synced_at") or "", st.get("failed_at") or "")
    try:
        return not last or clock.now() - datetime.fromisoformat(last) >= sync_every()
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


def docs_by_request(ids):
    """{id заявки: [документ платежу]} — № платіжного документа біля оплачених заявок у списках."""
    ids = [i for i in ids if i]
    if not ids:
        return {}
    try:
        by_req = store().links_for_requests(ids)
        guids = {g for v in by_req.values() for g in v}
        docs = documents(store().lines_of(guids)) if guids else {}
    except Exception:  # списки заявок мають відкриватися й без копії платежів
        logger.exception("payments: docs_by_request")
        return {}
    return {rid: sorted((docs[g] for g in v if g in docs), key=lambda d: (d["date"] or date.min, d["number"]))
            for rid, v in by_req.items()}


# ---------------------------------------------------------------- винятки «можна без заявки» й звіт «Звірка»

def operations():
    """Види операцій, які трапляються в платежах (варіанти для винятків в адмінці)."""
    try:
        return sorted(store().operations())
    except Exception:
        return []


def payment_types_used():
    """{id виду оплати: назва} — види оплати, що трапляються в платежах (варіанти для винятків і фільтра звіту)."""
    import payment_accounts
    try:
        ids = store().payment_types()
    except Exception:
        ids = set()
    return {i: payment_accounts.label(i) for i in sorted(ids)}


def item_departments():
    """{код статті: {відділи статті}} — з довідника статей 1С (відділ статті, а не документа платежу)."""
    return {i["code"]: set(i.get("departments") or []) for i in expense_items.get_items()}


OK_KEYS = {"operations": "payments_ok_operations", "payment_types": "payments_ok_payment_types",
           "items": "payments_ok_items", "departments": "payments_ok_departments"}


def _iso_date(value):
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


def exclusions():
    """Винятки «платіж без заявки — не помилка» (з датами «діє до») і дати підключення відділів до PayDesk."""
    import dept_settings
    until = app_settings.get("payments_ok_until") or {}
    return {**{kind: set(app_settings.get(key) or []) for kind, key in OK_KEYS.items()},
            "until": {kind: {v: _iso_date(d) for v, d in (until.get(key) or {}).items() if _iso_date(d)}
                      for kind, key in OK_KEYS.items()},
            "dept_since": dept_settings.all_requests_since(),
            "dept_default": app_settings.get("payments_dept_default") or "connected"}


def _why(kind, name, text, code=None, detail=None, until=None):
    if until:
        text += f" (до {until:%d.%m.%Y})"
    return {"kind": kind, "code": code, "name": name, "detail": detail, "until": until, "text": text}


def _ok_on(ok, kind, value, day):
    """Виняток є й діє на дату платежу: без «діє до» — завжди, з датою — для платежів не пізніше неї. -> (так/ні, дата)"""
    if value not in ok[kind]:
        return False, None
    until = (ok.get("until") or {}).get(kind, {}).get(value)
    return (until is None or day is None or day <= until), until


def not_connected_reason(doc, ok):
    """Платіж відділу, який ще не працює через PayDesk, — заявка не потрібна. Відділ — з документа 1С (рядки
    розшифровки); якщо там кілька відділів, заявка не потрібна, лише коли жоден із них ще не підключено."""
    codes = []
    for guid in doc.get("departments") or []:
        code = departments.code_by_guid(guid)
        if code and code not in codes:
            codes.append(code)
    if not codes:
        return None
    day, since = doc.get("date"), ok.get("dept_since") or {}

    def connected(code):
        if since.get(code):
            return day is None or day >= since[code]
        return ok.get("dept_default", "connected") == "connected"

    if any(connected(c) for c in codes):
        return None
    name = departments.get_name(codes[0]) or codes[0]
    start = since.get(codes[0])
    detail = f"заявки обов'язкові з {start:%d.%m.%Y}" if start else "дату підключення ще не задано"
    return _why("Відділ ще не працює через PayDesk", name, f"відділ «{name}» ще не працює через PayDesk ({detail})",
                detail=detail)


def allowed_reason(doc, ok=None, item_deps=None):
    """Чому платіж без заявки не вважається помилкою або None: відділ документа ще не працює через PayDesk або
    виняток («Звірка з 1С» → винятки), що діє на дату платежу.

    {"kind": «Відділ ще не працює через PayDesk» / «Стаття» / «Вид операції» / «Вид оплати» / «Відділ статті»,
    "code", "name", "detail", "until" (виняток діє до) — для показу, "text" — те саме одним рядком}."""
    ok = ok if ok is not None else exclusions()
    item_deps = item_deps if item_deps is not None else item_departments()
    day = doc.get("date")
    why = not_connected_reason(doc, ok)
    if why:
        return why
    on, until = _ok_on(ok, "operations", doc.get("operation"), day)
    if on:
        return _why("Вид операції", doc["operation"], f"вид операції «{doc['operation']}»", until=until)
    on, until = _ok_on(ok, "payment_types", doc.get("payment_type"), day)
    if on:
        import payment_accounts
        label = payment_accounts.label(doc["payment_type"])
        return _why("Вид оплати", label, f"вид оплати «{label}»", until=until)
    for code in doc.get("items") or []:
        name = expense_items.get_name(code) or ""
        on, until = _ok_on(ok, "items", code, day)
        if on:
            return _why("Стаття", name, f"стаття {code}" + (f" «{name}»" if name else ""), code=code, until=until)
        for dep in sorted(item_deps.get(code, set()) & ok["departments"]):
            on, until = _ok_on(ok, "departments", dep, day)
            if on:
                return _why("Відділ статті", dep, f"відділ статті «{dep}»", detail=f"{code} {name}".strip(), until=until)
    return None


REPORT_TABS = {"unlinked": "Без заявки", "issues": "Розбіжності", "linked": "Зіставлені з заявками",
               "allowed": "Дозволені без заявки"}
MATCH_LABELS = {"number": "за #номером у призначенні", "auto": "за сумою й контрагентом", "manual": "прив'язано вручну"}


def paid_by_payment(req, doc):
    """Чи «Оплачено» в заявці поставив PayDesk за цим платежем (а не бухгалтер вручну ще до платежу)."""
    e = wf.paid_entry(req) if req and req.get("status") == "paid" else None
    comment = (e or {}).get("comment") or ""
    return bool(comment.startswith("Оплачено — ") and " 1С: " in comment and f"№ {doc['number']} " in comment)


def _brief(req):
    """Коротко про заявку для звіту й вікна вибору."""
    return {"id": req["id"], "number": req["number"], "status": req["status"],
            "status_label": wf.STATUSES.get(req["status"], (req["status"], "grey"))[0],
            "status_color": wf.STATUSES.get(req["status"], ("", "grey"))[1],
            "counterparty_id": req.get("counterparty_id"), "counterparty": counterparties.name(req.get("counterparty_id")),
            "amount": Decimal(str(req.get("amount") or 0)), "currency": req.get("currency") or "",
            "author": req.get("author_name") or req.get("author_email") or "", "pay_date": req.get("pay_date"),
            "expense_code": req.get("expense_code") or ""}


def report(d_from, d_to):
    """Платежі за період для звіту «Звірка»: {вкладка: [документ + подробиці]}, новіші — вгорі.
    «Зіставлені» — з заявками, до яких прив'язано платіж; «Розбіжності» — із заявками, на які вказує #номер."""
    s = store()
    docs = documents([ln for ln in s.lines_since(d_from) if ln["pay_date"] and ln["pay_date"] <= d_to])
    links, issues = s.all_links(), s.all_issues()
    ok, item_deps = exclusions(), item_departments()
    out = {k: [] for k in REPORT_TABS}
    for d in sorted(docs.values(), key=lambda d: (d["date"] or date.min, d["number"]), reverse=True):
        issue = issues.get(d["guid"])
        if d["guid"] not in links and not d["active"]:
            continue
        if issue:
            numbers = (payment_match.numbers_in(d["purpose"]) if issue["issue"].startswith("number_")
                       else [n.strip() for n in (issue.get("detail") or "").split(",") if n.strip().isdigit()]
                       if issue["issue"] == "ambiguous" else [])
            out["issues"].append({**d, "issue": issue["issue"], "issue_label": payment_match.ISSUES.get(issue["issue"], ""),
                                  "issue_detail": issue.get("detail") or "", "ref_numbers": numbers,
                                  "linked_ids": sorted(links.get(d["guid"], {}))})
        elif d["guid"] in links:
            out["linked"].append({**d, "links": links[d["guid"]]})
        else:
            reason = allowed_reason(d, ok, item_deps)
            out["allowed" if reason else "unlinked"].append({**d, "allowed": reason})
    # заявки до рядків — одним запитом
    found = db.ids_by_numbers({n for d in out["issues"] for n in d["ref_numbers"]})
    ids = set(found.values()) | {rid for d in out["linked"] for rid in d["links"]} | {i for d in out["issues"] for i in d["linked_ids"]}
    reqs = db.get_many(sorted(ids)) if ids else {}
    for d in out["linked"]:
        d["requests"] = [{**_brief(reqs[rid]), "match": v.get("match"), "match_label": MATCH_LABELS.get(v.get("match"), ""),
                          "by_name": v.get("by_name") or "", "paid_here": paid_by_payment(reqs[rid], d)}
                         for rid, v in sorted(d.pop("links").items()) if rid in reqs]
    for d in out["issues"]:
        refs = [found[n] for n in d["ref_numbers"] if n in found] + d["linked_ids"]
        d["requests"] = [_brief(reqs[i]) for i in dict.fromkeys(refs) if i in reqs]
        d["missing_numbers"] = [n for n in d["ref_numbers"] if n not in found]
    return out


# ---------------------------------------------------------------- ручна прив'язка й відв'язка

def can_link(req, roles):
    """Хто може вручну прив'язати платіж до заявки («Звірка з 1С» → «Хто може прив'язати»)."""
    who = set(app_settings.get("payment_link_roles") or [])
    return bool(("admin" in who and "admin" in roles) or ("cfo" in who and "cfo" in roles)
                or ("accountant" in who and wf.accountant_role(req) in roles))


def can_link_any(roles):
    """Чи може користувач прив'язувати платежі хоч до якоїсь заявки (кнопки у звіті «Звірка»)."""
    who = set(app_settings.get("payment_link_roles") or [])
    return bool(("admin" in who and "admin" in roles) or ("cfo" in who and "cfo" in roles)
                or ("accountant" in who and set(roles) & wf.ACCOUNTANT_ROLES))


def link_candidates(req, days=60):
    """Неприв'язані платежі у валюті заявки за останні days днів — для вибору вручну: спершу той самий контрагент
    і сума, далі новіші."""
    s = store()
    docs = documents(s.lines_since(max(since(), clock.today() - timedelta(days=days))))
    links, amount = s.all_links(), Decimal(req.get("amount") or 0)
    out = []
    for d in docs.values():
        if not d["active"] or d["guid"] in links or d["currency"] != req.get("currency"):
            continue
        score = 2 * (req.get("counterparty_id") in d["counterparties"]) + (abs(d["amount"] - amount) < Decimal("0.01"))
        out.append({**d, "score": score})
    out.sort(key=lambda d: (-d["score"], -(d["date"].toordinal() if d["date"] else 0), d["number"]))
    return out[:80]


def request_candidates(guid, roles, q="", days=60, limit=50):
    """Заявки, до яких можна вручну прив'язати платіж (вікно «Знайти заявку» у звірці): «До оплати» й оплачені за days
    днів у валюті платежу, ще без платежу. Спершу — з #номером у призначенні, далі той самий контрагент і сума.
    Повертає (документ або None, [заявка коротко + «by_number», «same_cp», «same_amount»])."""
    s = store()
    doc = documents(s.lines_of([guid])).get(guid)
    if not doc:
        return None, []
    taken = {rid for v in s.all_links().values() for rid in v}
    ids = [i for i in db.payment_candidate_ids(clock.today() - timedelta(days=days)) if i not in taken]
    wanted = set(payment_match.numbers_in(doc["purpose"]))
    q = (q or "").strip().lower().lstrip("#")
    out = []
    for req in db.get_many(ids).values():
        if req.get("currency") != doc["currency"] or req["status"] not in ("to_pay", "paid") or not can_link(req, roles):
            continue
        b = _brief(req)
        if q and q not in " ".join([b["number"], str(int(b["number"])), b["counterparty"], b["author"],
                                    f"{b['amount']:.2f}", b["expense_code"]]).lower():
            continue
        b.update(by_number=b["number"] in wanted, same_cp=bool(b["counterparty_id"]) and b["counterparty_id"] in doc["counterparties"],
                 same_amount=abs(b["amount"] - doc["amount"]) < Decimal("0.01"))
        b["score"] = 4 * b["by_number"] + 2 * b["same_cp"] + b["same_amount"]
        out.append(b)
    out.sort(key=lambda b: (-b["score"], b["status"] != "to_pay", b["number"]))
    return doc, out[:limit]


ALLOW_KEYS = {"operation": "payments_ok_operations", "payment_type": "payments_ok_payment_types",
              "item": "payments_ok_items", "department": "payments_ok_departments"}


def allow(key, value, user, until=None):
    """Адміністратор зі звірки: такі платежі — «Дозволені без заявки» (додає value до винятку key). until — дата,
    до якої діє виняток (платежі пізніше неї знову потребують заявки); None — назавжди."""
    setting = ALLOW_KEYS.get(key)
    value = (value or "").strip()
    if not setting or not value:
        raise wf.WorkflowError("Невідомий виняток")
    who = user.get("email") or user.get("name") or ""
    current = list(app_settings.get(setting) or [])
    if value not in current:
        app_settings.set(setting, current + [value], who)
    dates = {k: dict(v) for k, v in (app_settings.get("payments_ok_until") or {}).items()}
    old = dates.get(setting, {}).get(value)
    new = until.isoformat() if until else None
    if new != old:
        if new:
            dates.setdefault(setting, {})[value] = new
        else:
            dates.get(setting, {}).pop(value, None)
        app_settings.set("payments_ok_until", {k: v for k, v in dates.items() if v}, who)
    return setting


def link_manual(guid, request_id, user, roles):
    """Прив'язати платіж до заявки вручну: заявка «До оплати» стає оплаченою, в уже оплаченій — запис в історії.
    Кидає WorkflowError з поясненням."""
    s = store()
    if guid in s.all_links():
        raise wf.WorkflowError("Цей платіж уже прив'язано до заявки")
    doc = documents(s.lines_of([guid])).get(guid)
    if not doc or not doc["active"]:
        raise wf.WorkflowError("Платіж не знайдено серед платежів з 1С (або його видалено)")
    req = db.get_request(request_id)
    if not req:
        raise wf.WorkflowError("Заявку не знайдено")
    if not can_link(req, roles):
        raise wf.WorkflowError("Прив'язувати платежі до цієї заявки вам не дозволено («Налаштування → Інтеграція з 1С»)")
    if req["status"] not in ("to_pay", "paid"):
        raise wf.WorkflowError(f"Заявка № {req['number']} не «До оплати» й не оплачена — платіж прив'язати не можна")
    if req.get("currency") != doc["currency"]:
        raise wf.WorkflowError(f"Платіж у {doc['currency']}, а заявка № {req['number']} — у {req.get('currency')}")
    s.add_link(guid, request_id, "manual", user["name"])
    if req["status"] == "to_pay":
        if not _pay(request_id, doc, user=user):
            s.remove_link(guid, request_id)
            raise wf.WorkflowError("Заявку саме змінили — оновіть сторінку й спробуйте ще раз")
    else:
        wf.add_history(req, "payment_link", user, wf.history_role(req, roles, user["email"]), label(doc),
                       req["status"], req["status"])
        db.save_request(req)
    issues = s.all_issues()
    if issues.pop(guid, None):
        s.replace_issues({g: (v["issue"], v.get("detail") or "") for g, v in issues.items()})
    return db.get_request(request_id)


def unlink(guid, request_id, user):
    """Адміністратор відв'язує помилково прив'язаний платіж (статус заявки не змінюється)."""
    s = store()
    doc = documents(s.lines_of([guid])).get(guid)
    s.remove_link(guid, request_id)
    req = db.get_request(request_id)
    if req:
        wf.add_history(req, "payment_unlink", user, "admin", label(doc) if doc else guid, req["status"], req["status"])
        db.save_request(req)
