"""Кешований довідник з таблиці семантичної моделі Power BI (дані з 1С).

Та сама схема, що й для статей витрат (expense_items): результат тримається в пам'яті й
зберігається у файл; після перезапуску одразу віддається збережена копія, а свіжі дані
підтягуються у фоні. Якщо Power BI не налаштований — мокові дані.
"""
import json
import logging
import os
import threading
import time

import clock
import expense_items

logger = logging.getLogger(__name__)


def truthy(value):
    """Позначка з Power BI (True/False, 1/0, «так»/«ні») -> bool."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value or "").strip().lower() in ("1", "true", "так", "yes", "y", "истина")


class CachedTable:
    def __init__(self, key, table_env, default_table, parse, mock):
        """key — для файлу копії; parse(rows) -> список записів; mock() -> записи без Power BI."""
        self.key, self.table_env, self.default_table = key, table_env, default_table
        self.parse, self.mock = parse, mock
        self._lock, self._bg_lock = threading.Lock(), threading.Lock()
        self._state = {"items": None, "loaded_at": 0.0, "source": None, "updated": None, "error": None,
                       "refreshing": False}

    @property
    def table(self):
        return os.environ.get(self.table_env) or self.default_table

    def _cache_file(self):
        base = "/home/data" if os.environ.get("WEBSITE_SITE_NAME") else os.path.join(os.path.dirname(__file__), "data")
        return os.path.join(os.environ.get("PBI_CACHE_DIR") or base, f"pbi_{self.key}.json")

    def _save_copy(self, items, updated):
        path = self._cache_file()
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"updated": updated, "items": items}, f, ensure_ascii=False)
            os.replace(tmp, path)
        except OSError:
            logger.exception("Не вдалося зберегти копію довідника %s", self.key)

    def _load_copy(self):
        try:
            with open(self._cache_file(), encoding="utf-8") as f:
                data = json.load(f)
            return data.get("items") or None, data.get("updated")
        except (OSError, ValueError):
            return None, None

    def refresh(self):
        """Завантажити з Power BI зараз (синхронно). Повертає True, якщо вдалося."""
        with self._lock:
            if not expense_items._configured():
                self._state.update(items=self.mock(), loaded_at=time.time(), source="mock", updated=None, error=None)
                return True
            try:
                items = self.parse(expense_items.query_table(self.table))
                if not items:
                    raise RuntimeError(f"Power BI повернув порожню таблицю «{self.table}» або в ній немає потрібних колонок")
                updated = clock.now().isoformat(timespec="seconds")
                self._state.update(items=items, loaded_at=time.time(), source="powerbi", updated=updated, error=None)
                self._save_copy(items, updated)
                return True
            except Exception as e:  # мережа, права, формат — показуємо в адмінці
                logger.exception("Помилка завантаження довідника %s з Power BI", self.key)
                self._state["error"] = str(e)
                self._state["loaded_at"] = time.time()
                if self._state["items"] is None:
                    items, updated = self._load_copy()
                    self._state.update(items=items or [], source="copy" if items else "empty", updated=updated)
                return False

    def _refresh_in_background(self):
        with self._bg_lock:
            if self._state["refreshing"]:
                return
            self._state["refreshing"] = True

        def run():
            try:
                self.refresh()
            finally:
                self._state["refreshing"] = False

        threading.Thread(target=run, name=f"pbi-{self.key}-refresh", daemon=True).start()

    def items(self):
        """Поточні записи. Застарілий кеш віддається одразу, оновлення — у фоні."""
        st = self._state
        if st["items"] is None:
            items, updated = self._load_copy() if expense_items._configured() else (None, None)
            if items:
                st.update(items=items, loaded_at=0.0, source="copy", updated=updated)
                self._refresh_in_background()
            else:
                self.refresh()
        elif time.time() - st["loaded_at"] > expense_items._ttl():
            self._refresh_in_background()
        return st["items"] or []

    def status(self):
        items = self.items()
        return {"source": self._state["source"], "updated": self._state["updated"], "count": len(items),
                "active": sum(1 for i in items if not i.get("deleted")), "error": self._state["error"],
                "table": self.table, "refreshing": self._state["refreshing"]}
