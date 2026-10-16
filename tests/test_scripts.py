"""Сценарні тести системи: кожен tests/scripts/*_test.py запускається в окремому процесі.

Сценарій друкує «PASS …» / «FAIL …» і в кінці «ALL OK» (або падає на assert). Тест вважається
пройденим, якщо процес завершився без помилки і немає жодного рядка «FAIL».

Кожен сценарій отримує власну тимчасову папку для файлів налаштувань і довідників, фіктивні
облікові дані Entra ID і не бачить робочих змінних (бази, Power BI, Blob, Telegram, пошти).

Запуск:  python -m pytest            (усі)
         python -m pytest -k stage30  (один)
"""
import os
import subprocess
import sys
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
    "USER_PREFS_FILE": "user_prefs.json",
}


def _env(tmp):
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith(PRIVATE_PREFIXES)}
    env.update(CLIENT_ID="test", TENANT_ID="test", CLIENT_SECRET="test", FLASK_SECRET_KEY="test",
               REDIRECT_URI="http://localhost:5000/getAToken", PYTHONIOENCODING="utf-8",
               PBI_CACHE_DIR=str(tmp), UPLOAD_DIR=str(tmp / "uploads"))
    env.update({k: str(tmp / name) for k, name in FILE_ENV.items()})
    return env


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.stem)
def test_script(script, tmp_path):
    args = [sys.executable, str(ROOT / "tests" / "_run_script.py"), str(script)]
    if "csrf" in script.stem:
        args.append("--csrf")
    r = subprocess.run(args, cwd=ROOT, env=_env(tmp_path), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=600)
    failed = [line for line in r.stdout.splitlines() if line.startswith("FAIL") or line.endswith("FAILED")]
    assert r.returncode == 0 and not failed, "\n".join(failed) + "\n\n" + (r.stdout + r.stderr)[-6000:]
