"""Сценарні тести системи: кожен tests/scripts/*_test.py запускається в окремому процесі.

Сценарій друкує «PASS …» / «FAIL …» і в кінці «ALL OK» (або падає на assert). Тест вважається
пройденим, якщо процес завершився без помилки і немає жодного рядка «FAIL».

Кожен сценарій отримує власну тимчасову папку для файлів налаштувань і довідників, фіктивні
облікові дані Entra ID і не бачить робочих змінних (бази, Power BI, Blob, Telegram, пошти).

Кожен сценарій проганяється зі сховищем у пам'яті (memory) і в базі SQLite (sql), а якщо задано
TEST_MSSQL_URL — ще й на SQL Server (mssql), як в Azure: так перевіряється, що робочий режим поводиться
так само, як демо, і що запити працюють на тій самій СУБД, що й на сайті.

Запуск:  python -m pytest -n auto              (усі, паралельно)
         python -m pytest -k "stage30 and sql"  (один)
"""
import contextlib
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = sorted((ROOT / "tests" / "scripts").glob("*_test.py"))

# Робочі змінні, які тестам бачити не можна (тести завжди працюють на демо-даних у пам'яті)
PRIVATE_PREFIXES = ("DATABASE_URL", "AZURE_SQL", "BLOB_", "PBI_", "TELEGRAM_", "SMTP", "MAIL_", "WEBSITE_",
                    "APP_BASE_URL", "GROUP_", "ADMIN_EMAILS", "HANDOVER_REMINDERS", "CLIENT_", "TENANT_",
                    "FLASK_SECRET_KEY", "REDIRECT_URI")
FILE_ENV = {
    "APP_SETTINGS_FILE": "app_settings.json", "COUNTERPARTIES_PENDING_FILE": "counterparties.json",
    "EXPENSE_ITEMS_CACHE": "expense_items.json", "ORG_STRUCTURE_FILE": "org_structure.json",
    "TELEGRAM_LINKED_FILE": "telegram_linked.json", "USER_BUDGET_FILE": "user_budget.json",
    "USER_DIRECTORY_FILE": "user_directory.json", "USER_ONEC_FILE": "user_onec.json",
    "USER_PREFS_FILE": "user_prefs.json", "USER_ROLES_FILE": "user_roles.json", "DEPT_SETTINGS_FILE": "dept_settings.json",
}


def _env(tmp):
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith(PRIVATE_PREFIXES)}
    env.update(CLIENT_ID="test", TENANT_ID="test", CLIENT_SECRET="test", FLASK_SECRET_KEY="test",
               REDIRECT_URI="http://localhost:5000/getAToken", PYTHONIOENCODING="utf-8",
               PBI_CACHE_DIR=str(tmp), UPLOAD_DIR=str(tmp / "uploads"))
    env.update({k: str(tmp / name) for k, name in FILE_ENV.items()})
    return env


# Перевіряють JSON-файли налаштувань — лише без бази (з базою налаштування зберігаються в ній)
FILE_BASED = {"stage10_test", "stage11_test", "stage12_test", "stage15_test"}
# Самі підключають базу: TEST_DATABASE_URL (SQL Server) або власний файл SQLite
OWN_DB = {"db_test", "storage_equiv_test", "multiprocess_test", "schema_upgrade_test"}
# SQL Server для тестів (у GitHub Actions — контейнер): адреса сервера з базою master, напр.
# mssql+pyodbc://sa:…@127.0.0.1:1433/master?driver=ODBC+Driver+18+for+SQL+Server&TrustServerCertificate=yes
MSSQL_URL = os.environ.get("TEST_MSSQL_URL")


def _modes(stem):
    modes = ["memory"]
    if stem not in FILE_BASED | OWN_DB:
        modes.append("sql")
    if MSSQL_URL and stem not in FILE_BASED:
        modes.append("mssql")
    return modes


CASES = [pytest.param(s, mode, id=f"{s.stem}-{mode}") for s in SCRIPTS for mode in _modes(s.stem)]


@contextlib.contextmanager
def _mssql_database():
    """Окрема чиста база на SQL Server для одного сценарію; після нього — видаляється."""
    import sqlalchemy as sa
    name = "t_" + uuid.uuid4().hex[:12]
    server = sa.create_engine(MSSQL_URL, isolation_level="AUTOCOMMIT")
    with server.connect() as c:
        c.execute(sa.text(f"CREATE DATABASE [{name}]"))
    try:
        yield str(sa.engine.make_url(MSSQL_URL).set(database=name).render_as_string(hide_password=False))
    finally:
        with server.connect() as c:
            c.execute(sa.text(f"ALTER DATABASE [{name}] SET SINGLE_USER WITH ROLLBACK IMMEDIATE"))
            c.execute(sa.text(f"DROP DATABASE [{name}]"))
        server.dispose()


@pytest.mark.parametrize("script,mode", CASES)
def test_script(script, mode, tmp_path):
    args = [sys.executable, str(ROOT / "tests" / "_run_script.py"), str(script)]
    if "csrf" in script.stem:
        args.append("--csrf")
    env = _env(tmp_path)
    with contextlib.ExitStack() as stack:
        url = None
        if mode == "sql":
            url = "sqlite:///" + str(tmp_path / "test.db").replace("\\", "/")
        elif mode == "mssql":
            url = stack.enter_context(_mssql_database())
        if url and script.stem in OWN_DB:
            env["TEST_DATABASE_URL"] = url
        elif url:
            args.append("--sql")
            env["DATABASE_URL"] = url
        r = subprocess.run(args, cwd=ROOT, env=env, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=600)
    failed = [line for line in r.stdout.splitlines() if line.startswith("FAIL") or line.endswith("FAILED")]
    assert r.returncode == 0 and not failed, "\n".join(failed) + "\n\n" + (r.stdout + r.stderr)[-6000:]
