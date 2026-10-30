"""Повільні запити — у журнал роботи сайту (ops_log): метод і шлях (без параметрів запиту — у них бувають коди
входу), код відповіді, хто і куди пішов час (скільки звернень до бази й Power BI і скільки вони тривали).
Підключається першим, тож час включає й перевірки на початку запиту."""
import time

from flask import g, request

import ops_log


def _start():
    g.request_t0 = time.monotonic()


def _finish(response):
    t0 = getattr(g, "request_t0", None)
    if t0 is not None:
        ms = (time.monotonic() - t0) * 1000
        if ms >= ops_log.SLOW_MS:
            user = getattr(g, "user", None) or {}
            detail = " · ".join(x for x in (user.get("email") or "", ops_log.timing_summary(g.get("timings"))) if x)
            ops_log.record("slow", f"{request.method} {request.path}", ms, response.status_code, detail)
    return response


def init_app(app):
    app.before_request(_start)
    app.after_request(_finish)
