"""Види оплати з 1С і хто з бухгалтерів до яких має доступ.

Таблиці семантичної моделі Power BI:
- PaymentTypes: GUID, Code, Name, IsDeleted — довідник «Види оплати» (НАЛ, КАРТА, КАСА, NLB LITBIA…);
- UserPaymentTypes: UserGUID, UserName, PaymentTypeGUID, IsDeleted — вкладка «Виды оплаты»
  в картці користувача 1С (до яких видів оплати в нього є доступ).
Користувача системи зв'язує з користувачем 1С адмін у картці користувача (user_onec.py),
бо пошта не збігається з логіном 1С. Без Power BI — мокові дані в тому ж форматі.
"""
import difflib
import re

import pbi_cache

MOCK_TYPES = [
    {"GUID": "PT-NAL", "Code": "000000001", "Name": "НАЛ", "IsDeleted": False},
    {"GUID": "PT-CARD", "Code": "000000002", "Name": "КАРТА", "IsDeleted": False},
    {"GUID": "PT-KASA", "Code": "000000003", "Name": "КАСА", "IsDeleted": False},
    {"GUID": "PT-NLB", "Code": "000000004", "Name": "NLB LITBIA", "IsDeleted": False},
    {"GUID": "PT-PUMB", "Code": "000000005", "Name": "ПУМБ ФТП, ТОВ", "IsDeleted": False},
]
MOCK_ACCESS = [
    {"UserGUID": "U-IRINA", "UserName": "Irina.Chagan", "PaymentTypeGUID": "PT-NAL", "IsDeleted": False},
    {"UserGUID": "U-IRINA", "UserName": "Irina.Chagan", "PaymentTypeGUID": "PT-CARD", "IsDeleted": False},
    {"UserGUID": "U-IRINA", "UserName": "Irina.Chagan", "PaymentTypeGUID": "PT-PUMB", "IsDeleted": False},
    {"UserGUID": "U-OLENA", "UserName": "Olena.Bondar", "PaymentTypeGUID": "PT-KASA", "IsDeleted": False},
    {"UserGUID": "U-OLENA", "UserName": "Olena.Bondar", "PaymentTypeGUID": "PT-NLB", "IsDeleted": False},
]


def _parse_types(rows):
    by_id = {}
    for r in rows:
        guid, name = str(r.get("GUID") or "").strip(), str(r.get("Name") or "").strip()
        if guid and name:
            by_id[guid] = {"id": guid, "code": str(r.get("Code") or "").strip(), "name": name,
                           "deleted": pbi_cache.truthy(r.get("IsDeleted"))}
    return sorted(by_id.values(), key=lambda t: t["name"].lower())


def _parse_access(rows):
    out = []
    for r in rows:
        user, ptype = str(r.get("UserGUID") or "").strip(), str(r.get("PaymentTypeGUID") or "").strip()
        if user and ptype:
            out.append({"user": user, "user_name": str(r.get("UserName") or "").strip(), "type": ptype,
                        "deleted": pbi_cache.truthy(r.get("IsDeleted"))})
    return out


_types = pbi_cache.CachedTable("payment_types", "PBI_PAYMENT_TYPES_TABLE", "PaymentTypes", _parse_types,
                               lambda: _parse_types(MOCK_TYPES))
_access = pbi_cache.CachedTable("user_payment_types", "PBI_USER_PAYMENT_TYPES_TABLE", "UserPaymentTypes",
                                _parse_access, lambda: _parse_access(MOCK_ACCESS))


INACTIVE_PREFIX = "---"  # так у 1С позначають неактивні / вільні види оплати («---ЛЕНА», «---можнавикористовувати4»)


def is_active(t):
    return not t["deleted"] and not t["name"].startswith(INACTIVE_PREFIX)


def types(include_deleted=False):
    """Види оплати для вибору: без помічених на видалення і без неактивних («---» на початку назви)."""
    return [t for t in _types.items() if include_deleted or is_active(t)]


def get(type_id):
    return next((t for t in _types.items() if t["id"] == str(type_id or "")), None)


def name(type_id):
    t = get(type_id)
    return t["name"] if t else (f"{type_id} (немає в довіднику)" if type_id else "")


def onec_users():
    """Користувачі 1С, у яких є доступ хоча б до одного виду оплати: [{"guid", "name"}] за іменем."""
    seen = {}
    for a in _access.items():
        if not a["deleted"]:
            seen.setdefault(a["user"], a["user_name"] or a["user"])
    return sorted(({"guid": g, "name": n} for g, n in seen.items()), key=lambda u: u["name"].lower())


def onec_user_name(guid):
    return next((u["name"] for u in onec_users() if u["guid"] == guid), guid or "")


def types_for_user(onec_guid):
    """Види оплати, до яких користувач 1С має доступ (без помічених на видалення)."""
    if not onec_guid:
        return []
    allowed = {a["type"] for a in _access.items() if a["user"] == onec_guid and not a["deleted"]}
    return [t for t in types() if t["id"] in allowed]


_TRANSLIT = {"а": "a", "б": "b", "в": "v", "г": "h", "ґ": "g", "д": "d", "е": "e", "є": "ie", "ж": "zh", "з": "z",
             "и": "y", "і": "i", "ї": "i", "й": "i", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p",
             "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh",
             "щ": "shch", "ь": "", "ю": "iu", "я": "ia", "ё": "e", "ы": "y", "э": "e", "ъ": ""}


def _letters(value):
    """Лише літери для порівняння імен (латиниця й кирилиця; Iryna ≈ Irina)."""
    return re.sub(r"[^a-z]", "", "".join(_TRANSLIT.get(ch, ch) for ch in (value or "").lower()))


def _tokens(value):
    """Слова імені для порівняння: транслітерація, г/h/g і и/y/i — однаково, без ініціалів."""
    out = []
    for word in re.split(r"[\s._\-]+", value or ""):
        w = _letters(word).replace("y", "i").replace("h", "g")
        if len(w) > 1:
            out.append(w)
    return out


def _score(a_tokens, b_tokens):
    """Схожість двох імен: кожне слово першого має мати пару в другому; 0..1 (мінімум по словах)."""
    if not a_tokens or not b_tokens:
        return 0.0
    best = [max(difflib.SequenceMatcher(None, a, b).ratio() for b in b_tokens) for a in a_tokens]
    score = min(best) if len(a_tokens) > 1 else best[0] * 0.9  # одне слово — менше довіри
    # Зайві слова в кандидата («Test.Ivan.Isaienko») — трохи менше довіри, ніж точному збігу
    return score - 0.06 * max(0, len(b_tokens) - len(a_tokens))


def suggest_user(full_name, email=""):
    """Найсхожіший користувач 1С для користувача системи або None.
    Обидва слова (ім'я і прізвище) мають бути схожі, а відрив від другого кандидата — помітний."""
    sources = [t for t in (_tokens(full_name), _tokens((email or "").split("@")[0])) if t]
    scored = []
    for u in onec_users():
        cand = _tokens(u["name"])
        scored.append((max(_score(src, cand) for src in sources) if sources else 0.0, u))
    scored.sort(key=lambda x: -x[0])
    if not scored or scored[0][0] < 0.8:
        return None
    if len(scored) > 1 and scored[0][0] - scored[1][0] < 0.05:
        return None  # двоє однаково схожих — хай адмін вибере сам
    return scored[0][1]


def refresh():
    ok1, ok2 = _types.refresh(), _access.refresh()
    return ok1 and ok2


def status():
    st = _types.status()
    acc = _access.status()
    st.update(users=len(onec_users()), access_error=acc["error"], access_source=acc["source"])
    if acc["error"] and not st["error"]:
        st["error"] = acc["error"]
    return st


def from_powerbi():
    """Чи взяті види оплати з Power BI (а не мокові)."""
    return _types.status()["source"] in ("powerbi", "copy")
