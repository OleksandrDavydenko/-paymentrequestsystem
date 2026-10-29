"""Вхід, ролі й доступ: Google або Microsoft → ролі, декоратори login_required / requires, головна сторінка.

Ролі зберігаються в самій системі (user_roles, картка користувача в адмінці); людину знаходимо за поштою,
тож однаково працює вхід і через Google, і через Microsoft. Як входять люди — налаштування «Вхід у систему»
(Дані й система):
  both   — Google і Microsoft;
  google — лише Google; адміністратори з ADMIN_EMAILS можуть увійти й через Microsoft (запасний вхід).
Без OAuth-клієнта Google (google_login.configured) — лише Microsoft.
"""
import os
from functools import wraps

import identity.flask
from dotenv import load_dotenv
from flask import abort, g, redirect, render_template, request, session, url_for

import app_settings
import dept_settings
import google_login
import notifications
import org_structure
import storage as db
import user_directory
import user_roles
import workflow as wf

load_dotenv()  # локальний запуск: змінні з .env (на App Service — з налаштувань сайту)

# Single-tenant authority: увійти можуть лише користувачі нашого тенанту Entra ID.
# До додатку підключається в app.py (auth.init_app) — після налаштування сеансів.
auth = identity.flask.Auth(
    None,
    authority=f"https://login.microsoftonline.com/{os.environ['TENANT_ID']}",
    client_id=os.environ["CLIENT_ID"],
    client_credential=os.environ["CLIENT_SECRET"],
    redirect_uri=os.environ["REDIRECT_URI"],  # напр. http://localhost:5000/getAToken
)


# Керівник відділу і «бачить заявки відділу» — призначення в картці користувача (org_structure);
# ролі dept_head / dept_viewer додаються під час входу.
# Страховка від блокування: ці люди завжди адміністратори
ADMIN_EMAILS = {e.strip().lower() for e in os.environ.get("ADMIN_EMAILS", "od@ftpua.com").split(",") if e.strip()}


def login_mode():
    """both / google (див. опис модуля); microsoft — Google не налаштований, лише вхід через Microsoft."""
    if not google_login.configured():
        return "microsoft"
    return "google" if app_settings.get("login_methods") == "google" else "both"


def current_claims():
    """Хто увійшов: {"preferred_username", "name", "oid", "login": google|microsoft, …} або None."""
    mode = login_mode()
    gu = session.get("google_user") if mode != "microsoft" else None
    if gu:
        return {"preferred_username": gu["email"], "name": gu["name"], "oid": None, "login": "google"}
    ms = auth._auth.get_user()
    if not ms:
        return None
    if mode == "google" and (ms.get("preferred_username") or "").lower() not in ADMIN_EMAILS:
        return None  # лише Google; через Microsoft — тільки адміністратори (запасний вхід)
    return {**ms, "login": "microsoft"}


def login_required(function):
    """Сторінка лише для тих, хто увійшов (Google або Microsoft). Передає у view context={"user": claims}."""
    @wraps(function)
    def wrapper(*args, **kwargs):
        user = current_claims()
        if not user:
            return redirect(url_for("login.login_page", next=request.full_path.rstrip("?")))
        return function(*args, context={"user": user}, **kwargs)
    return wrapper


def _app_user(claims, email):
    """Людина в системі: (id, ролі). Запис ролей за поштою; без запису — Entra oid (з токена або довідника),
    інакше пошта. Ім'я, якого адміністратор не знав (лише пошта), оновлюємо з акаунта на вході."""
    rec = user_roles.find_by_email(email)
    if rec:
        name = claims.get("name") or ""
        if name and name.lower() != email.lower() and rec["name"] in (rec["email"], rec["id"]):
            user_roles.set_name(rec["id"], name)
        return rec["id"], set(rec["roles"])
    known = user_directory.find_active(email)
    return (claims.get("oid") or (known or {}).get("id") or email.lower()), set()


def requires(*needed):
    """Після @login_required: заповнює g.user / g.roles і перевіряє доступ.
    Без жодної ролі (або без потрібної) — сторінка «Немає доступу»."""
    def deco(view):
        @wraps(view)
        def wrapper(*args, context, **kwargs):
            claims = context["user"]
            email = claims.get("preferred_username", "")
            uid, roles = _app_user(claims, email)
            if email.lower() in ADMIN_EMAILS:
                roles.add("admin")
            # Ім'я — з 1С («Прізвище Ім'я»), якщо людину там знайдено за поштою, інакше з акаунта входу
            name = user_roles.display_name(email, claims.get("name") or email)
            g.user = {"email": email, "name": name, "oid": uid, "login": claims.get("login")}
            # Керівник відділу / «бачить заявки відділу» — з картки користувача
            heads = org_structure.head_departments(g.user["oid"])
            views = org_structure.viewer_departments(g.user["oid"])
            # Заступник (бачить заявки відділу, де так налаштовано) погоджує замість керівника
            heads = sorted(set(heads) | {d for d in views if dept_settings.deputies_approve(d)})
            if heads:
                roles.add("dept_head")
            if views:
                roles.add("dept_viewer")
            user_directory.remember([{"id": g.user["oid"], "email": email, "name": g.user["name"]}],
                                    active=bool(roles))
            # Бухгалтер «Лише оплачує» (картка користувача): бухгалтерськими ролями — лише оплата
            pay_only = roles & wf.ACCOUNTANT_ROLES if user_roles.acc_mode(uid) == "pay" else ()
            g.roles = wf.RoleSet(roles, heads, views, pay_only)
            if not g.roles or (needed and not g.roles & set(needed)):
                return render_template("no_access.html", user_name=g.user["name"], missing=needed), 403
            return view(*args, context=context, **kwargs)
        return wrapper
    return deco


def _role_members(role):
    """Учасники ролі з поштою (для розсилки)."""
    return user_roles.members(role)


notifications.role_members = _role_members


notifications.admin_emails = lambda: sorted(ADMIN_EMAILS)


def _deciding_roles(roles):
    """Ролі, якими користувач ухвалює рішення. Керівник відділу — лише якщо хоч один його відділ погоджує (налаштування
    відділу або загальне) чи в черзі вже є заявки, що чекають його рішення. Бухгалтер «Лише оплачує» не погоджує."""
    deciding = set(roles & wf.APPROVER_ROLES) - wf.pays_only(roles)
    heads = wf.head_deps(roles)
    if "dept_head" in deciding and not any(dept_settings.head_approves(d) for d in heads) \
            and not db.count("queue", wf.RoleSet({"dept_head"}, heads)):
        deciding.discard("dept_head")
    # Фіндиректор-спостерігач — без черги (крім заявок, відправлених ще з його кроком)
    if "cfo" in deciding and app_settings.get("cfo_mode") == "observe" and not db.count("queue", frozenset({"cfo"})):
        deciding.discard("cfo")
    return deciding


def _sees_all_requests(roles):
    """«Усі заявки»: адміністратор і фіндиректор (лише перегляд)."""
    return bool(roles & {"admin", "cfo"})


def _home():
    if "initiator" not in g.roles and _deciding_roles(g.roles):
        return url_for("lists.approvals")
    if "initiator" not in g.roles and g.roles & {"dept_head", "dept_viewer"}:
        return url_for("lists.department_requests")
    if g.roles == {"admin"} or ("initiator" not in g.roles and "cfo" in g.roles):
        return url_for("lists.admin_requests")
    if "initiator" not in g.roles and wf.can_see_payments(g.roles):  # бухгалтер «Лише оплачує»
        return url_for("payments.to_pay")
    return url_for("requests.requests_list")


def _load_visible(request_id):
    """Заявка, яку користувач може бачити, або 404. Для дій з форми картки (POST з полем version):
    якщо заявку змінили після того, як користувач відкрив сторінку, — StaleRequestError (див. stale_request)."""
    req = db.get_request(request_id)
    if not req or not wf.can_view(req, g.roles, g.user["email"]):
        abort(404)
    seen = request.form.get("version") if request.method == "POST" else None
    if seen and seen.isdigit() and int(seen) != (req.get("version") or 0):
        raise db.StaleRequestError(req)
    return req
