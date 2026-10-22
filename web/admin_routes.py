"""Адміністрування: користувачі й ролі, пошта, організації, журнал сповіщень (налаштування — web/admin_settings.py)."""
import re
from decimal import Decimal, InvalidOperation

from flask import Blueprint, current_app, flash, g, redirect, render_template, request, url_for

import app_settings
import counterparties
import currencies
import db_sql
import departments
import expense_items
import google_login
import graph
import mailer
import notifications
import notify_log
import org_structure
import organizations
import payment_types
import rates
import secret_box
import storage as db
import telegram_notify
import user_budget
import user_directory
import user_onec
import user_prefs
import user_roles
import workflow as wf

from web.auth import GROUPS, login_required, requires, roles_in_app
from web.request_helpers import _link_counterparty


bp = Blueprint("admin", __name__)


@bp.route("/admin/mail", methods=["POST"])
@login_required
@requires("admin")
def admin_mail(*, context):
    f = request.form
    sender = (f.get("mail_sender") or "").strip()
    host = (f.get("smtp_host") or "").strip()
    try:
        port = int(f.get("smtp_port") or 0)
    except ValueError:
        port = 0
    security = f.get("smtp_security") if f.get("smtp_security") in mailer.SECURITY else "starttls"
    errors = []
    if sender and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", sender):
        errors.append("некоректна адреса відправника")
    if not 0 < port < 65536:
        errors.append("порт має бути числом від 1 до 65535")
    if errors:
        flash("Налаштування не збережено: " + "; ".join(errors), "error")
        return redirect(url_for("admin.admin_settings", tab="notify") + "#mail-settings")

    who = g.user["email"]
    app_settings.set("mail_sender", sender, who)
    app_settings.set("mail_sender_name", (f.get("mail_sender_name") or "").strip()
                     or app_settings.DEFAULTS["mail_sender_name"], who)
    app_settings.set("smtp_host", host, who)
    app_settings.set("smtp_port", port, who)
    app_settings.set("smtp_security", security, who)
    app_settings.set("smtp_username", (f.get("smtp_username") or "").strip(), who)
    # Пароль: порожнє поле — лишити збережений; галочка — видалити
    if f.get("smtp_password_clear") == "1":
        app_settings.set("smtp_password", "", who)
    elif f.get("smtp_password"):
        app_settings.set("smtp_password", secret_box.encrypt(f.get("smtp_password")), who)

    enabled = f.get("mail_enabled") == "1"
    if enabled and not (sender and host):
        flash("Щоб увімкнути розсилку, вкажіть SMTP-сервер і пошту відправника", "error")
        enabled = False
    app_settings.set("mail_enabled", enabled, who)
    app_settings.set("allow_custom_email", f.get("allow_custom_email") == "1", who)
    app_settings.set("error_alerts", f.get("error_alerts") == "1", who)
    flash("Налаштування пошти збережено" + (" — розсилку увімкнено" if enabled else " — розсилку вимкнено"),
          "success")
    return redirect(url_for("admin.admin_settings", tab="notify") + "#mail-settings")


@bp.route("/admin/mail/password", methods=["POST"])
@login_required
@requires("admin")
def admin_mail_password(*, context):
    """Збережений пароль SMTP — лише на явний запит адміна (кнопки «Показати / Копіювати»), не в HTML сторінки."""
    password = secret_box.decrypt(app_settings.get("smtp_password"))
    if password is None:
        return {"error": "Пароль не задано або його не вдалося розшифрувати"}, 404
    resp = current_app.make_response({"password": password})
    resp.headers["Cache-Control"] = "no-store"
    return resp


@bp.route("/admin/mail/test", methods=["POST"])
@login_required
@requires("admin")
def admin_mail_test(*, context):
    cfg = notifications.smtp_config()
    to = g.user["email"]
    if not (cfg["sender"] and cfg["host"]):
        error = "Спочатку вкажіть і збережіть SMTP-сервер і пошту відправника"
    elif app_settings.get("smtp_password") and cfg["password"] is None:
        error = "Збережений пароль не вдалося розшифрувати (змінився FLASK_SECRET_KEY?) — введіть пароль заново"
    else:
        html = render_template("email/test.html", user_name=g.user["name"], sender=cfg["sender"], host=cfg["host"])
        error = mailer.deliver(cfg, to, "Тестовий лист — Система заявок на оплату", html, {"event": "test"})
    if error:
        flash("Тестовий лист не надіслано: " + error, "error")
    else:
        flash(f"Тестовий лист надіслано на {to} від {cfg['sender']}. Перевірте пошту (і папку «Спам»).", "success")
    return redirect(url_for("admin.admin_settings", tab="notify") + "#mail-settings")


@bp.route("/admin/organizations", methods=["POST"])
@login_required
@requires("admin")
def admin_organizations(*, context):
    """Які організації можна вибирати в заявках. Зберігаємо вимкнені — нові з 1С дозволені автоматично."""
    allowed = set(request.form.getlist("allowed"))
    known = {o["code"] for o in organizations.get_all()}
    if not allowed & known:
        flash("Залиште дозволеною хоча б одну організацію — інакше заявку не можна буде створити", "error")
        return redirect(url_for("admin.admin_settings", tab="payment") + "#organizations")
    # Вимкнені, яких зараз немає в довіднику (напр. помічені на видалення), лишаються вимкненими
    disabled = sorted((known - allowed) | (organizations.disabled_codes() - known))
    app_settings.set("disabled_organizations", disabled, g.user["email"])
    # Хто з бухгалтерів веде організацію (порожньо — автоматично); призначення зниклих з довідника — лишаються
    overrides = {c: v for c, v in organizations.accountant_overrides().items() if c not in known}
    for code in known:
        forms = {f: request.form.get(f"acc_{f}_{code}") for f in ("cash", "bank")}
        forms = {f: r for f, r in forms.items() if r in wf.ACCOUNTANT_ROLES}
        if forms:
            overrides[code] = forms
    app_settings.set("org_accountants", overrides, g.user["email"])
    db.reindex()  # бухгалтер заявки — похідна колонка в базі (списки «Оплачені» тощо)
    flash(f"Організації збережено: дозволено для вибору {len(known & allowed)} з {len(known)}; "
          f"призначено бухгалтерів для {len([c for c in overrides if c in known])}. "
          "Заявки, що вже в роботі, переходять до призначеного бухгалтера одразу.", "success")
    return redirect(url_for("admin.admin_settings", tab="payment") + "#organizations")


ADMIN_ROLE_ORDER = ["admin", "initiator", "acc_cash", "acc_resident", "acc_nonresident", "cfo"]


def _azure_group_url(group_id):
    return f"https://portal.azure.com/#view/Microsoft_AAD_IAM/GroupDetailsMenuBlade/~/Members/groupId/{group_id}"


def _configured_groups():
    return {role: gid for role, gid in GROUPS.items() if gid}


def _group_users(token):
    """Користувачі з груп ролей: ({id: {id, name, email, roles}}, текст помилки або None)."""
    users, error = {}, None
    try:
        for role, gid in _configured_groups().items():
            for u in graph.group_members(token, gid):
                users.setdefault(u["id"], {**u, "roles": set()})["roles"].add(role)
    except graph.GraphError as e:
        error = str(e)
    user_directory.remember(users.values(), active=True)  # пошта погоджувачів для листів, список користувачів
    return users, error


def _roles_source(token):
    """Звідки ролі для адмінки: ({id: людина з ролями}, {роль: група або «app»}, помилка).
    Ролі в системі — user_roles; інакше — групи Azure через Microsoft Graph (токен адміністратора)."""
    if roles_in_app():
        return {u["id"]: u for u in user_roles.all_users()}, {r: "app" for r in ADMIN_ROLE_ORDER}, None
    users, error = _group_users(token)
    return users, _configured_groups(), error


def import_azure_roles(who):
    """Перенести ролі з груп Azure в систему (токен самого додатку): (скільки людей додано, помилка або None).
    Хто вже має запис ролей у системі — не змінюється."""
    try:
        token = mailer.app_token()
    except Exception as e:  # немає CLIENT_SECRET / доступу до Graph
        return 0, str(e)
    users, error = _group_users(token)
    if error:
        return 0, error
    return user_roles.import_people(users.values(), who), None


def new_person_allowed(email):
    """Нового користувача (ролі в системі) додаємо за робочою поштою дозволеного домену."""
    email = (email or "").strip().lower()
    if not EMAIL_RE.match(email):
        return False
    domains = google_login.allowed_domains()
    return not domains or email.rsplit("@", 1)[-1] in domains


def _migrate_legacy_departments():
    """Тестові відділи з тими самими назвами, що й у 1С, — на коди з 1С (одноразово, повторно — без змін)."""
    if departments.status()["source"] not in ("powerbi", "copy"):
        return
    mapping = departments.legacy_mapping()
    if mapping and (org_structure.remap_departments(mapping, g.user["email"]) + db.remap_departments(mapping)):
        flash("Відділи з тими самими назвами, що й у 1С, переведено на довідник 1С: "
              + ", ".join(departments.get_name(v) for v in mapping.values()), "success")


@bp.route("/admin/users")
@login_required(scopes=graph.ADMIN_SCOPES)
@requires("admin")
def admin_users(*, context):
    _migrate_legacy_departments()
    users, groups, error = _roles_source(context.get("access_token"))
    return render_template(
        "admin_users.html",
        user_name=g.user["name"],
        users=sorted(users.values(), key=lambda u: u["name"].lower()),
        role_order=ADMIN_ROLE_ORDER,
        groups=groups,
        missing_groups=[r for r in ADMIN_ROLE_ORDER if r not in groups],
        group_url=_azure_group_url,
        error=error,
        me_oid=g.user["oid"],
        user_departments={uid: org_structure.department_of(uid) for uid in users},
        onec_links=user_onec.all_links(),
        head_departments={uid: org_structure.head_departments(uid) for uid in users},
        alt_addresses=user_prefs.confirmed_addresses() if app_settings.get("allow_custom_email") else {},
        tg_on=telegram_notify.enabled(),
        tg_overrides=user_prefs.telegram_overrides(),
        tg_linked=lambda e: telegram_notify.is_linked(user_prefs.telegram_lookup(e)),
        budgets={uid: user_budget.get(uid) for uid in users},
        budget_summary=user_budget.summary,
        roles_in_app=roles_in_app(),
        google_domain=(google_login.allowed_domains() or [None])[0],
    )


@bp.route("/admin/expense-items/refresh", methods=["POST"])
@login_required
@requires("admin")
def admin_expense_refresh(*, context):
    if not payment_types.refresh():
        flash("Не вдалося оновити види оплати з 1С: " + (payment_types.status()["error"] or "невідома помилка"), "error")
    if not counterparties.refresh():
        flash("Не вдалося оновити довідник контрагентів: " + (counterparties.status()["error"] or "невідома помилка"), "error")
    for pending, target in counterparties.auto_link_candidates():
        _, _, count = _link_counterparty(pending["id"], target["id"], wf.SYSTEM_USER, "admin")
        flash(f"Контрагента «{pending['name']}» автоматично прив'язано до «{target['name']}» з 1С (заявок: {count})",
              "success")
    for name, ref in (("організацій", organizations), ("відділів", departments), ("валют", currencies),
                      ("курсів валют", rates)):
        if not ref.refresh():
            flash(f"Не вдалося оновити довідник {name}: " + (ref.status()["error"] or "невідома помилка"), "error")
    if expense_items.refresh():
        st = expense_items.status()
        flash(f"Довідник статей витрат оновлено: {st['count']} статей", "success")
    else:
        flash("Не вдалося оновити довідник: " + (expense_items.status()["error"] or "невідома помилка"), "error")
    return redirect(url_for("admin.admin_settings", tab="data") + "#expense-items")


@bp.route("/admin/users/search")
@login_required(scopes=graph.ADMIN_SCOPES)
@requires("admin")
def admin_users_search(*, context):
    q = (request.args.get("q") or "").strip()
    if not roles_in_app():
        try:
            return {"users": graph.search_users(context["access_token"], q)}
        except graph.GraphError as e:
            return {"error": str(e)}, 502
    # Ролі в системі: серед тих, кому вже призначено ролі, і тих, хто входив; або нова людина за поштою
    found, ql = {}, q.lower()
    for u in user_roles.all_users():
        if len(ql) >= 2 and (ql in u["name"].lower() or ql in u["email"].lower()):
            found[u["email"].lower()] = {"id": u["id"], "name": u["name"], "email": u["email"]}
    for u in user_directory.search(q):
        found.setdefault(u["email"].lower(), {"id": u["id"], "name": u["name"], "email": u["email"]})
    users = list(found.values())[:15]
    if new_person_allowed(q) and ql not in found:
        users.append({"id": ql, "name": f"Додати {ql}", "email": "нова людина — ролі можна призначити одразу",
                      "new": True})
    return {"users": users}


def _apply_role_changes(token, user_id, wanted, current):
    """Додати/видалити людину в групах так, щоб її ролі стали = wanted. Повертає (додано, знято)."""
    groups = _configured_groups()
    if user_id == g.user["oid"] and "admin" in current and "admin" not in wanted:
        raise graph.GraphError("Не можна зняти роль Адміністратора із самого себе")
    added, removed = [], []
    for role in ADMIN_ROLE_ORDER:
        if role not in groups:
            continue
        if role in wanted and role not in current:
            graph.add_member(token, groups[role], user_id)
            added.append(wf.ROLES[role])
        elif role in current and role not in wanted:
            graph.remove_member(token, groups[role], user_id)
            removed.append(wf.ROLES[role])
    return added, removed


@bp.route("/admin/users/<user_id>", methods=["GET", "POST"])
@login_required(scopes=graph.ADMIN_SCOPES)
@requires("admin")
def admin_user_card(user_id, *, context):
    """Картка користувача: ролі, відділ, керівництво й перегляд відділів, статті бюджету — в одному місці."""
    token = context.get("access_token")
    users, groups, error = _roles_source(token)
    person = users.get(user_id)
    if not person and roles_in_app():  # новий: ще без ролей у системі
        known = user_directory.lookup(user_id)
        if known:
            person = {"id": user_id, "email": known["email"], "name": known.get("name") or known["email"], "roles": set()}
        elif new_person_allowed(user_id):
            email = user_id.strip().lower()
            rec = user_roles.find_by_email(email) or user_directory.find_active(email)
            if rec:  # уже є в системі під своїм id (Entra oid) — його заявки лишаються його
                return redirect(url_for("admin.admin_user_card", user_id=rec["id"]))
            person = {"id": email, "email": email, "name": email, "roles": set()}
        else:
            flash("Користувача не знайдено — додайте його за робочою поштою", "error")
            return redirect(url_for("admin.admin_users"))
    elif not person:  # новий: ще не має жодної ролі
        try:
            person = {**graph.get_user(token, user_id), "roles": set()}
        except graph.GraphError as e:
            flash(f"Користувача не знайдено в Azure: {e}", "error")
            return redirect(url_for("admin.admin_users"))
    if request.method == "POST":
        if error:  # не знаємо поточних ролей — нічого не змінюємо
            flash("Не вдалося прочитати ролі з Azure, тож зміни не збережено: " + error, "error")
            return redirect(url_for("admin.admin_user_card", user_id=user_id))
        return _save_user_card(token, person)
    allow_alt = app_settings.get("allow_custom_email")
    return render_template(
        "admin_user_card.html",
        user_name=g.user["name"],
        person=person,
        is_new=not person["roles"],
        error=error,
        me_oid=g.user["oid"],
        groups=groups,
        group_url=_azure_group_url,
        roles_in_app=roles_in_app(),
        ROLE_GROUPS=wf.ROLE_GROUPS,
        ROLE_HINTS=wf.ROLE_HINTS,
        departments_list=departments.get_departments(),
        user_department=org_structure.department_of(user_id),
        dept_staff={d["code"]: org_structure.staff(d["code"]) for d in departments.get_departments()},
        head_deps=org_structure.head_departments(user_id),
        view_deps=org_structure.viewer_departments(user_id),
        head_mode=app_settings.get("dept_head_mode"),
        accountant_default=org_structure.accountant_default_of(user_id),
        ACCOUNTANT_CHOICES=wf.ACCOUNTANT_CHOICES,
        org_mode=app_settings.get("org_field_mode"),
        budget=user_budget.get(user_id) or {},
        budget_departments=expense_items.get_departments(),
        budget_items=[{"k": i["kind"], "d": i["departments"]} for i in expense_items.get_items()],
        expense_status=expense_items.status(),
        delivery=user_prefs.delivery_address(person["email"], allow_alt),
        tg_on=telegram_notify.enabled(),
        tg_manual=user_prefs.get(person["email"])["telegram_email"],
        tg_lookup=user_prefs.telegram_lookup(person["email"]),
        tg_linked=telegram_notify.is_linked(user_prefs.telegram_lookup(person["email"])),
        tg_channel=telegram_notify.CHANNELS.get(user_prefs.channel(person["email"])),
        onec_link=user_onec.get(user_id),
        onec_users=payment_types.onec_users(),
        onec_suggested=None if user_onec.get(user_id) else payment_types.suggest_user(person["name"], person["email"]),
        onec_types={u["guid"]: [t["name"] for t in payment_types.types_for_user(u["guid"])]
                    for u in payment_types.onec_users()},
        is_accountant_or_cfo=bool(set(person["roles"]) & (wf.ACCOUNTANT_ROLES | {"cfo"})),
    )


EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _save_user_card(token, person):
    f = request.form
    uid, name, email, who = person["id"], person["name"], person["email"], g.user["email"]
    current = set(person["roles"])
    changes = []

    # Ролі (групи Azure). Свою роль адміністратора зняти не можна — поле вимкнене.
    wanted = {r for r in f.getlist("roles") if r in ADMIN_ROLE_ORDER}
    if uid == g.user["oid"] and "admin" in current:
        wanted.add("admin")
    if roles_in_app():  # ролі в системі: записати одразу, діють з наступної сторінки
        if (person.get("name") or "") == email and (f.get("person_name") or "").strip():
            name = f.get("person_name").strip()  # нова людина: ім'я вводить адміністратор
        added = [wf.ROLES[r] for r in ADMIN_ROLE_ORDER if r in wanted - current]
        removed = [wf.ROLES[r] for r in ADMIN_ROLE_ORDER if r in current - wanted]
        if added or removed or user_roles.get(uid) is None or name != person.get("name"):
            user_roles.set_roles(uid, email, name, wanted, who)
    else:
        try:
            added, removed = _apply_role_changes(token, uid, wanted, current)
        except graph.GraphError as e:
            flash(str(e), "error")
            return redirect(url_for("admin.admin_user_card", user_id=uid))
    if added:
        changes.append("додано ролі: " + ", ".join(added))
    if removed:
        changes.append("знято ролі: " + ", ".join(removed))

    # Відділ співробітника
    known = departments.get_map()
    dep = f.get("department") or None
    if dep != org_structure.department_of(uid) and (dep is None or dep in known):
        org_structure.set_department(uid, email, name, dep, who)
        changes.append(f"відділ — {departments.get_name(dep) if dep else 'не вказано'}")

    # Бухгалтер за замовчуванням (режим «Бухгалтер» у заявці)
    if "accountant_default" in f:
        acc = f.get("accountant_default") or None
        if acc in (None, *wf.ACCOUNTANT_CHOICES) and acc != org_structure.accountant_default_of(uid):
            org_structure.set_accountant_default(uid, email, name, acc, who)
            changes.append("бухгалтер за замовчуванням — " + (wf.ACCOUNTANT_CHOICES[acc] if acc else "не задано"))

    # Які відділи очолює / заявки яких бачить (відділи, яких уже немає в довіднику, не чіпаємо)
    old_heads, old_views = org_structure.head_departments(uid), org_structure.viewer_departments(uid)
    heads = {d for d in f.getlist("head_deps") if d in known} | {d for d in old_heads if d not in known}
    views = ({d for d in f.getlist("view_deps") if d in known} | {d for d in old_views if d not in known}) - heads
    if heads != set(old_heads) or views != set(old_views):
        replaced = org_structure.set_person_assignments({"id": uid, "email": email, "name": name}, heads, views, who)
        changes.append("очолює: " + (", ".join(departments.get_name(d) for d in sorted(heads)) or "жодного відділу"))
        changes.append("бачить заявки: " + (", ".join(departments.get_name(d) for d in sorted(views)) or "жодного відділу"))
        for code, prev in replaced:
            changes.append(f"у відділі «{departments.get_name(code)}» замінено керівника {prev['name']}")

    # Пошта для Telegram-бота (порожньо — автоматично за робочою)
    if "telegram_email" in f:
        tg = (f.get("telegram_email") or "").strip().lower()
        if tg == email.lower():
            tg = ""
        if tg and not EMAIL_RE.match(tg):
            flash(f"«{tg}» — це не схоже на адресу пошти; пошту для Telegram не змінено", "error")
        elif tg != (user_prefs.get(email)["telegram_email"] or ""):
            user_prefs.set_telegram_email(email, tg, who)
            changes.append(f"пошта для Telegram — {tg}" if tg else "пошта для Telegram — автоматично за робочою")

    # Користувач 1С (для «Видів оплати»)
    if "onec_user" in f:
        guid = (f.get("onec_user") or "").strip()
        known_onec = {u["guid"]: u["name"] for u in payment_types.onec_users()}
        current_link = user_onec.guid_for(uid)
        if guid != (current_link or "") and (not guid or guid in known_onec):
            user_onec.set_link(uid, email, guid, known_onec.get(guid, ""), who)
            changes.append("користувач 1С — " + (known_onec[guid] if guid else "не зв'язано"))

    # Статті бюджету
    mode = f.get("mode")
    known_exp = {d for d, _ in expense_items.get_departments()}
    prev = user_budget.get(uid) or {}
    exp_deps = [d for d in f.getlist("departments") if d in known_exp or d in (prev.get("departments") or [])] \
        if mode == "selected" else []
    new = {"direct": f.get("direct") == "1", "all_departments": mode == "all", "departments": exp_deps}
    norm = lambda b: (bool(b.get("direct")), bool(b.get("all_departments")), sorted(b.get("departments") or []))
    if norm(prev) != norm(new):
        saved = user_budget.save_settings(uid, email, name, updated_by=who, **new)
        changes.append(f"статті бюджету — доступно {len(user_budget.allowed_items(saved, expense_items.get_items()))}")

    if changes:
        flash(f"{name}: " + "; ".join(changes) + ".", "success")
        if (added or removed) and not roles_in_app():
            flash("Ролі вже змінено в Azure. Щоб вони запрацювали, користувачу треба вийти з системи й увійти знову.",
                  "success")
        if current and not wanted:
            flash(f"{name} більше не має жодної ролі — доступу до системи немає.", "success")
    else:
        flash("Змін немає", "success")
    return redirect(url_for("admin.admin_user_card", user_id=uid))


@bp.route("/admin/notifications")
@login_required
@requires("admin")
def admin_notifications(*, context):
    """Журнал сповіщень: пошта й Telegram — кому, коли, про яку заявку, результат; і кому не надіслано та чому."""
    args = request.args
    channel = args.get("channel") if args.get("channel") in ("email", "telegram", "-") else ""
    status = args.get("status") if args.get("status") in ("problems", "skipped", "sent", "queued") else ""
    q = (args.get("q") or "").strip()
    rows = notify_log.query(500, channel, status, q)
    return render_template("admin_notifications.html", user_name=g.user["name"], rows=rows, channel=channel,
                           status=status, q=q, STATUSES=notify_log.STATUSES, CHANNEL_LABELS=notify_log.CHANNELS,
                           EVENT_LABELS=notify_log.EVENT_LABELS)


@bp.route("/admin/telegram/test", methods=["POST"])
@login_required
@requires("admin")
def admin_telegram_test(*, context):
    if not telegram_notify.enabled():
        flash("Telegram-бот не підключено: у налаштуваннях App Service немає TELEGRAM_BOT_KEY", "error")
    else:
        msg_id = telegram_notify.enqueue(g.user["email"], "✅ <b>Тестове повідомлення</b> системи заявок на оплату.\n"
                                         "Якщо ви його бачите — сповіщення в Telegram працюють.", request.host_url)
        notify_log.record("telegram", g.user["email"], "queued", "Тестове повідомлення", event="test", ref=f"tg:{msg_id}")
        flash("Тестове повідомлення поставлено в чергу — бот забере його протягом ~30 секунд", "success")
    return redirect(url_for("admin.admin_settings", tab="notify") + "#mail-settings")
