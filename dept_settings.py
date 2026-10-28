"""Налаштування погодження для кожного відділу («Налаштування → Відділи»).

{код відділу: {"head_mode", "head_threshold", "deputy_approve", "cfo", "updated_by", "updated_at"}}:
  head_mode       — default (як у «Налаштування → Погодження»), approve (керівник погоджує), view (лише бачить);
  head_threshold  — керівник погоджує лише заявки від цієї суми (валюта — як у порога фіндиректора,
                    перерахунок за курсом 1С); 0 — усі;
  deputy_approve  — ті, хто «бачить заявки відділу», можуть погодити замість керівника;
  cfo             — default (загальні правила фіндиректора) або always (фіндиректор погоджує завжди);
  contractor_codes — статті для «Оплата підряднику» (None — як за замовчуванням, contractor_expense_codes);
  invoices_required — для «Оплата підряднику» обов'язковий хоча б один рахунок;
  direct_allowed  — чи робить відділ прямі витрати: None — за замовчуванням (профітцентр — так, інший — ні),
                    True / False — задано адміністратором. Ні — у заявці кроку «Що оплачуємо» немає, вона непряма.
  requests_since  — з якої дати відділ працює через PayDesk (ISO-дата): платежі 1С з таким відділом у документі,
                    раніше цієї дати, — «Дозволені без заявки» у звірці; None — як «Відділи без дати» (payments.py).
Маршрут визначається під час відправки й запам'ятовується в заявці, тож зміна налаштувань не ламає заявки в роботі.
У базі даних (docstore), а без бази — JSON-файл (/home/data на Azure). Окремо від org_structure: там записи
відділів (керівник, хто бачить) перезаписуються цілком.
"""
import os
import threading
from datetime import date
from decimal import Decimal

import app_settings
import cfo_rules
import clock
import departments
import docstore
import org_structure
import rates
import workflow as wf

HEAD_MODES = {"default": "Як у загальних налаштуваннях", "approve": "Погоджує", "view": "Лише бачить"}
CFO_MODES = {"default": "За загальними правилами", "always": "Погоджує завжди"}
DEFAULT = {"head_mode": "default", "head_threshold": 0, "deputy_approve": False, "cfo": "default",
           "contractor_codes": None, "invoices_required": False, "direct_allowed": None, "requests_since": None}
LABELS = {"head_mode": "керівник відділу", "head_threshold": "поріг керівника", "deputy_approve": "заступник погоджує",
          "cfo": "фіндиректор", "contractor_codes": "статті прямих витрат", "invoices_required": "рахунки обов'язкові",
          "direct_allowed": "прямі витрати", "requests_since": "заявки обов'язкові з"}

_lock = threading.Lock()
_data = None


def _path():
    base = "/home/data" if os.environ.get("WEBSITE_SITE_NAME") else os.path.join(os.path.dirname(__file__), "data")
    return os.environ.get("DEPT_SETTINGS_FILE") or os.path.join(base, "dept_settings.json")


def _load():
    global _data
    if _data is None:
        try:
            _data = docstore.read("dept_settings", _path())
        except (OSError, ValueError):
            _data = {}
    return _data


def reload():
    global _data
    with _lock:
        _data = None
        _load()


def get(code):
    """Налаштування відділу (незбережені — за замовчуванням)."""
    with _lock:
        saved = _load().get(code or "") or {}
    return {k: saved.get(k, v) for k, v in DEFAULT.items()}


def head_approves(code):
    """Чи погоджує керівник цього відділу (з урахуванням загального налаштування «Керівник відділу»)."""
    mode = get(code)["head_mode"]
    return app_settings.get("dept_head_mode") == "approve" if mode == "default" else mode == "approve"


def deputies_approve(code):
    return bool(code) and get(code)["deputy_approve"]


def deputies(code):
    """Ті, хто бачить заявки відділу і може погодити замість керівника (якщо так налаштовано)."""
    return org_structure.staff(code)["viewers"] if deputies_approve(code) else []


def approvers(code):
    """Хто погоджує на кроці керівника: керівник і (за налаштуванням) заступники — для листів і нагадувань."""
    return org_structure.heads_of(code) + deputies(code)


def contractor_codes(code):
    """Статті, з яких вибирають для «Оплата підряднику» у цьому відділі (свої або за замовчуванням)."""
    own = get(code)["contractor_codes"] if code else None
    return list(own) if own is not None else list(app_settings.get("contractor_expense_codes") or [])


def direct_allowed(code):
    """Чи може відділ робити прямі витрати (оплата рахунків підрядників за угодами). Без відділу — так."""
    if not code:
        return True
    own = get(code)["direct_allowed"]
    return departments.is_profit_center(code) if own is None else bool(own)


def all_requests_since():
    """{код відділу: дата}, з якої відділ працює через PayDesk (лише відділи, де дату задано)."""
    with _lock:
        data = dict(_load())
    out = {}
    for code, s in data.items():
        try:
            out[code] = date.fromisoformat(s["requests_since"]) if (s or {}).get("requests_since") else None
        except (TypeError, ValueError):
            out[code] = None
    return {c: d for c, d in out.items() if d}


def invoices_required(code):
    return bool(code) and get(code)["invoices_required"]


def threshold_ok(code, req):
    """(чи досягає заявка порога керівника, пояснення для історії або ""). Без порога — (True, "")."""
    limit = get(code)["head_threshold"] or 0
    if not limit:
        return True, ""
    cur = cfo_rules.get()["currency"]
    value, day = rates.convert(req.get("amount"), req.get("currency"), cur)
    if value is None or value >= Decimal(limit):
        return True, ""  # не вдалося перерахувати — керівник погоджує (безпечніше)
    when = f" (курс 1С на {date.fromisoformat(day):%d.%m.%Y})" if day else ""
    return False, (f"Погодження керівника відділу пропущено: ≈ {cfo_rules._money(value)} {cur} — менше за поріг "
                   f"керівника {wf.fmt_usd(limit)} {cur}{when}")


def set_many(changes, updated_by):
    """Зберегти {код: налаштування}; повертає [(код, поле, було, стало)] — для журналу змін."""
    log = []
    with _lock:
        data = _load()
        stamp = clock.now().isoformat(timespec="seconds")
        for code, new in changes.items():
            prev = {k: (data.get(code) or {}).get(k, d) for k, d in DEFAULT.items()}
            new = {k: new.get(k, prev[k]) for k in DEFAULT}
            if new == prev:
                continue
            log += [(code, k, prev[k], new[k]) for k in DEFAULT if prev[k] != new[k]]
            if new == DEFAULT:
                data.pop(code, None)
            else:
                data[code] = {**new, "updated_by": updated_by, "updated_at": stamp}
        if log:
            docstore.write("dept_settings", _path(), data)
    return log


def log_key(code, field):
    return f"dept:{code}:{field}"


def log_label(key):
    """Підпис запису журналу змін («Відділ «Логістика»: поріг керівника») або None — не наш ключ."""
    parts = key.split(":")
    if len(parts) != 3 or parts[0] != "dept":
        return None
    return f"Відділ «{departments.get_name(parts[1])}»: {LABELS.get(parts[2], parts[2])}"


def log_value(key, value):
    field = key.split(":")[-1]
    if field == "head_mode":
        return HEAD_MODES.get(value, value)
    if field == "cfo":
        return CFO_MODES.get(value, value)
    if field == "direct_allowed":
        return "за замовчуванням (профітцентр — так)" if value is None else ("дозволено" if value else "заборонено")
    if field in ("deputy_approve", "invoices_required"):
        return "так" if value else "ні"
    if field == "requests_since":
        try:
            return date.fromisoformat(value).strftime("%d.%m.%Y") if value else "не задано"
        except ValueError:
            return str(value)
    if field == "contractor_codes":
        return "як за замовчуванням" if value is None else (", ".join(value) or "—")
    return f"{wf.fmt_usd(value)} {cfo_rules.get()['currency']}" if value else "усі заявки"


def route_text(code):
    """Маршрут заявок відділу для сторінки «Відділи»."""
    s, steps = get(code), []
    if head_approves(code):
        head = "Керівник" + (" або заступник" if s["deputy_approve"] else "")
        if s["head_threshold"]:
            head += f" (від {wf.fmt_usd(s['head_threshold'])} {cfo_rules.get()['currency']})"
        steps.append(head)
    steps.append("Бухгалтер")
    if s["cfo"] == "always":
        steps.append("Фіндиректор (завжди)")
    elif app_settings.get("cfo_mode") != "observe":
        steps.append("Фіндиректор" + (" (за правилами)" if app_settings.get("cfo_mode") == "threshold" else ""))
    return " → ".join(steps + ["Оплата"])


# Інший процес сайту змінив документ — перечитати (docstore.sync)
docstore.register("dept_settings", reload)
