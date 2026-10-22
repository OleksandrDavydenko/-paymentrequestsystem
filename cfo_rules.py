"""Правила, за якими фіндиректор погоджує заявку (режим «Погоджує за правилами»).

Таблиця за типом бухгалтера заявки (workflow.accountant_role): для кожного — «завжди», «від суми» або
«ніколи»; поріг — у вибраній валюті (USD, EUR чи UAH) за останнім відомим курсом 1С на момент відправки.
Додаткові умови (новий контрагент, якого ще немає в 1С; стаття витрат зі списку) вмикають фіндиректора
незалежно від таблиці. Рішення ухвалюється при відправці й записується в історію заявки з поясненням,
тож зміна правил не впливає на заявки, що вже на погодженні.

Поки правила не збережено, усі три рядки — «від суми» з порогом cfo_threshold_usd (як було до правил).
"""
from datetime import date
from decimal import Decimal

import app_settings
import counterparties
import rates
import workflow as wf

ROWS = ("acc_cash", "acc_resident", "acc_nonresident")
ROW_LABELS = {"acc_cash": "Бухгалтер по готівці", "acc_resident": "Бухгалтер резидентів",
              "acc_nonresident": "Бухгалтер нерезидентів"}
MODES = {"always": "завжди", "threshold": "від суми", "never": "ніколи"}
CURRENCIES = ("USD", "EUR", "UAH")


def _money(value):
    return f"{Decimal(value):,.2f}".replace(",", " ").replace(".", ",")


def rows(saved=None):
    """Рядки таблиці {роль: {"mode", "limit"}}: збережені або (поки правил немає) — від порогу cfo_threshold_usd."""
    saved = app_settings.get("cfo_rules") if saved is None else saved
    default_limit = int(app_settings.get("cfo_threshold_usd") or 1000)
    out = {}
    for role in ROWS:
        r = (saved or {}).get(role) or {}
        mode = r.get("mode") if r.get("mode") in MODES else "threshold"
        out[role] = {"mode": mode, "limit": int(r.get("limit") or default_limit)}
    return out


def get():
    """{"rows", "currency", "new_counterparty", "expense_codes"} — поточні правила."""
    currency = app_settings.get("cfo_currency")
    return {"rows": rows(), "currency": currency if currency in CURRENCIES else "USD",
            "new_counterparty": bool(app_settings.get("cfo_new_counterparty")),
            "expense_codes": list(app_settings.get("cfo_expense_codes") or [])}


def _row_text(row, currency):
    return f"від {wf.fmt_usd(row['limit'])} {currency}" if row["mode"] == "threshold" else MODES[row["mode"]]


def describe_rows(table, currency="USD"):
    """«готівка — завжди; резиденти — від 1 000 USD; нерезиденти — ніколи» (журнал змін, підказки)."""
    table = rows(table)
    short_names = {"acc_cash": "готівка", "acc_resident": "резиденти", "acc_nonresident": "нерезиденти"}
    return "; ".join(f"{short_names[r]} — {_row_text(table[r], currency)}" for r in ROWS)


def short(cfg=None):
    """Коротко для підсумку маршруту: «від 1 000 USD», «завжди» або «за правилами»."""
    cfg = cfg or get()
    values = list(cfg["rows"].values())
    if all(v == values[0] for v in values) and not cfg["new_counterparty"] and not cfg["expense_codes"]:
        return _row_text(values[0], cfg["currency"])
    return "за правилами"


def decide(req, cfg=None):
    """Чи погоджує фіндиректор заявку за правилами: (cfo_step, пояснення для історії)."""
    cfg = cfg or get()
    if cfg["new_counterparty"] and counterparties.is_pending(req.get("counterparty_id")):
        return True, "Потрібне погодження фіндиректора: контрагента ще немає в 1С (правило «новий контрагент»)"
    if req.get("expense_code") and req["expense_code"] in cfg["expense_codes"]:
        return True, (f"Потрібне погодження фіндиректора: стаття витрат {req['expense_code']} — у списку статей, "
                      "які погоджує фіндиректор")
    role = wf.accountant_role(req)
    row = cfg["rows"].get(role) or {"mode": "always", "limit": 0}
    who = ROW_LABELS.get(role, "Бухгалтер").lower()
    if row["mode"] == "always":
        return True, f"Потрібне погодження фіндиректора: {who} — завжди"
    if row["mode"] == "never":
        return False, f"Фіндиректор не погоджує: {who} — без фіндиректора"
    cur = cfg["currency"]
    value, day = rates.convert(req.get("amount"), req.get("currency"), cur)
    limit_s = f"{wf.fmt_usd(row['limit'])} {cur}"
    if value is None:
        return True, f"Потрібне погодження фіндиректора: не вдалося перерахувати суму в {cur} ({day})"
    when = f" (курс 1С на {date.fromisoformat(day):%d.%m.%Y})" if day else ""
    if value >= Decimal(row["limit"]):
        return True, f"Потрібне погодження фіндиректора ({who}): ≈ {_money(value)} {cur} — не менше за поріг {limit_s}{when}"
    return False, f"Фіндиректор не погоджує ({who}): ≈ {_money(value)} {cur} — менше за поріг {limit_s}{when}"
