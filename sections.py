"""Розділи-звіти, які адміністратор може точково відкрити чи закрити людині (картка користувача → «Доступ до
розділів»). Типово — за роллю, як і було; у картці зберігається лише виняток (user_roles: sections), тож коли
людині змінюють роль, типове значення змінюється саме, а виняток лишається.

Робочих розділів тут немає: «На погодження», «До оплати», «Заявки відділу» випливають з ролей і відділів (без ролі
погоджувати чи оплачувати нічого), «Адміністрування» — лише роль адміністратора.

Розділ дає перегляд, не дії: прив'язувати платежі у «Звірці» може лише той, кого вказано в «Звірка з 1С → Хто може
прив'язати», а дії в заявці визначають ролі. Хто бачить розділ з OPENS_CARDS, відкриває й картки заявок з нього
(перегляд і коментар, як адміністратор чи фіндиректор).

Новий розділ (як «Бюджет» — типово керівникам відділів): запис у SECTIONS, маршрут з
@requires_section("код") (web/auth.py) і пункт меню в base.html — {% if 'код' in nav_sections %}. У картці користувача
він з'являється сам.
"""
from collections import namedtuple

# setting — налаштування-вимикач (settings_schema): розділ є лише коли воно ввімкнене; без нього — завжди
Section = namedtuple("Section", "label endpoint default_roles hint setting", defaults=(None,))

SECTIONS = {
    "all_requests": Section("Усі заявки", "lists.admin_requests", frozenset({"admin", "cfo"}),
                            "усі заявки компанії — лише перегляд"),
    "reconcile": Section("Звірка", "payments.reconcile", frozenset({"admin", "cfo"}),
                         "платежі з 1С і заявки; прив'язувати — лише кому дозволено в «Звірка з 1С»"),
    "budget": Section("Бюджет", "budget.index", frozenset({"dept_head", "admin", "cfo"}),
                      "план і факт бюджету відділів з 1С; чий бюджет — «Бюджет: які відділи» нижче",
                      "budget_enabled"),
}
# Розділи, з яких відкриваються картки будь-яких заявок (лише перегляд)
OPENS_CARDS = frozenset({"all_requests", "reconcile"})


def is_on(code):
    """Чи ввімкнено розділ у налаштуваннях системи. Вимкнений розділ ніхто не бачить (ні в меню, ні в картці
    користувача), а збережені для нього винятки лишаються й діють знову, щойно розділ увімкнуть."""
    setting = SECTIONS[code].setting
    if not setting:
        return True
    import app_settings  # тут, а не вгорі: цей модуль імпортують workflow і user_roles
    return bool(app_settings.get(setting))


def by_role(code, roles):
    """Чи бачить розділ людина з такими ролями без винятків (типове значення)."""
    return bool(set(roles) & SECTIONS[code].default_roles)


def clean(overrides):
    """Винятки лише для відомих розділів: {код: True — дозволити, False — заборонити}."""
    return {k: bool(v) for k, v in (overrides or {}).items() if k in SECTIONS}


def effective(roles, overrides):
    """Розділи, які людина бачить: за роллю, з урахуванням винятків."""
    ov = clean(overrides)
    return frozenset(code for code in SECTIONS if is_on(code) and ov.get(code, by_role(code, roles)))


def describe(overrides):
    """«+ Звірка, − Усі заявки» — винятки коротко (список користувачів, журнал картки)."""
    ov = clean(overrides)
    return ", ".join(("+ " if ov[c] else "− ") + SECTIONS[c].label for c in SECTIONS if c in ov and is_on(c))
