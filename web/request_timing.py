"""Повільні запити — у журнал роботи сайту (ops_log): метод і шлях (без параметрів запиту — у них бувають коди
входу), код відповіді, хто і куди пішов час (скільки звернень до бази й Power BI і скільки вони тривали).
Підключається першим, тож час включає й перевірки на початку запиту.

Профіль запиту (діагностика): адміністратор відкриває сторінку з `?_profile=1` — повний звіт cProfile пишеться у
файл (на App Service — /home/LogFiles/paydesk-profiles, видно в Kudu), коротко — у журнал роботи («Профіль запиту»):
які функції забрали найбільше часу. У Python 3.12 профайлер бачить і фонові потоки — видно, якщо процес гальмує
не сама сторінка, а фонова задача поруч.
"""
import io
import os
import tempfile
import time

from flask import g, request

import clock
import ops_log

PROFILE_ARG = "_profile"
# Службова перевірка App Service після перезапуску (чи піднявся контейнер; 404 — нормально): не користувач, і старт
# сайту з його тривалістю й так у журналі («Старт сайту»)
PLATFORM_PROBES = ("/robots933456.txt",)
_WRAPPERS = ("flask", "werkzeug", "jinja2", "flask_session", "flask_wtf", "cProfile", "pstats")


def _start():
    g.request_t0 = time.monotonic()
    if request.args.get(PROFILE_ARG) == "1":
        try:
            import cProfile
            prof = cProfile.Profile()
            prof.enable()
            g.profiler = prof
        except Exception:  # інший профіль уже йде — цей запит без профілю
            g.profiler = None


def _finish(response):
    t0 = getattr(g, "request_t0", None)
    prof = g.pop("profiler", None)
    if prof is not None:
        prof.disable()
    if t0 is not None:
        ms = (time.monotonic() - t0) * 1000
        user = getattr(g, "user", None) or {}
        if prof is not None and "admin" in (getattr(g, "roles", None) or ()):
            _save_profile(prof, ms, response.status_code)
        if ms >= ops_log.SLOW_MS and request.path not in PLATFORM_PROBES:
            detail = " · ".join(x for x in (user.get("email") or "", ops_log.timing_summary(g.get("timings"))) if x)
            ops_log.record("slow", f"{request.method} {request.path}", ms, response.status_code, detail)
    return response


def _save_profile(prof, ms, status):
    try:
        import pstats
        out = io.StringIO()
        stats = pstats.Stats(prof, stream=out).sort_stats("cumulative")
        stats.print_stats(80)
        folder = (os.path.join("/home", "LogFiles", "paydesk-profiles") if os.environ.get("WEBSITE_SITE_NAME")
                  else os.path.join(tempfile.gettempdir(), "paydesk-profiles"))
        os.makedirs(folder, exist_ok=True)
        name = f"{clock.now():%Y%m%d-%H%M%S}-{request.path.strip('/').replace('/', '_') or 'root'}.txt"
        with open(os.path.join(folder, name), "w", encoding="utf-8") as f:
            f.write(f"{request.method} {request.path} — {ms:.0f} мс, {status}\n"
                    f"{ops_log.timing_summary(g.get('timings'))}\n\n{out.getvalue()}")
        top = []
        for func in stats.fcn_list[:150]:
            file, line, fn = func
            cumulative = stats.stats[func][3]
            parts = file.replace("\\", "/").split("/")
            if cumulative < 0.05 or any(w in parts for w in _WRAPPERS):
                continue
            top.append(f"{cumulative:.2f} с {parts[-1]}:{line} {fn}")
            if len(top) >= 15:
                break
        ops_log.record("profile", f"{request.method} {request.path}", ms, status, f"файл {name} · " + "; ".join(top))
    except Exception:
        ops_log.logger.exception("Не вдалося зберегти профіль запиту")


def init_app(app):
    app.before_request(_start)
    app.after_request(_finish)
