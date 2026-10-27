"""Реєстр налаштувань системи: усе, що змінює адміністратор, описано тут один раз.

Звідси беруться значення за замовчуванням (app_settings.DEFAULTS), вкладка й назва на сторінці
«Адміністрування → Налаштування», перевірка значень із форми (parse_tab), підписи для журналу змін
і перевірка файлу при імпорті (parse_import). Модуль нічого не імпортує з системи на рівні модуля —
списки варіантів (choices) задаються функціями й читаються лише тоді, коли потрібні.

kind:
  bool    — галочка (у формі вкладки: немає галочки = вимкнено);
  multi   — кілька варіантів з choices (галочки; у формі вкладки: жодної = порожній список);
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
    ("reconcile", "Звірка з 1С"),
    ("notify", "Сповіщення"),
    ("design", "Дизайн"),
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


# Коли контрагент, внесений у заявці, уже має бути в 1С (прив'язаний до довідника). Код "block" — історично «до
# Оплачено»; ранні етапи потрібні для звірки з платежами 1С: «Оплачено» за платежем ставиться лише тоді, коли
# контрагент заявки — з 1С, тож його краще прив'язати ще до «До оплати».
PENDING_CP_MODES = {
    "accountant": "до погодження бухгалтером: бухгалтер не погодить заявку, доки контрагента не прив'язано до 1С",
    "to_pay": "до «До оплати»: заявка не перейде в «До оплати», доки контрагента не прив'язано до 1С",
    "block": "до «Оплачено»: заявку не можна позначити оплаченою, доки контрагента не прив'язано до 1С",
    "allow": "не вимагати: оплату можна позначити й без контрагента в 1С (з попередженням)",
}
PENDING_CP_TITLES = {"accountant": "До погодження бухгалтером", "to_pay": "До переходу в «До оплати»",
                     "block": "До «Оплачено»", "allow": "Не вимагати"}


def _pending_cp_modes():
    return PENDING_CP_MODES


def _login_methods():
    return {"both": "Google і Microsoft",
            "google": "Лише Google"}


def _new_user_roles():
    """Ролі, які можна давати новим співробітникам із 1С за правилом (без адміністратора)."""
    import workflow
    return {r: workflow.ROLES[r] for r in ("initiator", "acc_cash", "acc_resident", "acc_nonresident", "cfo")}


def _invoices_modes():
    return {"on": "Увімкнено: тип оплати й підбір рахунків з 1С", "off": "Вимкнено: без рахунків у заявці"}


def _invoices_block_modes():
    return {"show": "Показувати: рахунки — за бажанням ініціатора", "hide": "Не показувати",
            "auto": "За статтею: для прямої статті відділу — показувати"}


# Кольорові теми всієї програми (<html data-theme>) і колір рядка браузера на телефоні для кожної
COLOR_THEMES = {"blue": "Синя: стандартна, як було",
                "dark": "Темна: темний фон і картки, світлий текст — менше втомлює очі",
                "emerald": "Смарагдова: світла, із зеленим акцентом",
                "graphite": "Графіт: темно-сіра шапка й фіолетовий акцент"}
THEME_COLORS = {"blue": "#0f6cbd", "dark": "#161b22", "emerald": "#0f7b6c", "graphite": "#2b2f36"}


# Дизайн картки заявки: класична — як була; майстер і чек-лист — ті самі поля, інший порядок заповнення
CARD_DESIGNS = {"classic": "Класична: блоки на одній сторінці й панель «Підсумок» праворуч",
                "wizard": "Покроковий майстер: один крок на екрані, «Далі» й «Назад», наприкінці — перевірка всієї заявки",
                "checklist": "Чек-лист: усе на одній сторінці, заповнений розділ згортається в рядок, унизу — сума й кнопки"}


def _card_designs():
    return CARD_DESIGNS


def _holidays():
    import holidays
    return {code: name for code, (name, _) in holidays.HOLIDAYS.items()}


def _holiday_now():
    return {"auto": "За календарем", **_holidays()}


def _color_themes():
    return COLOR_THEMES


def _pay_modes():
    return {"manual": "Вручну: «Оплачено» ставить бухгалтер, як зараз",
            "auto": "Автоматично: «Оплачено» ставить система, щойно платіж з'явиться в 1С",
            "both": "Обидва: система ставить сама, бухгалтер може й вручну"}


def _link_roles():
    return {"accountant": "Бухгалтер заявки (той, хто її оплачує)", "cfo": "Фіндиректор", "admin": "Адміністратор"}


def _ok_item_departments():
    """Відділи зі статей витрат (довідник 1С) — винятки «платіж без заявки — не помилка»."""
    import app_settings
    import expense_items
    names = {d for i in expense_items.get_items() for d in i.get("departments") or []}
    return {n: n for n in sorted(names | set(app_settings.get("payments_ok_departments") or []), key=str.lower)}


def _ok_items():
    import app_settings
    import expense_items
    items = {i["code"]: f"{i['code']} — {i['name']}" for i in expense_items.get_items()}
    return {**{c: c for c in app_settings.get("payments_ok_items") or []}, **items}


def _ok_operations():
    import app_settings
    import payments
    return {o: o for o in sorted(set(payments.operations()) | set(app_settings.get("payments_ok_operations") or []))}


def _ok_payment_types():
    """Види оплати (рахунки й каси), що трапляються в платежах з 1С, плюс уже вибрані."""
    import app_settings
    import payment_accounts
    import payments
    used = payments.payment_types_used()
    chosen = {i: payment_accounts.label(i) for i in app_settings.get("payments_ok_payment_types") or [] if i not in used}
    return dict(sorted({**used, **chosen}.items(), key=lambda kv: kv[1].lower()))


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


def _doc_modes():
    return {"no": "Не обов'язковий", "always": "Обов'язковий завжди", "amount": "Обов'язковий від суми"}


def _withdraw_modes():
    return {"off": "Вимкнено", "before_decision": "До першого рішення погоджувача",
            "before_full": "До повного погодження (поки заявка на погодженні)"}


def _cancel_modes():
    return {"off": "Вимкнено", "editable": "Лише чернетка й доопрацювання",
            "before_pay": "До «До оплати» (і поки заявку погоджують)"}


# Формати документів, які можна дозволити (розширення -> підпис)
DOC_FORMATS = {"pdf": "PDF", "jpg": "JPG", "jpeg": "JPEG", "png": "PNG", "doc": "Word (doc)", "docx": "Word (docx)",
               "xls": "Excel (xls)", "xlsx": "Excel (xlsx)", "xml": "XML", "zip": "ZIP", "txt": "TXT", "csv": "CSV"}


def _doc_formats():
    return DOC_FORMATS


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
    Setting("req_document", "Документ (рахунок) перед відправкою", "request", "choice", "no", _doc_modes),
    Setting("req_document_limit", "Документ обов'язковий від суми", "request", "int", 1000, min=1, max=10**9),
    Setting("req_min_pay_days", "Дата оплати — не раніше ніж через N робочих днів", "request", "int", 0, min=0, max=30),
    Setting("req_lines_required", "Рядки рахунків обов'язкові", "request", "bool", False),
    Setting("withdraw_mode", "Автор може відкликати заявку", "request", "choice", "before_full", _withdraw_modes),
    Setting("cancel_mode", "Автор може скасувати заявку", "request", "choice", "before_pay", _cancel_modes),
    Setting("doc_extensions", "Дозволені формати документів", "request", "multi",
            ["pdf", "jpg", "jpeg", "png", "doc", "docx", "xls", "xlsx"], _doc_formats),
    Setting("doc_max_mb", "Максимальний розмір файлу, МБ", "request", "int", 10, min=1, max=25),
    Setting("invoices_mode", "Рахунки в заявці", "request", "choice", "on", _invoices_modes),
    # Без питання ініціатор бачить свої статті, а тип заявки визначає стаття (пряма стаття відділу — пряма витрата)
    Setting("kind_question", "Питання «Що оплачуємо?» у заявці", "request", "bool", True),
    Setting("invoices_block", "Блок «Рахунки», коли питання «Що оплачуємо?» вимкнено", "request", "choice", "show",
            _invoices_block_modes),
    # Статті для «Оплата підряднику» у відділах без власного списку («Адміністрування → Відділи»)
    Setting("contractor_expense_codes", "Статті оплати підряднику за замовчуванням", "request", "json", ["CR000065"],
            managed=True, fmt=_fmt_codes),
    # ---- Оплата й готівка
    Setting("require_payment_account", "Бухгалтер обов'язково вибирає «Вид оплати»", "payment", "bool", True,
            marker="require_payment_account_sent"),
    Setting("cash_handover_confirm", "Автор підтверджує передачу готівки підряднику", "payment", "bool", True,
            marker="cash_handover_sent"),
    Setting("cash_handover_remind_days", "Нагадувати про передачу готівки кожні N днів", "payment", "int", 1, min=0, max=30),
    Setting("handover_max_reminders", "Нагадувань про передачу готівки — не більше", "payment", "int", 5, min=1, max=20),
    Setting("pending_cp_payment", "Контрагент, якого ще немає в 1С", "payment", "choice", "block", _pending_cp_modes),
    Setting("cp_onec_owner", "Хто заводить нового контрагента в 1С", "payment", "choice", "accountant",
            _wf("CP_OWNER_MODES")),
    Setting("disabled_organizations", "Організації, недоступні для вибору", "payment", "json", [], managed=True),
    Setting("org_accountants", "Бухгалтери організацій", "payment", "json", {}, managed=True),
    # ---- Звірка з платежами 1С (payments.py)
    Setting("payments_since", "Звіряти платежі з 1С від дати", "reconcile", "text", "2026-09-01"),
    Setting("pay_mode_acc_cash", "«Оплачено»: бухгалтер по готівці", "reconcile", "choice", "manual", _pay_modes),
    Setting("pay_mode_acc_resident", "«Оплачено»: бухгалтер резидентів", "reconcile", "choice", "manual", _pay_modes),
    Setting("pay_mode_acc_nonresident", "«Оплачено»: бухгалтер нерезидентів", "reconcile", "choice", "manual", _pay_modes),
    Setting("payment_link_roles", "Хто може вручну прив'язати платіж з 1С", "reconcile", "multi", ["accountant", "admin"],
            _link_roles),
    # Платіж без заявки — не помилка («Звірка»): відділ статті витрат (відділи підключають поступово), стаття, вид операції
    Setting("payments_ok_departments", "Без заявки дозволено: відділи статей", "reconcile", "multi", [], _ok_item_departments),
    Setting("payments_ok_items", "Без заявки дозволено: статті витрат", "reconcile", "multi", [], _ok_items),
    Setting("payments_ok_operations", "Без заявки дозволено: види операцій", "reconcile", "multi", [], _ok_operations),
    Setting("payments_ok_payment_types", "Без заявки дозволено: види оплати", "reconcile", "multi", [], _ok_payment_types),
    # ---- Сповіщення (пошта — власна форма)
    Setting("mail_enabled", "Розсилка увімкнена", "notify", "bool", False, managed=True),
    Setting("mail_sender", "Пошта відправника", "notify", "text", "", managed=True),
    Setting("mail_sender_name", "Ім'я відправника", "notify", "text", "PayDesk", managed=True),
    Setting("smtp_host", "SMTP-сервер", "notify", "text", "smtp.gmail.com", managed=True),
    Setting("smtp_port", "SMTP-порт", "notify", "int", 587, min=1, max=65535, managed=True),
    Setting("smtp_security", "Шифрування SMTP", "notify", "choice", "starttls", _security, managed=True),
    Setting("smtp_username", "SMTP-логін", "notify", "text", "", managed=True),
    Setting("smtp_password", "SMTP-пароль", "notify", "secret", "", managed=True),
    Setting("allow_custom_email", "Користувачі можуть вказати іншу пошту", "notify", "bool", False, managed=True),
    Setting("error_alerts", "Листи адміністраторам про помилки сайту", "notify", "bool", True, managed=True),
    Setting("approver_remind_days", "Нагадувати погоджувачу, якщо заявка чекає довше N днів", "notify", "int", 0,
            min=0, max=30),
    Setting("approver_remind_max", "Нагадувань погоджувачу — не більше", "notify", "int", 3, min=1, max=20),
    Setting("approver_remind_cfo_copy", "Копія нагадування фіндиректору, якщо заявка чекає бухгалтера", "notify",
            "bool", False),
    # ---- Дизайн
    Setting("app_name", "Назва системи", "design", "text", "PayDesk"),
    Setting("card_design", "Дизайн картки заявки", "design", "choice", "classic", _card_designs),
    Setting("color_theme", "Кольорова тема", "design", "choice", "blue", _color_themes),
    # Святкове оформлення: відмічені свята — самі у свої дні; «Показати зараз» — будь-яке вручну
    Setting("holidays", "Святкове оформлення за календарем", "design", "multi", [], _holidays),
    Setting("holiday_now", "Святкове оформлення: показати зараз", "design", "choice", "auto", _holiday_now),
    # ---- Дані й система
    Setting("data_mode", "Дані", "data", "choice", "work", _data_modes, available=_db_on),
    Setting("login_methods", "Вхід у систему", "data", "choice", "both", _login_methods),
    # Правило для співробітників, яких додають зі сторінки «Співробітники з 1С» (там і зберігається)
    Setting("new_user_roles", "Нові співробітники з 1С: ролі", "data", "multi", ["initiator"], _new_user_roles,
            managed=True),
    Setting("new_user_budget_own", "Нові співробітники з 1С: статті з розподілом свого відділу", "data", "bool", True,
            managed=True),
    Setting("onec_auto_sync", "Автоматично додавати співробітників з 1С (корпоративна пошта) і закривати доступ "
            "звільненим", "data", "bool", False, managed=True),
    Setting("onec_auto_last", "Остання автоматична синхронізація з 1С", "data", "internal", None),
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
    if s.kind == "multi":
        return ", ".join(value) if value else "—"
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
    if s.kind == "multi":
        options = s.choices() if s.choices else {}
        values = raw if isinstance(raw, list) else []
        bad = [v for v in values if v not in options]
        if bad:
            return None, f"«{s.label}»: невідомі значення {', '.join(map(str, bad))}"
        return [v for v in options if v in values], None   # порядок — як у переліку
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
        if s.kind in ("bool", "multi"):
            if (legacy and s.marker not in form) or (not legacy and form.get("tab") != s.tab):
                continue
            raw = form.getlist(s.key) if s.kind == "multi" else form.get(s.key)
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
        elif s.kind == "multi":
            value, error = _parse_value(s, raw) if isinstance(raw, list) else (None, f"«{s.label}»: має бути список")
        else:
            value, error = _parse_value(s, raw)
        if error:
            errors.append(error)
        elif value != current(key):
            changes[key] = value
    return changes, errors, unknown
