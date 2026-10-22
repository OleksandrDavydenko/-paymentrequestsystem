"""Вхід і вихід: сторінка «Увійти» (Google і / або Microsoft — налаштування «Вхід у систему»), вхід через Google."""
import logging

from flask import Blueprint, redirect, render_template, request, session, url_for

import google_login
import graph

from web.auth import auth, current_claims, login_mode

bp = Blueprint("login", __name__)
logger = logging.getLogger(__name__)


def _safe_next(raw):
    """Куди повернутися після входу: лише сторінка цього сайту (не чужа адреса)."""
    raw = raw or "/"
    return raw if raw.startswith("/") and not raw.startswith("//") and "\\" not in raw else "/"


def _microsoft_url(next_link, scopes=()):
    """Адреса входу Microsoft (бібліотека identity запам'ятовує стан у сеансі й поверне на next_link)."""
    try:
        return auth._auth.log_in(scopes=list(scopes), redirect_uri=auth._redirect_uri, prompt=auth._prompt,
                                 next_link=next_link).get("auth_uri")
    except Exception:
        logger.exception("Не вдалося підготувати вхід через Microsoft")
        return None


@bp.route("/login")
def login_page(error=None):
    nxt = _safe_next(request.args.get("next"))
    # scopes=admin — адмінці потрібен доступ до груп Azure (Microsoft Graph): увійти через Microsoft ще раз
    graph_scopes = request.args.get("scopes") == "admin"
    if current_claims() and not graph_scopes:
        return redirect(nxt)
    mode = login_mode()
    if graph_scopes:
        return render_template("login.html", mode=mode, next=nxt, microsoft_url=_microsoft_url(nxt, graph.ADMIN_SCOPES),
                               error=error or "Щоб керувати ролями в групах Azure, увійдіть через Microsoft ще раз.")
    return render_template(
        "login.html", mode=mode, error=error, next=nxt,
        google_url=url_for("login.google_start", next=nxt) if mode != "microsoft" else None,
        microsoft_url=_microsoft_url(nxt) if mode != "google" or request.args.get("method") == "microsoft"
        else None,
        admin_fallback_url=_microsoft_url(nxt) if mode == "google" else None)


@bp.route("/auth/google/login")
def google_start():
    if login_mode() == "microsoft":
        return redirect(url_for("login.login_page"))
    redirect_uri = url_for("login.google_callback", _external=True)
    url, pending = google_login.start(redirect_uri)
    session["google_pending"] = {**pending, "next": _safe_next(request.args.get("next"))}
    return redirect(url)


@bp.route("/auth/google/callback")
def google_callback():
    pending = session.pop("google_pending", None)
    try:
        user = google_login.finish(request.args, pending, url_for("login.google_callback", _external=True))
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
