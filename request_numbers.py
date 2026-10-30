"""Номер заявки для людей і його розпізнавання в тексті (призначення платежу, Примечание 1С, пошук).

У базі номер — 9 цифр з нулями (`000000123`, req["number"]); префікс і нулі — лише показ («Налаштування → Заявка →
Номер заявки»): зміна одразу міняє вигляд усіх заявок і нічого в даних не переписує. За замовчуванням — як було:
без префікса, 9 цифр.

label(number)   — `PD000123` (префікс + цифри з нулями до заданої довжини; довших номерів це не обрізає);
marker(number)  — що писати в призначенні платежу й Примечании 1С: з префіксом — label, без префікса — `#123`;
numbers_in(text) — номери заявок у тексті: після `#` (`#123`, `# 000123`, `#PD123`) і з префіксом без `#`
                   (`PD123`, `PD-123`, `PD 000123`, `pd123`, кирилична «Р» замість «P»); поточний і колишні префікси.
                   Голе число не розпізнається — у призначенні є номери рахунків, суми, ЄДРПОУ.
"""
import re

import app_settings

MAX_DIGITS = 9
PREFIX_RE = re.compile(r"[A-Z]{0,6}")
# Кириличні літери, схожі на латинські: «РD123», набране з кириличною «Р», — те саме, що «PD123»
_LOOKALIKE = str.maketrans("АВЕКМНОРСТХІаеікмортсх", "ABEKMHOPCTXIaeikmoptcx")


def prefix():
    return str(app_settings.get("request_prefix") or "").strip().upper()


def digits():
    try:
        return max(1, min(int(app_settings.get("request_digits") or MAX_DIGITS), MAX_DIGITS))
    except (TypeError, ValueError):
        return MAX_DIGITS


def all_prefixes():
    """Поточний префікс і колишні (щоб старі позначки в 1С і виписках розпізнавалися й після зміни)."""
    out = []
    for p in [prefix(), *(app_settings.get("request_prefix_history") or [])]:
        p = str(p or "").strip().upper()
        if p and p not in out:
            out.append(p)
    return out


def _int(number):
    try:
        return int(str(number).strip())
    except (TypeError, ValueError):
        return None


def label(number):
    """`PD000123` для номера `000000123` (або числа 123); порожній номер — ""."""
    n = _int(number)
    return "" if not n else prefix() + str(n).zfill(digits())


def marker(number):
    """Позначка заявки в призначенні платежу й Примечании 1С: з префіксом — `PD000123`, без — `#123` (як було)."""
    n = _int(number)
    if not n:
        return ""
    return label(n) if prefix() else f"#{n}"


def markers(number):
    """Усі позначки, за якими обробка 1С шукає вже створену платіжку заявки (нові й колишні)."""
    n = _int(number)
    if not n:
        return []
    out = [marker(n), label(n), f"#{n}"]
    for p in all_prefixes():
        out += [p + str(n), p + str(n).zfill(digits())]
    return list(dict.fromkeys(x for x in out if x))


def canonical(n):
    """Номер у вигляді, як у базі (`000000123`)."""
    return f"{int(n):0{MAX_DIGITS}d}"


def _patterns():
    prefixes = "|".join(re.escape(p) for p in sorted(all_prefixes(), key=len, reverse=True))
    pfx = rf"(?:{prefixes})[\s-]?" if prefixes else None
    hash_part = rf"#\s?(?:{pfx})?" if pfx else r"#\s?"
    found = [re.compile(rf"{hash_part}(\d{{1,{MAX_DIGITS}}})(?!\d)", re.IGNORECASE)]
    if pfx:
        found.append(re.compile(rf"(?<![\w#]){pfx}(\d{{1,{MAX_DIGITS}}})(?!\d)", re.IGNORECASE))
    query = re.compile(rf"(?:{hash_part}|{pfx})(\d{{1,{MAX_DIGITS}}})" if pfx else rf"{hash_part}(\d{{1,{MAX_DIGITS}}})",
                       re.IGNORECASE)
    return found, query


def numbers_in(text):
    """Номери заявок у тексті: ["000000005", "000000012"] (за зростанням, без повторів)."""
    text = (text or "").translate(_LOOKALIKE)
    found, _ = _patterns()
    nums = {int(m.group(1)) for rx in found for m in rx.finditer(text)}
    return sorted(canonical(n) for n in nums if n)


def parse_query(word):
    """Слово пошуку — номер заявки з `#` чи префіксом (`#123`, `pd123`, `PD-000123`)? Номер як у базі або None."""
    m = _patterns()[1].fullmatch((word or "").strip().translate(_LOOKALIKE))
    return canonical(m.group(1)) if m and int(m.group(1)) else None


def matches(q, number):
    """Чи збігається запит пошуку з номером заявки: номер із `#` / префіксом — точно, інакше — підрядком у будь-якому
    вигляді номера (`000000123`, `123`, `PD000123`)."""
    q = (q or "").strip()
    if not q or not number:
        return False
    exact = parse_query(q)
    if exact:
        return exact == number
    hay = f"{number} {_int(number)} {label(number)}".lower()
    return q.lower().lstrip("#") in hay
