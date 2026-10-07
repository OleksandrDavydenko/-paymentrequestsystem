"""Процес погодження заявки: ролі, етапи, дозволені дії та історія.

Права перевіряються за МНОЖИНОЮ ролей користувача (ролей може бути кілька).
"""
from datetime import datetime

ROLES = {
    "admin": "Адміністратор",
    "initiator": "Ініціатор",
    "acc_cash": "Бухгалтер (готівка)",
    "acc_resident": "Бухгалтер (безготівка, резидент)",
    "acc_nonresident": "Бухгалтер (безготівка, нерезидент)",
    "cfo": "Фіндиректор",
}
# Короткі назви для заголовків таблиць
ROLE_SHORT = {
    **ROLES,
    "acc_cash": "Бух. готівка",
    "acc_resident": "Бух. резидент",
    "acc_nonresident": "Бух. нерезидент",
}

# Форму оплати вибирає ініціатор
PAYMENT_FORMS = {
    "cash": "Готівка",
    "bank": "Безготівка",
}
# Канал оплати = форма оплати + резидентність організації. Він визначає,
# який бухгалтер перевіряє й проводить оплату заявки.
CHANNELS = {
    "cash": "Готівка",
    "bank_resident": "Безготівка — резидент",
    "bank_nonresident": "Безготівка — нерезидент",
}
ACCOUNTANT_BY_CHANNEL = {
    "cash": "acc_cash",
    "bank_resident": "acc_resident",
    "bank_nonresident": "acc_nonresident",
}
ACCOUNTANT_ROLES = set(ACCOUNTANT_BY_CHANNEL.values())

# ID організацій-нерезидентів. Заповнюється з довідника організацій (mock_data, пізніше — 1С).
NONRESIDENT_ORGANIZATIONS = set()


def channel_of(req):
    """Готівка — завжди «cash»; безготівка — резидент/нерезидент за організацією заявки."""
    form = req.get("payment_form")
    if form == "cash":
        return "cash"
    if form == "bank":
        return "bank_nonresident" if req.get("organization_id") in NONRESIDENT_ORGANIZATIONS else "bank_resident"
    return None
APPROVER_ROLES = ACCOUNTANT_ROLES | {"cfo"}

# код -> (назва, css-клас бейджа)
STATUSES = {
    "draft": ("Чернетка", "grey"),
    "accountant": ("Перевірка бухгалтера", "amber"),
    "cfo": ("Погодження фіндиректора", "amber"),
    "to_pay": ("До оплати", "green"),
    "paid": ("Оплачено", "blue"),
    "rework": ("На доопрацюванні", "orange"),
    "rejected": ("Відхилено", "red"),
}
EDITABLE_STATUSES = {"draft", "rework"}
CLOSED_STATUSES = {"paid", "rejected"}

# Смуга маршруту в картці: (статус етапу, назва)
STAGES = [
    ("accountant", "Бухгалтер"),
    ("cfo", "Фіндиректор"),
    ("to_pay", "Оплата"),
]

# код дії -> (назва кнопки, запис в історії, css-клас, коментар обов'язковий)
ACTIONS = {
    "create": (None, "Створено", "grey", False),
    "submit": ("Відправити на погодження", "Відправлено на погодження", "blue", False),
    "approve": ("Погодити", "Погоджено", "green", False),
    "rework": ("Повернути на доопрацювання", "Повернуто на доопрацювання", "orange", True),
    "reject": ("Відхилити", "Відхилено", "red", True),
    "pay": ("Позначити оплаченою", "Оплачено", "blue", False),
    "file_add": (None, "Додано документ", "grey", False),
    "file_delete": (None, "Видалено документ", "grey", False),
}

# статус -> (хто діє: "author", "accountant" (за формою оплати) або роль, {дія: новий статус})
WORKFLOW = {
    "draft": ("author", {"submit": "accountant"}),
    "rework": ("author", {"submit": "accountant"}),
    "accountant": ("accountant", {"approve": "cfo", "rework": "rework", "reject": "rejected"}),
    "cfo": ("cfo", {"approve": "to_pay", "rework": "rework", "reject": "rejected"}),
    "to_pay": ("accountant", {"pay": "paid"}),
}


class WorkflowError(Exception):
    pass


def is_author(req, user_email):
    return req.get("author_email", "").lower() == (user_email or "").lower()


def stage_actor(req):
    """Хто має діяти на поточному етапі: "author", конкретна роль або None (закрита)."""
    actor, _ = WORKFLOW.get(req["status"], (None, {}))
    if actor == "accountant":
        return ACCOUNTANT_BY_CHANNEL.get(channel_of(req))
    return actor


def acting_role(req, roles, user_email):
    """Роль, якою користувач може діяти на поточному етапі, або None."""
    actor = stage_actor(req)
    if actor == "author":
        return "initiator" if "initiator" in roles and is_author(req, user_email) else None
    return actor if actor in roles else None


def in_queue(req, roles):
    """Чи чекає заявка на рішення однієї з ролей погоджувача (для «На погодження»).
    Етап «До оплати» сюди не входить — він у окремому розділі."""
    actor = stage_actor(req)
    return req["status"] != "to_pay" and actor in APPROVER_ROLES and actor in roles


def can_see_payments(roles):
    return bool(roles & (ACCOUNTANT_ROLES | {"cfo"}))


def in_payments(req, roles):
    """Розділ «До оплати»: фіндиректор бачить усі, бухгалтер — лише свою форму оплати."""
    return req["status"] == "to_pay" and ("cfo" in roles or stage_actor(req) in roles)


def can_view(req, roles, user_email):
    # Адміністратор бачить усі заявки (лише перегляд — дії визначаються іншими ролями)
    return is_author(req, user_email) or bool(roles & (APPROVER_ROLES | {"admin"}))


def can_edit(req, roles, user_email):
    return "initiator" in roles and is_author(req, user_email) and req["status"] in EDITABLE_STATUSES


def can_attach(req, roles, user_email):
    if req["status"] in CLOSED_STATUSES:
        return False
    return ("initiator" in roles and is_author(req, user_email)) or acting_role(req, roles, user_email) is not None


def available_actions(req, roles, user_email):
    if acting_role(req, roles, user_email) is None:
        return []
    return list(WORKFLOW[req["status"]][1])


def add_history(req, action, user, role, comment="", from_status=None, to_status=None):
    req.setdefault("history", []).append({
        "at": datetime.now(),
        "user_name": user["name"],
        "user_email": user["email"],
        "role": role,
        "action": action,
        "from_status": from_status,
        "to_status": to_status,
        "comment": comment,
    })


def history_role(req, roles, user_email):
    """Роль для запису в історію, коли дія не є кроком процесу (напр. додано файл)."""
    role = acting_role(req, roles, user_email)
    if role:
        return role
    if "initiator" in roles and is_author(req, user_email):
        return "initiator"
    return next((r for r in (*sorted(ACCOUNTANT_ROLES), "cfo", "initiator", "admin") if r in roles), "initiator")


def apply_action(req, action, roles, user, comment=""):
    """Виконати дію процесу над заявкою (змінює req). Кидає WorkflowError."""
    comment = (comment or "").strip()
    role = acting_role(req, roles, user["email"])
    if role is None or action not in WORKFLOW[req["status"]][1]:
        raise WorkflowError("Ця дія недоступна для вашої ролі на поточному етапі")
    if ACTIONS[action][3] and not comment:
        raise WorkflowError("Вкажіть коментар — що саме потрібно виправити або чому заявку відхилено")
    old = req["status"]
    req["status"] = WORKFLOW[old][1][action]
    add_history(req, action, user, role, comment, old, req["status"])


def stage_names(req):
    """Назви етапів для смуги маршруту; етап бухгалтера — за формою оплати."""
    accountant = ROLES.get(ACCOUNTANT_BY_CHANNEL.get(channel_of(req)), "Бухгалтер")
    return [(code, accountant if code == "accountant" else name) for code, name in STAGES]


def stage_states(req):
    """Стан кожного етапу для смуги маршруту: done / current / returned / todo / rejected."""
    stages = stage_names(req)
    order = [code for code, _ in stages]
    status = req["status"]
    if status == "paid":
        return [(name, "done") for _, name in stages]
    if status in order:
        idx = order.index(status)
        return [(name, "done" if i < idx else "current" if i == idx else "todo")
                for i, (_, name) in enumerate(stages)]
    # Для rework/rejected — показати, на якому етапі це сталося
    last = next((h for h in reversed(req.get("history", []))
                 if h["to_status"] == status and h["from_status"] in order), None)
    idx = order.index(last["from_status"]) if last else -1
    mark = "returned" if status == "rework" else "rejected"
    return [(name, "done" if i < idx else mark if i == idx else "todo")
            for i, (_, name) in enumerate(stages)]
