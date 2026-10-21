"""Pure text-parsing helpers for the conversation router (no DB, no I/O).

Kept apart from ``conversation.py`` so every rule here has a fast unit test.
The guiding rule for every pattern: **anchor it**. The router runs handlers in
sequence and a pattern that matches anywhere in a sentence steals messages that
belong to a later handler (an expense, a task, a note).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date, timedelta

# ── Text normalisation ────────────────────────────────────────────────────────


def _strip_char(c: str) -> str:
    """One char → one char: drop the accent only when exactly one base char remains.

    Lone combining marks (U+FE0F emoji variation selector!), Hangul and other
    characters whose NFD form is not "base + marks" are kept as they are, so the
    output has the same length as the input.
    """
    stripped = "".join(
        x for x in unicodedata.normalize("NFD", c) if unicodedata.category(x) != "Mn"
    )
    return stripped if len(stripped) == 1 else c


def strip_accents(text: str) -> str:
    """'está água' → 'esta agua'. Always ``len(out) == len(text)``, so match positions
    found on the plain text can be sliced out of the original."""
    return "".join(map(_strip_char, text))


def like_escape(text: str) -> str:
    """Escape %, _ and \\ so user text is matched literally by ILIKE (escape='\\')."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# ── Money ─────────────────────────────────────────────────────────────────────

# "12,50", "€ 12", "45€", "20 euro", "$5" — a message with an amount is almost
# always an expense, so command handlers with loose wording must step aside.
MONEY_RE = re.compile(
    r"(?:[€$£]\s*\d|\d\s*(?:[€$£]|eur\b|euros?\b|dollars?\b|usd\b|gbp\b))",
    re.IGNORECASE,
)
HAS_DIGIT_RE = re.compile(r"\d")


def has_money(text: str) -> bool:
    return bool(MONEY_RE.search(text))


# ── Trips (M14) ───────────────────────────────────────────────────────────────

# START — must be the whole message. Destination = everything after the preposition
# (cleaned by ``clean_destination``).
TRIP_START_RE = re.compile(
    r"^(?:"
    r"(?:em\s+)?viagem\s+(?:a|para|em)\s+(?P<pt>.+)"
    r"|(?:criar|nova|novo|iniciar|come[cç]ar|abrir)\s+viagem(?:\s+(?:a|para|em))?\s+(?P<pt2>.+)"
    r"|(?:vou|estou)\s+(?:de\s+)?viagem\s+(?:para|a)\s+(?P<pt3>.+)"
    r"|trip\s*:\s*(?P<en>.+)"
    r"|(?:start\s+|new\s+)?trip\s+to\s+(?P<en2>.+)"
    r"|(?:op\s+reis|nieuwe\s+reis)\s+naar\s+(?P<nl>.+)"
    r"|voyage\s+(?:à|a|en|au|aux)\s+(?P<fr>.+)"
    r"|reise\s+nach\s+(?P<de>.+)"
    r")\s*$",
    re.IGNORECASE | re.DOTALL,
)

# END — full-message only. Closing a trip is destructive: "20 euro terug gekregen"
# or "voltei a gastar 20 no jumbo" must never end (or complain about) a trip.
TRIP_END_RE = re.compile(
    r"^(?:"
    r"voltei(?:\s+(?:a\s+casa|de\s+viagem))?"
    r"|cheguei\s+(?:a\s+casa|em\s+casa|de\s+volta|de\s+viagem)|estou\s+de\s+volta|voltei\s+para\s+casa"
    r"|viagem\s+terminada|fim\s+da\s+viagem|terminar\s+(?:a\s+)?viagem"
    r"|trip\s+(?:end(?:ed)?|done|finished?|over)|end\s+(?:the\s+)?trip"
    r"|back\s+(?:home|in\s+\w+)|i(?:'m|\s+am)\s+back(?:\s+home)?"
    r"|(?:ik\s+ben\s+)?terug(?:\s+thuis)?|reis\s+(?:afgelopen|voorbij|afgerond)"
    r"|(?:je\s+suis\s+)?rentr[eé]e?|de\s+retour|fin\s+du\s+voyage"
    r"|(?:ich\s+bin\s+)?(?:wieder\s+)?zuhause|zur[uü]ck|reise\s+beendet"
    r")\s*[.!]*$",
    re.IGNORECASE,
)

# QUERY — a trip-summary phrase, optionally with up to 3 filler words before it
# and a destination after. (Bare "reis" is NOT a trigger: "ns reis 12,50" is an expense.)
_TRIP_QUERY_PHRASES = (
    r"quanto\s+gastei\s+(?:n[ao]\s+)?viagem|resumo\s+(?:da\s+)?viagem"
    r"|(?:despesas|gastos)\s+(?:da\s+)?viagem"
    r"|trip\s+(?:summary|expenses?|total|spending|costs?)"
    r"|how\s+much\s+(?:did\s+i\s+spend|have\s+i\s+spent)\s+(?:on|in|during)\s+(?:the\s+|my\s+)?trip"
    r"|reiskosten|reis\s+overzicht"
    r"|hoeveel\s+(?:heb\s+ik|ik)\s+uitgegeven\s+(?:op|tijdens)\s+(?:de\s+)?reis"
    r"|d[eé]penses?\s+(?:du\s+)?voyage|r[eé]sum[eé]\s+(?:du\s+)?voyage"
    r"|reisekosten"
)
TRIP_QUERY_RE = re.compile(
    rf"^(?:\w+\s+){{0,3}}(?:{_TRIP_QUERY_PHRASES})(?:\s+\w+){{0,3}}\s*[?.!]*$",
    re.IGNORECASE,
)

# LIST — whole message.
TRIP_LIST_RE = re.compile(
    r"^(?:"
    r"(?:as\s+minhas\s+|minhas\s+|lista\s+(?:de\s+)?|ver\s+(?:as\s+)?(?:minhas\s+)?)?"
    r"viagens(?:\s+anteriores)?"
    r"|(?:my\s+|list\s+(?:my\s+)?|show\s+(?:my\s+)?)?trips|trip\s+history"
    r"|mijn\s+reizen?|mes\s+voyages?|meine\s+reisen?"
    r")\s*[?.!]*$",
    re.IGNORECASE,
)

# Cut a destination at the first digit / currency / comma / "de <digit>" / "com …".
_DEST_CUT_RE = re.compile(
    r"[\d€$£,;:()]"
    r"|\s+(?:de|from|van|vom|du|do|da|dia|day|op|le|am)\s+\d"
    r"|\s+(?:com|with|met|mit|avec)\s+",
    re.IGNORECASE,
)


def clean_destination(raw: str) -> str | None:
    """'portugal €500 de 1 a 7 de outubro' → 'Portugal'; None when nothing sensible is left."""
    cut = _DEST_CUT_RE.search(raw)
    dest = (raw[: cut.start()] if cut else raw).strip(" .!?-–—\n")
    if not dest or len(dest) > 60 or len(dest.split()) > 4:
        return None
    return dest.title()


# Month names (accent-free, all five languages) → 1..12. Used for "treinos de agosto"
# and to recognise the date part of "criar viagem Portugal €500 de 1 a 7 de outubro".
MONTH_WORDS: dict[str, int] = {}
for _num, _names in enumerate(
    (
        ("janeiro", "januari", "january", "janvier", "januar", "jan"),
        ("fevereiro", "februari", "february", "fevrier", "februar", "fev", "feb"),
        ("marco", "maart", "march", "mars", "marz", "mar", "mrt"),
        ("abril", "april", "avril", "abr", "apr", "avr"),
        ("maio", "mei", "may", "mai"),
        ("junho", "juni", "june", "juin", "jun"),
        ("julho", "juli", "july", "juillet", "jul"),
        ("agosto", "augustus", "august", "aout", "aug", "ago"),
        ("setembro", "september", "septembre", "set", "sep"),
        ("outubro", "oktober", "october", "octobre", "out", "oct", "okt"),
        ("novembro", "november", "novembre", "nov"),
        ("dezembro", "december", "decembre", "dez", "dec"),
    ),
    start=1,
):
    for _n in _names:
        MONTH_WORDS[_n] = _num

_MONTH_ALT = "|".join(sorted(MONTH_WORDS, key=len, reverse=True))
_TRIP_MONEY_RE = re.compile(
    r"(?:[€$£]\s*\d[\d.,]*|\d[\d.,]*\s*(?:[€$£]|eur\b|euros?\b|dollars?\b))", re.IGNORECASE
)
_TRIP_DATE_RE = re.compile(
    r"\d{1,2}[/.-]\d{1,2}(?:[/.-]\d{2,4})?"
    rf"|\d{{1,2}}\s*(?:(?:a|ate|to|tot|au|bis|-)\s*\d{{1,2}}\s*)?(?:de\s+|of\s+)?(?:{_MONTH_ALT})\b"
    r"(?:\s*(?:de\s+|of\s+)?\d{4})?",
    re.IGNORECASE,
)
_TRIP_FILLER = frozenset(
    "de do da a ate para com with met avec mit budget orcamento from to tot van vom bis du au "
    "dia dias days dagen jours tage noites nights e and en et und of".split()
)


_TRIP_START_DATE_RE = re.compile(
    rf"(\d{{1,2}})\s*(?:(?:a|ate|to|tot|au|bis|-)\s*\d{{1,2}}\s*)?(?:de\s+|of\s+)?({_MONTH_ALT})\b"
    r"(?:\s*(?:de\s+|of\s+)?(\d{4}))?",
    re.IGNORECASE,
)


_TRIP_DAY_ONLY_RE = re.compile(r"\b(?:dia|day|op|le|am)\s+(\d{1,2})\b(?!\s*[/.:h-])")
_TRIP_TIME_RE = re.compile(r"\b(?:as|a|at|om|um)?\s*\d{1,2}\s*(?:h\d{0,2}|:\d{2})\b")


def parse_trip_start_date(text: str, today):
    """Start date from "de 1 a 7 de outubro" / "1-7 oct 2027", or None.

    Without a year the next occurrence is meant (a trip is never planned in the past).
    """
    from datetime import date

    m = _TRIP_START_DATE_RE.search(strip_accents(text.lower()))
    if not m:
        d_only = _TRIP_DAY_ONLY_RE.search(strip_accents(text.lower()))
        if not d_only:
            return None
        try:  # "criar viagem Lisboa dia 25": the next 25th
            d = date(today.year, today.month, int(d_only.group(1)))
            if d < today:
                d = date(today.year + (today.month == 12), today.month % 12 + 1, d.day)
            return d
        except ValueError:
            return None
    month = MONTH_WORDS.get(m.group(2))
    if not month:
        return None
    try:
        if m.group(3):
            return date(int(m.group(3)), month, int(m.group(1)))
        d = date(today.year, month, int(m.group(1)))
        if (today - d).days > 30:
            d = date(today.year + 1, month, int(m.group(1)))
        return d
    except ValueError:
        return None


def _only_trip_details(rest: str) -> bool:
    """True if what follows the destination is just a budget / dates / filler words.

    "viagem a lisboa 30€ gasolina" leaves "gasolina" → it is an expense, not a trip.
    """
    r = strip_accents(rest.lower())
    r = _TRIP_MONEY_RE.sub(" ", r)
    r = _TRIP_DATE_RE.sub(" ", r)
    r = _TRIP_TIME_RE.sub(" ", _TRIP_DAY_ONLY_RE.sub(" ", r))  # "dia 25 às 10h"
    words = re.sub(r"[\s,;:()\-–/.]+", " ", r).split()
    return all(w in _TRIP_FILLER for w in words)


def match_trip_start(text: str) -> str | None:
    """Destination if ``text`` is a start-trip command, else None."""
    m = TRIP_START_RE.match(text.strip())
    if not m:
        return None
    raw = next((g for g in m.groups() if g), "")
    dest = clean_destination(raw)
    if dest is None:
        return None
    cut = _DEST_CUT_RE.search(raw)
    rest = raw[cut.start() :] if cut else ""
    if m.group("pt2"):  # A-48: after "criar viagem Roma" a bare number (800) is the budget
        rest = _TRIP_BARE_BUDGET_RE.sub(" ", rest)
    # Digits after the destination must be a budget or dates. "viagem em uber 15,00" or
    # "viagem a lisboa 30€ gasolina" are expenses, not trips. Digit-free tails
    # ("… com a Maria") are harmless.
    if HAS_DIGIT_RE.search(rest) and not _only_trip_details(rest):
        return None
    return dest


_TRIP_BARE_BUDGET_RE = re.compile(
    r"(?:\s|^)(?:orcamento\s+|orçamento\s+)?\d{2,7}(?:[.,]\d{1,2})?\s*$"
)


def trip_bare_budget(text: str) -> float | None:
    """The bare trailing number of "criar viagem Roma 800" (only for that explicit form)."""
    m = TRIP_START_RE.match(text.strip())
    if not m or not m.group("pt2"):
        return None
    b = _TRIP_BARE_BUDGET_RE.search(m.group("pt2"))
    return to_amount(b.group(0).split()[-1]) if b else None


# ── Category corrections (M12) ────────────────────────────────────────────────

# "Jumbo é supermarkt" · "Albert Heijn is not restaurant, is supermarkt" ·
# "isso não é wonen, é transport". Whole message, no digits/currency, short.
CORRECTION_RE = re.compile(
    r"^(?P<merchant>[^,\n]{1,40}?)\s+"
    r"(?:(?:é|e|is|ist|est)\s+)?"
    r"(?:(?:não|nao|niet|not|nicht|pas|kein|keine)(?:\s+(?:é|e|is|ist|est))?\s+[^,\s]+"
    r"\s*,\s*(?:mas\s+|maar\s+|but\s+|mais\s+|sondern\s+)?)?"
    r"(?:é|e|is|ist|est)\s+"
    r"(?:(?:um|uma|een|a|an|de|het|le|la|ein|eine)\s+)?(?P<cat>[^\s,]+)$",
    re.IGNORECASE,
)

# "that / it / this expense" — refers to the most recent expense.
CORRECTION_PRONOUNS = frozenset(
    {
        "isso",
        "isto",
        "essa",
        "esta",
        "essa despesa",
        "a última",
        "a ultima",
        "that",
        "it",
        "this",
        "the last one",
        "dat",
        "dit",
        "die",
        "het",
        "ça",
        "cela",
        "ce",
        "das",
        "es",
        "dies",
    }  # fmt: skip
)


def parse_category_correction(text: str) -> tuple[str, str] | None:
    """(merchant_or_pronoun, raw_category) for a short correction message, else None.

    The caller must still validate the category and that the merchant is known.
    """
    t = text.strip().rstrip(".!")
    if len(t) > 80 or HAS_DIGIT_RE.search(t) or any(c in t for c in "€$£:;\n"):
        return None
    m = CORRECTION_RE.match(t)
    if not m:
        return None
    merchant = m.group("merchant").strip()
    if not merchant or len(merchant.split()) > 4:
        return None
    return merchant, m.group("cat").strip()


# ── Trip budget ("criar viagem Portugal €500") ────────────────────────────────

_BUDGET_RE = re.compile(
    r"(?:[€$£]\s*(?P<a>\d[\d.,]*)|(?P<b>\d[\d.,]*)\s*(?:[€$£]|eur\b|euros?\b|dollars?\b))",
    re.IGNORECASE,
)


def to_amount(raw: str) -> float | None:
    """'1.500' → 1500 · '1.500,50' → 1500.5 · '12,5' → 12.5 · '500' → 500."""
    raw = raw.rstrip(".,")
    if not raw:
        return None
    last = max(raw.rfind("."), raw.rfind(","))
    if last == -1:
        digits = raw
    else:
        tail = raw[last + 1 :]
        other_sep = "," if raw[last] == "." else "."
        head = raw[:last]
        if len(tail) == 3 and other_sep not in raw and head not in ("0", "") and head != "00":
            digits = raw.replace(".", "").replace(",", "")  # thousands separator
        else:
            digits = raw[:last].replace(".", "").replace(",", "") + "." + tail
    try:
        value = float(digits)
    except ValueError:
        return None
    return value if 0 < value < 1_000_000 else None


def parse_budget(text: str) -> float | None:
    """First money amount in ``text`` (needs a currency marker), else None."""
    m = _BUDGET_RE.search(text)
    if not m:
        return None
    return to_amount(m.group("a") or m.group("b"))


# ── Expense-query windows ("ontem", "de setembro", "este ano") ────────────────
# The LLM classifier only knows today/week/month, so "ontem" used to be answered as
# "hoje" and "resumo de setembro" as last month. Dates are cheap to parse exactly, so
# they are resolved here (accent-free, lower-case input) and override the LLM's guess.


@dataclass(frozen=True)
class DateWindow:
    """[start, end) in local dates; ``end=None`` means "up to now"."""

    # today | yesterday | today_yesterday | days | current_week | last_week | current_month |
    # last_month | month | year
    kind: str
    start: date
    end: date | None = None
    days: int = 0  # kind "days": the N of "últimos N dias"


def _w(*phrases: str) -> re.Pattern[str]:
    return re.compile(r"\b(?:" + "|".join(phrases) + r")\b")


_YESTERDAY_RE = _w("ontem", "yesterday", "gisteren", "hier", "gestern")
_TODAY_RE = _w("hoje", "today", "vandaag", "aujourd hui", "aujourdhui", "heute")
_LAST_WEEK_RE = _w(
    "semana passada",
    "ultima semana",
    "last week",
    "vorige week",
    "semaine derniere",
    "letzte woche",
    "letzten woche",
)
_THIS_WEEK_RE = _w(
    "esta semana",
    "desta semana",
    "nesta semana",
    "this week",
    "deze week",
    "cette semaine",
    "diese woche",
    "diesen woche",
)
_LAST_MONTH_RE = _w(
    "mes passado",
    "last month",
    "vorige maand",
    "mois dernier",
    "letzten monat",
    "letzter monat",
)
_THIS_MONTH_RE = _w(
    "este mes",
    "neste mes",
    "deste mes",
    "this month",
    "deze maand",
    "ce mois",
    "diesen monat",
    "dieser monat",
)
_LAST_YEAR_RE = _w(
    "ano passado", "last year", "vorig jaar", "annee derniere", "letztes jahr", "letzten jahr"
)
_THIS_YEAR_RE = _w(
    "este ano",
    "neste ano",
    "deste ano",
    "ano todo",
    "this year",
    "year to date",
    "dit jaar",
    "cette annee",
    "dieses jahr",
    "diesem jahr",
)
_YEAR_NUM_RE = re.compile(r"\b(20\d{2})\b")
# "últimos 7 dias" / "last 30 days" / "afgelopen 14 dagen" / "derniers 7 jours" / "letzten 7 tage"
_LAST_DAYS_RE = re.compile(
    r"\b(?:ultim[oa]s|last|past|afgelopen|laatste|derniers|letzten)\s+(\d{1,3})\s+"
    r"(?:dias|days|dagen|jours|tage[n]?)\b"
)
# "hoje e ontem" / "ontem e hoje" / "today and yesterday"
_TODAY_AND_YESTERDAY_RE = re.compile(
    r"\b(?:hoje e ontem|ontem e hoje|today and yesterday|yesterday and today|vandaag en gisteren|"
    r"gisteren en vandaag|hier et aujourd hui|aujourd hui et hier|heute und gestern|"
    r"gestern und heute)\b"
)


_PERSON_LEADS = frozenset({"o", "com", "pro", "pelo", "ao", "with", "met", "avec", "mit"})


def parse_period(text_plain: str, today: date) -> DateWindow | None:
    """Resolve the time expression in an (accent-free, lower-case) query, or None.

    Most specific phrases win ("semana passada" before "hoje"); a named month means its
    most recent occurrence unless a year is given ("agosto 2025").
    """
    t = text_plain
    week0 = today - timedelta(days=today.weekday())
    if _TODAY_AND_YESTERDAY_RE.search(t):
        return DateWindow("today_yesterday", today - timedelta(days=1), today + timedelta(days=1))
    m_days = _LAST_DAYS_RE.search(t)
    if m_days and 1 <= int(m_days.group(1)) <= 366:
        n = int(m_days.group(1))
        return DateWindow("days", today - timedelta(days=n - 1), today + timedelta(days=1), days=n)
    if _YESTERDAY_RE.search(t):
        y = today - timedelta(days=1)
        return DateWindow("yesterday", y, today)
    if _LAST_WEEK_RE.search(t):
        return DateWindow("last_week", week0 - timedelta(days=7), week0)
    if _THIS_WEEK_RE.search(t):
        return DateWindow("current_week", week0, None)
    if _LAST_YEAR_RE.search(t):
        return DateWindow("year", date(today.year - 1, 1, 1), date(today.year, 1, 1))
    if _THIS_YEAR_RE.search(t):
        return DateWindow("year", date(today.year, 1, 1), None)
    if _LAST_MONTH_RE.search(t):
        first = (today.replace(day=1) - timedelta(days=1)).replace(day=1)
        return DateWindow("last_month", first, today.replace(day=1))
    if _THIS_MONTH_RE.search(t):
        return DateWindow("current_month", today.replace(day=1), None)
    prev = ""
    for word in re.findall(r"[a-z]+", t):
        month = MONTH_WORDS.get(word)
        after_person = prev in _PERSON_LEADS  # "com o Marco" is a person, not March
        prev = word
        if month and len(word) > 3 and not after_person:  # "mar"/"set" are also ordinary words
            ym = _YEAR_NUM_RE.search(t)
            year = (
                int(ym.group(1)) if ym else (today.year if month <= today.month else today.year - 1)
            )
            start = date(year, month, 1)
            nxt = date(year + (month == 12), month % 12 + 1, 1)
            is_now = start == today.replace(day=1)
            return DateWindow("month", start, None if is_now else nxt)
    if _TODAY_RE.search(t):
        return DateWindow("today", today, today + timedelta(days=1))
    ym = _YEAR_NUM_RE.search(t)  # "gastos em 2025": a bare year is that whole year
    if ym and 2000 <= int(ym.group(1)) <= today.year:
        y = int(ym.group(1))
        return DateWindow("year", date(y, 1, 1), None if y == today.year else date(y + 1, 1, 1))
    return None


_EXPENSE_WORDS = (
    r"(?:despesas?|gastos?|compras?|transacoes|transacao|expenses?|uitgaven?|"
    r"depenses?|achats?|ausgaben?|kaeufe)"
)
_LAST_WORDS = r"(?:ultim[ao]s?|last|latest|recent[es]*|laatste|derniere?s?|letzte[n]?)"
_INCOME_WORDS = r"(?:receitas?|entradas?|rendimentos?|income|incomes|inkomsten|revenus|einnahmen)"
_MY = r"(?:as|os|my|mijn|mes|meine|minhas|meus|mostra\s+as|mostra\s+os)"
LAST_N_RE = re.compile(
    rf"^(?:(?:mostra|mostrar|show|toon|montre|zeig)\s+)?(?:{_MY}\s+)?"
    rf"{_LAST_WORDS}\s*(\d{{1,2}})?\s*{_EXPENSE_WORDS}\s*\??$"
)
LAST_N_INCOME_RE = re.compile(
    rf"^(?:(?:mostra|mostrar|show|toon|montre|zeig)\s+)?(?:{_MY}\s+)?"
    rf"{_LAST_WORDS}\s*(\d{{1,2}})?\s*{_INCOME_WORDS}\s*\??$"
)
# "quanto recebi este mês?" / "minhas receitas de setembro": the income list, not the spending
INCOME_QUERY_RE = re.compile(
    r"^(?:quanto\s+(?:eu\s+)?(?:recebi|ganhei|entrou)|(?:minhas|as)\s+receitas|receitas\b|"
    r"how\s+much\s+(?:did\s+i\s+)?(?:receive|earn)|my\s+income|hoeveel\s+(?:heb\s+ik\s+)?"
    r"(?:ontvangen|verdiend)|mijn\s+inkomsten|combien\s+j\s*ai\s+(?:recu|gagne)|mes\s+revenus|"
    r"wie\s*viel\s+habe\s+ich\s+(?:bekommen|verdient)|meine\s+einnahmen)"
)
TOP_CATEGORIES_RE = re.compile(
    r"^(?:top|maiores?|biggest|largest|grootste|principales?|groessten?)\s+"
    r"(?:categorias?|categories|categorieen|kategorien)\b"
)
DELETE_LAST_EXPENSE_RE = re.compile(
    rf"^(?:apaga|apagar|elimina|eliminar|remove|delete|verwijder|supprime|supprimer|loesch|loeschen|"
    rf"desfazer|desfaz|desfaca|undo|ongedaan maken|ruckgangig|rueckgaengig)"
    rf"(?:\s+(?:a\s+|o\s+|the\s+|de\s+|die\s+|la\s+|le\s+|den\s+|das\s+)?{_LAST_WORDS}"
    rf"(?:\s+{_EXPENSE_WORDS})?|\s+(?:isso|isto|esse|essa|this|that|it|dit|dat|ca|cela|das))?\s*[.!]?$"
)  # "apaga isso" is the same as "apaga"


def parse_last_n(text_plain: str, *, income: bool = False) -> int | None:
    """ "ultimas 5 despesas" → 5 (default 5, capped at 20); None when it is not that command.

    ``income=True`` reads "últimas receitas" instead.
    """
    m = (LAST_N_INCOME_RE if income else LAST_N_RE).match(text_plain.strip())
    if not m:
        return None
    return max(1, min(int(m.group(1)), 20)) if m.group(1) else 5
