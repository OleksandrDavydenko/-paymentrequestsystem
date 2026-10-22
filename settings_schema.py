"""Реєстр налаштувань системи: усе, що змінює адміністратор, описано тут один раз.

Звідси беруться значення за замовчуванням (app_settings.DEFAULTS), вкладка й назва на сторінці
«Адміністрування → Налаштування», перевірка значень із форми (parse_tab), підписи для журналу змін
і перевірка файлу при імпорті (parse_import). Модуль нічого не імпортує з системи на рівні модуля —
списки варіантів (choices) задаються функціями й читаються лише тоді, коли потрібні.

kind:
  bool    — галочка (у формі вкладки: немає галочки = вимкнено);
  int     — ціле число в межах [min, max];
  choice  — одне з варіантів choices;
  text    — рядок;
  json    — складне значення (словник / список), яке зберігає власна форма (організації тощо);
  secret  — пароль: у журналі «••••», в експорт не потрапляє;
  internal— службове, не показується й не експортується.
managed — значення зберігає власна форма (пошта, організації), а не загальне збереження вкладки.
"""
from dataclasses import dataclass

TABS = [
    ("approval", "Погодження"),
    ("request", "Заявка"),
    ("payment", "Оплата й готівка"),
    ("notify", "Сповіщення"),
    ("data", "Дані й система"),
    ("log", "Журнал змін"),
]
TAB_NAMES = dict(TABS)


@dataclass(frozen=True)
class Setting:
    key: str
    label: str
    tab: str
    kind: str
    default: object
    choices: object = None    # функція -> {значення: підпис}
    min: int = None
    max: int = None
    marker: str = None        # прихована позначка старої форми для галочки (сумісність)
    managed: bool = False     # зберігає власна форма, а не загальне збереження вкладки
    available: object = None  # функція -> чи доступне налаштування зараз
    fmt: object = None        # функція значення -> текст для журналу (для складних значень)


def _wf(name):
    def choices():
        import workflow
        return getattr(workflow, name)
    return choices


PENDING_CP_MODES = {
    "block": "заборонити позначати «Оплачено», доки контрагента не прив'язано до 1С",
    "allow": "дозволити оплату з попередженням",
}


def _pending_cp_modes():
    return PENDING_CP_MODES


def _data_modes():
    return {"work": "Робоча база", "test": "Тестові дані"}


def _db_on():
    import db_sql
    return db_sql.configured()


def _security():
    import mailer
    return mailer.SECURITY


def _cfo_currencies():
    import cfo_rules
    return {c: c for c in cfo_rules.CURRENCIES}


def _fmt_cfo_rules(value):
    import app_settings
    import cfo_rules
    return cfo_rules.describe_rows(value, app_settings.get("cfo_currency") or "USD")


def _fmt_codes(value):
    return ", ".join(value) if value else "—"


_LIST = [
    # ---- Погодження
    Setting("approval_mode", "Порядок погодження", "approval", "choice", "sequential", _wf("APPROVAL_MODES")),
    Setting("dept_head_mode", "Керівник відділу", "approval", "choice", "view", _wf("HEAD_MODES")),
    Setting("cfo_mode", "Фіндиректор", "approval", "choice", "approve", _wf("CFO_MODES")),
    # Поріг до появи правил за типом бухгалтера: правила без збереженої таблиці беруть його для всіх рядків
    Setting("cfo_threshold_usd", "Поріг фіндиректора, USD", "approval", "int", 1000, min=1, max=10**9),
    Setting("cfo_rules", "Правила фіндиректора за типом бухгалтера", "approval", "json", {}, managed=True,
            fmt=_fmt_cfo_rules),
    Setting("cfo_currency", "Валюта порогу фіндиректора", "approval", "choice", "USD", _cfo_currencies),
    Setting("cfo_new_counterparty", "Фіндиректор погоджує заявки з новим контрагентом", "approval", "bool", False),
    Setting("cfo_expense_codes", "Статті, які завжди погоджує фіндиректор", "approval", "json", [], managed=True,
            fmt=_fmt_codes),
    Setting("no_self_approval", "Автор не може погоджувати свою заявку", "approval", "bool", False,
            marker="no_self_approval_sent"),
    # ---- Заявка
    Setting("org_field_mode", "Що вибирає ініціатор у заявці", "request", "choice", "organization", _wf("ORG_FIELD_MODES")),
    # ---- Оплата й готівка
    Setting("require_payment_account", "Бухгалтер обов'язково вибирає «Вид оплати»", "payment", "bool", True,
            marker="require_payment_account_sent"),
    Setting("cash_handover_confirm", "Автор підтверджує передачу готівки підряднику", "payment", "bool", True,
            marker="cash_handover_sent"),
    Setting("cash_handover_remind_days", "Нагадувати про передачу готівки кожні N днів", "payment", "int", 1, min=0, max=30),
    Setting("pending_cp_payment", "Контрагент, якого ще немає в 1С", "payment", "choice", "block", _pending_cp_modes),
    Setting("cp_onec_owner", "Хто заводить нового контрагента в 1С", "payment", "choice", "accountant",
            _wf("CP_OWNER_MODES")),
    Setting("disabled_organizations", "Організації, недоступні для вибору", "payment", "json", [], managed=True),
    Setting("org_accountants", "Бухгалтери організацій", "payment", "json", {}, managed=True),
    # ---- Сповіщення (пошта — власна форма)
    Setting("mail_enabled", "Розсилка увімкнена", "notify", "bool", False, managed=True),
    Setting("mail_sender", "Пошта відправника", "notify", "text", "", managed=True),
    Setting("mail_sender_name", "Ім'я відправника", "notify", "text", "Система заявок на оплату", managed=True),
    Setting("smtp_host", "SMTP-сервер", "notify", "text", "smtp.gmail.com", managed=True),
    Setting("smtp_port", "SMTP-порт", "notify", "int", 587, min=1, max=65535, managed=True),
    Setting("smtp_security", "Шифрування SMTP", "notify", "choice", "starttls", _security, managed=True),
    Setting("smtp_username", "SMTP-логін", "notify", "text", "", managed=True),
    Setting("smtp_password", "SMTP-пароль", "notify", "secret", "", managed=True),
    Setting("allow_custom_email", "Користувачі можуть вказати іншу пошту", "notify", "bool", False, managed=True),
    Setting("error_alerts", "Листи адміністраторам про помилки сайту", "notify", "bool", True, managed=True),
    # ---- Дані й система
    Setting("data_mode", "Дані", "data", "choice", "work", _data_modes, available=_db_on),
    # ---- службові
    Setting("mail_enabled_work", "Розсилка до тестового режиму", "data", "internal", None),
]
SETTINGS = {s.key: s for s in _LIST}


def defaults():
    return {s.key: s.default for s in _LIST}


def label(key):
    s = SETTINGS.get(key)
    return s.label if s else key


def value_label(key, value):
    """Значення для людини: підпис варіанта, «так/ні», «••••» для пароля."""
    s = SETTINGS.get(key)
    if s is None:
        return str(value)
    if s.kind == "secret":
        return "••••" if value else "—"
    if s.kind == "bool":
        return "так" if value else "ні"
    if s.kind == "choice" and s.choices:
        text = s.choices().get(value, value)
        return str(text).split(":")[0] if isinstance(text, str) else str(text)
    if s.fmt:
        return s.fmt(value)
    if s.kind == "json":
        return f"{len(value or [])} записів" if isinstance(value, (list, dict)) else str(value)
    return "—" if value in (None, "") else str(value)


def tab_settings(tab):
    return [s for s in _LIST if s.tab == tab and s.kind != "internal"]


def _parse_value(s, raw):
    """(значення, текст помилки)."""
    if s.kind == "bool":
        return raw in ("1", "on", "true", True), None
    if s.kind == "int":
        try:
            value = int(str(raw).replace(" ", "").replace(" ", "").split(",")[0].split(".")[0])
        except (TypeError, ValueError):
            return None, f"«{s.label}»: вкажіть ціле число"
        if (s.min is not None and value < s.min) or (s.max is not None and value > s.max):
            return None, f"«{s.label}»: від {s.min} до {s.max}"
        return value, None
    if s.kind == "choice":
        options = s.choices() if s.choices else {}
        return (raw, None) if raw in options else (None, f"«{s.label}»: невідомий варіант")
    if s.kind == "text":
        return (raw or "").strip(), None
    return raw, None


def parse_tab(tab, form, current, legacy=False):
    """Зміни з форми вкладки: ({ключ: нове значення}, {ключ: помилка}). current — функція ключ -> поточне значення.
    legacy — стара спільна форма «Налаштування процесу» (без вкладок): галочки — лише за їхніми позначками."""
    changes, errors = {}, {}
    for s in _LIST:
        if s.managed or s.kind in ("internal", "json", "secret") or (not legacy and s.tab != tab):
            continue
        if s.available and not s.available():
            continue
        if s.kind == "bool":
            if (legacy and s.marker not in form) or (not legacy and form.get("tab") != s.tab):
                continue
            raw = form.get(s.key)
        elif s.key in form:
            raw = form.get(s.key)
        else:
            continue  # поля немає у формі (напр., неактивна радіокнопка) — не змінюємо
        value, error = _parse_value(s, raw)
        if error:
            errors[s.key] = error
        elif value != current(s.key):
            changes[s.key] = value
    return changes, errors


def exportable():
    return [s for s in _LIST if s.kind not in ("secret", "internal")]


def parse_import(data, current):
    """Перевірити файл налаштувань: ({ключ: нове значення}, [помилки], [невідомі ключі])."""
    if not isinstance(data, dict) or not isinstance(data.get("settings"), dict):
        return {}, ["Це не файл налаштувань системи заявок (немає розділу «settings»)"], []
    changes, errors, unknown = {}, [], []
    for key, raw in data["settings"].items():
        s = SETTINGS.get(key)
        if s is None or s.kind in ("secret", "internal"):
            unknown.append(key)
            continue
        if s.kind == "json":
            ok = isinstance(raw, type(s.default))
            value, error = (raw, None) if ok else (None, f"«{s.label}»: неправильний формат")
        elif s.kind == "bool":
            value, error = (raw, None) if isinstance(raw, bool) else (None, f"«{s.label}»: має бути так/ні")
        else:
            value, error = _parse_value(s, raw)
        if error:
            errors.append(error)
        elif value != current(key):
            changes[key] = value
    return changes, errors, unknown
