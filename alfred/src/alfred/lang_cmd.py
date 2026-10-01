"""Change the language of the replies: "idioma inglês" · "change to English" · "spreek Nederlands".

Found in the production hard test (01/10): "Change to English" was logged as a meditation habit
because there was no command at all — the language was fixed at the first message.

Rule-based and deliberately strict: the whole message must be a (short) language request, so
an expense like "aula de inglês 40" is never mistaken for one.
"""

from __future__ import annotations

import re
import unicodedata

__all__ = ["STRINGS", "parse_language_command"]

_LANG_WORDS = {
    "pt": {"portugues", "portuguese", "portugees", "portugais", "portugiesisch", "pt"},
    "nl": {
        "holandes",
        "neerlandes",
        "nederlands",
        "dutch",
        "hollands",
        "neerlandais",
        "niederlandisch",
        "nl",
    },
    "en": {"ingles", "english", "engels", "anglais", "englisch", "en"},
    "fr": {"frances", "francais", "french", "frans", "franzosisch", "fr"},
    "de": {"alemao", "deutsch", "german", "duits", "allemand", "de"},
}
_WORD_TO_LANG = {w: lg for lg, ws in _LANG_WORDS.items() for w in ws}
# Short codes ("en", "de") are only accepted after an explicit "language" word, not alone.
_CODES = {"pt", "nl", "en", "fr", "de"}

_LEAD = {
    "mudar",
    "muda",
    "mude",
    "trocar",
    "troca",
    "troque",
    "change",
    "switch",
    "verander",
    "wissel",
    "changer",
    "wechsle",
    "wechseln",
    "aendern",
    "speak",
    "fala",
    "falar",
    "fale",
    "spreek",
    "praat",
    "parle",
    "parler",
    "sprich",
    "sprechen",
    "quero",
    "want",
    "wil",
    "veux",
    "will",
    "idioma",
    "language",
    "taal",
    "langue",
    "sprache",
    "lingua",
    "set",
    "use",
    "usar",
}
_FILLER = {
    "para",
    "o",
    "a",
    "de",
    "do",
    "da",
    "em",
    "no",
    "na",
    "to",
    "the",
    "my",
    "in",
    "naar",
    "het",
    "mijn",
    "en",
    "au",
    "le",
    "la",
    "auf",
    "zu",
    "op",
    "pour",
    "i",
    "ik",
    "je",
    "ich",
    "me",
    "mim",
    "us",
    "you",
    "please",
    "por",
    "favor",
    "alsjeblieft",
    "bitte",
    "plait",
    "svp",
    "s",
    "il",
    "te",
    "mich",
    "idioma",
    "language",
    "taal",
    "langue",
    "sprache",
    "lingua",
    "nu",
    "now",
    "agora",
    "maintenant",
    "jetzt",
}
_POLITE = {"please", "favor", "alsjeblieft", "bitte", "plait", "svp"}
_PREP = {"em", "in", "auf", "au", "op", "no", "na"}
_LANG_NOUNS = {"idioma", "language", "taal", "langue", "sprache", "lingua"}


def _plain(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9 ]+", " ", text.replace("'", " "))


def parse_language_command(body: str) -> str | None:
    """Target language code when the whole message asks to switch language, else None."""
    tokens = _plain(body).split()
    while tokens and tokens[-1] in _POLITE:  # "english please", "inglês por favor"
        tokens.pop()
        if tokens and tokens[-1] in {"por", "s", "il", "te"}:
            tokens.pop()
    if not tokens or len(tokens) > 8:
        return None
    last = tokens[-1]
    target = _WORD_TO_LANG.get(last)
    if target is None:
        return None
    head = tokens[:-1]
    if not head:
        # a bare language name ("english", "nederlands") — two-letter codes alone are ambiguous
        return target if last not in _CODES else None
    if any(t not in _LEAD and t not in _FILLER for t in head):
        return None
    if not any(t in _LEAD for t in head):
        # "em inglês", "in het Nederlands", "auf Deutsch": a preposition alone is a clear request
        if not (head[0] in _PREP and all(t in _PREP | {"het", "o", "a"} for t in head)):
            return None
    if last in _CODES and not any(t in _LANG_NOUNS for t in head):
        return None  # "mudar en" is too vague
    return target


def _all(pt: str, nl: str, en: str, fr: str, de: str) -> dict[str, str]:
    return {"pt": pt, "nl": nl, "en": en, "fr": fr, "de": de}


STRINGS: dict[str, dict[str, str]] = {
    "lang_changed": _all(
        "Pronto, agora falo português com você.",
        "Klaar, ik praat nu Nederlands met je.",
        "Done, I'll speak English with you from now on.",
        "C'est fait, je te parle en français maintenant.",
        "Erledigt, ich spreche jetzt Deutsch mit dir.",
    ),
}
