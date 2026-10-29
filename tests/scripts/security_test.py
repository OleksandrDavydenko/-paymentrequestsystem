"""Заголовки безпеки на кожній відповіді (CSP лише зі своєю адресою, без вбудовування в чужі сторінки, HSTS лише
через HTTPS) і обмеження частоти запитів до API: обробка 1С і Telegram-бот — забагато запитів чи спроб з
невірним ключем з однієї IP — 429."""
import os, sys, tempfile
sys.path.insert(0, os.getcwd())
tmp = tempfile.mkdtemp()
os.environ.update(UPLOAD_DIR=tmp, ADMIN_EMAILS="adm@ftpua.com", TELEGRAM_BOT_KEY="tg-test-key")
import app as m
import app_settings, onec_api, ratelimit

ok = True
def check(label, cond):
    global ok
    ok &= bool(cond); print(("PASS " if cond else "FAIL ") + label)

c = m.app.test_client()

# ---------- заголовки
r = c.get("/login")
h = r.headers
csp = h.get("Content-Security-Policy", "")
check("CSP: only own origin, no framing, no plugins", "default-src 'self'" in csp and "frame-ancestors 'none'" in csp
      and "object-src 'none'" in csp and "form-action 'self'" in csp and "http" not in csp)
check("other headers", h.get("X-Frame-Options") == "DENY" and h.get("X-Content-Type-Options") == "nosniff"
      and h.get("Referrer-Policy") == "same-origin" and "camera=()" in h.get("Permissions-Policy", ""))
check("no HSTS over plain HTTP", "Strict-Transport-Security" not in h)
h = c.get("/login", headers={"X-Forwarded-Proto": "https"}).headers
check("HSTS behind the HTTPS proxy", h.get("Strict-Transport-Security") == "max-age=31536000")
check("headers on API and static too", "Content-Security-Policy" in c.get("/health").headers
      and c.get("/static/style.css").headers.get("X-Content-Type-Options") == "nosniff")

# ---------- API 1С: ліміт запитів
key = onec_api.new_key({"name": "t", "email": "t"})
app_settings.set("onec_api_enabled", True, "t")
ratelimit.API_REQUESTS = ratelimit.Limiter(5, 60)
codes = [c.get("/api/1c/v1/ping", headers={"X-Api-Key": key}).status_code for _ in range(6)]
r = c.get("/api/1c/v1/ping", headers={"X-Api-Key": key})
check("1C API: 5 requests a minute pass, then 429 with a message", codes == [200] * 5 + [429] and r.status_code == 429
      and "Забагато запитів" in r.get_json()["error"])
ratelimit.API_REQUESTS = ratelimit.Limiter(600, 60)
check("other IP is not limited", c.get("/api/1c/v1/ping", headers={"X-Api-Key": key},
                                       environ_base={"REMOTE_ADDR": "10.1.1.1"}).status_code == 200)

# ---------- невірний ключ: після 3 спроб — 429 навіть з правильним
ratelimit.API_BAD_KEYS = ratelimit.Limiter(3, 600)
codes = [c.get("/api/1c/v1/ping", headers={"X-Api-Key": "wrong"}).status_code for _ in range(3)]
r = c.get("/api/1c/v1/ping", headers={"X-Api-Key": key})
check("bad key x3 -> 403, then 429 even with the right key", codes == [403] * 3 and r.status_code == 429
      and "невірним ключем" in r.get_json()["error"])
check("bot API shares the block for this IP", c.get("/api/telegram/outbox", headers={"X-Api-Key": "tg-test-key"}).status_code == 429)
ratelimit.API_BAD_KEYS.reset()
check("after the window: right key works again", c.get("/api/1c/v1/ping", headers={"X-Api-Key": key}).status_code == 200)

# ---------- Telegram-бот: ліміт запитів
ratelimit.API_REQUESTS = ratelimit.Limiter(2, 60)
codes = [c.get("/api/telegram/outbox", headers={"X-Api-Key": "tg-test-key"}).status_code for _ in range(3)]
check("bot API: limited too", codes == [200, 200, 429])

# ---------- сам лічильник
lim = ratelimit.Limiter(2, 60)
check("limiter: per key, blocked() does not count", lim.hit("a") and lim.hit("a") and not lim.hit("a") and lim.hit("b")
      and lim.blocked("a") and not lim.blocked("b"))
print("ALL OK" if ok else "SOME FAILED")
