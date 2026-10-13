"""«Вид оплати» — з якого рахунку чи каси організації платимо. Вибирають бухгалтер і фіндиректор.

Ініціатор вибирає лише організацію; конкретний рахунок (безготівка) або касу (готівка) визначає
бухгалтер. Поки таблиці з 1С (OrganizationAccounts, CashDesks) не підключені — мокові дані.
"""

# organization_id — як у mock_data.ORGANIZATIONS (пізніше — GUID організації з 1С)
MOCK_ACCOUNTS = [
    {"id": 1, "onec_key": "acc-0001", "organization_id": 1, "kind": "bank", "name": "ПриватБанк, поточний", "iban": "UA213052990000026001000000101", "currency": "UAH"},
    {"id": 2, "onec_key": "acc-0002", "organization_id": 1, "kind": "bank", "name": "Райффайзен, поточний", "iban": "UA383808050000000026000000102", "currency": "UAH"},
    {"id": 3, "onec_key": "acc-0003", "organization_id": 1, "kind": "bank", "name": "ПриватБанк, валютний USD", "iban": "UA563052990000026001000000103", "currency": "USD"},
    {"id": 4, "onec_key": "acc-0004", "organization_id": 1, "kind": "bank", "name": "ПриватБанк, валютний EUR", "iban": "UA743052990000026001000000104", "currency": "EUR"},
    {"id": 5, "onec_key": "cash-0001", "organization_id": 1, "kind": "cash", "name": "Каса головного офісу", "iban": "", "currency": "UAH"},
    {"id": 6, "onec_key": "acc-0006", "organization_id": 2, "kind": "bank", "name": "Ощадбанк, поточний", "iban": "UA183223130000026002000000201", "currency": "UAH"},
    {"id": 7, "onec_key": "cash-0002", "organization_id": 2, "kind": "cash", "name": "Каса складу", "iban": "", "currency": "UAH"},
    {"id": 8, "onec_key": "acc-0008", "organization_id": 3, "kind": "bank", "name": "Монобанк, ФОП", "iban": "UA403220010000026003000000301", "currency": "UAH"},
    {"id": 9, "onec_key": "cash-0003", "organization_id": 3, "kind": "cash", "name": "Каса ФОП", "iban": "", "currency": "UAH"},
    {"id": 10, "onec_key": "acc-0010", "organization_id": 4, "kind": "bank", "name": "Swedbank, EUR", "iban": "LT617300010000000401", "currency": "EUR"},
    {"id": 11, "onec_key": "acc-0011", "organization_id": 4, "kind": "bank", "name": "Swedbank, USD", "iban": "LT617300010000000402", "currency": "USD"},
    {"id": 12, "onec_key": "cash-0004", "organization_id": 4, "kind": "cash", "name": "Каса Litbia", "iban": "", "currency": "EUR"},
]
KINDS = {"bank": "Рахунок", "cash": "Каса"}


def get_all():
    return [dict(a) for a in MOCK_ACCOUNTS]


def get(account_id):
    try:
        account_id = int(account_id)
    except (TypeError, ValueError):
        return None
    return next((dict(a) for a in MOCK_ACCOUNTS if a["id"] == account_id), None)


def label(account_id):
    """«ПриватБанк, поточний · UAH · …0101» або «Каса складу · UAH»."""
    a = get(account_id)
    if not a:
        return f"#{account_id} (немає в довіднику)" if account_id else ""
    tail = f" · …{a['iban'][-4:]}" if a.get("iban") else ""
    return f"{a['name']} · {a['currency']}{tail}"


def options_for(req):
    """Рахунки (безготівка) або каси (готівка) організації заявки; спершу — у валюті заявки."""
    kind = "cash" if req.get("payment_form") == "cash" else "bank"
    rows = [a for a in get_all() if a["organization_id"] == req.get("organization_id") and a["kind"] == kind]
    return sorted(rows, key=lambda a: (a["currency"] != req.get("currency"), a["name"].lower()))


def valid_for(req, account_id):
    return any(a["id"] == int(account_id) for a in options_for(req)) if str(account_id).isdigit() else False
