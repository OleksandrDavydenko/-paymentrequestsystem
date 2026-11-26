"""Групи статей витрат: варіанти однієї статті з різною базою розподілу в 1С.

У довіднику 1С одна стаття часто має кілька варіантів:
  • з розподілом — «Ремонт і сервіс»: 1С розносить її на відділ-власник статті (Owner);
  • пряма — «Ремонт і сервіс (прямий)»: лягає на відділ рядка, можлива лише для профітцентру;
  • позапланова — база розподілу «Не розподіляти» (або «поза план» у назві): 1С кидає її на «Інше».
У 1С варіанти між собою не пов'язані, тож групи задає адміністратор («Налаштування → Заявка й погодження → Групи
статей і розподіл», item_groups). Діють лише збережені групи — довідник, що змінився в 1С, їх сам не міняє.
Кнопка «Знайти групи за назвою» (suggest) пропонує групи за назвою без дужок у кінці («Ремонт і сервіс (прямий)» →
«ремонт і сервіс»): роль — guess_role; пропозиція є, лише коли на кожну роль одна стаття й варіантів хоча б два,
інакше — «не вдалося розібрати». Пропозицію адміністратор додає (тоді вона — звичайна збережена група) або позначає
«не пропонувати» (item_groups_skip — база назви). Старі пари split_pairs («пряма → з розподілом») діють як групи, доки
розділ не збережено.

Для чого група: розподіл заявки між відділами (expense_split), рядок групи у вкладці «Бюджет» (budget.report),
підказка «Інші варіанти» під статтею в заявці.
"""
import re

import app_settings
import expense_items

ROLES = ("allocated", "direct", "unplanned")
ROLE_LABELS = {"allocated": "з розподілом", "direct": "пряма", "unplanned": "позапланова"}
DIRECT_MARK = "(прямий)"  # так у довіднику 1С названо прямі статті
UNPLANNED_NAME = re.compile(r"поза\s*план", re.IGNORECASE)  # «(поза план)», «(позапланові)», «(позапланова)»
_TAIL = re.compile(r"\s*\([^()]*\)\s*$")


def base_name(name):
    """«Ремонт і сервіс (прямий)» → «ремонт і сервіс»: назва без дужок у кінці, малими літерами."""
    name = (name or "").strip()
    while True:
        cut = _TAIL.sub("", name)
        if cut == name or not cut:
            break
        name = cut
    return re.sub(r"\s+", " ", name).strip().lower()


def unplanned_by_name(name):
    return bool(UNPLANNED_NAME.search(name or ""))


def guess_role(item):
    """Роль статті в групі за довідником: позапланова (ознака 1С «Не розподіляти» або «поза план» у назві), пряма
    («(прямий)» у назві) або з розподілом."""
    name = item.get("name") or ""
    if item.get("unplanned") or unplanned_by_name(name):
        return "unplanned"
    if DIRECT_MARK in name.lower():
        return "direct"
    return "allocated"


_detect_cache = {}


def detect():
    """Збіги за назвою в усьому довіднику: (групи, неоднозначні). Група — {"base", "allocated", "direct",
    "unplanned"}; неоднозначна — {"base", "items": [{"code", "name", "role"}], "why"} (на одну роль кілька статей)."""
    items = expense_items.get_items()
    if _detect_cache.get("key") is items:
        return _detect_cache["value"]
    by_base = {}
    for i in items:
        b = base_name(i.get("name"))
        if b:
            by_base.setdefault(b, []).append(i)
    groups, ambiguous = [], []
    for b, members in sorted(by_base.items()):
        if len(members) < 2:
            continue
        roles = {}
        for i in members:
            roles.setdefault(guess_role(i), []).append(i["code"])
        if any(len(v) > 1 for v in roles.values()):
            why = "; ".join(f"{ROLE_LABELS[r]} — {len(v)} статті" for r, v in roles.items() if len(v) > 1)
            ambiguous.append({"base": b, "why": why,
                              "items": [{"code": i["code"], "name": i["name"], "role": guess_role(i)} for i in members]})
            continue
        g = {"base": b, **{r: (roles.get(r) or [""])[0] for r in ROLES}}
        if sum(1 for r in ROLES if g[r]) >= 2:
            groups.append(g)
    _detect_cache.update(key=items, value=(groups, ambiguous))
    return groups, ambiguous


def suggest():
    """Пропозиції кнопки «Знайти групи за назвою»: (групи, неоднозначні) без статей, що вже в групах, і без баз назв
    «не пропонувати»."""
    used = {g[r] for g in manual() for r in ROLES if g[r]}
    skip = set(app_settings.get("item_groups_skip") or [])
    found, ambiguous = detect()
    names = {i["code"]: i["name"] for i in expense_items.get_items()}
    out = []
    for g in found:
        if g["base"] in skip or any(g[r] in used for r in ROLES if g[r]):
            continue
        out.append({**g, "name": title(g, names), "names": {r: names.get(g[r]) for r in ROLES}})
    amb = [a for a in ambiguous if a["base"] not in skip and not all(i["code"] in used for i in a["items"])]
    return out, amb


def _clean(g):
    return {r: str(g.get(r) or "").strip() for r in ROLES}


def manual():
    """Збережені групи: item_groups, а поки розділ не збережено — ще й старі пари split_pairs."""
    out = [_clean(g) for g in app_settings.get("item_groups") or []]
    used = {g[r] for g in out for r in ROLES if g[r]}
    for p in app_settings.get("split_pairs") or []:
        d, a = str(p.get("direct") or "").strip(), str(p.get("allocated") or "").strip()
        if d and a and d != a and d not in used and a not in used:
            out.append({"allocated": a, "direct": d, "unplanned": ""})
            used |= {d, a}
    return [g for g in out if sum(1 for r in ROLES if g[r]) >= 2]


def groups():
    """Діючі групи (лише збережені): [{"allocated", "direct", "unplanned", "name"}]."""
    names = {i["code"]: i["name"] for i in expense_items.get_items()}
    return [{**g, "name": title(g, names)} for g in manual()]


def title(g, names=None):
    """Назва групи: назва варіанта з розподілом, інакше база назви (з великої літери)."""
    names = names or {}
    for r in ROLES:
        code = g.get(r)
        if code:
            name = names.get(code) or expense_items.get_name(code) or code
            return name if r == "allocated" else (_TAIL.sub("", name).strip() or name)
    return g.get("base") or ""


def by_code():
    """{код статті: група} для діючих груп."""
    out = {}
    for g in groups():
        for r in ROLES:
            if g[r]:
                out[g[r]] = g
    return out


def group_of(code):
    return by_code().get(code) if code else None


def role(code):
    """Роль статті в її групі (allocated / direct / unplanned) або None."""
    g = group_of(code)
    if not g:
        return None
    return next(r for r in ROLES if g[r] == code)


def is_unplanned(code):
    """Позапланова стаття: роль у групі, ознака «Не розподіляти» з довідника або «поза план» у назві."""
    if not code:
        return False
    if role(code) == "unplanned":
        return True
    item = expense_items.get_map().get(code)
    return bool(item and (item.get("unplanned") or unplanned_by_name(item.get("name"))))


def describe(g):
    """«Ремонт і сервіс: з розподілом 05.001 / пряма 05.011 / позапланова 05.021» — для журналу змін."""
    parts = [f"{ROLE_LABELS[r]} {g[r]}" for r in ROLES if g.get(r)]
    return f"{title(g)}: " + " / ".join(parts)
