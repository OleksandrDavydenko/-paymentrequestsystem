"""Сховище вкладених файлів заявок: Azure Blob Storage, а без нього — папка на диску (як раніше).

Вмикається змінними оточення App Service:
  BLOB_ACCOUNT_URL=https://<сховище>.blob.core.windows.net, BLOB_CONTAINER=attachments.
Вхід без ключів — керованою ідентичністю сайту (роль «Storage Blob Data Contributor» на сховищі).
Файли, що вже лежать на диску (/home/data/uploads), переносяться в Blob у фоні після запуску
й при першому відкритті; копії на диску лишаються як резервні.
"""
import logging
import mimetypes
import os
import threading

logger = logging.getLogger(__name__)

_container_client = None
_lock = threading.Lock()
_migration = {"started": False, "moved": 0, "error": None}


def upload_dir():
    return os.environ.get("UPLOAD_DIR") or (
        "/home/data/uploads" if os.environ.get("WEBSITE_SITE_NAME")  # змінна є лише на App Service
        else os.path.join(os.path.dirname(__file__), "uploads"))


def configured():
    return bool(os.environ.get("BLOB_ACCOUNT_URL") and os.environ.get("BLOB_CONTAINER"))


def _container():
    """Клієнт контейнера Blob (створюється один раз)."""
    global _container_client
    if _container_client is None:
        with _lock:
            if _container_client is None:
                from azure.identity import DefaultAzureCredential, ManagedIdentityCredential
                from azure.storage.blob import BlobServiceClient
                cred = ManagedIdentityCredential() if os.environ.get("IDENTITY_ENDPOINT") else DefaultAzureCredential()
                service = BlobServiceClient(os.environ["BLOB_ACCOUNT_URL"].rstrip("/"), credential=cred)
                _container_client = service.get_container_client(os.environ["BLOB_CONTAINER"])
    return _container_client


def _name(file_id, ext):
    return f"{file_id}.{ext}"


def _local_path(file_id, ext):
    return os.path.join(upload_dir(), _name(file_id, ext))


def save(file_id, ext, stream):
    """Зберегти файл (stream — відкритий файл / werkzeug FileStorage.stream). Кидає виняток, якщо не вдалося."""
    if configured():
        from azure.storage.blob import ContentSettings
        content_type = mimetypes.guess_type(_name(file_id, ext))[0] or "application/octet-stream"
        _container().upload_blob(_name(file_id, ext), stream, overwrite=True,
                                 content_settings=ContentSettings(content_type=content_type))
        return
    os.makedirs(upload_dir(), exist_ok=True)
    with open(_local_path(file_id, ext), "wb") as f:
        while chunk := stream.read(1024 * 1024):
            f.write(chunk)


def read(file_id, ext):
    """Вміст файлу (bytes) або None, якщо його немає. Файл, що лежить лише на диску, — заодно переноситься в Blob."""
    local = _local_path(file_id, ext)
    if configured():
        from azure.core.exceptions import ResourceNotFoundError
        try:
            return _container().download_blob(_name(file_id, ext)).readall()
        except ResourceNotFoundError:
            pass
        if os.path.exists(local):
            with open(local, "rb") as f:
                data = f.read()
            try:
                with open(local, "rb") as f:
                    save(file_id, ext, f)
            except Exception:
                logger.exception("Не вдалося перенести %s у Blob", local)
            return data
        return None
    if not os.path.exists(local):
        return None
    with open(local, "rb") as f:
        return f.read()


def delete(file_id, ext):
    if configured():
        from azure.core.exceptions import ResourceNotFoundError
        try:
            _container().delete_blob(_name(file_id, ext))  # у сховищі ввімкнено «м'яке видалення» — 7 днів
        except ResourceNotFoundError:
            pass
    try:
        os.remove(_local_path(file_id, ext))
    except FileNotFoundError:
        pass


def migrate_local():
    """Перенести в Blob файли, що лежать на диску й ще не в Blob. Повертає кількість перенесених."""
    if not configured() or not os.path.isdir(upload_dir()):
        return 0
    existing = {b.name for b in _container().list_blobs()}
    moved = 0
    for name in sorted(os.listdir(upload_dir())):
        path = os.path.join(upload_dir(), name)
        if name in existing or not os.path.isfile(path) or name.endswith(".tmp"):
            continue
        file_id, _, ext = name.rpartition(".")
        with open(path, "rb") as f:
            save(file_id, ext, f)
        moved += 1
    return moved


def start_migration():
    """Один раз після запуску — у фоні, щоб не затримувати сайт."""
    if _migration["started"] or not configured():
        return
    with _lock:  # кілька потоків обробляють запити одночасно — запускаємо рівно один раз
        if _migration["started"]:
            return
        _migration["started"] = True

    def run():
        try:
            _migration["moved"] = migrate_local()
            if _migration["moved"]:
                logger.info("Перенесено в Blob файлів: %s", _migration["moved"])
        except Exception as e:
            _migration["error"] = str(e)
            logger.exception("Не вдалося перенести файли в Blob")

    threading.Thread(target=run, name="blob-migration", daemon=True).start()


def status():
    """Для /health."""
    if not configured():
        return {"files": "disk"}
    try:
        _container().get_container_properties()
        info = {"files": "blob", "files_migrated": _migration["moved"]}
        if _migration["error"]:
            info["files_migration_error"] = _migration["error"][:300]
        return info
    except Exception as e:
        return {"files": "blob", "files_error": str(e)[:300]}
