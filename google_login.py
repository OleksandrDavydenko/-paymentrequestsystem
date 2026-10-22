"""Вхід через Google (OpenID Connect): робочий Google-акаунт компанії, той самий пароль, що й у пошті.

Налаштування — змінні App Service (OAuth-клієнт створюється в Google Cloud Console, доступ до консолі
адміністратора Google Workspace не потрібен):
  GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET — OAuth-клієнт типу «Web application»;
  GOOGLE_ALLOWED_DOMAINS — домени пошти, яким дозволено входити (через кому), напр. ftpua.com.
Адреса повернення: https://<сайт>/auth/google/callback (зареєстрована в клієнті).

Код авторизації обмінюється на ID-токен прямо з сервером Google через HTTPS (OpenID Connect Core 3.1.3.7:
у такому разі підпис токена можна не перевіряти — достатньо TLS), далі перевіряються видавець, клієнт,
термін дії, nonce, підтверджена пошта й домен.
"""
import base64
import json
import os
import secrets
import time
from urllib.parse import urlencode

import requests

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
ISSUERS = {"https://accounts.google.com", "accounts.google.com"}
TIMEOUT = 20


class GoogleLoginError(Exception):
    pass


def configured():
    """Вхід через Google можливий: є клієнт і хоча б один дозволений домен (без доменів — не пускаємо нікого)."""
    return bool(os.environ.get("GOOGLE_CLIENT_ID") and os.environ.get("GOOGLE_CLIENT_SECRET") and allowed_domains())


def allowed_domains():
    return [d.strip().lower().lstrip("@") for d in os.environ.get("GOOGLE_ALLOWED_DOMAINS", "").split(",") if d.strip()]


def start(redirect_uri):
    """Адреса сторінки входу Google і дані, які треба зберегти в сеансі до повернення: (url, {"state", "nonce"})."""
    pending = {"state": secrets.token_urlsafe(24), "nonce": secrets.token_urlsafe(24)}
    params = {"client_id": os.environ["GOOGLE_CLIENT_ID"], "redirect_uri": redirect_uri, "response_type": "code",
              "scope": "openid email profile", "state": pending["state"], "nonce": pending["nonce"],
              "prompt": "select_account"}
    domains = allowed_domains()
    if len(domains) == 1:
        params["hd"] = domains[0]  # Google одразу пропонує акаунти компанії
    return f"{AUTH_URL}?{urlencode(params)}", pending


def _claims(id_token):
    try:
        payload = id_token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (IndexError, ValueError):
        raise GoogleLoginError("Google повернув неправильний токен входу")


def finish(args, pending, redirect_uri):
    """Завершити вхід за параметрами повернення від Google. Повертає {"email", "name", "sub"}. Кидає GoogleLoginError."""
    if args.get("error"):
        raise GoogleLoginError("Вхід через Google скасовано" if args["error"] == "access_denied"
                               else f"Google не дозволив вхід: {args['error']}")
    if not pending or not args.get("state") or not secrets.compare_digest(args["state"], pending.get("state", "")):
        raise GoogleLoginError("Сторінка входу застаріла — спробуйте ще раз")
    if not args.get("code"):
        raise GoogleLoginError("Google не повернув код входу — спробуйте ще раз")
    try:
        resp = requests.post(TOKEN_URL, data={
            "code": args["code"], "client_id": os.environ["GOOGLE_CLIENT_ID"],
            "client_secret": os.environ["GOOGLE_CLIENT_SECRET"], "redirect_uri": redirect_uri,
            "grant_type": "authorization_code"}, timeout=TIMEOUT)
    except requests.RequestException as e:
        raise GoogleLoginError(f"Не вдалося з'єднатися з Google: {e}")
    if resp.status_code != 200:
        try:
            reason = resp.json().get("error", "")
        except ValueError:
            reason = ""
        raise GoogleLoginError(f"Google відхилив вхід ({resp.status_code} {reason})".strip())
    c = _claims(resp.json().get("id_token") or "")
    if c.get("iss") not in ISSUERS or c.get("aud") != os.environ["GOOGLE_CLIENT_ID"]:
        raise GoogleLoginError("Токен входу виданий не для цього сайту")
    if int(c.get("exp") or 0) < time.time():
        raise GoogleLoginError("Токен входу прострочений — спробуйте ще раз")
    if c.get("nonce") != pending.get("nonce"):
        raise GoogleLoginError("Сторінка входу застаріла — спробуйте ще раз")
    email = (c.get("email") or "").strip().lower()
    if not email or not c.get("email_verified"):
        raise GoogleLoginError("Google не підтвердив адресу пошти цього акаунта")
    domain = email.rsplit("@", 1)[-1]
    domains = allowed_domains()
    if domain not in domains and (c.get("hd") or "").lower() not in domains:
        raise GoogleLoginError(f"Вхід дозволено лише робочим акаунтам ({', '.join('@' + d for d in domains)}). "
                               f"Ви увійшли як {email} — виберіть робочий акаунт.")
    return {"email": email, "name": c.get("name") or email, "sub": c.get("sub")}
