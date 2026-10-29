"""Заголовки безпеки для кожної відповіді сайту.

Сайт нічого не підключає з інших адрес (скрипти, стилі, шрифти, картинки — лише свої), не вбудовується в чужі сторінки
й сам нічого не вбудовує, а вхід через Microsoft і Google — звичайні посилання. Тому Content-Security-Policy дозволяє
лише власну адресу. Вбудовані <script> і style= у шаблонах поки потрібні ('unsafe-inline'); щоб CSP захищав і від
вставленого в сторінку скрипта, їх треба перенести у файли static/ (або позначати nonce).
"""
from flask import request

CSP = "; ".join([
    "default-src 'self'",
    "script-src 'self' 'unsafe-inline'",
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: blob:",
    "font-src 'self' data:",
    "connect-src 'self'",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
])

HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Frame-Options": "DENY",                      # для старих браузерів, що не знають frame-ancestors
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",               # адреси заявок і файлів не йдуть на інші сайти
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
}
HSTS = "max-age=31536000"  # рік: браузер більше не відкриє сайт через HTTP


def _add(response):
    for name, value in HEADERS.items():
        response.headers.setdefault(name, value)
    if request.is_secure:  # за проксі App Service — завдяки ProxyFix (X-Forwarded-Proto)
        response.headers.setdefault("Strict-Transport-Security", HSTS)
    return response


def init_app(app):
    app.after_request(_add)
