"""Хто з бухгалтерів бачить і оплачує заявки «До оплати» й «Оплачені», і контроль «нікому оплатити».

Режим — «Налаштування → Оплата й готівка → Хто з бухгалтерів бачить заявки до оплати» (pay_visibility):
  role      — лише за роллю бухгалтера (готівка / резидент / нерезидент), як було;
  role_type — за роллю і видом оплати з 1С: бухгалтер бачить і оплачує заявку, лише якщо її «Вид оплати» йому
              призначено в 1С (картка користувача 1С → «Виды оплаты»; людину знаходимо за поштою, payment_types.onec_link).
              Заявку без виду оплати бачать усі бухгалтери ролі — хто вибере свій вид, у того вона й лишиться.
              Фіндиректор і адміністратор бачать усе, як і раніше.
Суворий режим діє, лише коли види оплати беруться з 1С і таблицю доступу завантажено; інакше — за роллю, щоб збій
Power BI не сховав від бухгалтерів усі заявки (в адмінці — попередження).

Саме правило — у workflow (RoleSet.pay_types, pay_type_ok): у «До оплати», «Оплачених», лічильнику меню, кнопці
«Оплачено» й масовій оплаті. Тут — звідки беруться види оплати людини та контроль.

Контроль: заявка «До оплати», яку нікому з бухгалтерів оплатити (у ролі немає людей або її вид оплати нікому з них
не призначено), — у списку на вкладці налаштувань, з позначкою в «До оплати» й картці у фіндиректора, і листом
адміністраторам. Лист — раз на випадок: пам'ять «уже повідомили» (документ pay_orphans: {id заявки: код причини});
знову — лише якщо заявку вже було кому оплатити, а тепер знову нікому, або причина змінилася.
"""
import logging
import os
import threading

import app_settings
import docstore
import payment_accounts
import payment_types
import user_roles
import workflow as wf

logger = logging.getLogger(__name__)

UNRESTRICTED = frozenset({"cfo", "admin"})  # бачать усі заявки до оплати й оплачені — без обмеження за видом


# ---------------------------------------------------------------- режим і види оплати людини

def setting_strict():
    """Адміністратор вибрав «За роллю і видом оплати з 1С»."""
    return app_settings.get("pay_visibility") == "role_type"


def data_ready():
    """Чи можна застосувати правило за видом оплати: види оплати — з 1С, і доступи користувачів 1С завантажено."""
    return payment_accounts.use_types() and payment_types.access_loaded()


def strict():
    """Діє правило «за роллю і видом оплати» (вибрано в адмінці й є дані з 1С)."""
    return setting_strict() and data_ready()


def allowed_types(email):
    """id видів оплати, призначених людині в 1С (за поштою → співробітник → користувач 1С); немає зв'язку — порожньо."""
    user = payment_types.onec_user_for_email(email)
    return payment_types.type_ids_for_user(user["guid"]) if user else frozenset()


def pay_types_for(roles, email):
    """Значення RoleSet.pay_types для того, хто увійшов: види оплати бухгалтера в суворому режимі, інакше None."""
    roles = set(roles)
    if not roles & wf.ACCOUNTANT_ROLES or roles & UNRESTRICTED or not strict():
        return None
    return allowed_types(email)


# ---------------------------------------------------------------- хто може оплатити заявку

def _role_of(req):
    """Бухгалтер заявки: з повної заявки — за правилом, з легкого рядка списку — збережена похідна колонка."""
    return wf.accountant_role(req) if "history" in req else req.get("accountant_role")


def _fits(req, types):
    return types is None or not req.get("payment_account_id") or str(req["payment_account_id"]) in types


def _people(role, strict_mode, cache):
    """[(людина, її види оплати або None — без обмеження)] — усі, кому в системі призначено роль role."""
    key = (role, strict_mode)
    if key not in cache:
        rows = []
        for u in user_roles.all_users():
            if role in u["roles"] and u["email"]:
                rows.append((u, allowed_types(u["email"]) if strict_mode and not u["roles"] & UNRESTRICTED else None))
        cache[key] = rows
    return cache[key]


def payers(req, strict_mode=None, cache=None):
    """Бухгалтери, які можуть оплатити заявку зараз: [{"id", "email", "name", …}]."""
    strict_mode = strict() if strict_mode is None else strict_mode
    role = _role_of(req)
    if not role:
        return []
    return [u for u, types in _people(role, strict_mode, {} if cache is None else cache) if _fits(req, types)]


def _link_note(email):
    return "без цього виду" if payment_types.onec_user_for_email(email) else "не зв'язаний з користувачем 1С"


def _nobody(req, strict_mode, cache):
    """(код, текст причини), якщо заявку нікому з бухгалтерів оплатити, інакше None."""
    role = _role_of(req)
    if not role:
        return "no_role", "не визначено, який бухгалтер оплачує заявку"
    people = _people(role, strict_mode, cache)
    label = wf.ROLE_SHORT.get(role, role)
    if not people:
        return f"empty:{role}", f"у ролі «{label}» немає жодного користувача"
    if any(_fits(req, types) for _, types in people):
        return None
    details = [f"{u['name']} — {_link_note(u['email'])}" for u, _ in people[:5]]
    more = f" і ще {len(people) - 5}" if len(people) > 5 else ""
    return (f"type:{role}:{req['payment_account_id']}",
            f"вид оплати «{payment_accounts.label(req['payment_account_id'])}» у 1С не призначено жодному бухгалтеру "
            f"ролі «{label}» ({'; '.join(details)}{more})")


def nobody_reason(req, strict_mode=None, cache=None):
    """Чому заявку нікому з бухгалтерів оплатити (текст), або None — є кому."""
    found = _nobody(req, strict() if strict_mode is None else strict_mode, {} if cache is None else cache)
    return found[1] if found else None


def orphans(strict_mode=None, rows=None):
    """[(заявка, причина, код)] — заявки «До оплати», які нікому з бухгалтерів оплатити. strict_mode — для
    попереднього розрахунку в адмінці («що буде, якщо ввімкнути»); rows — уже завантажені рядки «До оплати»."""
    import storage as db
    strict_mode = strict() if strict_mode is None else strict_mode
    if rows is None:
        rows = db.list_requests_full("to_pay", wf.RoleSet({"cfo"}))
    cache, out = {}, []
    for r in rows:
        if r["status"] != "to_pay":
            continue
        found = _nobody(r, strict_mode, cache)
        if found:
            out.append((r, found[1], found[0]))
    return out


def accountants_without_onec():
    """Бухгалтери без зв'язку з користувачем 1С: у суворому режимі бачать лише заявки без виду оплати."""
    return [u for u in user_roles.all_users()
            if u["roles"] & wf.ACCOUNTANT_ROLES and not u["roles"] & UNRESTRICTED and u["email"]
            and not payment_types.onec_user_for_email(u["email"])]


# ---------------------------------------------------------------- пам'ять «уже повідомили»

_lock = threading.Lock()
_data = None


def _path():
    base = "/home/data" if os.environ.get("WEBSITE_SITE_NAME") else os.path.join(os.path.dirname(__file__), "data")
    return os.environ.get("PAY_ORPHANS_FILE") or os.path.join(base, "pay_orphans.json")


def _load():
    global _data
    if _data is None:
        try:
            _data = docstore.read("pay_orphans", _path())
        except (OSError, ValueError):
            _data = {}
    return _data


def reload():
    global _data
    with _lock:
        _data = None
        _load()


docstore.register("pay_orphans", reload)


def needs_alert(request_id, code):
    with _lock:
        return _load().get(str(request_id)) != code


def mark_alerted(request_id, code):
    with _lock:
        _load()[str(request_id)] = code
        docstore.write("pay_orphans", _path(), _data)


def forget(request_id):
    """Заявку вже є кому оплатити (або вона не «До оплати») — наступного разу «нікому» знову буде новим випадком."""
    with _lock:
        if _load().pop(str(request_id), None) is not None:
            docstore.write("pay_orphans", _path(), _data)


def forget_resolved(current_ids):
    with _lock:
        keep = {str(i) for i in current_ids}
        stale = [k for k in _load() if k not in keep]
        for k in stale:
            _data.pop(k)
        if stale:
            docstore.write("pay_orphans", _path(), _data)


# ---------------------------------------------------------------- повідомлення адміністраторам

def _alert(req, code, text):
    """Лист адміністраторам про заявку, яку нікому оплатити, — якщо про цей випадок ще не повідомляли.
    Розсилку вимкнено (чи тестовий режим) — не запам'ятовуємо: повідомимо, коли ввімкнуть."""
    if not needs_alert(req["id"], code):
        return False
    import notifications
    if notifications.send_pay_orphan(req, text) is None:
        return False
    mark_alerted(req["id"], code)
    return True


def check_now(req):
    """Заявка щойно змінилася (після дії чи зміни «Виду оплати»): «До оплати» й нікому оплатити — повідомити
    адміністраторів (раз на випадок); є кому або вже не «До оплати» — забути випадок. Помилки не ламають дію."""
    try:
        if not req or not req.get("id"):
            return
        found = _nobody(req, strict(), {}) if req.get("status") == "to_pay" else None
        if found:
            _alert(req, *found)
        else:
            forget(req["id"])
    except Exception:
        logger.exception("Не вдалося перевірити, чи є кому оплатити заявку")


def run_check():
    """Фонова перевірка (щогодини): ролі людей і доступи в 1С могли змінитися. Повертає номери заявок, про які
    щойно повідомлено адміністраторів."""
    docstore.sync()
    found = orphans()
    sent = [req["number"] for req, text, code in found if _alert(req, code, text)]
    forget_resolved([req["id"] for req, _, _ in found])
    return sent
