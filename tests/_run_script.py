"""Запуск одного сценарію tests/scripts/*_test.py (викликає tests/test_scripts.py, кожен — в окремому процесі).

Сценарії надсилають форми тестовим клієнтом без CSRF-токена, тому перевірку токена тут вимкнено.
Сам захист перевіряє csrf_test.py — для нього запуск із прапорцем --csrf лишає перевірку ввімкненою.

--sql: той самий сценарій зі сховищем у базі (SQLite, DATABASE_URL задає test_scripts.py). Перед першим
зверненням до бази в неї записуються демо-заявки з тими самими номерами, що й у пам'яті, тож сценарій
бачить однакові дані в обох режимах.
"""
import os
import runpy
import sys
from copy import deepcopy

sys.path.insert(0, os.getcwd())  # корінь проєкту (test_scripts.py запускає з нього)

script = sys.argv[1]
if "--csrf" not in sys.argv:
    from flask_wtf.csrf import CSRFProtect
    CSRFProtect.protect = lambda self: None
if "--sql" in sys.argv:
    import storage.demo as demo
    import storage.sql as dbs
    real_engine, seeded = dbs._engine, []

    def _engine():
        eng = real_engine()
        if not seeded:
            seeded.append(True)
            if dbs.last_id() == 0:
                for r in sorted(demo._REQUESTS.values(), key=lambda r: r["id"]):
                    r = deepcopy(r)
                    want = r.pop("id")
                    r.pop("number")
                    assert dbs.save_request(r) == want, "демо-заявки мають отримати ті самі номери"
        return eng

    dbs._engine = _engine
sys.argv = [script]
runpy.run_path(script, run_name="__main__")
