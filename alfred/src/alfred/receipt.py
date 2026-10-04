# ruff: noqa: E501
"""V2-07 (photo part) — a receipt read from a photo or a PDF becomes one draft entry.

The model's JSON is untrusted: the total must be a positive finite number within the cap, the
currency must be euro (anything else is refused, never converted), the category must be one we
know, and the date is only used when it is a real date that is not in the future. The draft is the
same Confirm/Adjust/Cancel one as a multi-entry message (``batch``); nothing is written before the tap.
"""

from __future__ import annotations

import math
import re
from datetime import date, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from alfred import clock
from alfred.batch import create_draft
from alfred.models import Member
from alfred.training import Reply
from alfred.validation import CATEGORIES, MAX_AMOUNT

_MARKUP = re.compile(r"[*_~`]")
MAX_DAYS_BACK = 90
_EURO = {"EUR", "€", "EURO", "EUROS"}


def _t(key: str, lang: str, **kw: object) -> str:
    from alfred.conversation import _t as t

    return t(key, lang, **kw)


def _text(value: object, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    out = " ".join(_MARKUP.sub("", value).split())[:limit].strip()
    return out or None


def _total(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        text = value.strip().replace("€", "").replace(" ", "")
        if "," in text and "." in text:
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", ".")
        try:
            value = float(text)
        except ValueError:
            return None
    if not isinstance(value, int | float) or not math.isfinite(value):
        return None
    amount = round(abs(float(value)), 2)
    return amount if 0 < amount <= MAX_AMOUNT else None


def _date(value: object) -> date | None:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value.strip()):
        return None
    try:
        day = date.fromisoformat(value.strip())
    except ValueError:
        return None
    today = clock.today_local()
    return day if 0 <= (today - day).days <= MAX_DAYS_BACK else None


def item_from_reading(reading: dict) -> tuple[dict | None, str | None]:
    """``(entry, problem)``: the entry to draft, or why there is none ("currency" | "total")."""
    currency = reading.get("currency")
    if isinstance(currency, str) and currency.strip() and currency.strip().upper() not in _EURO:
        return None, "currency"
    amount = _total(reading.get("total"))
    if amount is None:
        return None, "total"
    category = str(reading.get("category") or "").lower().strip()
    if category not in CATEGORIES or category == "inkomen":
        category = "overig"
    day = _date(reading.get("date"))
    return {
        "type": "expense",
        "amount": amount,
        "currency": "EUR",
        "merchant": _text(reading.get("merchant"), 60),
        "category": category,
        "description": None,
        "days_ago": (clock.today_local() - day).days if day else 0,
    }, None


async def receipt_reply(reading: dict, member: Member, lang: str, session: AsyncSession) -> Reply:
    item, problem = item_from_reading(reading)
    if item is None:
        return Reply(_t("receipt_currency" if problem == "currency" else "receipt_no_total", lang))
    when = ""
    if item["days_ago"]:
        day = clock.today_local() - timedelta(days=item["days_ago"])
        when = f" ({day:%d/%m})"
    draft = await create_draft(
        session, member, [item], lang, min_items=1, title=_t("receipt_title", lang) + when
    )
    if draft is None:
        return Reply(_t("receipt_no_total", lang))
    return Reply(draft.text, draft.buttons)


def _all(pt: str, nl: str, en: str, fr: str, de: str) -> dict[str, str]:
    return {"pt": pt, "nl": nl, "en": en, "fr": fr, "de": de}


STRINGS: dict[str, dict[str, str]] = {
    "receipt_title": _all(
        "Li este recibo:",
        "Dit heb ik op de bon gelezen:",
        "This is what I read on the receipt:",
        "Voici ce que j'ai lu sur le reçu :",
        "Das habe ich auf dem Beleg gelesen:",
    ),
    "receipt_currency": _all(
        'Esse recibo não está em euros e, por enquanto, só registro em euros. Mande o valor em euros por texto, por exemplo "45 mercado".',
        "Deze bon is niet in euro's en ik registreer voorlopig alleen euro's. Stuur het bedrag in euro's als tekst, bijvoorbeeld \"45 supermarkt\".",
        'That receipt isn\'t in euros and for now I only record euros. Send the amount in euros as text, for example "45 groceries".',
        "Ce reçu n'est pas en euros et pour l'instant je n'enregistre que des euros. Envoie le montant en euros par texte, par exemple \"45 courses\".",
        'Dieser Beleg ist nicht in Euro und ich erfasse vorerst nur Euro. Sende den Betrag in Euro als Text, zum Beispiel "45 Supermarkt".',
    ),
    "receipt_no_total": _all(
        'Não consegui ler o total desse recibo. Mande uma foto mais nítida ou escreva o valor, por exemplo "45 mercado".',
        'Ik kon het totaal op deze bon niet lezen. Stuur een scherpere foto of typ het bedrag, bijvoorbeeld "45 supermarkt".',
        'I couldn\'t read the total on that receipt. Send a sharper photo or type the amount, for example "45 groceries".',
        'Je n\'ai pas pu lire le total de ce reçu. Envoie une photo plus nette ou écris le montant, par exemple "45 courses".',
        'Ich konnte den Gesamtbetrag auf diesem Beleg nicht lesen. Sende ein schärferes Foto oder tippe den Betrag, zum Beispiel "45 Supermarkt".',
    ),
}
