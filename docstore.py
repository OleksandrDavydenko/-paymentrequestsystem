"""Збереження налаштувань і довідників системи: у базі даних, а без неї — у JSON-файлах (як раніше).

read(name, path) / write(name, path, data) — однаковий інтерфейс для обох варіантів.
Коли базу щойно підключили, перший read переносить наявний JSON-файл у базу (файл лишається як резервна копія).
"""
import json
import logging
import os

import db_sql

logger = logging.getLogger(__name__)


def _read_file(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def read(name, path):
    """Вміст документа. Кидає OSError, якщо його ще немає (як і відкриття відсутнього файлу)."""
    if not db_sql.configured():
        return _read_file(path)
    data = db_sql.get_document(name)
    if data is not None:
        return data
    try:
        data = _read_file(path)  # разове перенесення з файлу
    except FileNotFoundError:
        raise
    except (OSError, ValueError):
        logger.exception("Не вдалося прочитати %s для перенесення в базу", path)
        raise FileNotFoundError(path)
    db_sql.put_document(name, data)
    logger.info("Перенесено в базу: %s (%s)", name, path)
    return data


def write(name, path, data):
    if db_sql.configured():
        db_sql.put_document(name, data)
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)  # атомарно: файл ніколи не буде наполовину записаним
