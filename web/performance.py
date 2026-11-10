"""Швидкість сторінок: легкі запити без бази, кеш статичних файлів, шаблони скомпільовані заздалегідь.

Легкі запити — статичні файли (стилі, скрипти, значки), маніфест ярлика й favicon. Браузер просить їх на кожній
сторінці, по кілька одночасно. Раніше кожен такий запит читав сеанс входу з бази, оновлював його там (Flask-Session
оновлює сеанс на кожен запит) і перевіряв зміни налаштувань — 2–3 звернення до бази на файл, а одночасні запити ще й
відкривали нові з'єднання (по кілька секунд). Тепер для них сеанс не відкривається (порожній сеанс Flask, який і не
зберігається), а налаштування не перечитуються (background._sync_shared_state).

Кеш: адреса статичного файлу з шаблону (url_for('static', …)) має версію — ?v=<хеш вмісту>. Такий файл браузер
тримає в кеші рік і не перепитує; змінився вміст — змінилася адреса. Файли без версії (картинки зі style.css,
значки з маніфесту) — як і раніше, браузер перевіряє, чи не змінилися.

Шаблони: Jinja компілює шаблон при першому показі в процесі — після перезапуску сайту перша людина, що відкрила картку
заявки, чекала на це кілька секунд. Тепер шаблони компілюються у фоні одразу після старту (warm_templates, з
background), а якщо компіляція таки трапилась під час запиту — її час видно в журналі повільних запитів.
"""
import hashlib
import logging
import os
import threading
import time

from flask import request

import ops_log

logger = logging.getLogger(__name__)

LIGHT_PATHS = ("/site.webmanifest", "/favicon.ico")
STATIC_MAX_AGE = 365 * 24 * 3600  # файл з версією в адресі не змінюється — кеш на рік
_static_prefix = "/static/"
_versions = {}
_versions_lock = threading.Lock()


def is_light(req=None):
    """Запит статичного файлу, маніфесту чи favicon — без сеансу входу й без звернень до бази."""
    path = (req or request).path
    return path.startswith(_static_prefix) or path in LIGHT_PATHS


class LightSessions:
    """Інтерфейс сеансів Flask-Session, який для легких запитів сеанс не відкриває (Flask тоді бере порожній
    «нульовий» сеанс і не зберігає його). Решта — як у Flask-Session."""

    def __init__(self, inner):
        self._inner = inner

    def open_session(self, app, req):
        if is_light(req):
            return None
        return self._inner.open_session(app, req)

    def __getattr__(self, name):
        return getattr(self._inner, name)


def static_version(static_folder, filename):
    """Коротка версія файлу — хеш вмісту (той самий файл після нового розгортання — та сама адреса й кеш)."""
    with _versions_lock:
        if filename in _versions:
            return _versions[filename]
    try:
        with open(os.path.join(static_folder, filename), "rb") as f:
            version = hashlib.md5(f.read()).hexdigest()[:10]
    except OSError:
        version = None
    with _versions_lock:
        _versions[filename] = version
    return version


# ---------------------------------------------------------------- шаблони

def _time_compile(env):
    """Час компіляції шаблонів під час запиту — у його підсумок (журнал повільних запитів: «компіляція шаблонів»)."""
    compile_ = env.compile

    def compile(source, name=None, filename=None, raw=False, defer_init=False):
        t0 = time.monotonic()
        try:
            return compile_(source, name, filename, raw, defer_init)
        finally:
            ops_log.add_timing("tpl", time.monotonic() - t0)
    env.compile = compile


def warm_templates(app):
    """Скомпілювати всі шаблони сторінок і листів (кеш Jinja живе до перезапуску процесу). Повертає кількість."""
    env = app.jinja_env
    done = 0
    for name in env.list_templates(filter_func=lambda n: n.endswith(".html")):
        try:
            env.get_template(name)
            done += 1
        except Exception:
            logger.exception("Не вдалося скомпілювати шаблон %s", name)
    return done


def start_warmup(app):
    """У фоні одразу після старту процесу (перший запит — зазвичай перевірка App Service після перезапуску)."""
    def run():
        try:
            with ops_log.timed("templates_warmup") as info:
                info["result"] = {"шаблонів": warm_templates(app)}
        except Exception:
            logger.exception("Помилка підготовки шаблонів")
    threading.Thread(target=run, name="templates-warmup", daemon=True).start()


def init_app(app):
    """Підключити після auth.init_app (там Flask-Session ставить свій інтерфейс сеансів)."""
    global _static_prefix
    _static_prefix = (app.static_url_path or "/static").rstrip("/") + "/"
    app.session_interface = LightSessions(app.session_interface)
    _time_compile(app.jinja_env)

    @app.url_defaults
    def _static_v(endpoint, values):
        if endpoint == "static" and values.get("filename") and "v" not in values:
            version = static_version(app.static_folder, values["filename"])
            if version:
                values["v"] = version

    default_max_age = app.get_send_file_max_age

    def get_send_file_max_age(filename):
        # Версія в адресі — файл не змінюється, кеш надовго; без версії — як було (браузер перепитує)
        if request.args.get("v"):
            return STATIC_MAX_AGE
        return default_max_age(filename)
    app.get_send_file_max_age = get_send_file_max_age
