"""Поточний час у часовому поясі компанії (сервер Azure працює в UTC).

Повертає «наївні» datetime/date у київському часі — у тому ж вигляді, що й решта дат системи.
Пояс можна змінити змінною APP_TIMEZONE.
"""
import os
from datetime import datetime
from zoneinfo import ZoneInfo

TZ = ZoneInfo(os.environ.get("APP_TIMEZONE", "Europe/Kyiv"))


def now():
    return datetime.now(TZ).replace(tzinfo=None)


def today():
    return now().date()
