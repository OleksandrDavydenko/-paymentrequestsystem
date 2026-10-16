"""Сеанси входу в базі (таблиця web_sessions) — спільні для всіх процесів і екземплярів сайту.

Підключається до Flask-Session як бекенд cachelib (SESSION_TYPE="cachelib"). Без бази сеанси
лишаються у файлах на диску, як раніше. Прострочені сеанси прибирає щоденна фонова задача (jobs.py).
Дані сеансу (токени входу Microsoft) серіалізуються pickle, як і у файловому сховищі Flask-Session:
їх пише лише сам сайт, у браузер потрапляє тільки випадковий ідентифікатор сеансу.
"""
import pickle
from datetime import timedelta

from cachelib.base import BaseCache

import clock
import db_sql

DEFAULT_TIMEOUT = 31 * 24 * 3600


class DbSessionCache(BaseCache):
    def __init__(self, default_timeout=DEFAULT_TIMEOUT):
        super().__init__(default_timeout=default_timeout)

    def get(self, key):
        raw = db_sql.session_get(key, clock.now())
        return pickle.loads(raw) if raw is not None else None

    def set(self, key, value, timeout=None):
        timeout = self._normalize_timeout(timeout) or DEFAULT_TIMEOUT
        db_sql.session_set(key, pickle.dumps(value, protocol=pickle.HIGHEST_PROTOCOL),
                           clock.now() + timedelta(seconds=timeout))
        return True

    def add(self, key, value, timeout=None):
        if self.has(key):
            return False
        return self.set(key, value, timeout)

    def delete(self, key):
        db_sql.session_delete(key)
        return True

    def has(self, key):
        return db_sql.session_get(key, clock.now()) is not None

    def clear(self):
        return False  # усі сеанси разом не прибираємо


def cleanup():
    """Прибрати прострочені сеанси. Повертає кількість."""
    return db_sql.session_cleanup(clock.now())
