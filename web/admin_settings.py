"""Адміністрування → Налаштування: вкладки, збереження, журнал змін, експорт / імпорт.

Що є налаштуванням, його назва, вкладка й перевірка — у реєстрі settings_schema.py. Пошта (SMTP)
і організації мають власні форми (admin_routes.py) й лише показуються на своїх вкладках.
"""
import json
from copy import deepcopy
from datetime import date as date_cls, timedelta
from decimal import Decimal

from flask import Response, flash, g, redirect, render_template, request, session, url_for

import app_settings
import cfo_rules
import clock
import counterparties
import currencies
import db_sql
import departments
import dept_settings
import employees
import expense_items
import google_login
import holidays
import mailer
import notify_log
import org_structure
import organizations
import payment_types
import rates
import request_numbers
import settings_log
import settings_schema as schema
import storage as db
import telegram_notify
import user_roles
import workflow as wf

from web.admin_routes import bp
from web.auth import ADMIN_EMAILS, login_mode, login_required, requires

ROUTE_KEYS = ("approval_mode", "dept_head_mode", "cfo_mode", "cfo_threshold_usd", "cfo_rules", "cfo_currency",
              "cfo_new_counterparty", "cfo_expense_codes")
IMPORT_KEY = "settings_import"  # зміни з файлу, що чекають підтвердження (у сеансі)


def _switch_mail_for_mode(data_mode, who):
    """Перехід у тестовий режим вимикає розсилку («Email-сповіщення» → «Розсилка увімкнена»); повернення
    до робочої бази відновлює її стан, який був до тестового режиму."""
    if data_mode == "test":
        app_settings.set("mail_enabled_work", bool(app_settings.get("mail_enabled")), who)
        if app_settings.get("mail_enabled"):
            app_settings.set("mail_enabled", False, who)
            flash("Розсилку вимкнено на час тестового режиму. Щоб перевірити сповіщення, увімкніть її в "
                  "«Email-сповіщення» — листи й Telegram підуть з позначкою «[ТЕСТ]».", "success")
        return
    before = app_settings.get("mail_enabled_work")
    if before is not None:
        app_settings.set("mail_enabled_work", None, who)
        if bool(before) != bool(app_settings.get("mail_enabled")):
            app_settings.set("mail_enabled", bool(before), who)
            flash("Розсилку " + ("знову увімкнено" if before else "вимкнено") + ", як було до тестового режиму.",
                  "success")


def _check_login_switch(changes):
    """«Лише Google» — тільки з налаштованим OAuth-клієнтом. Повертає текст помилки (тоді не зберігати) або None."""
    if changes.get("login_methods") == "google" and not google_login.configured():
        return ("Спершу налаштуйте вхід через Google: змінні GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET і "
                "GOOGLE_ALLOWED_DOMAINS в App Service (див. README)")
    return None


def _message(key, value):
    """Повідомлення про збережену зміну (для маршруту — один підсумок, див. _apply)."""
    if key == "org_field_mode":
        return "У заявці ініціатор вибирає: " + {"payer": "лише платника — українська чи іноземна компанія",
                                                "accountant": "тип бухгалтера"}.get(value, "організацію")
    if key == "data_mode":
        return "Система показує: " + ("тестові дані — нові заявки не зберігаються в базі" if value == "test"
                                      else "робочу базу даних")
    if key == "cp_onec_owner":
        return "Нових контрагентів у 1С заводить: " + ("адміністратор" if value == "admin" else "бухгалтер")
    if key == "pending_cp_payment":
        return f"Контрагент, якого ще немає в 1С, має бути в 1С: {schema.PENDING_CP_MODES[value]}"
    if key == "login_methods":
        return "Вхід у систему: " + ("Google і Microsoft" if value == "both" else
                                     "лише Google (адміністратори з ADMIN_EMAILS — ще й через Microsoft)")
    if key == "cash_handover_remind_days":
        return f"Нагадування про передачу готівки: кожні {value} дн." if value else "Нагадування про передачу готівки вимкнено"
    return f"{schema.label(key)}: {schema.value_label(key, value)}"


def _parse_request_prefix(changes, errors):
    """Префікс номера заявки: до 6 латинських літер (великими). Колишній непорожній — в історію: звірка й пошук
    розпізнають його й далі (позначки в 1С і виписках лишаються старі)."""
    new = changes["request_prefix"].strip().upper()
    old = str(app_settings.get("request_prefix") or "").upper()
    if not request_numbers.PREFIX_RE.fullmatch(new):
        errors["request_prefix"] = "«Префікс номера заявки»: до 6 латинських літер (A–Z), без цифр, риски й пробілів"
    elif new == old:
        changes.pop("request_prefix")
    else:
        changes["request_prefix"] = new
        if old:
            history = [p for p in app_settings.get("request_prefix_history") or [] if p not in (old, new)]
            changes["request_prefix_history"] = history + [old]


def _apply(changes, who):
    """Зберегти зміни з побічними діями й повідомленнями. Повертає кількість змін."""
    for key, value in changes.items():
        app_settings.set(key, value, who)
    if any(k in changes for k in ROUTE_KEYS):
        route = wf.route_label(app_settings.get("approval_mode"), app_settings.get("dept_head_mode"),
                               app_settings.get("cfo_mode"), cfo_rules.short())
        flash(f"Маршрут: {route}. Діє для нових і повторно відправлених заявок.", "success")
        if "cfo_rules" in changes:
            flash("Правила фіндиректора: " + cfo_rules.describe_rows(changes["cfo_rules"], app_settings.get("cfo_currency")),
                  "success")
    for key, value in changes.items():
        if key not in ROUTE_KEYS:
            flash(_message(key, value), "success")
        if key == "data_mode":
            _switch_mail_for_mode(value, who)
        if key == "org_accountants":
            db.reindex()  # бухгалтер заявки — похідна колонка в базі
    return len(changes)


def _context(tab):
    """Дані для вкладки (лише те, що вона показує)."""
    ctx = {"TABS": schema.TABS, "tab": tab, "route_label": wf.route_label}
    if tab == "approval":
        items = sorted(expense_items.get_items(), key=lambda i: i["code"])
        ctx.update(approval_mode=app_settings.info("approval_mode"), head_mode=app_settings.info("dept_head_mode"),
                   cfo_mode=app_settings.info("cfo_mode"), cfo=cfo_rules.get(), cfo_short=cfo_rules.short(),
                   CFO_ROWS=cfo_rules.ROWS, CFO_ROW_LABELS=cfo_rules.ROW_LABELS, CFO_RULE_MODES=cfo_rules.MODES,
                   CFO_CURRENCIES=cfo_rules.CURRENCIES, cfo_expense_items=items,
                   cfo_expense_known={i["code"] for i in items},
                   APPROVAL_MODES=wf.APPROVAL_MODES, HEAD_MODES=wf.HEAD_MODES, CFO_MODES=wf.CFO_MODES,
                   no_self_approval=app_settings.get("no_self_approval"))
    elif tab == "request":
        ctx.update(request_prefix=request_numbers.prefix(), request_digits=request_numbers.digits(),
                   request_prefix_history=[p for p in request_numbers.all_prefixes() if p != request_numbers.prefix()],
                   number_example=request_numbers.label(123), marker_example=request_numbers.marker(123))
        ctx.update(org_mode=app_settings.get("org_field_mode"), ORG_FIELD_MODES=wf.ORG_FIELD_MODES,
                   **{k: app_settings.get(k) for k in ("req_document", "req_document_limit", "req_min_pay_days",
                                                       "req_lines_required", "withdraw_mode", "cancel_mode",
                                                       "doc_extensions", "doc_max_mb")},
                   REQ_DOCUMENT_MODES=schema.SETTINGS["req_document"].choices(),
                   WITHDRAW_MODES=schema.SETTINGS["withdraw_mode"].choices(),
                   CANCEL_MODES=schema.SETTINGS["cancel_mode"].choices(), DOC_FORMATS=schema.DOC_FORMATS,
                   invoices_mode=app_settings.get("invoices_mode"),
                   INVOICES_MODES=schema.SETTINGS["invoices_mode"].choices(),
                   kind_question=app_settings.get("kind_question") is not False,
                   invoices_block=app_settings.get("invoices_block"),
                   INVOICES_BLOCK_MODES=schema.SETTINGS["invoices_block"].choices(),
                   threshold_currency=cfo_rules.get()["currency"])
    elif tab == "payment":
        pending = counterparties.pending_items()
        ctx.update(org_mode=app_settings.get("org_field_mode"),
                   require_payment_account=app_settings.get("require_payment_account"),
                   cash_handover_confirm=app_settings.get("cash_handover_confirm"),
                   cash_handover_remind_days=app_settings.get("cash_handover_remind_days"),
                   handover_max_reminders=wf.handover_max(),
                   pending_cp=app_settings.info("pending_cp_payment"), PENDING_CP_MODES=schema.PENDING_CP_MODES, PENDING_CP_TITLES=schema.PENDING_CP_TITLES,
                   cp_owner=app_settings.get("cp_onec_owner"), CP_OWNER_MODES=wf.CP_OWNER_MODES,
                   pending_counterparties=pending,
                   pending_cp_requests={c["id"]: db.first_request_with_counterparty(c["id"]) for c in pending},
                   org_list=organizations.get_all(), org_disabled=organizations.disabled_codes(),
                   org_accountants=organizations.accountant_overrides(),
                   ACCOUNTANT_ROLE_LIST=[(r, wf.ROLE_SHORT[r]) for r in ("acc_cash", "acc_resident", "acc_nonresident")])
    elif tab == "notify":
        ctx.update(mail={k: app_settings.get(k) for k in ("mail_enabled", "mail_sender", "mail_sender_name", "smtp_host",
                                                          "smtp_port", "smtp_security", "smtp_username",
                                                          "allow_custom_email", "error_alerts")},
                   **{k: app_settings.get(k) for k in ("approver_remind_days", "approver_remind_max",
                                                       "approver_remind_cfo_copy")},
                   mail_password=app_settings.info("smtp_password"), SMTP_PRESETS=mailer.PRESETS,
                   SMTP_SECURITY=mailer.SECURITY, telegram=telegram_notify.status(),
                   mail_problems=len(notify_log.query(200, status="problems")))
    elif tab == "onec_api":
        import onec_api
        ctx.update(api_enabled=bool(app_settings.get("onec_api_enabled")), api_ips=app_settings.get("onec_api_ips") or "",
                   api_key_info=app_settings.get("onec_api_key_info") if app_settings.get("onec_api_key_hash") else None,
                   api_last=onec_api.last_access(), api_base=request.host_url.rstrip("/"))
    elif tab == "reconcile":
        import payments
        ctx.update(payments_since=app_settings.get("payments_since"), PAY_MODES=schema.SETTINGS["pay_mode_acc_cash"].choices(),
                   pay_modes={r: payments.pay_mode(r) for r in wf.ACCOUNTANT_ROLES},
                   ACCOUNTANT_ROLE_LIST=[(r, wf.ROLES[r]) for r in ("acc_cash", "acc_resident", "acc_nonresident")],
                   sync_state=payments.status(),
                   link_roles=app_settings.get("payment_link_roles") or [], LINK_ROLES=schema.SETTINGS["payment_link_roles"].choices(),
                   ok_departments=app_settings.get("payments_ok_departments") or [],
                   OK_DEPARTMENTS=schema.SETTINGS["payments_ok_departments"].choices(),
                   ok_items=app_settings.get("payments_ok_items") or [], OK_ITEMS=schema.SETTINGS["payments_ok_items"].choices(),
                   ok_operations=app_settings.get("payments_ok_operations") or [],
                   OK_OPERATIONS=schema.SETTINGS["payments_ok_operations"].choices(),
                   ok_payment_types=app_settings.get("payments_ok_payment_types") or [],
                   ok_until=app_settings.get("payments_ok_until") or {},
                   dept_default=app_settings.get("payments_dept_default") or "connected",
                   DEPT_DEFAULT_MODES=schema.DEPT_DEFAULT_MODES,
                   OK_PAYMENT_TYPES=schema.SETTINGS["payments_ok_payment_types"].choices(),
                   cp_stage=app_settings.get("pending_cp_payment"), PENDING_CP_TITLES=schema.PENDING_CP_TITLES)
    elif tab == "design":
        today, enabled = clock.today(), app_settings.get("holidays") or []
        upcoming = holidays.next_up(today, enabled)
        ctx.update(app_name_value=app_settings.get("app_name"), color_theme=app_settings.get("color_theme"),
                   COLOR_THEMES=schema.COLOR_THEMES,
                   card_design=app_settings.get("card_design"), CARD_DESIGNS=schema.CARD_DESIGNS,
                   holiday_list=[{"code": code, "name": name, "greet": greet, "when": holidays.when(code, today),
                                  "on": code in enabled} for code, (name, greet) in holidays.HOLIDAYS.items()],
                   holiday_active=holidays.active(today, enabled, app_settings.get("holiday_now")),
                   holiday_now=app_settings.get("holiday_now"), HOLIDAY_NOW=schema.SETTINGS["holiday_now"].choices(),
                   holiday_next=(holidays.HOLIDAYS[upcoming[0]][0], holidays.fmt_day(upcoming[1])) if upcoming else None)
    elif tab == "data":
        ctx.update(login="google" if app_settings.get("login_methods") == "google" else "both",
                   login_effective=login_mode(),
                   LOGIN_METHODS=schema.SETTINGS["login_methods"].choices(), google_ready=google_login.configured(),
                   google_domains=google_login.allowed_domains(), app_role_users=len(user_roles.all_users()),
                   external_users=sum(1 for u in user_roles.all_users() if google_login.is_external(u["email"])),
                   admin_emails=sorted(ADMIN_EMAILS))
        ctx.update(db_on=db_sql.configured(), data_mode=app_settings.get("data_mode"),
                   expense_status=expense_items.status(), rates_status=rates.status(),
                   departments_status=departments.status(), organizations_status=organizations.status(),
                   currencies_status=currencies.status(), counterparties_status=counterparties.status(),
                   employees_status=employees.status(),
                   profit_center_names=[departments.get_name(c) for c in departments.profit_centers()],
                   payment_types_status=payment_types.status(), import_preview=_import_preview())
    elif tab == "log":
        key = request.args.get("key") or None
        ctx.update(log=settings_log.query(300, key), log_key=key,
                   log_keys=[(s.key, s.label) for s in schema.exportable()])
    return ctx


def _page_context(tab):
    """Вкладка з розділом tab: дані всіх її розділів (назви в них не перетинаються)."""
    _, title, sections = schema.page_of(tab)
    ctx = {}
    for section in sections:
        ctx.update(_context(section))
    ctx.update(tab=tab, sections=sections, page_title=title)
    return ctx


def settings_url(tab):
    """Посилання на розділ: вкладка з ним, а якщо розділів на вкладці кілька — одразу до нього."""
    url = url_for("admin.admin_settings", tab=tab)
    return url + "#s-" + tab if len(schema.page_of(tab)[2]) > 1 else url


def _parse_ok_until(form, changes, errors):
    """«Діє до» для винятків «без заявки дозволено»: лише для позначених значень; порожня дата — виняток назавжди."""
    until = {}
    for name in form:
        if not name.startswith("ok_until__"):
            continue
        _, key, value = name.split("__", 2)
        raw = (form.get(name) or "").strip()
        if key not in schema.OK_UNTIL_KINDS or not raw or value not in form.getlist(key):
            continue
        try:
            until.setdefault(key, {})[value] = date_cls.fromisoformat(raw).isoformat()
        except ValueError:
            errors["payments_ok_until"] = "«Діє до» у винятках: вкажіть дату"
    if until != (app_settings.get("payments_ok_until") or {}):
        changes["payments_ok_until"] = until


def _parse_cfo_rules(form, changes, errors):
    """Таблиця правил фіндиректора й список статей із форми вкладки «Погодження»."""
    table = {}
    for role in cfo_rules.ROWS:
        mode = form.get(f"cfo_rule_{role}")
        if mode not in cfo_rules.MODES:
            errors[f"cfo_rule_{role}"] = f"«{cfo_rules.ROW_LABELS[role]}»: невідомий варіант"
            continue
        limit = cfo_rules.rows()[role]["limit"]
        raw = (form.get(f"cfo_limit_{role}") or "").replace(" ", "").replace(" ", "")
        if mode == "threshold":
            try:
                limit = int(raw.split(",")[0].split(".")[0])
            except ValueError:
                limit = 0
            if limit <= 0:
                errors[f"cfo_limit_{role}"] = (f"«{cfo_rules.ROW_LABELS[role]}»: вкажіть поріг фіндиректора — суму, "
                                               "більшу за 0")
                continue
        table[role] = {"mode": mode, "limit": limit}
    if len(table) == len(cfo_rules.ROWS) and table != cfo_rules.rows():
        changes["cfo_rules"] = table
    codes = sorted({c.strip() for c in form.getlist("cfo_expense_codes") if c and c.strip()})
    if codes != sorted(app_settings.get("cfo_expense_codes") or []):
        changes["cfo_expense_codes"] = codes


def _sample_request(user):
    """Заявка-приклад для «Відкрити приклад»: контрагент і стаття з довідників, два рахунки, сума й дата."""
    found, _ = counterparties.search("", limit=1, scope="onec")
    # Відділ із прямими витратами — щоб у прикладі були всі кроки (тип, рахунки)
    own = org_structure.department_of(user["oid"])
    dept = own if own and dept_settings.direct_allowed(own) else next(
        (d for d in departments.get_map() if dept_settings.direct_allowed(d)), own)
    code = next((c for c in dept_settings.contractor_codes(dept) if expense_items.get_name(c)), None) or next(
        (i["code"] for i in expense_items.get_items()), None)
    org_mode = app_settings.get("org_field_mode")
    return {"id": None, "number": None, "author_email": user["email"], "author_name": user["name"], "department": dept,
            "status": "draft", "payment_form": "bank", "currency": "USD", "amount": Decimal("12500.00"),
            "pay_date": clock.today() + timedelta(days=3), "counterparty_id": found[0]["id"] if found else None,
            "expense_code": code, "payment_kind": "contractor",
            "note": "Морське перевезення контейнера для клієнта — приклад примітки для бухгалтера",
            "organization_id": next(iter(organizations.options()), None) if org_mode == "organization" else None,
            "accountant_type": "acc_resident" if org_mode == "accountant" else None,
            "payer": "resident" if org_mode == "payer" else None,
            "lines": [{"invoice": "НП-104512", "amount": Decimal("7800.00"), "currency": "USD", "deal": "Угода №15/2025 · фрахт"},
                      {"invoice": "НП-104788", "amount": Decimal("4700.00"), "currency": "USD",
                       "deal": "Угода №15/2025 · митне оформлення"}],
            "history": [], "attachments": [], "participants": [], "created_at": clock.now(), "version": 0}


@bp.route("/admin/settings/design/preview")
@login_required
@requires("admin")
def design_preview(*, context):
    """Приклад картки заявки в будь-якому дизайні й темі (не змінюючи налаштувань): заповнення ініціатором або
    перегляд бухгалтером. Дані — приклад, форми на сторінці нічого не надсилають."""
    from web.context import theme_context
    from web.request_helpers import contractor_items
    from web.requests_routes import _render_card
    a, user = request.args, g.user
    design = a.get("design") if a.get("design") in schema.CARD_DESIGNS else app_settings.get("card_design")
    theme = a.get("theme") if a.get("theme") in schema.COLOR_THEMES else app_settings.get("color_theme")
    mode = "view" if a.get("mode") == "view" else "edit"
    holiday = a.get("holiday") if a.get("holiday") in holidays.HOLIDAYS else (
        None if a.get("holiday") == "none" else holidays.active(clock.today(), app_settings.get("holidays"),
                                                                 app_settings.get("holiday_now")))
    req = _sample_request(user)
    routed = deepcopy(req)
    wf.add_history(routed, "create", user, "initiator", to_status="draft")
    try:
        wf.apply_action(routed, "submit", {"initiator"}, user)
    except wf.WorkflowError:
        pass
    if mode == "view":  # заявку подав інший співробітник, бачить бухгалтер, чия черга
        req = {**routed, "id": 10 ** 9, "number": "000000123", "author_email": "manager@example.com",
               "author_name": "Ірина Петренко (приклад)"}
        roles = {wf.accountant_role(req) or "acc_resident"}
    else:
        roles = {"initiator"}
    dept = req["department"]
    contractor_lists = {dept or "": contractor_items(dept), "": contractor_items(None)}
    return _render_card(req, req, {}, user, roles, expense_items.get_items(), contractor_lists, design=design, preview={
        "preview_mode": mode, "CARD_DESIGNS": schema.CARD_DESIGNS, "COLOR_THEMES": schema.COLOR_THEMES,
        "holiday": holiday, "preview_holiday": holiday or "none", "HOLIDAYS": holidays.HOLIDAYS,
        "steps_static": [name for _, name in wf.stage_names(routed)], "back_url": settings_url("design"),
        **theme_context(theme)})


@bp.route("/admin/settings", methods=["GET", "POST"])
@login_required
@requires("admin")
def admin_settings(*, context):
    if request.method == "GET":
        tab = request.args.get("tab") if request.args.get("tab") in schema.TAB_NAMES else "approval"
        return render_template("admin_settings.html", user_name=g.user["name"], **_page_context(tab))
    tab = request.form.get("tab")
    legacy = tab not in schema.TAB_NAMES  # стара спільна форма «Налаштування процесу»
    changes, errors = schema.parse_tab(tab, request.form, app_settings.get, legacy=legacy)
    if "cfo_threshold_usd" in errors:
        mode = changes.get("cfo_mode", app_settings.get("cfo_mode"))
        if mode == "threshold":
            errors["cfo_threshold_usd"] = "Вкажіть поріг фіндиректора — суму в USD, більшу за 0"
        else:
            errors.pop("cfo_threshold_usd")  # поріг не діє в цьому режимі — порожнє поле не помилка
    if (changes.get("cfo_mode") == "threshold" and "cfo_threshold_usd" not in changes
            and not app_settings.get("cfo_threshold_usd")):
        errors["cfo_threshold_usd"] = "Вкажіть поріг фіндиректора — суму в USD, більшу за 0"
    if tab == "approval" and "cfo_rules_sent" in request.form:
        _parse_cfo_rules(request.form, changes, errors)
    if tab == "reconcile" and "ok_until_sent" in request.form:
        _parse_ok_until(request.form, changes, errors)
    if "payments_since" in changes:
        try:
            changes["payments_since"] = date_cls.fromisoformat(changes["payments_since"]).isoformat()
        except ValueError:
            errors["payments_since"] = "«Звіряти платежі з 1С від дати»: вкажіть дату"
    if "request_prefix" in changes:
        _parse_request_prefix(changes, errors)
    if "app_name" in changes and not changes["app_name"]:
        errors["app_name"] = "Вкажіть назву системи"
    elif len(changes.get("app_name") or "") > 40:
        errors["app_name"] = "Назва системи — не довше 40 символів"
    if changes.get("doc_extensions") == []:
        errors["doc_extensions"] = "Залиште дозволеним хоча б один формат документів"
    back = settings_url("approval" if legacy else tab)
    if not errors and (error := _check_login_switch(changes)):
        errors["login_methods"] = error
    if errors:
        for message in errors.values():
            flash(message, "error")
        return redirect(back)
    if not _apply(changes, g.user["email"]):
        flash("Змін немає", "success")
    return redirect(back)


# ---------------------------------------------------------------- експорт / імпорт

@bp.route("/admin/onec-api/key", methods=["POST"])
@login_required
@requires("admin")
def onec_api_key(*, context):
    """Створити новий ключ API для 1С (старий перестає діяти); ключ показується один раз — на цій сторінці."""
    import onec_api
    key = onec_api.new_key(g.user)
    return render_template("admin_settings.html", user_name=g.user["name"], **_page_context("onec_api"), new_api_key=key)


@bp.route("/admin/payments/sync", methods=["POST"])
@login_required
@requires("admin")
def payments_sync_now(*, context):
    """«Синхронізувати зараз»: забрати платежі з 1С, перевірити видалені й зіставити з заявками."""
    import payments
    state = payments.sync(check_keys=True)
    if state.get("busy"):
        flash("Синхронізація вже йде — оновіть сторінку за хвилину", "success")
    elif state.get("error"):
        flash("Не вдалося забрати платежі з 1С: " + state["error"], "error")
    else:
        paid = state.get("paid_last") or []
        flash(f"Платежі з 1С синхронізовано: рядків у копії {state.get('rows', 0)}, нових або змінених "
              f"{state.get('loaded_last', 0)}, прив'язано платежів {state.get('linked_last', 0)}"
              + (f", позначено оплаченими: {', '.join('№ ' + request_numbers.label(n) for n in paid)}" if paid else ""),
              "success")
    return redirect(settings_url("reconcile"))


@bp.route("/admin/settings/export")
@login_required
@requires("admin")
def admin_settings_export(*, context):
    from web.context import app_name
    data = {"system": app_name(), "exported_at": clock.today().isoformat(),
            "exported_by": g.user["email"],
            "settings": {s.key: app_settings.get(s.key) for s in schema.exportable()}}
    body = json.dumps(data, ensure_ascii=False, indent=1, default=str)
    return Response(body, mimetype="application/json", headers={
        "Content-Disposition": f"attachment; filename=payreq-settings-{clock.today().isoformat()}.json"})


def _import_preview():
    pending = session.get(IMPORT_KEY)
    if not pending:
        return None
    return [{"key": k, "label": schema.label(k), "tab": schema.TAB_NAMES.get(schema.SETTINGS[k].tab, ""),
             "old": schema.value_label(k, app_settings.get(k)), "new": schema.value_label(k, v)}
            for k, v in pending["changes"].items() if k in schema.SETTINGS]


@bp.route("/admin/settings/import", methods=["POST"])
@login_required
@requires("admin")
def admin_settings_import(*, context):
    back = url_for("admin.admin_settings", tab="data") + "#import"
    f = request.files.get("file")
    try:
        data = json.loads((f.read() if f else b"").decode("utf-8-sig"))
    except (ValueError, UnicodeDecodeError):
        flash("Не вдалося прочитати файл — потрібен JSON, збережений кнопкою «Завантажити налаштування»", "error")
        return redirect(back)
    changes, errors, unknown = schema.parse_import(data, app_settings.get)
    if errors:
        for message in errors:
            flash(message, "error")
        return redirect(back)
    if not changes:
        flash("У файлі ті самі налаштування, що й зараз — змін немає", "success")
        return redirect(back)
    session[IMPORT_KEY] = {"changes": changes, "unknown": unknown}
    if unknown:
        flash("Пропущено невідомі для цієї версії налаштування: " + ", ".join(unknown), "error")
    return redirect(back)


@bp.route("/admin/settings/import/apply", methods=["POST"])
@login_required
@requires("admin")
def admin_settings_import_apply(*, context):
    pending = session.pop(IMPORT_KEY, None)
    back = url_for("admin.admin_settings", tab="data") + "#import"
    if request.form.get("action") != "apply" or not pending:
        flash("Імпорт скасовано", "success")
        return redirect(back)
    # Ще раз перевірити: між переглядом і застосуванням налаштування могли змінитися
    changes, errors, _ = schema.parse_import({"settings": pending["changes"]}, app_settings.get)
    if not errors and (error := _check_login_switch(changes)):
        errors = [error]
    if errors:
        for message in errors:
            flash(message, "error")
        return redirect(back)
    n = _apply(changes, g.user["email"])
    flash(f"Імпортовано налаштувань: {n}", "success")
    return redirect(back)
