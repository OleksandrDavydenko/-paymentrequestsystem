"""«Вид оплати» — з чого платимо. Бачать і вибирають бухгалтер, фіндиректор і адмін; ініціатор поля не бачить.

Коли Power BI підключений — це «Види оплати» з 1С (payment_types): бухгалтер бачить лише ті,
до яких має доступ у 1С (через зв'язок з користувачем 1С у його картці), фіндиректор — усі.
Без Power BI — мокові рахунки й каси організацій (для локальної розробки й тестів).
Ідентифікатор виду оплати в заявці — рядок (GUID з 1С або номер мокового рахунку).
"""
import expense_items
import payment_types
import user_onec

# organization_id — код організації з довідника organizations (мокові дані)
MOCK_ACCOUNTS = [
    {"id": "1", "organization_id": "1", "kind": "bank", "name": "ПриватБанк, поточний", "iban": "UA213052990000026001000000101", "currency": "UAH"},
    {"id": "2", "organization_id": "1", "kind": "bank", "name": "Райффайзен, поточний", "iban": "UA383808050000000026000000102", "currency": "UAH"},
    {"id": "3", "organization_id": "1", "kind": "bank", "name": "ПриватБанк, валютний USD", "iban": "UA563052990000026001000000103", "currency": "USD"},
    {"id": "4", "organization_id": "1", "kind": "bank", "name": "ПриватБанк, валютний EUR", "iban": "UA743052990000026001000000104", "currency": "EUR"},
    {"id": "5", "organization_id": "1", "kind": "cash", "name": "Каса головного офісу", "iban": "", "currency": "UAH"},
    {"id": "6", "organization_id": "2", "kind": "bank", "name": "Ощадбанк, поточний", "iban": "UA183223130000026002000000201", "currency": "UAH"},
    {"id": "7", "organization_id": "2", "kind": "cash", "name": "Каса складу", "iban": "", "currency": "UAH"},
    {"id": "8", "organization_id": "3", "kind": "bank", "name": "Монобанк, ФОП", "iban": "UA403220010000026003000000301", "currency": "UAH"},
    {"id": "9", "organization_id": "3", "kind": "cash", "name": "Каса ФОП", "iban": "", "currency": "UAH"},
    {"id": "10", "organization_id": "4", "kind": "bank", "name": "Swedbank, EUR", "iban": "LT617300010000000401", "currency": "EUR"},
    {"id": "11", "organization_id": "4", "kind": "bank", "name": "Swedbank, USD", "iban": "LT617300010000000402", "currency": "USD"},
    {"id": "12", "organization_id": "4", "kind": "cash", "name": "Каса Litbia", "iban": "", "currency": "EUR"},
]
ACCOUNTANT_ROLES = {"acc_cash", "acc_resident", "acc_nonresident"}


def use_types():
    """Види оплати з 1С (Power BI) замість мокових рахунків."""
    return expense_items._configured()


# ---------------------------------------------------------------- мокові рахунки

def _mock_get(account_id):
    return next((dict(a) for a in MOCK_ACCOUNTS if a["id"] == str(account_id or "")), None)


def mock_options_for(req):
    """Мокові рахунки (безготівка) або каси (готівка) організації заявки; спершу — у валюті заявки."""
    kind = "cash" if req.get("payment_form") == "cash" else "bank"
    rows = [dict(a) for a in MOCK_ACCOUNTS
            if a["organization_id"] == str(req.get("organization_id") or "") and a["kind"] == kind]
    return sorted(rows, key=lambda a: (a["currency"] != req.get("currency"), a["name"].lower()))


# ---------------------------------------------------------------- спільне

def get(account_id):
    """Вид оплати за id: {"id", "name", ...} або None."""
    if use_types():
        t = payment_types.get(account_id)
        return {"id": t["id"], "name": t["name"], "currency": "", "iban": ""} if t else None
    return _mock_get(account_id)


def label(account_id):
    """Назва виду оплати для показу: «НАЛ» або «ПриватБанк, поточний · UAH · …0101»."""
    if not account_id:
        return ""
    if use_types():
        return payment_types.name(account_id)
    a = _mock_get(account_id)
    if not a:
        return f"#{account_id} (немає в довіднику)"
    tail = f" · …{a['iban'][-4:]}" if a.get("iban") else ""
    return f"{a['name']} · {a['currency']}{tail}"


def options_for(req, user=None, roles=frozenset()):
    """Що може вибрати цей користувач у «Виді оплати» цієї заявки.
    З 1С: фіндиректор і адміністратор — усі види оплати, бухгалтер — ті, до яких має доступ у 1С."""
    if not use_types():
        return mock_options_for(req)
    if roles & {"cfo", "admin"}:
        rows = payment_types.types()
    elif roles & ACCOUNTANT_ROLES and user:
        rows = payment_types.types_for_user(user_onec.guid_for(user.get("oid")))
    else:
        rows = []
    return [{"id": t["id"], "name": t["name"], "currency": "", "iban": "", "kind": "type"} for t in rows]


def valid_for(req, account_id, user=None, roles=frozenset()):
    return any(a["id"] == str(account_id) for a in options_for(req, user, roles))


def no_options_hint(req, user=None, roles=frozenset()):
    """Чому список порожній — для примітки в картці заявки."""
    if not use_types():
        return f"Для цієї організації в довіднику ще немає {'кас' if req.get('payment_form') == 'cash' else 'рахунків'}"
    if user and not user_onec.guid_for(user.get("oid")):
        return "Вас ще не зв'язано з користувачем 1С — адміністратор робить це в картці користувача"
    return "У 1С вам не призначено жодного виду оплати (картка користувача 1С → вкладка «Виды оплаты»)"
