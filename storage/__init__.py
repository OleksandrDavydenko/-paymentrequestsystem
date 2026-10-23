"""Сховище заявок для маршрутів app.py.

Робочий режим (є база й адмін не ввімкнув «Тестові дані») — storage/sql.py, інакше — демо в пам'яті
(storage/memory.py: тестовий режим, локальний запуск і тести без бази). Що створено в тестовому режимі,
у базу не потрапляє.

Списки: scope_rows(список, ролі, пошта) — легкі рядки (storage/derive.py) у порядку списку; повні заявки
для рядків сторінки — get_many(ids). Назви списків і правила — storage/scopes.py.
"""
import app_settings
import db_sql
import workflow

from . import derive, memory, scopes, sql  # noqa: F401
from .demo import _REQUESTS, INVOICES  # noqa: F401  (_REQUESTS — демо-заявки, для тестів)
from .errors import StaleRequestError  # noqa: F401

PAYMENT_FORMS = workflow.PAYMENT_FORMS
STATUSES = workflow.STATUSES


def work_mode():
    """Чи працює система з робочою базою (а не з тестовими даними)."""
    return db_sql.configured() and app_settings.get("data_mode") != "test"


def _store():
    return sql if work_mode() else memory


def get_request(request_id):
    return _store().get_request(request_id)


def get_many(ids):
    return _store().get_many(list(ids))


def save_request(req):
    """Зберегти заявку. Без 'id' — створити нову (номер присвоюється тут). Повертає id; req["version"]
    оновлюється. StaleRequestError — заявку вже змінив хтось інший після того, як її прочитали."""
    return _store().save_request(req)


def last_id():
    return _store().last_id()


def scope_rows(scope, roles=frozenset(), email="", period=None):
    return _store().scope_rows(scope, roles, email, period)


def count(scope, roles=frozenset(), email=""):
    return _store().count(scope, roles, email)


def find_attachment(file_id):
    """Повертає (заявка, метадані файлу) або (None, None)."""
    return _store().find_attachment(file_id)


def last_accountant_type(email):
    """Тип бухгалтера з найсвіжішої заявки автора, де його вибрано (режим «Бухгалтер»)."""
    return _store().last_accountant_type(email)


def first_request_with_counterparty(cp_id):
    return _store().first_request_with_counterparty(cp_id)


def invoice_usage(cp_id, numbers, exclude_id=None):
    """У яких заявках у роботі (не відхилених і не скасованих) уже є рахунки цього контрагента: {рахунок: [№ заявок]}."""
    return _store().invoice_usage(cp_id, list(numbers), exclude_id)


def reindex():
    """Перерахувати похідні колонки в базі (після зміни налаштувань організацій / довідників)."""
    return sql.reindex() if work_mode() else 0


def _update_each(ids, change):
    """Змінити кілька заявок (кожну — окремим збереженням; при одночасній зміні — ще одна спроба)."""
    count_ = 0
    for rid in ids:
        for _ in range(3):
            req = get_request(rid)
            if not req or not change(req):
                break
            try:
                save_request(req)
                count_ += 1
                break
            except StaleRequestError:
                continue
    return count_


def replace_counterparty(old_id, new_id, user, role, comment):
    """Прив'язка тимчасового контрагента до 1С: усі заявки з old_id отримують new_id. Повертає кількість."""
    def change(r):
        if r.get("counterparty_id") != old_id:
            return False
        r["counterparty_id"] = new_id
        workflow.add_history(r, "cp_link", user, role, comment, r["status"], r["status"])
        return True
    return _update_each(_store().ids_with_counterparty(old_id), change)


def remap_departments(mapping):
    """Замінити старі коди відділів у заявках ({старий: новий}). Повертає кількість заявок."""
    def change(r):
        if r.get("department") not in mapping:
            return False
        r["department"] = mapping[r["department"]]
        return True
    return _update_each(_store().ids_with_departments(set(mapping)), change)


# ---------------------------------------------------------------- повні заявки списком (тести, дрібні вибірки)

def list_requests_full(scope, roles=frozenset(), email=""):
    """Повні заявки списку (для невеликих списків «в роботі» й тестів)."""
    rows = scope_rows(scope, roles, email)
    full = get_many([r["id"] for r in rows])
    return [full[r["id"]] for r in rows if r["id"] in full]


def list_requests(author_email, status=None):
    return [r for r in list_requests_full("author", email=author_email) if not status or r["status"] == status]


def list_participating(email):
    return list_requests_full("participant", email=email)


def list_queue(roles, user_email=""):
    return list_requests_full("queue", roles, user_email)


def list_at_accountants():
    return list_requests_full("at_accountants")


def list_all():
    return list_requests_full("all")


def list_processed(roles):
    return list_requests_full("processed", roles)


def list_paid(roles):
    return list_requests_full("paid", roles)


def list_to_pay(roles):
    return list_requests_full("to_pay", roles)
