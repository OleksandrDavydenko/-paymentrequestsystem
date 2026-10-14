"""Процес погодження заявки: ролі, етапи, дозволені дії та історія.

Права перевіряються за МНОЖИНОЮ ролей користувача (ролей може бути кілька).
"""

import clock
import organizations

ROLES = {
    "admin": "Адміністратор",
    "initiator": "Ініціатор",
    "acc_cash": "Бухгалтер (готівка)",
    "acc_resident": "Бухгалтер (безготівка, резидент)",
    "acc_nonresident": "Бухгалтер (безготівка, нерезидент)",
    "cfo": "Фіндиректор",
    "dept_head": "Керівник відділу",
    # Ролі з оргструктури та заявки (не групи Azure) — для історії й підписів
    "dept_viewer": "Перегляд заявок відділу",
    "participant": "Учасник заявки",
}
# Короткі назви для заголовків таблиць
ROLE_SHORT = {
    **ROLES,
    "acc_cash": "Бух. готівка",
    "acc_resident": "Бух. резидент",
    "acc_nonresident": "Бух. нерезидент",
    "dept_head": "Кер. відділу",
}


# Пояснення ролей для картки користувача в адмінці
ROLE_HINTS = {
    "admin": "Налаштування системи й користувачів, бачить усі заявки, може змінити статус будь-якої заявки",
    "initiator": "Створює заявки на оплату, бачить свої заявки та ті, де він учасник",
    "acc_cash": "Перевіряє й оплачує заявки з оплатою готівкою (будь-яка організація)",
    "acc_resident": "Перевіряє й оплачує безготівкові заявки організацій-резидентів (напр. ФТП)",
    "acc_nonresident": "Перевіряє й оплачує безготівкові заявки організацій-нерезидентів (напр. Litbia)",
    "cfo": "Погоджує заявки; бачить «До оплати» й «Оплачені» всіх бухгалтерів",
}
ROLE_GROUPS = [
    ("Доступ", ["admin", "initiator"]),
    ("Погодження та оплата", ["acc_cash", "acc_resident", "acc_nonresident", "cfo"]),
]


class RoleSet(frozenset):
    """Ролі користувача + коди відділів, які він очолює і заявки яких бачить.

    Поводиться як звичайна множина ролей, тож усі перевірки `"cfo" in roles` працюють як раніше.
    """
    def __new__(cls, roles=(), head_departments=(), view_departments=()):
        obj = super().__new__(cls, roles)
        obj.head_departments = frozenset(head_departments)
        obj.view_departments = frozenset(view_departments)
        return obj


def head_deps(roles):
    return getattr(roles, "head_departments", frozenset())


def view_deps(roles):
    return getattr(roles, "view_departments", frozenset())


def sees_department(req, roles):
    """Чи бачить користувач заявки відділу автора (як керівник або як «бачить заявки відділу»)."""
    dep = req.get("department")
    return bool(dep) and dep in (head_deps(roles) | view_deps(roles))


def is_participant(req, user_email):
    email = (user_email or "").lower()
    return bool(email) and any(p["email"].lower() == email for p in req.get("participants") or [])


def heads_department(req, roles):
    """Чи очолює користувач відділ автора заявки."""
    return "dept_head" in roles and bool(req.get("department")) and req["department"] in head_deps(roles)

# Форму оплати вибирає ініціатор
PAYMENT_FORMS = {
    "cash": "Готівка",
    "bank": "Безготівка",
}
# Хто отримує готівку в касі (для форми «Готівка», якщо в адмінці ввімкнено підтвердження передачі)
CASH_RECEIVERS = {
    "contractor": "Підрядник отримує сам у касі",
    "author": "Я отримую й передаю підряднику",
}
HANDOVER_MAX_REMINDERS = 5
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



def accountant_role(req):
    """Хто з бухгалтерів перевіряє й оплачує заявку: призначений організації в адмінці
    для цієї форми оплати, а якщо не призначено — за правилом (форма оплати + резидентність)."""
    form = req.get("payment_form")
    override = organizations.accountant_for(req.get("organization_id"), form) if form else None
    if override in ACCOUNTANT_ROLES:
        return override
    return ACCOUNTANT_BY_CHANNEL.get(channel_of(req))


def channel_of(req):
    """Готівка — завжди «cash»; безготівка — резидент/нерезидент за організацією заявки."""
    form = req.get("payment_form")
    if form == "cash":
        return "cash"
    if form == "bank":
        return "bank_nonresident" if organizations.is_nonresident(req.get("organization_id")) else "bank_resident"
    return None
APPROVER_ROLES = ACCOUNTANT_ROLES | {"cfo", "dept_head"}

# код -> (назва, css-клас бейджа)
STATUSES = {
    "draft": ("Чернетка", "grey"),
    "dept_head": ("Погодження керівника відділу", "amber"),
    "approval": ("На погодженні", "amber"),  # паралельний режим: бухгалтер і фіндиректор одночасно
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
    ("dept_head", "Керівник відділу"),  # лише для заявок з кроком керівника (req["head_step"])
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
    "admin_status": (None, "Статус змінено адміністратором", "purple", True),
    "comment": (None, "Коментар", "slate", True),
    "auto_skip": (None, "Крок пропущено", "grey", False),
    "participant_add": (None, "Додано учасника", "grey", False),
    "participant_remove": (None, "Прибрано учасника", "grey", False),
    "cp_link": (None, "Контрагента прив'язано до 1С", "grey", False),
    "pay_account": (None, "Вид оплати", "grey", False),  # бачать лише бухгалтери й фіндиректор
    "cash_receiver": (None, "Хто отримує готівку", "grey", False),
    "handover": (None, "Готівку передано підряднику", "green", False),
    "handover_undo": (None, "Підтвердження передачі скасовано", "purple", True),
}
# Записи історії, які бачать лише ті, хто бачить «Вид оплати»
PAYMENT_ACCOUNT_ACTIONS = {"pay_account"}

# Режими погодження (налаштовуються в адмінці, запам'ятовуються в заявці при відправці)
APPROVAL_MODES = {
    "sequential": "Послідовно: Бухгалтер → Фіндиректор → Оплата",
    "parallel": "Паралельно: бухгалтер і фіндиректор одночасно → Оплата",
}
# Роль керівника відділу (налаштовується в адмінці, запам'ятовується в заявці при відправці)
HEAD_MODES = {
    "view": "Лише бачить: керівник бачить усі заявки свого відділу, але не погоджує їх",
    "approve": "Погоджує першим кроком: заявка спершу йде керівнику відділу автора",
}
SYSTEM_USER = {"name": "Система", "email": ""}


def route_label(mode, head_mode):
    """Підсумок маршруту для адмінки: «Керівник відділу → Бухгалтер → Фіндиректор → Оплата»."""
    steps = ["Керівник відділу"] if head_mode == "approve" else []
    steps += ["Бухгалтер + Фіндиректор (одночасно)"] if mode == "parallel" else ["Бухгалтер", "Фіндиректор"]
    return " → ".join(steps + ["Оплата"])
MAX_COMMENT = 2000

# статус -> (хто діє: "author", "accountant" (за формою оплати) або роль, {дія: новий статус})
WORKFLOW = {
    "draft": ("author", {"submit": "accountant"}),
    "rework": ("author", {"submit": "accountant"}),
    "accountant": ("accountant", {"approve": "cfo", "rework": "rework", "reject": "rejected"}),
    "cfo": ("cfo", {"approve": "to_pay", "rework": "rework", "reject": "rejected"}),
    # Керівник відділу: «approve» веде до бухгалтера або до паралельного погодження (див. apply_action)
    "dept_head": ("dept_head", {"approve": "accountant", "rework": "rework", "reject": "rejected"}),
    # Паралельно: «approve» веде до to_pay лише коли погодили всі (див. apply_action)
    "approval": ("parallel", {"approve": "to_pay", "rework": "rework", "reject": "rejected"}),
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
        return accountant_role(req)
    return actor


def parallel_roles(req):
    """Хто погоджує в паралельному режимі: бухгалтер каналу оплати і фіндиректор."""
    return [r for r in (accountant_role(req), "cfo") if r]


def pending_roles(req):
    """Паралельні погоджувачі, які ще не погодили заявку."""
    done = req.get("approvals") or {}
    return [r for r in parallel_roles(req) if r not in done]


def acting_role(req, roles, user_email):
    """Роль, якою користувач може діяти на поточному етапі, або None."""
    if req["status"] == "approval":
        return next((r for r in pending_roles(req) if r in roles), None)
    actor = stage_actor(req)
    if actor == "author":
        return "initiator" if "initiator" in roles and is_author(req, user_email) else None
    if actor == "dept_head":  # лише керівник відділу автора
        return actor if heads_department(req, roles) else None
    return actor if actor in roles else None


def in_queue(req, roles):
    """Чи чекає заявка на рішення однієї з ролей погоджувача (для «На погодження»).
    Етап «До оплати» сюди не входить — він у окремому розділі."""
    if req["status"] == "approval":
        return any(r in roles for r in pending_roles(req))
    actor = stage_actor(req)
    if actor == "dept_head":
        return heads_department(req, roles)
    return req["status"] != "to_pay" and actor in APPROVER_ROLES and actor in roles


def can_see_payment_account(roles):
    """«Вид оплати» (з чого платимо) бачать і змінюють лише бухгалтери, фіндиректор і адміністратор."""
    return bool(roles & (ACCOUNTANT_ROLES | {"cfo", "admin"}))


def history_visible(entry, roles):
    return entry["action"] not in PAYMENT_ACCOUNT_ACTIONS or can_see_payment_account(roles)


def visible_history(req, roles):
    return [h for h in req.get("history") or [] if history_visible(h, roles)]


def set_payment_account(req, account_id, account_label, old_label, user, role):
    """Змінити «Вид оплати» із записом в історії (видно лише бухгалтерам і фіндиректору)."""
    if (req.get("payment_account_id") or None) == (account_id or None):
        return False
    req["payment_account_id"] = account_id or None
    add_history(req, "pay_account", user, role, f"{old_label or '—'} → {account_label or '—'}",
                req["status"], req["status"])
    return True


def can_see_payments(roles):
    return bool(roles & (ACCOUNTANT_ROLES | {"cfo"}))


def in_payments(req, roles):
    """Розділ «До оплати»: фіндиректор бачить усі, бухгалтер — лише свою форму оплати."""
    return req["status"] == "to_pay" and ("cfo" in roles or stage_actor(req) in roles)


DECISION_ACTIONS = {"approve", "rework", "reject", "pay"}


def processed_entry(req, roles):
    """Останнє рішення, ухвалене однією з ролей погоджувача (для «Опрацьовані»), або None."""
    mine = roles & APPROVER_ROLES
    if "dept_head" in mine and not heads_department(req, roles):
        mine = mine - {"dept_head"}  # рішення керівників інших відділів — не «мої»
    return next((h for h in reversed(req.get("history", []))
                 if h["action"] in DECISION_ACTIONS and h["role"] in mine), None)


def in_paid(req, roles):
    """Оплачені заявки: фіндиректор бачить усі, бухгалтер — свого каналу оплати."""
    return req["status"] == "paid" and (
        "cfo" in roles or accountant_role(req) in roles)


def paid_entry(req):
    """Запис історії, яким заявку позначено оплаченою (останній), або None."""
    return next((h for h in reversed(req.get("history", []))
                 if h.get("to_status") == "paid"), None)


def admin_set_status(req, new_status, user, comment, handover=False):
    """Адміністратор вручну змінює статус будь-якої заявки. Коментар обов'язковий.
    handover — чи ввімкнено підтвердження передачі готівки (для переходу в «Оплачено»)."""
    comment = (comment or "").strip()
    if new_status not in STATUSES:
        raise WorkflowError("Невідомий статус")
    if new_status == req["status"]:
        raise WorkflowError("Заявка вже має цей статус")
    if not comment:
        raise WorkflowError("Вкажіть причину зміни статусу — вона буде видна в історії заявки")
    old = req["status"]
    req["status"] = new_status
    req["approvals"] = {}
    if new_status == "dept_head":
        req["head_step"] = True
        req.setdefault("route_mode", "sequential")
    elif new_status == "approval":
        req["route_mode"] = "parallel"
    elif new_status in ("accountant", "cfo"):
        req["route_mode"] = "sequential"
    start_handover(req, handover and new_status == "paid")
    add_history(req, "admin_status", user, "admin", comment, old, new_status)


# ---------------------------------------------------------------- готівка через автора заявки

def handover_applies(req):
    """Готівку отримує автор і сам передає підряднику — після оплати потрібне його підтвердження."""
    return req.get("payment_form") == "cash" and req.get("cash_receiver") == "author"


def start_handover(req, enabled):
    """Після «Оплачено»: заявка чекає підтвердження передачі (або не чекає, якщо не потрібно)."""
    req.pop("handover", None)
    if enabled and req["status"] == "paid" and handover_applies(req):
        req["handover"] = {"status": "pending", "since": clock.today(), "reminders": 0, "last_reminder": None}


def needs_handover(req):
    return req.get("status") == "paid" and (req.get("handover") or {}).get("status") == "pending"


def handover_done(req):
    return req.get("status") == "paid" and (req.get("handover") or {}).get("status") == "done"


def handover_days(req, today=None):
    """Скільки днів минуло від видачі готівки."""
    h = req.get("handover") or {}
    return ((today or clock.today()) - h["since"]).days if h.get("since") else 0


def is_handover_accountant(req, roles):
    """Бухгалтер, який оплачує цю готівкову заявку (по готівці або призначений організації)."""
    return accountant_role(req) in roles


def can_confirm_handover(req, roles, user_email):
    """Підтвердити передачу: автор, а за нього — бухгалтер цієї заявки або адміністратор."""
    return needs_handover(req) and (is_author(req, user_email) or is_handover_accountant(req, roles)
                                    or "admin" in roles)


def can_change_cash_receiver(req, roles):
    """Виправити «Хто отримує готівку» до оплати: бухгалтер цієї заявки або адміністратор."""
    return (req.get("payment_form") == "cash" and req["status"] not in ("draft", "paid", "rejected")
            and (is_handover_accountant(req, roles) or "admin" in roles))


def set_cash_receiver(req, value, user, role):
    if value not in CASH_RECEIVERS:
        raise WorkflowError("Оберіть, хто отримує готівку")
    if req.get("cash_receiver") == value:
        return False
    old = CASH_RECEIVERS.get(req.get("cash_receiver"), "—")
    req["cash_receiver"] = value
    add_history(req, "cash_receiver", user, role, f"{old} → {CASH_RECEIVERS[value]}", req["status"], req["status"])
    return True


def confirm_handover(req, user, roles, when, comment=""):
    """Автор (або за нього бухгалтер / адмін) підтверджує, що передав готівку підряднику. Кидає WorkflowError."""
    if not needs_handover(req):
        raise WorkflowError("Ця заявка не чекає підтвердження передачі готівки")
    if not can_confirm_handover(req, roles, user["email"]):
        raise WorkflowError("Підтвердити передачу може автор заявки, бухгалтер по готівці або адміністратор")
    comment = (comment or "").strip()
    if len(comment) > MAX_COMMENT:
        raise WorkflowError(f"Коментар задовгий (максимум {MAX_COMMENT} символів)")
    h = req["handover"]
    if when is None:
        raise WorkflowError("Вкажіть дату передачі")
    if when < h["since"]:
        raise WorkflowError(f"Дата передачі не може бути раніше видачі готівки ({h['since']:%d.%m.%Y})")
    if when > clock.today():
        raise WorkflowError("Дата передачі не може бути в майбутньому")
    if is_author(req, user["email"]):
        role = "initiator"
    elif is_handover_accountant(req, roles):
        role = accountant_role(req)
    else:
        role = "admin"
    h.update({"status": "done", "date": when, "by_name": user["name"], "by_email": user["email"],
              "at": clock.now(), "comment": comment, "on_behalf": role != "initiator"})
    text = f"Передано {when:%d.%m.%Y}"
    if role != "initiator":
        text += " (підтверджено за автора)"
    add_history(req, "handover", user, role, text + (f". {comment}" if comment else ""), "paid", "paid")


def undo_handover(req, user, comment):
    """Адміністратор скасовує підтвердження (помилково натиснули). Коментар обов'язковий."""
    comment = (comment or "").strip()
    if not handover_done(req):
        raise WorkflowError("Передачу готівки ще не підтверджено")
    if not comment:
        raise WorkflowError("Вкажіть причину скасування — вона буде видна в історії заявки")
    h = req["handover"]
    h.update({"status": "pending", "date": None, "last_reminder": clock.today()})
    add_history(req, "handover_undo", user, "admin", comment, "paid", "paid")


def handover_reminder_due(req, every_days, today=None):
    """Чи час нагадати автору підтвердити передачу (кожні every_days днів, не більше HANDOVER_MAX_REMINDERS)."""
    if not needs_handover(req) or not every_days or every_days <= 0:
        return False
    h = req["handover"]
    if h.get("reminders", 0) >= HANDOVER_MAX_REMINDERS:
        return False
    last = h.get("last_reminder") or h["since"]
    return ((today or clock.today()) - last).days >= every_days


def mark_reminded(req, today=None):
    h = req["handover"]
    h["reminders"] = h.get("reminders", 0) + 1
    h["last_reminder"] = today or clock.today()


def add_comment(req, user, role, text):
    """Коментар без зміни статусу. Кидає WorkflowError для порожнього / задовгого тексту."""
    text = (text or "").strip()
    if not text:
        raise WorkflowError("Напишіть текст коментаря")
    if len(text) > MAX_COMMENT:
        raise WorkflowError(f"Коментар задовгий (максимум {MAX_COMMENT} символів)")
    add_history(req, "comment", user, role, text, req["status"], req["status"])


def can_view(req, roles, user_email):
    # Адміністратор бачить усі заявки (лише перегляд — дії визначаються іншими ролями).
    # Учасник — заявки, до яких його додали (і чернетки теж).
    # Керівник і ті, хто бачить заявки відділу, — заявки свого відділу (крім чернеток).
    if is_author(req, user_email) or is_participant(req, user_email):
        return True
    if roles & (ACCOUNTANT_ROLES | {"cfo", "admin"}):
        return True
    return in_department(req, roles)


def in_department(req, roles):
    """Розділ «Заявки відділу»: заявки відділів, які користувач очолює або бачить (без чернеток)."""
    return sees_department(req, roles) and req["status"] != "draft"


def can_manage_participants(req, roles, user_email):
    """Додавати й прибирати учасників може автор заявки та адміністратор."""
    return ("initiator" in roles and is_author(req, user_email)) or "admin" in roles


def add_participant(req, person, user, role):
    """Додати учасника {"email", "name"}. Кидає WorkflowError."""
    email = (person.get("email") or "").strip()
    if not email:
        raise WorkflowError("Оберіть користувача зі списку")
    if is_author(req, email):
        raise WorkflowError("Це автор заявки — він і так її бачить")
    if is_participant(req, email):
        raise WorkflowError(f"{person.get('name') or email} уже є учасником заявки")
    req.setdefault("participants", []).append({
        "email": email, "name": person.get("name") or email,
        "added_by": user["name"], "added_at": clock.now(),
    })
    add_history(req, "participant_add", user, role, person.get("name") or email, req["status"], req["status"])


def remove_participant(req, email, user, role):
    """Прибрати учасника за поштою. Повертає його ім'я. Кидає WorkflowError."""
    found = next((p for p in req.get("participants") or [] if p["email"].lower() == (email or "").lower()), None)
    if not found:
        raise WorkflowError("Такого учасника в заявці немає")
    req["participants"] = [p for p in req["participants"] if p is not found]
    add_history(req, "participant_remove", user, role, found["name"], req["status"], req["status"])
    return found["name"]


def can_edit(req, roles, user_email):
    return "initiator" in roles and is_author(req, user_email) and req["status"] in EDITABLE_STATUSES


def can_attach(req, roles, user_email):
    if req["status"] in CLOSED_STATUSES:
        return False
    return (("initiator" in roles and is_author(req, user_email)) or is_participant(req, user_email)
            or acting_role(req, roles, user_email) is not None)


def available_actions(req, roles, user_email):
    if acting_role(req, roles, user_email) is None:
        return []
    return list(WORKFLOW[req["status"]][1])


def add_history(req, action, user, role, comment="", from_status=None, to_status=None):
    req.setdefault("history", []).append({
        "at": clock.now(),
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
    if is_participant(req, user_email):
        return "participant"
    if heads_department(req, roles):
        return "dept_head"
    if sees_department(req, roles):
        return "dept_viewer"
    return next((r for r in (*sorted(ACCOUNTANT_ROLES), "cfo", "initiator", "admin") if r in roles),
                "initiator")


def apply_action(req, action, roles, user, comment="", mode="sequential", head_mode="view", heads=(), guard=None):
    """Виконати дію процесу над заявкою (змінює req). Кидає WorkflowError.

    mode / head_mode — режим погодження і роль керівника відділу з налаштувань; враховуються
    лише при відправці (submit) і запам'ятовуються в заявці, щоб зміна налаштувань не ламала
    заявки в процесі. heads — пошта керівників відділу заявки (для submit).
    guard — {"cp_pending": контрагента ще немає в 1С, "block_pending": тоді не можна оплатити,
             "account_required": чи обов'язковий «Вид оплати» (є рахунки / каси організації в довіднику),
             "handover": чи ввімкнено підтвердження передачі готівки підряднику (для pay)}.
    """
    comment = (comment or "").strip()
    role = acting_role(req, roles, user["email"])
    if role is None or action not in WORKFLOW[req["status"]][1]:
        raise WorkflowError("Ця дія недоступна для вашої ролі на поточному етапі")
    if ACTIONS[action][3] and not comment:
        raise WorkflowError("Вкажіть коментар — що саме потрібно виправити або чому заявку відхилено")
    # «Вид оплати» обов'язковий, якщо в довіднику є рахунки / каси організації заявки
    account_required = (guard or {}).get("account_required", True)
    if action == "approve" and role in ACCOUNTANT_ROLES and account_required and not req.get("payment_account_id"):
        raise WorkflowError("Перед погодженням оберіть «Вид оплати» — з якого рахунку чи каси платимо")
    if action == "pay":
        if account_required and not req.get("payment_account_id"):
            raise WorkflowError("Оберіть «Вид оплати» — з якого рахунку чи каси платимо")
        if guard and guard.get("cp_pending") and guard.get("block_pending"):
            raise WorkflowError("Контрагента ще немає в 1С — заведіть його в 1С і прив'яжіть у заявці, "
                                "тоді заявку можна буде позначити оплаченою")
    old = req["status"]
    new = WORKFLOW[old][1][action]
    if action == "submit":
        _submit(req, user, comment, mode, head_mode, heads)
        return
    if old == "dept_head" and action == "approve":
        new = _after_head(req)
    elif old == "approval" and action == "approve":
        req.setdefault("approvals", {})[role] = {"at": clock.now(), "user_name": user["name"]}
        new = "approval" if pending_roles(req) else "to_pay"
    elif action in ("rework", "reject"):
        req["approvals"] = {}
    req["status"] = new
    if action == "pay":
        start_handover(req, (guard or {}).get("handover", False))
    add_history(req, action, user, role, comment, old, new)


def _after_head(req):
    """Куди йде заявка після керівника відділу (або одразу, якщо кроку керівника немає)."""
    req["approvals"] = {}
    return "approval" if req.get("route_mode") == "parallel" else "accountant"


def _submit(req, user, comment, mode, head_mode, heads):
    old = req["status"]
    req["route_mode"] = "parallel" if mode == "parallel" else "sequential"
    after = _after_head(req)
    req["head_step"] = False
    skip_reason = None
    if head_mode == "approve":
        if not req.get("department"):
            skip_reason = "у автора не вказано відділ"
        elif not heads:
            skip_reason = "у відділу автора немає керівника"
        else:
            req["head_step"] = True
    if not req["head_step"]:
        req["status"] = after
        add_history(req, "submit", user, "initiator", comment, old, after)
        if skip_reason:
            add_history(req, "auto_skip", SYSTEM_USER, "dept_head",
                        f"Погодження керівника відділу пропущено: {skip_reason}", None, after)
        return
    req["status"] = "dept_head"
    add_history(req, "submit", user, "initiator", comment, old, "dept_head")
    if (user.get("email") or "").lower() in {h.lower() for h in heads}:
        # Автор сам очолює свій відділ — крок керівника зараховується автоматично
        req["status"] = after
        add_history(req, "approve", user, "dept_head", "Автоматично: автор — керівник відділу", "dept_head", after)


def stage_names(req):
    """Назви етапів для смуги маршруту; етап бухгалтера — за формою оплати."""
    accountant = ROLES.get(accountant_role(req), "Бухгалтер")
    return [(code, accountant if code == "accountant" else name) for code, name in STAGES
            if code != "dept_head" or req.get("head_step")]


def _role_stage(role):
    if role == "dept_head":
        return "dept_head"
    return "cfo" if role == "cfo" else "accountant" if role in ACCOUNTANT_ROLES else None


def stage_states(req):
    """Стан кожного етапу для смуги маршруту: done / current / returned / todo / rejected."""
    stages = stage_names(req)
    order = [code for code, _ in stages]
    status = req["status"]
    if status == "approval":  # паралельно: кожен погоджувач окремо
        done = {_role_stage(r) for r in (req.get("approvals") or {})}
        return [(name, "done" if code in done or code == "dept_head" else
                 "current" if code in ("accountant", "cfo") else "todo")
                for code, name in stages]
    last = next((h for h in reversed(req.get("history", []))
                 if h["to_status"] == status and h["from_status"] == "approval"), None)
    if status in ("rework", "rejected") and last:
        stage = _role_stage(last["role"])
        mark = "returned" if status == "rework" else "rejected"
        return [(name, mark if code == stage else "todo") for code, name in stages]
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
