"""Довідник контрагентів: записи з 1С (таблиця Counterparties у Power BI) + тимчасові, внесені в заявці.

Таблиця з 1С: GUID, Code, Name, FullName, Edrpou, IsNonResident, IsDeleted (DirectQuery — дані живі).
Записів десятки тисяч, тому пошук іде на сервері (search), а не в браузері.

Контрагента, якого ще немає в 1С, ініціатор вносить сам (status = pending). Бухгалтер заводить його
в 1С і прив'язує (status = linked, linked_to = id з 1С) — або система прив'язує сама за ЄДРПОУ:
під час оновлення довідника чи живою перевіркою (find_onec_by_edrpou) перед оплатою.
Ідентифікатор контрагента в системі — рядок: GUID з 1С або номер тимчасового (від 100001).
Тимчасові — JSON-файл (/home/data на Azure), поки немає бази даних.
"""
import json
import logging
import os
import re
import threading

import clock
import expense_items
import pbi_cache

logger = logging.getLogger(__name__)

# Мокові записи з 1С (без Power BI) — у форматі таблиці Counterparties
MOCK_ONEC = [
    {"GUID": "1", "Code": "CR00000001", "Name": "ТОВ «Нова Пошта»", "FullName": "ТОВАРИСТВО З ОБМЕЖЕНОЮ ВІДПОВІДАЛЬНІСТЮ «НОВА ПОШТА»",
     "Edrpou": "31316718", "IsNonResident": False, "IsDeleted": False},
    {"GUID": "2", "Code": "CR00000002", "Name": "ПрАТ «Київстар»", "FullName": "ПРИВАТНЕ АКЦІОНЕРНЕ ТОВАРИСТВО «КИЇВСТАР»",
     "Edrpou": "21673832", "IsNonResident": False, "IsDeleted": False},
    {"GUID": "3", "Code": "CR00000003", "Name": "ТОВ «Епіцентр К»", "FullName": "ТОВАРИСТВО З ОБМЕЖЕНОЮ ВІДПОВІДАЛЬНІСТЮ «ЕПІЦЕНТР К»",
     "Edrpou": "32490244", "IsNonResident": False, "IsDeleted": False},
    {"GUID": "4", "Code": "CR00000004", "Name": "ТОВ «Ю-Контрол»", "FullName": "ТОВАРИСТВО З ОБМЕЖЕНОЮ ВІДПОВІДАЛЬНІСТЮ «Ю-КОНТРОЛ»",
     "Edrpou": "38123456", "IsNonResident": False, "IsDeleted": False},
    {"GUID": "5", "Code": "CR00000005", "Name": "Maersk A/S", "FullName": "A.P. Møller - Mærsk A/S",
     "Edrpou": "22756214", "IsNonResident": True, "IsDeleted": False},
    {"GUID": "6", "Code": "CR00000006", "Name": "ТОВ «Офісний Світ»", "FullName": "ТОВАРИСТВО З ОБМЕЖЕНОЮ ВІДПОВІДАЛЬНІСТЮ «ОФІСНИЙ СВІТ»",
     "Edrpou": "40112233", "IsNonResident": False, "IsDeleted": False},
]
FIRST_PENDING_ID = 100001
# ЄДРПОУ резидента: 8 цифр — юрособи, 10 цифр — ІПН (РНОКПП) ФОП і фізосіб
RESIDENT_CODE_RE = re.compile(r"\d{8}|\d{10}")


def edrpou_checksum_ok(code):
    """Контрольна цифра коду ЄДРПОУ (8 цифр) за алгоритмом ДПС / Держстату."""
    d = [int(c) for c in code]
    n = int(code)
    w = [1, 2, 3, 4, 5, 6, 7] if (n < 30000000 or n > 60000000) else [7, 1, 2, 3, 4, 5, 6]
    s = sum(a * b for a, b in zip(d[:7], w)) % 11
    if s == 10:  # другий прохід: ваги +2
        s = sum(a * (b + 2) for a, b in zip(d[:7], w)) % 11
        if s == 10:
            s = 0
    return s == d[7]


def ipn_checksum_ok(code):
    """Контрольна цифра РНОКПП (ІПН, 10 цифр) — ФОП і фізособи."""
    d = [int(c) for c in code]
    s = sum(a * b for a, b in zip(d[:9], (-1, 5, 7, 9, 4, 6, 10, 5, 7)))
    return (s % 11) % 10 == d[9]


def resident_code_error(code):
    """Текст помилки для коду резидента або None, якщо код коректний."""
    if not code:
        return "Для резидента ЄДРПОУ обов'язковий: 8 цифр для юросіб, 10 цифр (ІПН) для ФОП і фізосіб"
    if not RESIDENT_CODE_RE.fullmatch(code):
        return "ЄДРПОУ — 8 цифр для юросіб, 10 цифр (ІПН) для ФОП і фізосіб"
    if len(code) == 8 and not edrpou_checksum_ok(code):
        return "Код ЄДРПОУ некоректний (не збігається контрольна цифра) — перевірте, чи немає одруківки"
    if len(code) == 10 and not ipn_checksum_ok(code):
        return "ІПН некоректний (не збігається контрольна цифра) — перевірте, чи немає одруківки"
    return None


def _norm_name(value):
    return re.sub(r"[\s«»\"'’ʼ`.,]+", " ", (value or "").lower()).strip()


# ---------------------------------------------------------------- записи з 1С

def _record(r):
    guid = str(r.get("GUID") or "").strip()
    code = str(r.get("Code") or "").strip()
    name = str(r.get("Name") or "").strip()
    if not name or not (guid or code):
        return None
    return {"id": guid or code, "onec_key": guid or None, "code": code, "name": name,
            "full_name": str(r.get("FullName") or "").strip(), "edrpou": str(r.get("Edrpou") or "").strip(),
            "resident": not pbi_cache.truthy(r.get("IsNonResident")), "deleted": pbi_cache.truthy(r.get("IsDeleted")),
            "status": "onec", "_hay": _norm_name(name + " " + str(r.get("FullName") or ""))}


def parse(rows):
    by_id = {}
    for r in rows:
        rec = _record(r)
        if rec:
            by_id[rec["id"]] = rec
    return sorted(by_id.values(), key=lambda c: c["name"].lower())


_table = pbi_cache.CachedTable("counterparties", "PBI_COUNTERPARTIES_TABLE", "Counterparties", parse,
                               lambda: parse(MOCK_ONEC), ttl_env="PBI_COUNTERPARTIES_MINUTES", ttl_default_minutes=15)
_index = {"items": None, "by_id": {}}
_extra = {}  # знайдені живою перевіркою, поки кеш не оновився


def onec_items(include_deleted=False):
    return [c for c in _table.items() if include_deleted or not c["deleted"]]


def _onec_by_id():
    items = _table.items()
    if _index["items"] is not items:
        _index.update(items=items, by_id={c["id"]: c for c in items})
    return _index["by_id"]


def find_onec_by_edrpou(edrpou, live=True):
    """Контрагенти з 1С з цим ЄДРПОУ. live — живий запит до Power BI (DirectQuery: одразу після внесення в 1С)."""
    edrpou = (edrpou or "").strip()
    if not edrpou:
        return []
    found = [c for c in onec_items() if c["edrpou"] == edrpou]
    if found or not live or not expense_items._configured():
        return found
    try:
        table = _table.table
        rows = expense_items.query_dax(
            f"EVALUATE FILTER('{table}', '{table}'[Edrpou] = {expense_items.dax_string(edrpou)})")
    except Exception:
        logger.exception("Жива перевірка контрагента за ЄДРПОУ не вдалася")
        return []
    found = [c for c in (_record(r) for r in rows) if c and not c["deleted"]]
    for c in found:
        _extra[c["id"]] = c
    return found


# ---------------------------------------------------------------- перевірки

def validate(data):
    """Повертає (очищені дані, {поле: помилка})."""
    clean = {
        "name": (data.get("name") or "").strip()[:200],
        "resident": str(data.get("resident", "1")).lower() in ("1", "true", "yes", "on"),
        "edrpou": re.sub(r"\s+", "", data.get("edrpou") or "")[:40],
        "country": (data.get("country") or "").strip()[:60],
        "note": (data.get("note") or "").strip()[:500],
    }
    errors = {}
    if len(clean["name"]) < 2:
        errors["name"] = "Вкажіть назву контрагента"
    if clean["resident"]:
        clean["country"] = "Україна"
        error = resident_code_error(clean["edrpou"])
        if error:
            errors["edrpou"] = error
    return clean, errors


# ---------------------------------------------------------------- зберігання тимчасових

_lock = threading.Lock()
_data = None


def _path():
    base = "/home/data" if os.environ.get("WEBSITE_SITE_NAME") else os.path.join(os.path.dirname(__file__), "data")
    return os.environ.get("COUNTERPARTIES_PENDING_FILE") or os.path.join(base, "counterparties_pending.json")


def _load():
    global _data
    if _data is None:
        try:
            with open(_path(), encoding="utf-8") as f:
                _data = json.load(f)
        except (OSError, ValueError):
            _data = {}
        _data.setdefault("next_id", FIRST_PENDING_ID)
        _data.setdefault("items", {})
        for item in _data["items"].values():  # старий формат: числові id
            item["id"] = str(item["id"])
            if item.get("linked_to") is not None:
                item["linked_to"] = str(item["linked_to"])
    return _data


def _write():
    path = _path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(_data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def reload():
    global _data
    with _lock:
        _data = None
        _load()


def pending_items(include_linked=False):
    with _lock:
        items = [dict(v) for v in _load()["items"].values()]
    return [i for i in items if include_linked or i["status"] == "pending"]


# ---------------------------------------------------------------- читання

def get(cp_id):
    """Запис за id: з 1С (зокрема помічений на видалення) або тимчасовий (зокрема прив'язаний)."""
    if cp_id is None or cp_id == "":
        return None
    cp_id = str(cp_id)
    c = _onec_by_id().get(cp_id) or _extra.get(cp_id)
    if c:
        return c
    with _lock:
        item = _load()["items"].get(cp_id)
    return dict(item) if item else None


def name(cp_id):
    c = get(cp_id)
    return c["name"] if c else (f"{cp_id} (немає в довіднику)" if cp_id else "")


def is_selectable(cp_id):
    c = get(cp_id)
    return bool(c) and (c["status"] == "pending" or (c["status"] == "onec" and not c["deleted"]))


def is_pending(cp_id):
    c = get(cp_id)
    return bool(c) and c["status"] == "pending"


def brief(c):
    """Компактний запис для вікна вибору в браузері."""
    return {"id": c["id"], "name": c["name"], "edrpou": c.get("edrpou", ""), "resident": bool(c.get("resident")),
            "country": c.get("country", "") if c["status"] == "pending" else "", "pending": c["status"] == "pending"}


def search(query="", limit=20, kind="", scope="all"):
    """Пошук за назвою / повною назвою / ЄДРПОУ / кодом. kind: resident | nonresident | pending; scope: all | onec.
    Повертає (знайдені записи, скільки всього збігів)."""
    tokens = _norm_name(query).split()
    digits = re.sub(r"\s+", "", query or "")
    pool = (pending_items() if scope != "onec" else []) + ([] if kind == "pending" else onec_items())
    ranked = []
    for c in pool:
        if kind == "resident" and not c.get("resident") or kind == "nonresident" and c.get("resident"):
            continue
        if kind == "pending" and c["status"] != "pending":
            continue
        if not tokens:
            ranked.append((1 if c["status"] == "onec" else 0, c["name"].lower(), c))
            continue
        hay = c.get("_hay") or _norm_name(c["name"] + " " + c.get("full_name", ""))
        if digits and (c.get("edrpou") == digits or c.get("code", "").lower() == digits.lower()):
            rank = 0
        elif digits.isdigit() and len(digits) >= 3 and c.get("edrpou", "").startswith(digits):
            rank = 1
        elif all(t in hay for t in tokens):
            rank = 2 if hay.startswith(tokens[0]) else 3
        else:
            continue
        ranked.append((rank, c["name"].lower(), c))
    ranked.sort(key=lambda x: (x[0], x[1]))
    return [r[2] for r in ranked[:limit]], len(ranked)


def find_duplicates(edrpou, name_=""):
    """Наявні контрагенти з тим самим ЄДРПОУ / реєстр. номером або (якщо номера немає) з тією самою назвою."""
    edrpou, key = (edrpou or "").strip(), _norm_name(name_)
    pool = pending_items() + onec_items()
    if edrpou:
        return [c for c in pool if c.get("edrpou") == edrpou]
    return [c for c in pool if key and _norm_name(c["name"]) == key]


def used_names(ids):
    """{id: назва} для фільтра «Контрагент» — лише ті, що трапляються в заявках."""
    return {i: name(i) for i in ids if i}


# ---------------------------------------------------------------- зміни

def create_pending(data, user):
    """Внести тимчасового контрагента. Повертає (запис або None, помилки, дублікат або None)."""
    clean, errors = validate(data)
    if errors:
        # Код не пройшов контрольну цифру, але такий контрагент уже є в 1С — запропонувати його
        if set(errors) == {"edrpou"} and RESIDENT_CODE_RE.fullmatch(clean["edrpou"]):
            known = find_duplicates(clean["edrpou"]) or find_onec_by_edrpou(clean["edrpou"])
            if known:
                return None, {}, known[0]
        return None, errors, None
    dupes = find_duplicates(clean["edrpou"], clean["name"])
    if not dupes and clean["edrpou"]:
        dupes = find_onec_by_edrpou(clean["edrpou"])  # раптом його вже щойно завели в 1С
    if dupes:
        return None, {}, dupes[0]
    with _lock:
        d = _load()
        cp_id = str(d["next_id"])
        d["next_id"] += 1
        item = {"id": cp_id, "onec_key": None, "status": "pending", **clean,
                "created_by": user["name"], "created_by_email": user["email"],
                "created_at": clock.now().isoformat(timespec="seconds")}
        d["items"][cp_id] = item
        _write()
    return dict(item), {}, None


def link(pending_id, onec_id, user):
    """Прив'язати тимчасового контрагента до запису з 1С. Повертає (тимчасовий, запис 1С). Кидає ValueError."""
    target = get(onec_id)
    if not target or target["status"] != "onec":
        raise ValueError("Оберіть контрагента з 1С")
    with _lock:
        item = _load()["items"].get(str(pending_id))
        if not item or item["status"] != "pending":
            raise ValueError("Цього тимчасового контрагента вже прив'язано або не знайдено")
        item.update({"status": "linked", "linked_to": target["id"], "linked_by": user["name"],
                     "linked_at": clock.now().isoformat(timespec="seconds")})
        _write()
        return dict(item), target


def auto_link_candidates(live=False, only_id=None):
    """Пари (тимчасовий, запис 1С) зі збігом ЄДРПОУ / реєстраційного номера (для автоприв'язки)."""
    pairs = []
    for p in pending_items():
        if only_id is not None and p["id"] != str(only_id):
            continue
        match = next(iter(find_onec_by_edrpou(p.get("edrpou"), live=live)), None) if p.get("edrpou") else None
        if match:
            pairs.append((p, match))
    return pairs


def refresh():
    return _table.refresh()


def status():
    st = _table.status()
    st["pending"] = len(pending_items())
    return st
