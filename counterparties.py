"""Довідник контрагентів: записи з 1С (через Power BI) + тимчасові, внесені ініціатором у заявці.

Контрагента, якого ще немає в 1С, ініціатор вносить сам (ЄДРПОУ / реєстр. номер, назва) — це «тимчасовий»
контрагент (status = pending). Бухгалтер заводить його в 1С і прив'язує тимчасовий запис до
справжнього (status = linked, linked_to = id з 1С); заявки після цього посилаються на запис з 1С.
Поки таблиці з 1С не підключені — мокові дані у форматі майбутньої таблиці Counterparties.
Тимчасові — JSON-файл (/home/data на Azure), поки немає бази даних.
"""
import json
import os
import re
import threading

import clock

# Мокові записи з 1С (таблиця Counterparties)
MOCK_ONEC = [
    {"id": 1, "onec_key": "8f1c0a10-0001", "name": "ТОВ «Нова Пошта»", "full_name": "ТОВАРИСТВО З ОБМЕЖЕНОЮ ВІДПОВІДАЛЬНІСТЮ «НОВА ПОШТА»",
     "edrpou": "31316718", "country": "UA", "resident": True},
    {"id": 2, "onec_key": "8f1c0a10-0002", "name": "ПрАТ «Київстар»", "full_name": "ПРИВАТНЕ АКЦІОНЕРНЕ ТОВАРИСТВО «КИЇВСТАР»",
     "edrpou": "21673832", "country": "UA", "resident": True},
    {"id": 3, "onec_key": "8f1c0a10-0003", "name": "ТОВ «Епіцентр К»", "full_name": "ТОВАРИСТВО З ОБМЕЖЕНОЮ ВІДПОВІДАЛЬНІСТЮ «ЕПІЦЕНТР К»",
     "edrpou": "32490244", "country": "UA", "resident": True},
    {"id": 4, "onec_key": "8f1c0a10-0004", "name": "ТОВ «Ю-Контрол»", "full_name": "ТОВАРИСТВО З ОБМЕЖЕНОЮ ВІДПОВІДАЛЬНІСТЮ «Ю-КОНТРОЛ»",
     "edrpou": "38123456", "country": "UA", "resident": True},
    {"id": 5, "onec_key": "8f1c0a10-0005", "name": "Maersk A/S", "full_name": "A.P. Møller - Mærsk A/S",
     "edrpou": "22756214", "country": "DK", "resident": False},
    {"id": 6, "onec_key": "8f1c0a10-0006", "name": "ТОВ «Офісний Світ»", "full_name": "ТОВАРИСТВО З ОБМЕЖЕНОЮ ВІДПОВІДАЛЬНІСТЮ «ОФІСНИЙ СВІТ»",
     "edrpou": "40112233", "country": "UA", "resident": True},
]
FIRST_PENDING_ID = 100001
FIELDS = ("name", "edrpou", "country", "resident", "note")

_lock = threading.Lock()
_data = None


# ---------------------------------------------------------------- перевірки

def validate(data):
    """Повертає (очищені дані, {поле: помилка})."""
    clean = {
        "name": (data.get("name") or "").strip(),
        "resident": str(data.get("resident", "1")).lower() in ("1", "true", "yes", "on"),
        "edrpou": re.sub(r"\s+", "", data.get("edrpou") or ""),
        "country": (data.get("country") or "").strip().upper()[:40],
        "note": (data.get("note") or "").strip()[:500],
    }
    errors = {}
    if len(clean["name"]) < 2:
        errors["name"] = "Вкажіть назву контрагента"
    if clean["resident"]:
        clean["country"] = "UA"
        if not re.fullmatch(r"\d{8}|\d{10}", clean["edrpou"]):
            errors["edrpou"] = "ЄДРПОУ — 8 цифр (для ФОП — ІПН, 10 цифр)"
    else:
        if not clean["edrpou"]:
            errors["edrpou"] = "Вкажіть реєстраційний номер компанії в її країні"
        if not clean["country"] or clean["country"] == "UA":
            errors["country"] = "Вкажіть країну реєстрації нерезидента"
    return clean, errors


# ---------------------------------------------------------------- зберігання тимчасових

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


# ---------------------------------------------------------------- читання

def onec_items():
    """Контрагенти з 1С. Пізніше — таблиця Counterparties з Power BI (з кешем, як expense_items)."""
    return [{**c, "status": "onec"} for c in MOCK_ONEC]


def pending_items(include_linked=False):
    with _lock:
        items = [dict(v) for v in _load()["items"].values()]
    return [i for i in items if include_linked or i["status"] == "pending"]


def get_all():
    """Ті, кого можна вибрати в заявці: з 1С + тимчасові (ще не прив'язані)."""
    return onec_items() + pending_items()


def get(cp_id):
    """Запис за id (і прив'язаний тимчасовий теж — для історії) або None."""
    try:
        cp_id = int(cp_id)
    except (TypeError, ValueError):
        return None
    for c in onec_items():
        if c["id"] == cp_id:
            return c
    with _lock:
        item = _load()["items"].get(str(cp_id))
    return dict(item) if item else None


def names():
    """{id: назва} — для шаблонів, фільтрів і перевірки форми (усі, зокрема прив'язані)."""
    return {c["id"]: c["name"] for c in onec_items() + pending_items(include_linked=True)}


def selectable_ids():
    return {c["id"] for c in get_all()}


def is_pending(cp_id):
    c = get(cp_id)
    return bool(c) and c["status"] == "pending"


def picker_items():
    """Компактні записи для вікна вибору в картці заявки."""
    return [{"id": c["id"], "name": c["name"], "edrpou": c.get("edrpou", ""), "country": c.get("country", ""),
             "resident": bool(c.get("resident")), "pending": c["status"] == "pending"}
            for c in sorted(get_all(), key=lambda c: c["name"].lower())]


def search(query, limit=10):
    q = (query or "").strip().lower()
    if not q:
        return []
    return [c for c in get_all() if q in c["name"].lower() or q in (c.get("edrpou") or "")][:limit]


def find_duplicates(edrpou, exclude_id=None):
    """Наявні контрагенти (з 1С або тимчасові) з тим самим ЄДРПОУ / реєстраційним номером."""
    edrpou = (edrpou or "").strip()
    return [c for c in get_all() if c["id"] != exclude_id and edrpou and c.get("edrpou") == edrpou]


# ---------------------------------------------------------------- зміни

def create_pending(data, user):
    """Внести тимчасового контрагента. Повертає (запис або None, помилки, дублікат або None)."""
    clean, errors = validate(data)
    if errors:
        return None, errors, None
    dupes = find_duplicates(clean["edrpou"])
    if dupes:
        return None, {}, dupes[0]
    with _lock:
        d = _load()
        cp_id = d["next_id"]
        d["next_id"] = cp_id + 1
        item = {"id": cp_id, "onec_key": None, "status": "pending", **clean,
                "created_by": user["name"], "created_by_email": user["email"],
                "created_at": clock.now().isoformat(timespec="seconds")}
        d["items"][str(cp_id)] = item
        _write()
    return dict(item), {}, None


def link(pending_id, onec_id, user):
    """Прив'язати тимчасового контрагента до запису з 1С. Повертає (тимчасовий, запис 1С). Кидає ValueError."""
    target = next((c for c in onec_items() if c["id"] == int(onec_id)), None)
    if not target:
        raise ValueError("Оберіть контрагента з 1С")
    with _lock:
        item = _load()["items"].get(str(pending_id))
        if not item or item["status"] != "pending":
            raise ValueError("Цього тимчасового контрагента вже прив'язано або не знайдено")
        item.update({"status": "linked", "linked_to": target["id"], "linked_by": user["name"],
                     "linked_at": clock.now().isoformat(timespec="seconds")})
        _write()
        return dict(item), target


def auto_link_candidates():
    """Пари (тимчасовий, запис 1С) зі збігом ЄДРПОУ / реєстраційного номера (для автоприв'язки)."""
    onec = onec_items()
    pairs = []
    for p in pending_items():
        match = next((c for c in onec if p.get("edrpou") and c.get("edrpou") == p["edrpou"]), None)
        if match:
            pairs.append((p, match))
    return pairs
