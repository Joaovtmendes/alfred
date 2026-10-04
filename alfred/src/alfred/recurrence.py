"""Reminder cadence parsing ("toda segunda", "dias úteis", "todo dia 5") — V1-24 #1.

``parse_recurrence`` takes the free text of a reminder, finds the cadence phrase, and returns
the text without it plus the weekday bitmask (Mon=1 … Sun=64) and an optional day of the month.
Monthly reminders keep ``days_mask=127`` and carry the day in ``payload["day_of_month"]``, which
``scripts/daily_cron.py`` honours.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

ALL_DAYS = 127
WEEKDAYS = 31
WEEKEND = 96

_DAY_NAMES: dict[int, tuple[str, ...]] = {
    1: ("segunda-feira", "segunda", "monday", "maandag", "lundi", "montag", "mon"),
    2: ("terca-feira", "terca", "tuesday", "dinsdag", "mardi", "dienstag", "tue"),
    4: ("quarta-feira", "quarta", "wednesday", "woensdag", "mercredi", "mittwoch", "wed"),
    8: ("quinta-feira", "quinta", "thursday", "donderdag", "jeudi", "donnerstag", "thu"),
    16: ("sexta-feira", "sexta", "friday", "vrijdag", "vendredi", "freitag", "fri"),
    32: ("sabado", "saturday", "zaterdag", "samedi", "samstag", "sat"),
    64: ("domingo", "sunday", "zondag", "dimanche", "sonntag", "sun"),
}
_NAME_TO_BIT = {n: bit for bit, names in _DAY_NAMES.items() for n in names}
_DAY_ALT = "|".join(sorted(_NAME_TO_BIT, key=len, reverse=True))
_JOIN = r"(?:\s*,\s*|\s+(?:e|and|en|et|und)\s+)"

_MONTHLY = re.compile(
    r"\b(?:todo\s+dia\s+(\d{1,2})(?!\d)(?:\s+do\s+mes)?"
    r"|(?:on\s+the\s+)?(\d{1,2})(?:st|nd|rd|th)?\s+of\s+(?:every|each)\s+month"
    r"|elke\s+maand\s+op\s+de\s+(\d{1,2})e?"
    r"|le\s+(\d{1,2})\s+de\s+chaque\s+mois"
    r"|jeden\s+monat\s+am\s+(\d{1,2})\.?)(?!\d)",
)
_WEEKDAYS_RE = re.compile(
    r"\b(?:(?:todos\s+os|nos|em|de)\s+)?dias\s+(?:uteis|de\s+semana)\b"
    r"|\b(?:on\s+|every\s+)?weekdays?\b|\b(?:elke\s+)?werkdagen\b"
    r"|\b(?:les\s+)?jours\s+ouvrables\b|\b(?:jeden\s+)?werktag(?:e)?\b",
)
_WEEKEND_RE = re.compile(
    r"\b(?:(?:aos|nos|no|em)\s+)?(?:fins?|fim)\s+de\s+semana\b"
    r"|\b(?:on\s+(?:the\s+)?)?weekends?\b|\b(?:in\s+het\s+)?weekend\b"
    r"|\b(?:le\s+)?week-end\b|\b(?:am\s+)?wochenende\b",
)
_DAILY_RE = re.compile(
    r"\b(?:todos\s+os\s+dias|todo\s+dia|every\s+day|elke\s+dag|tous\s+les\s+jours"
    r"|jeden\s+tag|diariamente|daily)\b",
)
_DAYS_RE = re.compile(
    rf"\b(?:(?:toda\s+a|toda|todo|todas\s+as|todos\s+os|aos|as|nas|nos|em|every|each|elke|on|chaque|le|"
    rf"jeden|am)\s+)?(?:{_DAY_ALT})s?(?:{_JOIN}(?:{_DAY_ALT})s?)*\b",
)


@dataclass(frozen=True)
class Recurrence:
    text: str
    mask: int
    day_of_month: int | None


def _plain(text: str) -> str:
    nfd = unicodedata.normalize("NFD", unicodedata.normalize("NFC", text).lower())
    return unicodedata.normalize("NFC", "".join(c for c in nfd if unicodedata.category(c) != "Mn"))


def _cut(text: str, span: tuple[int, int]) -> str:
    out = f"{text[: span[0]]} {text[span[1] :]}"
    return re.sub(r"\s+", " ", out).strip(" ,;.")


def parse_recurrence(text: str) -> Recurrence:
    text = unicodedata.normalize("NFC", text.strip())
    plain = _plain(text)
    if len(plain) != len(text):  # exotic decomposition: don't risk cutting the wrong span
        return Recurrence(text, ALL_DAYS, None)
    m = _MONTHLY.search(plain)
    if m:
        day = int(next(g for g in m.groups() if g))
        if 1 <= day <= 31:
            return Recurrence(_cut(text, m.span()), ALL_DAYS, day)
    for rx, mask in ((_WEEKDAYS_RE, WEEKDAYS), (_WEEKEND_RE, WEEKEND), (_DAILY_RE, ALL_DAYS)):
        m = rx.search(plain)
        if m:
            return Recurrence(_cut(text, m.span()), mask, None)
    mask = 0
    cleaned = text
    for m in reversed(list(_DAYS_RE.finditer(plain))):
        for name in re.findall(rf"\b(?:{_DAY_ALT})", plain[m.start() : m.end()]):
            mask |= _NAME_TO_BIT[name]
        cleaned = _cut(cleaned, m.span())
    return Recurrence(cleaned, mask or ALL_DAYS, None) if mask else Recurrence(text, ALL_DAYS, None)


_DAY_FULL = {
    "pt": ("segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"),
    "nl": ("maandag", "dinsdag", "woensdag", "donderdag", "vrijdag", "zaterdag", "zondag"),
    "en": ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"),
    "fr": ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"),
    "de": ("Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"),
}
_DAY_SHORT = {
    "pt": ("seg", "ter", "qua", "qui", "sex", "sáb", "dom"),
    "nl": ("ma", "di", "wo", "do", "vr", "za", "zo"),
    "en": ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"),
    "fr": ("lun", "mar", "mer", "jeu", "ven", "sam", "dim"),
    "de": ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"),
}
_FIXED = {
    "pt": (
        "todo dia", "de segunda a sexta", "aos fins de semana", "todo dia {d} do mês",
        "toda semana ({days})", "toda {day}", "todo {day}",
    ),
    "nl": (
        "elke dag", "van maandag tot vrijdag", "in het weekend", "elke maand op de {d}e",
        "elke week ({days})", "elke {day}", "elke {day}",
    ),
    "en": (
        "every day", "every weekday", "every weekend", "on the {d}th of every month",
        "every week ({days})", "every {day}", "every {day}",
    ),
    "fr": (
        "chaque jour", "du lundi au vendredi", "le week-end", "le {d} de chaque mois",
        "chaque semaine ({days})", "chaque {day}", "chaque {day}",
    ),
    "de": (
        "jeden Tag", "von Montag bis Freitag", "am Wochenende", "jeden Monat am {d}.",
        "jede Woche ({days})", "jeden {day}", "jeden {day}",
    ),
}  # fmt: skip


def cadence_label(mask: int, day_of_month: int | None, lang: str) -> str:
    """Human phrase for the confirmation ("todo dia", "toda segunda", "todo dia 5 do mês")."""
    lang = lang if lang in _FIXED else "en"
    daily, weekdays, weekend, monthly, multi, one, one_m = _FIXED[lang]
    if day_of_month:
        return monthly.format(d=day_of_month)
    if mask == ALL_DAYS or mask == 0:
        return daily
    if mask == WEEKDAYS:
        return weekdays
    if mask == WEEKEND:
        return weekend
    bits = [i for i in range(7) if mask & (1 << i)]
    if len(bits) == 1:
        i = bits[0]
        return (one_m if i >= 5 and lang == "pt" else one).format(day=_DAY_FULL[lang][i])
    return multi.format(days=", ".join(_DAY_SHORT[lang][i] for i in bits))
