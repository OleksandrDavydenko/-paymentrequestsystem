"""Святкове оформлення («Налаштування → Дизайн»): які свята є, у які дні кожне показується і яке діє сьогодні.

Оформлення — лише CSS і SVG (static/holiday/, атрибут <html data-holiday>); тут — календар. Дні — за київським часом
(clock.today()); Великдень — за православною (юліанською) пасхалією, як його святкують в Україні.
"""
from datetime import date, timedelta

# Код -> (назва, привітання). Порядок — як у календарі (від січня)
HOLIDAYS = {
    "valentine": ("День закоханих", "З Днем закоханих!"),
    "women_day": ("8 Березня", "З 8 Березня!"),
    "easter": ("Великдень", "Христос воскрес!"),
    "vyshyvanka": ("День вишиванки", "З Днем вишиванки!"),
    "accountant": ("День бухгалтера", "З Днем бухгалтера!"),
    "independence": ("День Незалежності", "З Днем Незалежності!"),
    "halloween": ("Хелловін", "Щасливого Хелловіну!"),
    "new_year": ("Новий рік і Різдво", "З Новим роком і Різдвом!"),
}
MONTHS = ("січня", "лютого", "березня", "квітня", "травня", "червня", "липня", "серпня", "вересня", "жовтня",
          "листопада", "грудня")


def orthodox_easter(year):
    """Великдень за юліанською пасхалією (алгоритм Мееуса) у григоріанському календарі (+13 днів у 1900–2099)."""
    a, b, c = year % 4, year % 7, year % 19
    d = (19 * c + 15) % 30
    e = (2 * a + 4 * b - d + 34) % 7
    month, day = divmod(d + e + 114, 31)
    return date(year, month, day + 1) + timedelta(days=13)


def _third_thursday_of_may(year):
    first = date(year, 5, 1)
    return first + timedelta(days=(3 - first.weekday()) % 7 + 14)


def ranges(year):
    """Код -> (перший, останній день показу) у році year (новорічне — з грудня year по січень year + 1)."""
    easter, vyshyvanka = orthodox_easter(year), _third_thursday_of_may(year)
    return {
        "valentine": (date(year, 2, 10), date(year, 2, 14)),
        "women_day": (date(year, 3, 4), date(year, 3, 8)),
        "easter": (easter - timedelta(days=6), easter + timedelta(days=1)),
        "vyshyvanka": (vyshyvanka - timedelta(days=2), vyshyvanka),
        "accountant": (date(year, 7, 14), date(year, 7, 16)),
        "independence": (date(year, 8, 22), date(year, 8, 24)),
        "halloween": (date(year, 10, 27), date(year, 10, 31)),
        "new_year": (date(year, 12, 15), date(year + 1, 1, 14)),
    }


def _on(code, day):
    """Чи день day — у днях свята code (новорічне переходить через рік)."""
    for year in (day.year - 1, day.year):
        first, last = ranges(year)[code]
        if first <= day <= last:
            return True
    return False


def active(day, enabled, force="auto"):
    """Свято, яке показувати в день day: вибране вручну («Показати зараз») або ввімкнене, у дні якого day."""
    if force in HOLIDAYS:
        return force
    return next((code for code in HOLIDAYS if code in (enabled or []) and _on(code, day)), None)


def next_up(day, enabled):
    """(код, перший день) найближчого ввімкненого свята після day — для підказки в адмінці."""
    upcoming = [(first, code) for year in (day.year, day.year + 1) for code, (first, _) in ranges(year).items()
                if code in (enabled or []) and first > day]
    return (min(upcoming)[1], min(upcoming)[0]) if upcoming else None


def when(code, day):
    """Найближчі дні показу свята (поточні або наступні): «6–13 квітня 2026», «15 грудня 2026 – 14 січня 2027»."""
    for year in (day.year - 1, day.year, day.year + 1):
        first, last = ranges(year)[code]
        if last >= day:
            if first.year == last.year:
                return fmt_range(code, year) + f" {last.year}"
            return f"{fmt_day(first)} {first.year} – {fmt_day(last)} {last.year}"
    return ""


def fmt_day(d):
    return f"{d.day} {MONTHS[d.month - 1]}"


def fmt_range(code, year):
    """«12–19 квітня», «15 грудня – 14 січня» — дні показу свята в році year."""
    first, last = ranges(year)[code]
    if first.month == last.month:
        return f"{first.day}–{last.day} {MONTHS[last.month - 1]}"
    return f"{fmt_day(first)} – {fmt_day(last)}"
