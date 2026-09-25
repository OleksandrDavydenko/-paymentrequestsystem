"""Процес погодження заявки: ролі, етапи, дозволені дії та історія."""
from datetime import datetime

ROLES = {
    "initiator": "Ініціатор",
    "head": "Керівник відділу",
    "accountant": "Бухгалтер",
    "cfo": "Фіндиректор",
}
APPROVER_ROLES = {"head", "accountant", "cfo"}

# код -> (назва, css-клас бейджа)
STATUSES = {
    "draft": ("Чернетка", "grey"),
    "head": ("Погодження керівника", "amber"),
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
    ("head", "Керівник відділу"),
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

# статус -> (хто діє: "author" або роль, {дія: новий статус})
WORKFLOW = {
    "draft": ("author", {"submit": "head"}),
    "rework": ("author", {"submit": "head"}),
    "head": ("head", {"approve": "accountant", "rework": "rework", "reject": "rejected"}),
    "accountant": ("accountant", {"approve": "cfo", "rework": "rework", "reject": "rejected"}),
    "cfo": ("cfo", {"approve": "to_pay", "rework": "rework", "reject": "rejected"}),
    "to_pay": ("accountant", {"pay": "paid"}),
}


class WorkflowError(Exception):
    pass


def is_author(req, user_email):
    return req.get("author_email", "").lower() == (user_email or "").lower()


def is_current_actor(req, role, user_email):
    actor, _ = WORKFLOW.get(req["status"], (None, {}))
    if actor == "author":
        return role == "initiator" and is_author(req, user_email)
    return actor is not None and actor == role


def can_view(req, role, user_email):
    return is_author(req, user_email) or role in APPROVER_ROLES


def can_edit(req, role, user_email):
    return role == "initiator" and is_author(req, user_email) and req["status"] in EDITABLE_STATUSES


def can_attach(req, role, user_email):
    if req["status"] in CLOSED_STATUSES:
        return False
    return (role == "initiator" and is_author(req, user_email)) or is_current_actor(req, role, user_email)


def available_actions(req, role, user_email):
    if not is_current_actor(req, role, user_email):
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


def apply_action(req, action, role, user, comment=""):
    """Виконати дію процесу над заявкою (змінює req). Кидає WorkflowError."""
    comment = (comment or "").strip()
    if action not in available_actions(req, role, user["email"]):
        raise WorkflowError("Ця дія недоступна для вашої ролі на поточному етапі")
    if ACTIONS[action][3] and not comment:
        raise WorkflowError("Вкажіть коментар — що саме потрібно виправити або чому заявку відхилено")
    old = req["status"]
    req["status"] = WORKFLOW[old][1][action]
    add_history(req, action, user, role, comment, old, req["status"])


def stage_states(req):
    """Стан кожного етапу для смуги маршруту: done / current / returned / todo / rejected."""
    order = [code for code, _ in STAGES]
    status = req["status"]
    if status == "paid":
        return [(name, "done") for _, name in STAGES]
    if status in order:
        idx = order.index(status)
        return [(name, "done" if i < idx else "current" if i == idx else "todo")
                for i, (_, name) in enumerate(STAGES)]
    # Для rework/rejected — показати, на якому етапі це сталося
    last = next((h for h in reversed(req.get("history", []))
                 if h["to_status"] == status and h["from_status"] in order), None)
    idx = order.index(last["from_status"]) if last else -1
    mark = "returned" if status == "rework" else "rejected"
    return [(name, "done" if i < idx else mark if i == idx else "todo")
            for i, (_, name) in enumerate(STAGES)]
