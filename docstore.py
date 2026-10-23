"""Збереження налаштувань і довідників системи: у базі даних, а без неї — у JSON-файлах (як раніше).

read(name, path) / write(name, path, data) — однаковий інтерфейс для обох варіантів.
Коли базу щойно підключили, перший read переносить наявний JSON-файл у базу (файл лишається як резервна копія).

Кілька процесів сайту (з базою):
  • кожен документ має версію; модулі тримають його копію в пам'яті й реєструють reload (register);
  • sync() на початку кожного запиту одним маленьким запитом перевіряє версії й перечитує лише змінені
    документи — зміна налаштувань в одному процесі одразу видна в інших;
  • write() записує, лише якщо документ у базі не змінився з моменту читання. Якщо змінився — зміни
    зливаються автоматично (merge3): що змінили ми — наше, що змінив інший процес — його; той самий ключ,
    змінений обома, — наше (останній запис). Злитий результат записується в той самий об'єкт data.
"""
import json
import logging
import os
import threading
from contextlib import contextmanager
from copy import deepcopy

import db_sql

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_known = {}      # назва -> {"version": версія в базі, "base": вміст на момент читання/запису}
_reloaders = {}  # назва -> функція, що скидає копію модуля (наступне звернення перечитає документ)
_MISSING = object()
_batch = threading.local()  # відкладені записи масової дії в цьому потоці: {назва: (path, data)}


@contextmanager
def batch():
    """Масова дія (напр. додати сотню співробітників): документи, змінені всередині блоку, записуються в базу
    по одному разу в кінці, а не на кожну зміну. Лише для поточного потоку; вкладені блоки — одне ціле."""
    if getattr(_batch, "pending", None) is not None:
        yield
        return
    _batch.pending = {}
    try:
        yield
    finally:
        pending, _batch.pending = _batch.pending, None
        for name, (path, data) in pending.items():
            write(name, path, data)


def register(name, reload_fn):
    """Модуль, що тримає копію документа name, повідомляє, як її перечитати (див. sync)."""
    _reloaders[name] = reload_fn


def _read_file(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _remember(name, version, data):
    with _lock:
        _known[name] = {"version": version, "base": deepcopy(data)}


def read(name, path):
    """Вміст документа. Кидає OSError, якщо його ще немає (як і відкриття відсутнього файлу)."""
    if not db_sql.configured():
        return _read_file(path)
    data, version = db_sql.get_document_v(name)
    if data is not None:
        _remember(name, version, data)
        return data
    try:
        data = _read_file(path)  # разове перенесення з файлу
    except FileNotFoundError:
        raise
    except (OSError, ValueError):
        logger.exception("Не вдалося прочитати %s для перенесення в базу", path)
        raise FileNotFoundError(path)
    db_sql.put_document(name, data)
    _, version = db_sql.get_document_v(name)
    _remember(name, version, data)
    logger.info("Перенесено в базу: %s (%s)", name, path)
    return data


def merge3(base, ours, theirs):
    """Злиття змін двох процесів відносно спільного початкового стану (лише для словників;
    інші значення: змінене однією стороною — її, обома — наше)."""
    if not (isinstance(ours, dict) and isinstance(theirs, dict)):
        return ours
    base = base if isinstance(base, dict) else {}
    out = {}
    for key in list(theirs) + [k for k in ours if k not in theirs]:
        b, o, t = base.get(key, _MISSING), ours.get(key, _MISSING), theirs.get(key, _MISSING)
        if o == b:
            value = t                    # ми не змінювали — беремо їхнє
        elif t == b:
            value = o                    # вони не змінювали — наше
        elif isinstance(o, dict) and isinstance(t, dict):
            value = merge3(b if isinstance(b, dict) else {}, o, t)
        else:
            value = o                    # змінили обидва — наше
        if value is not _MISSING:
            out[key] = value
    return out


def write(name, path, data):
    pending = getattr(_batch, "pending", None)
    if pending is not None:  # усередині batch() — запишемо в кінці (data — той самий об'єкт модуля)
        pending[name] = (path, data)
        return
    if not db_sql.configured():
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)  # атомарно: файл ніколи не буде наполовину записаним
        return
    known = _known.get(name)
    expected = known["version"] if known else None
    base = known["base"] if known else {}
    ours = data
    for _ in range(10):
        done, version = db_sql.put_document_v(name, ours, expected)
        if done:
            break
        theirs, expected = db_sql.get_document_v(name)
        ours = merge3(base, ours, theirs if theirs is not None else {})
        base = theirs if theirs is not None else {}
        logger.info("Документ %s змінено іншим процесом — зміни злито", name)
    else:
        raise RuntimeError(f"Не вдалося записати документ {name}: його постійно змінюють інші процеси")
    if ours is not data and isinstance(data, dict):
        data.clear()
        data.update(ours)  # модуль тримає посилання на data — тепер у ньому злитий вміст
    _remember(name, version, data)


def sync():
    """Перечитати документи, які змінили інші процеси (на початку запиту; без бази — нічого)."""
    if not db_sql.configured() or not _known:
        return []
    versions = db_sql.document_versions()
    changed = [n for n, k in list(_known.items()) if versions.get(n) is not None and versions[n] != k["version"]]
    for name in changed:
        with _lock:
            _known.pop(name, None)
        fn = _reloaders.get(name)
        if fn:
            fn()
    return changed
