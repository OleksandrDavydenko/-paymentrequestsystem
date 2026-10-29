"""Обмеження частоти запитів до API (обробка 1С, Telegram-бот): у пам'яті процесу, ключ — IP.

Limiter(limit, seconds).hit(key) — врахувати запит; False, якщо за останні seconds їх уже limit.
blocked(key) — чи вичерпано ліміт, не враховуючи запит (для невдалих спроб ключа: рахуємо лише їх).
Кілька процесів сайту мають кожен свій лічильник — для захисту від перебору й зациклених клієнтів цього досить.
"""
import threading
import time
from collections import deque


class Limiter:
    def __init__(self, limit, seconds):
        self.limit, self.seconds = limit, seconds
        self._hits = {}
        self._lock = threading.Lock()
        self._cleaned = 0.0

    def _recent(self, key, now):
        q = self._hits.get(key)
        while q and q[0] <= now - self.seconds:
            q.popleft()
        return q

    def _cleanup(self, now):
        """Раз на вікно — прибрати ключі без свіжих запитів (щоб словник не ріс)."""
        if now - self._cleaned < self.seconds:
            return
        self._cleaned = now
        for key in [k for k, q in self._hits.items() if not q or q[-1] <= now - self.seconds]:
            del self._hits[key]

    def hit(self, key):
        now = time.monotonic()
        with self._lock:
            self._cleanup(now)
            q = self._recent(key, now)
            if q is None:
                q = self._hits[key] = deque()
            if len(q) >= self.limit:
                return False
            q.append(now)
            return True

    def blocked(self, key):
        now = time.monotonic()
        with self._lock:
            q = self._recent(key, now)
            return bool(q) and len(q) >= self.limit

    def reset(self):
        with self._lock:
            self._hits.clear()


# Обробка 1С: «Заповнити» — кілька запитів, «Створити документи» — запит на кожну платіжку; бухгалтери з одного
# офісу виходять з однієї IP-адреси, тож ліміт із запасом. Невірний ключ — не більше 20 спроб за 10 хвилин.
API_REQUESTS = Limiter(600, 60)
API_BAD_KEYS = Limiter(20, 600)
