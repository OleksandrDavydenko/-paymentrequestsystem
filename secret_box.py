"""Шифрування секретів, які адмін вводить в інтерфейсі (наприклад, пароль SMTP).

Ключ виводиться з FLASK_SECRET_KEY (змінна App Service), тому файл налаштувань
без цього ключа нічого не розкриває. Якщо FLASK_SECRET_KEY змінити — збережені
секрети треба ввести заново.
"""
import base64
import hashlib
import os

from cryptography.fernet import Fernet, InvalidToken


def _fernet():
    key = hashlib.sha256(("prs-secret-box:" + os.environ["FLASK_SECRET_KEY"]).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt(value):
    return _fernet().encrypt(value.encode()).decode() if value else ""


def decrypt(token):
    """Розшифрувати; None — якщо секрету немає або ключ змінився."""
    if not token:
        return None
    try:
        return _fernet().decrypt(token.encode()).decode()
    except (InvalidToken, ValueError):
        return None
