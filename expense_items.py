"""Довідник статей витрат із семантичної моделі Power BI (таблиця Expense_Item).

Доступ — від імені додатку (service principal, client credentials) з тими самими
CLIENT_ID / CLIENT_SECRET / TENANT_ID, що й для входу. Паролі користувачів не потрібні.

Користувачу показуємо лише елементи (без груп). Результат кешується в пам'яті та
зберігається у файл, щоб довідник був доступний після перезапуску чи збою Power BI.
Якщо Power BI не налаштований (немає PBI_DATASET_ID) — використовуються мокові статті.
"""
import json
import logging
import os
import re
import threading
import time

import msal
import requests

import clock

logger = logging.getLogger(__name__)

PBI_SCOPE = ["https://analysis.windows.net/powerbi/api/.default"]
PBI_API = "https://api.powerbi.com/v1.0/myorg"
GROUP_HINTS = ("груп", "папк", "group", "folder")

# Мокові статті для локальної розробки (без Power BI)
MOCK_ITEMS = [
    ("01.001", "Транспортні послуги"),
    ("01.002", "Експедиторські послуги"),
    ("01.003", "Митне оформлення"),
    ("01.004", "Митні платежі"),
    ("01.005", "Фрахт морський"),
    ("01.006", "Фрахт авіа"),
    ("01.007", "Складські послуги"),
    ("01.008", "Страхування вантажів"),
    ("02.001", "Зв'язок та інтернет"),
    ("02.002", "Мобільний зв'язок"),
    ("02.003", "Хостинг та хмарні сервіси"),
    ("02.004", "Програмне забезпечення"),
    ("02.005", "Ліцензії 1С"),
    ("03.001", "Оренда приміщень"),
    ("03.002", "Комунальні послуги"),
    ("03.003", "Охорона приміщень"),
    ("03.004", "Прибирання"),
    ("04.001", "Канцтовари та офісні витрати"),
    ("04.002", "Питна вода"),
    ("04.003", "Поштові витрати"),
    ("04.004", "Друкарські послуги"),
    ("05.001", "Ремонт та обслуговування"),
    ("05.002", "Ремонт оргтехніки"),
    ("05.003", "Обслуговування автомобілів"),
    ("05.004", "Пальне"),
    ("06.001", "Відрядження — проживання"),
    ("06.002", "Відрядження — квитки"),
    ("06.003", "Добові"),
    ("07.001", "Навчання персоналу"),
    ("07.002", "Підбір персоналу"),
    ("07.003", "Медичне страхування"),
    ("08.001", "Юридичні послуги"),
    ("08.002", "Аудиторські послуги"),
    ("08.003", "Консалтинг"),
    ("08.004", "Нотаріальні послуги"),
    ("09.001", "Банківські комісії"),
    ("09.002", "Курсові різниці"),
    ("10.001", "Реклама та маркетинг"),
    ("10.002", "Представницькі витрати"),
    ("10.003", "Благодійність"),
]
# Відділи мокових статей (за кодом статті або групою «01»). Немає — стаття пряма.
MOCK_DEPARTMENTS = {
    "01": ["Логістика"],
    "01.005": ["Логістика", "БАА"],
    "01.006": ["Логістика", "БАА"],
    "02": ["ІТ"],
    "02.001": ["ІТ", "Адміністрація", "БАА"],
    "04": ["Адміністрація", "БАА"],
    "05": ["Адміністрація"],
    "05.003": ["Логістика"],
    "05.004": ["Логістика", "Продажі"],
    "06": ["Продажі", "Логістика"],
    "07": ["Адміністрація"],
    "10": ["Продажі"],
}


def _cache_file():
    base = "/home/data" if os.environ.get("WEBSITE_SITE_NAME") else os.path.join(os.path.dirname(__file__), "data")
    return os.environ.get("EXPENSE_ITEMS_CACHE") or os.path.join(base, "expense_items.json")


def _natural_key(code):
    return [int(p) if p.isdigit() else p.lower() for p in re.split(r"(\d+)", code or "")]


# ---------------------------------------------------------------- Power BI

def _get_token():
    app = msal.ConfidentialClientApplication(
        os.environ["CLIENT_ID"],
        authority=f"https://login.microsoftonline.com/{os.environ['TENANT_ID']}",
        client_credential=os.environ["CLIENT_SECRET"],
    )
    result = app.acquire_token_for_client(scopes=PBI_SCOPE)
    if "access_token" not in result:
        raise RuntimeError(f"Не вдалося отримати токен Power BI: {result.get('error')}: "
                           f"{(result.get('error_description') or '').splitlines()[0] if result.get('error_description') else ''}")
    return result["access_token"]


def _explain_error(resp):
    """Зрозуміле повідомлення про помилку Power BI для адмінки."""
    code, detail = "", resp.text[:300]
    try:
        err = resp.json().get("error", {})
        code = err.get("code") or ""
        details = (err.get("pbi.error") or {}).get("details") or []
        detail = next((d["detail"]["value"] for d in details if d.get("detail", {}).get("value")), None) \
            or err.get("message") or detail
    except (ValueError, AttributeError, KeyError, TypeError):
        pass
    hints = {
        401: "Power BI не прийняв токен додатку: перевірте налаштування «Субʼєкти-служби можуть викликати API» "
             "і що додаток входить у вказану там групу безпеки (застосування — до 15 хв).",
        403: "Немає дозволу: перевірте налаштування «Execute Queries REST API» і роль додатку в робочій області.",
        404: "Додаток не бачить семантичну модель. Дайте Payment Request System роль «Учасник» (Contributor) "
             "у робочій області або право Build на модель; також перевірте PBI_GROUP_ID і PBI_DATASET_ID.",
    }
    hint = hints.get(resp.status_code, "")
    return f"Power BI {resp.status_code} {code}: {detail} {hint}".strip()


def _query_rows():
    group_id = os.environ["PBI_GROUP_ID"]
    dataset_id = os.environ["PBI_DATASET_ID"]
    table = os.environ.get("PBI_TABLE", "Expense_Item")
    resp = requests.post(
        f"{PBI_API}/groups/{group_id}/datasets/{dataset_id}/executeQueries",
        headers={"Authorization": f"Bearer {_get_token()}"},
        json={"queries": [{"query": f"EVALUATE '{table}'"}], "serializerSettings": {"includeNulls": True}},
        timeout=120,
    )
    if not resp.ok:
        raise RuntimeError(_explain_error(resp))
    tables = (resp.json().get("results") or [{}])[0].get("tables") or []
    rows = (tables[0].get("rows") if tables else None) or []
    # DAX повертає колонки як "Table[Column]"
    return [{(k.split("[", 1)[1][:-1] if k.endswith("]") and "[" in k else k): v for k, v in r.items()} for r in rows]


def _department(value):
    value = str(value or "").strip()
    return "" if value.lower() in ("", "none", "null", "nan") else value


def extract_items(rows):
    """Лише елементи: без груп (за Тип_вузла або за наявністю дочірніх рядків).

    Один код може трапитись у кількох рядках з різними відділами (стаття з розподілом) —
    відділи об'єднуються. Стаття без жодного відділу — «пряма».
    """
    parents = {str(r.get("Батько")).strip() for r in rows if r.get("Батько") not in (None, "")}
    by_code = {}
    for r in rows:
        code = str(r.get("Код") or "").strip()
        name = str(r.get("Назва") or "").strip()
        node_type = str(r.get("Тип_вузла") or "").lower()
        if not code or not name:
            continue
        if any(h in node_type for h in GROUP_HINTS) or code in parents or name in parents:
            continue
        item = by_code.setdefault(code, {"code": code, "name": name, "departments": []})
        dep = _department(r.get("Відділ"))
        if dep and dep not in item["departments"]:
            item["departments"].append(dep)
    items = sorted(by_code.values(), key=lambda i: _natural_key(i["code"]))
    for i in items:
        i["departments"].sort(key=str.lower)
        i["kind"] = "allocated" if i["departments"] else "direct"
    return items


KIND_LABELS = {"direct": "Пряма", "allocated": "З розподілом"}


def _mock_items():
    rows = []
    for code, name in MOCK_ITEMS:
        deps = MOCK_DEPARTMENTS.get(code) or MOCK_DEPARTMENTS.get(code.split(".")[0], [])
        for dep in deps or [None]:
            rows.append({"Код": code, "Назва": name, "Відділ": dep, "Тип_вузла": "Елемент", "Батько": None})
    return extract_items(rows)


# ---------------------------------------------------------------- кеш

_lock = threading.Lock()      # саме завантаження довідника
_bg_lock = threading.Lock()   # лише прапорець «оновлюється у фоні» — ніколи не чекає Power BI
_state = {"items": None, "loaded_at": 0.0, "source": None, "updated": None, "error": None, "refreshing": False}


def _configured():
    return bool(os.environ.get("PBI_DATASET_ID") and os.environ.get("PBI_GROUP_ID"))


def _ttl():
    try:
        return int(os.environ.get("PBI_CACHE_MINUTES", "60")) * 60
    except ValueError:
        return 3600


def _save_copy(items, updated):
    path = _cache_file()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"updated": updated, "items": items}, f, ensure_ascii=False)
    except OSError:
        logger.exception("Не вдалося зберегти копію довідника статей")


def _load_copy():
    try:
        with open(_cache_file(), encoding="utf-8") as f:
            data = json.load(f)
        items = data.get("items") or None
        for i in items or []:  # копії старого формату (без відділів)
            i.setdefault("departments", [])
            i.setdefault("kind", "allocated" if i["departments"] else "direct")
        return items, data.get("updated")
    except (OSError, ValueError):
        return None, None


def refresh():
    """Завантажити довідник із Power BI зараз (синхронно). Повертає True, якщо вдалося."""
    with _lock:
        if not _configured():
            _state.update(items=_mock_items(), loaded_at=time.time(), source="mock", updated=None, error=None)
            return True
        try:
            items = extract_items(_query_rows())
            if not items:
                raise RuntimeError("Power BI повернув порожній довідник (або всі рядки визначено як групи)")
            updated = clock.now().isoformat(timespec="seconds")
            _state.update(items=items, loaded_at=time.time(), source="powerbi", updated=updated, error=None)
            _save_copy(items, updated)
            return True
        except Exception as e:  # мережа, права, формат — показуємо в адмінці
            logger.exception("Помилка завантаження довідника статей з Power BI")
            _state["error"] = str(e)
            _state["loaded_at"] = time.time()  # не довбати Power BI на кожен запит після помилки
            if _state["items"] is None:
                items, updated = _load_copy()
                _state.update(items=items or [], source="copy" if items else "empty", updated=updated)
            return False


def _refresh_in_background():
    """Запустити оновлення у фоні (не більше одного одночасно)."""
    with _bg_lock:
        if _state["refreshing"]:
            return
        _state["refreshing"] = True

    def run():
        try:
            refresh()
        finally:
            _state["refreshing"] = False

    threading.Thread(target=run, name="expense-items-refresh", daemon=True).start()


def get_items():
    """Поточний довідник. Застарілий кеш віддається одразу, а оновлення йде у фоні."""
    if _state["items"] is None:
        items, updated = _load_copy() if _configured() else (None, None)
        if items:
            # Після перезапуску: одразу віддаємо збережену копію, свіжі дані — у фоні
            _state.update(items=items, loaded_at=0.0, source="copy", updated=updated)
            _refresh_in_background()
        else:
            refresh()  # немає ні пам'яті, ні копії — доводиться чекати
    elif time.time() - _state["loaded_at"] > _ttl():
        _refresh_in_background()
    return _state["items"] or []


def get_map():
    return {i["code"]: i for i in get_items()}


def get_name(code):
    item = get_map().get(code)
    return item["name"] if item else None


def get_departments():
    """Усі відділи довідника: [(відділ, кількість статей)], за алфавітом."""
    counts = {}
    for i in get_items():
        for d in i["departments"]:
            counts[d] = counts.get(d, 0) + 1
    return sorted(counts.items(), key=lambda kv: kv[0].lower())


def status():
    items = get_items()
    return {
        "source": _state["source"],
        "updated": _state["updated"],
        "count": len(items),
        "direct": sum(1 for i in items if i["kind"] == "direct"),
        "allocated": sum(1 for i in items if i["kind"] == "allocated"),
        "departments": len(get_departments()),
        "error": _state["error"],
        "configured": _configured(),
        "refreshing": _state["refreshing"],
    }
