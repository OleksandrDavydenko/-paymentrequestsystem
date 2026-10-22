"""Вхід і вихід: сторінка «Увійти» (Google і / або Microsoft — налаштування «Вхід у систему»), вхід через Google."""
import logging

from flask import Blueprint, redirect, render_template, request, session, url_for

import google_login
import user_roles

from web.auth import ADMIN_EMAILS, auth, current_claims, login_mode

bp = Blueprint("login", __name__)
logger = logging.getLogger(__name__)


def _safe_next(raw):
    """Куди повернутися після входу: лише сторінка цього сайту (не чужа адреса)."""
    raw = raw or "/"
    return raw if raw.startswith("/") and not raw.startswith("//") and "\\" not in raw else "/"


def _microsoft_url(next_link):
    """Адреса входу Microsoft (бібліотека identity запам'ятовує стан у сеансі й поверне на next_link)."""
    try:
        return auth._auth.log_in(scopes=[], redirect_uri=auth._redirect_uri, prompt=auth._prompt,
                                 next_link=next_link).get("auth_uri")
    except Exception:
        logger.exception("Не вдалося підготувати вхід через Microsoft")
        return None


def _external_allowed(email):
    """Зовнішнього користувача пускаємо, лише якщо адміністратор додав його й призначив хоча б одну роль."""
    rec = user_roles.find_by_email(email)
    return bool(rec and rec["roles"])


@bp.route("/login")
def login_page(error=None):
    nxt = _safe_next(request.args.get("next"))
    if current_claims():
        return redirect(nxt)
    mode = login_mode()
    return render_template(
        "login.html", mode=mode, error=error, next=nxt,
        google_url=url_for("login.google_start", next=nxt) if mode != "microsoft" else None,
        external_url=url_for("login.google_start", next=nxt, external=1) if mode != "microsoft" else None,
        microsoft_url=_microsoft_url(nxt) if mode != "google" or request.args.get("method") == "microsoft"
        else None,
        admin_fallback_url=_microsoft_url(nxt) if mode == "google" else None)


@bp.route("/privacy")
def privacy():
    """Політика конфіденційності — без входу (потрібна Google для OAuth-клієнта типу External)."""
    return render_template("privacy.html", contact=sorted(ADMIN_EMAILS)[0] if ADMIN_EMAILS else "")


@bp.route("/auth/google/login")
def google_start():
    if login_mode() == "microsoft":
        return redirect(url_for("login.login_page"))
    redirect_uri = url_for("login.google_callback", _external=True)
    url, pending = google_login.start(redirect_uri, company_hint=not request.args.get("external"))
    session["google_pending"] = {**pending, "next": _safe_next(request.args.get("next"))}
    return redirect(url)


@bp.route("/auth/google/callback")
def google_callback():
    pending = session.pop("google_pending", None)
    session.pop("google_user", None)  # невдалий вхід не лишає попередньої людини в сеансі
    try:
        user = google_login.finish(request.args, pending, url_for("login.google_callback", _external=True),
                                   is_allowed=_external_allowed)
    except google_login.GoogleLoginError as e:
        logger.warning("Вхід через Google: %s", e)
        return login_page(error=str(e))
    session["google_user"] = user
    return redirect(_safe_next((pending or {}).get("next")))


@bp.route("/auth/logout")  # «/logout» уже зайнятий бібліотекою identity (вихід лише з Microsoft)
def logout():
    """Вийти: з Google — забути сеанс; з Microsoft — ще й вихід у Microsoft (бібліотека identity)."""
    session.pop("google_user", None)
    session.pop("google_pending", None)
    if auth._auth.get_user():
        return redirect(url_for("identity.logout"))
    return redirect(url_for("login.login_page"))
