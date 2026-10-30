"""Фонові задачі сайту, що виконуються рівно в одному процесі навіть тоді, коли процесів кілька.

run(name, every, fn): процес бере «оренду» задачі в базі (таблиця job_lease) на every; поки оренда
діє, інші процеси задачу пропускають. Якщо процес зупинився посеред роботи — оренда просто спливе
й задачу виконає інший. Без бази (один процес) — fn виконується щоразу.
"""
import logging
import os
import socket
import uuid
from datetime import timedelta

import clock
import db_sql

logger = logging.getLogger(__name__)

OWNER = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:6]}"


def run(name, every, fn):
    """Виконати fn, якщо цей процес узяв задачу name на проміжок every (timedelta). Повертає результат fn
    або None, якщо задачу зараз виконує / нещодавно виконав інший процес. Кожен запуск (тривалість, підсумок,
    помилка) — у журнал роботи сайту (ops_log)."""
    if db_sql.configured():
        now = clock.now()
        if not db_sql.lease_acquire(name, OWNER, now, now + every):
            return None
        logger.info("Фонова задача %s (%s)", name, OWNER)
    import ops_log
    with ops_log.timed(name) as info:
        info["result"] = fn()
    return info["result"]


def status():
    """Для адмінки / діагностики: коли й який процес востаннє виконував задачі."""
    return db_sql.lease_info() if db_sql.configured() else {}


HOUR = timedelta(hours=1)
DAY = timedelta(days=1)
