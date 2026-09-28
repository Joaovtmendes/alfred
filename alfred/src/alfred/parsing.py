"""Pure text-parsing helpers for the conversation router (no DB, no I/O).

Kept apart from ``conversation.py`` so every rule here has a fast unit test.
The guiding rule for every pattern: **anchor it**. The router runs handlers in
sequence and a pattern that matches anywhere in a sentence steals messages that
belong to a later handler (an expense, a task, a note).
"""

from __future__ import annotations

import re

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
    r"|cheguei\s+(?:a\s+casa|de\s+volta)"
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
    r"|\s+(?:de|from|van|vom|du|do|da)\s+\d"
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


def match_trip_start(text: str) -> str | None:
    """Destination if ``text`` is a start-trip command, else None."""
    m = TRIP_START_RE.match(text.strip())
    if not m:
        return None
    raw = next((g for g in m.groups() if g), "")
    return clean_destination(raw)


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
