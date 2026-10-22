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

# Ролі тестових користувачів сценарії задають так, як колись у Azure: ID груп у claims["groups"] і змінні GROUP_*.
# Перед кожним запитом вони записуються в ролі системи (user_roles); ролі, змінені в адмінці, не перезаписуються,
# доки сценарій не змінить групи цієї людини.
GROUP_ENV = {"admin": ("GROUP_ADMIN",), "initiator": ("GROUP_INITIATOR",), "acc_cash": ("GROUP_ACC_CASH",),
             "acc_resident": ("GROUP_ACC_RESIDENT", "GROUP_ACCOUNTANT"), "acc_nonresident": ("GROUP_ACC_NONRESIDENT",),
             "cfo": ("GROUP_CFO",)}
_seeded = {}


def seed_roles(email, groups, name=None, uid=None):
    import user_roles
    roles = {r for r, envs in GROUP_ENV.items() if any(os.environ.get(e) and os.environ[e] in groups for e in envs)}
    rec = user_roles.find_by_email(email)
    uid = rec["id"] if rec else (uid or email.lower())
    if _seeded.get(uid) != (frozenset(groups), email) or rec is None:
        user_roles.set_roles(uid, email, name or (rec or {}).get("name") or email, roles, "test")
        _seeded[uid] = (frozenset(groups), email)


import flask  # noqa: E402

_preprocess = flask.Flask.preprocess_request


def _preprocess_with_roles(self):
    import web.auth as wa
    try:
        claims = wa.auth._auth.get_user()
    except Exception:
        claims = None
    if claims and "groups" in claims and claims.get("preferred_username"):
        seed_roles(claims["preferred_username"], claims["groups"], claims.get("name"), claims.get("oid"))
    return _preprocess(self)


flask.Flask.preprocess_request = _preprocess_with_roles
sys.argv = [script]
runpy.run_path(script, run_name="__main__")
