"""Запуск одного сценарію tests/scripts/*_test.py (викликає tests/test_scripts.py, кожен — в окремому процесі).

Сценарії надсилають форми тестовим клієнтом без CSRF-токена, тому перевірку токена тут вимкнено.
Сам захист перевіряє csrf_test.py — для нього запуск із прапорцем --csrf лишає перевірку ввімкненою.
"""
import runpy
import sys

script = sys.argv[1]
if "--csrf" not in sys.argv:
    from flask_wtf.csrf import CSRFProtect
    CSRFProtect.protect = lambda self: None
sys.argv = [script]
runpy.run_path(script, run_name="__main__")
