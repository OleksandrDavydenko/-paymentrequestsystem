"""Вхід, ролі й доступ: Google або Microsoft → ролі, декоратори login_required / requires, головна сторінка.

Як входять люди — налаштування «Вхід у систему» (Дані й система):
  microsoft — лише Microsoft (Entra ID), ролі — групи Azure (як було);
  both      — Google і Microsoft, ролі — у системі (user_roles); людину знаходимо за поштою;
  google    — лише Google, ролі — у системі; адміністратори з ADMIN_EMAILS можуть увійти й через
              Microsoft (запасний вхід, якщо з Google щось трапиться).
Без OAuth-клієнта Google (google_login.configured) система працює як «microsoft».
"""
import os
from functools import partial, wraps

import identity.flask
from dotenv import load_dotenv
from flask import abort, current_app, g, redirect, render_template, request, session, url_for

import app_settings
import google_login
import graph
import mailer
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


# Ролі = членство в групах безпеки Entra ID (ID груп приходять у токені, claim "groups").
GROUPS = {
    "admin": os.environ.get("GROUP_ADMIN", ""),
    "initiator": os.environ.get("GROUP_INITIATOR", ""),
    "acc_cash": os.environ.get("GROUP_ACC_CASH", ""),
    # GROUP_ACCOUNTANT — стара назва змінної (одна група бухгалтерів), тепер це бухгалтери-резиденти
    "acc_resident": os.environ.get("GROUP_ACC_RESIDENT") or os.environ.get("GROUP_ACCOUNTANT", ""),
    "acc_nonresident": os.environ.get("GROUP_ACC_NONRESIDENT", ""),
    "cfo": os.environ.get("GROUP_CFO", ""),
}


# Керівник відділу і «бачить заявки відділу» — не групи Azure, а призначення в картці користувача (org_structure)
# (org_structure); ролі dept_head / dept_viewer додаються під час входу.
# Страховка від блокування: ці люди завжди адміністратори
ADMIN_EMAILS = {e.strip().lower() for e in os.environ.get("ADMIN_EMAILS", "od@ftpua.com").split(",") if e.strip()}
OVERAGE_SCOPES = ["https://graph.microsoft.com/GroupMember.Read.All"]


def login_mode():
    """microsoft / both / google (див. опис модуля)."""
    mode = app_settings.get("login_methods")
    if mode not in ("both", "google") or not google_login.configured():
        return "microsoft"
    return mode


def roles_in_app():
    """Ролі — у системі (вхід через Google увімкнено), а не в групах Azure."""
    return login_mode() != "microsoft"


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


def login_required(function=None, *, scopes=None):
    """Сторінка лише для тих, хто увійшов (Google або Microsoft). Передає у view context={"user": claims}
    і, для входу через Microsoft з потрібними scopes, ще "access_token" (Microsoft Graph)."""
    if function is None:
        return partial(login_required, scopes=scopes)

    @wraps(function)
    def wrapper(*args, **kwargs):
        user = current_claims()
        if not user:
            return redirect(url_for("login.login_page", next=request.full_path.rstrip("?")))
        context = {"user": user}
        if scopes and user["login"] == "microsoft":
            result = auth._auth.get_token_for_user(scopes)
            if isinstance(result, dict) and "access_token" in result:
                context["access_token"] = result["access_token"]
            elif not roles_in_app():  # ролі в Azure — без токена Graph адмінка не працює: увійти знову
                return redirect(url_for("login.login_page", next=request.full_path.rstrip("?"), method="microsoft",
                                        scopes="admin"))
        return function(*args, context=context, **kwargs)
    return wrapper


def _app_user_id(claims, email):
    """Ідентифікатор людини в системі (ролі в системі): запис ролей за поштою, інакше Entra oid (з токена
    або довідника), інакше пошта."""
    rec = user_roles.find_by_email(email)
    if rec:
        return rec["id"], rec["roles"]
    known = user_directory.find_active(email)
    return (claims.get("oid") or (known or {}).get("id") or email.lower()), set()


def _token_groups(claims):
    """ID груп користувача з токена; якщо груп забагато (overage) — перевірка через Graph."""
    if "groups" in claims:
        return set(claims["groups"])
    overage = "groups" in (claims.get("_claim_names") or {}) or claims.get("hasgroups")
    if not overage:
        return set()
    cache_key = f"overage_groups:{claims.get('oid')}"
    if cache_key not in session:
        wanted = [gid for gid in GROUPS.values() if gid]
        result = auth._auth.get_token_for_user(OVERAGE_SCOPES)
        try:
            found = graph.check_member_groups(result["access_token"], wanted) if "access_token" in result else set()
        except graph.GraphError:
            current_app.logger.exception("Overage group check failed")
            found = set()
        session[cache_key] = sorted(found)
    return set(session[cache_key])


def resolve_roles(claims):
    groups = _token_groups(claims)
    roles = {role for role, gid in GROUPS.items() if gid and gid in groups}
    if claims.get("preferred_username", "").lower() in ADMIN_EMAILS:
        roles.add("admin")
    return roles


def requires(*needed):
    """Після @login_required: заповнює g.user / g.roles і перевіряє доступ.
    Без жодної ролі (або без потрібної) — сторінка «Немає доступу»."""
    def deco(view):
        @wraps(view)
        def wrapper(*args, context, **kwargs):
            claims = context["user"]
            email = claims.get("preferred_username", "")
            if roles_in_app():
                uid, roles = _app_user_id(claims, email)
                roles = set(roles) | ({"admin"} if email.lower() in ADMIN_EMAILS else set())
            else:
                uid, roles = claims.get("oid"), resolve_roles(claims)
            g.user = {"email": email, "name": claims.get("name") or email, "oid": uid, "login": claims.get("login")}
            # Керівник відділу / «бачить заявки відділу» — з картки користувача, не з груп Azure
            heads = org_structure.head_departments(g.user["oid"])
            views = org_structure.viewer_departments(g.user["oid"])
            if heads:
                roles.add("dept_head")
            if views:
                roles.add("dept_viewer")
            user_directory.remember([{"id": g.user["oid"], "email": email, "name": g.user["name"]}],
                                    active=bool(roles))
            g.roles = wf.RoleSet(roles, heads, views)
            if not g.roles or (needed and not g.roles & set(needed)):
                return render_template("no_access.html", user_name=g.user["name"], missing=needed), 403
            return view(*args, context=context, **kwargs)
        return wrapper
    return deco


def _role_members(role):
    """Учасники ролі з поштою: ролі в системі — з user_roles; інакше — група Azure (дозвіл додатку дає лише
    ID, пошту беремо з довідника)."""
    if roles_in_app():
        return user_roles.members(role)
    gid = GROUPS.get(role)
    if not gid:
        return []
    members, unknown = [], 0
    for m in graph.group_members_app(gid):
        if not m.get("email"):
            m = {**m, **(user_directory.lookup(m["id"]) or {})}
        if m.get("email"):
            members.append(m)
        else:
            unknown += 1
    if unknown:
        mailer.log_problem(
            f"роль «{wf.ROLES.get(role, role)}»", "Невідома пошта учасників",
            f"Для {unknown} з учасників групи не вдалося визначити пошту — вони не отримають лист. "
            "Відкрийте «Адміністрування» (пошта підтягнеться з Azure) або додайте додатку дозвіл "
            "Microsoft Graph → Application → User.ReadBasic.All з Grant admin consent.")
    return members


notifications.role_members = _role_members


notifications.admin_emails = lambda: sorted(ADMIN_EMAILS)


def _deciding_roles(roles):
    """Ролі, якими користувач ухвалює рішення. Керівник відділу — лише якщо він погоджує (налаштування)."""
    deciding = set(roles & wf.APPROVER_ROLES)
    if app_settings.get("dept_head_mode") != "approve":
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
