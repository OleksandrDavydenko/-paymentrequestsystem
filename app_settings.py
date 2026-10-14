"""Налаштування роботи системи, які змінює адміністратор (наприклад, режим погодження).

У базі даних (docstore), а без бази — JSON-файл (/home/data на Azure).
Кожна зміна запам'ятовує, хто і коли її зробив.
"""
import os
import threading

import clock
import docstore

DEFAULTS = {
    "approval_mode": "sequential",
    "mail_enabled": False,
    "mail_sender": "",
    "mail_sender_name": "Система заявок на оплату",
    "smtp_host": "smtp.gmail.com",
    "smtp_port": 587,
    "smtp_security": "starttls",
    "smtp_username": "",
    "smtp_password": "",  # зашифрований (secret_box)
    "allow_custom_email": False,  # користувачі можуть вказати іншу пошту для сповіщень
    "dept_head_mode": "view",  # керівник відділу: view — лише бачить заявки відділу, approve — погоджує першим
    "pending_cp_payment": "block",  # контрагент, якого ще немає в 1С: block — не можна «Оплачено», allow — з попередженням
    # Організації, які НЕ можна вибирати в заявках (коди). Порожньо — дозволені всі, зокрема нові з 1С.
    "disabled_organizations": [],
    # Хто з бухгалтерів веде організацію: {код: {"cash": роль, "bank": роль}}; немає — автоматично за правилом
    "org_accountants": {},
    # Бухгалтер обов'язково вибирає «Вид оплати» при погодженні (і перед оплатою), якщо йому є що вибрати
    "require_payment_account": True,
    # Готівка через автора заявки: після «Оплачено» автор підтверджує, що передав гроші підряднику
    "cash_handover_confirm": True,
    "cash_handover_remind_days": 1,  # нагадувати автору кожні N днів (0 — не нагадувати)
    "cfo_mode": "approve",  # фіндиректор: approve — погоджує, observe — лише спостерігає (погоджує тільки бухгалтер)
    "cp_onec_owner": "accountant",  # хто заводить нового контрагента в 1С і прив'язує в заявці: accountant | admin
    "data_mode": "work",
    "org_field_mode": "organization",  # що вибирає ініціатор: organization — організацію, payer — лише платника  # що показує система, коли підключено базу: work — робочі заявки, test — демо-дані
}

_lock = threading.Lock()
_data = None


def _path():
    base = "/home/data" if os.environ.get("WEBSITE_SITE_NAME") else os.path.join(os.path.dirname(__file__), "data")
    return os.environ.get("APP_SETTINGS_FILE") or os.path.join(base, "app_settings.json")


def _load():
    global _data
    if _data is None:
        try:
            _data = docstore.read("app_settings", _path())
        except (OSError, ValueError):
            _data = {}
    return _data


def reload():
    global _data
    with _lock:
        _data = None
        _load()


def get(key):
    with _lock:
        entry = _load().get(key)
    return entry["value"] if entry else DEFAULTS.get(key)


def info(key):
    """Значення разом із тим, хто і коли його змінив."""
    with _lock:
        entry = dict(_load().get(key) or {})
    entry.setdefault("value", DEFAULTS.get(key))
    return entry


def set(key, value, updated_by):
    with _lock:
        _load()[key] = {"value": value, "updated_by": updated_by,
                        "updated_at": clock.now().isoformat(timespec="seconds")}
        docstore.write("app_settings", _path(), _data)
