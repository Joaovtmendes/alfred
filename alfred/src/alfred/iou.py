# ruff: noqa: E501
"""V2-15 — "quem me deve" / "eu devo": small debts between the member and people they know.

Rule-based, no LLM. A message is only claimed when it is clearly one of these:
"Pedro me deve 25" · "devo 40 pro Lucas" · "quem me deve" · "o que eu devo" · "Pedro pagou" (all or
part) · "paguei o Lucas" · "cobra o Pedro" (a ready-to-copy reminder text; nothing is sent and
nothing is integrated with Tikkie). "paguei o aluguel" is *not* ours unless someone called aluguel
is owed, so the fixed-bills handler still gets it.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from alfred.audit import audit
from alfred.models import Iou, Member
from alfred.parsing import strip_accents, to_amount
from alfred.validation import MAX_AMOUNT

OWED = "owed_to_me"
OWE = "i_owe"
MAX_OPEN = 50
MAX_PERSON = 40
MAX_NOTE = 80

_AMT = r"(?:€\s*)?(\d[\d.,]*)\s*(?:€|eur|euros?)?"
_NAME = r"([a-z][a-z' \-]{0,38}[a-z]|[a-z])"
_NOTE = r"(?:\s+(?:do|da|de|dos|das|pelo|pela|por|for|voor|pour|fur)\s+(.{1,80}?))?"
_NOT_NAMES = {
    "quem",
    "ninguem",
    "alguem",
    "wie",
    "who",
    "qui",
    "wer",
    "eu",
    "ele",
    "ela",
    "voce",
    "he",
    "she",
    "you",
}


def _c(pattern: str) -> re.Pattern[str]:
    return re.compile(r"^" + pattern + r"$")


# (regex, group order) — groups: person, amount, note. Order is declared per pattern.
_CREATE_OWED = [
    _c(rf"{_NAME}\s+me\s+deve\s+{_AMT}{_NOTE}"),
    _c(rf"{_NAME}\s+owes\s+me\s+{_AMT}{_NOTE}"),
    _c(rf"{_NAME}\s+is\s+mij\s+{_AMT}\s+schuldig{_NOTE}"),
    _c(rf"{_NAME}\s+me\s+doit\s+{_AMT}{_NOTE}"),
    _c(rf"{_NAME}\s+schuldet\s+mir\s+{_AMT}{_NOTE}"),
]
# person first, then amount (shape A) or amount first, then person (shape B)
_CREATE_OWE_A = [
    _c(rf"(?:eu\s+)?devo\s+(?:ao|a|para o|para a)\s+{_NAME}\s+{_AMT}{_NOTE}"),
    _c(rf"i\s+owe\s+{_NAME}\s+{_AMT}{_NOTE}"),
    _c(rf"ik\s+ben\s+{_NAME}\s+{_AMT}\s+schuldig{_NOTE}"),
    _c(rf"ich\s+schulde\s+{_NAME}\s+{_AMT}{_NOTE}"),
]
_CREATE_OWE_B = [
    _c(rf"(?:eu\s+)?devo\s+{_AMT}\s+(?:pro|pra|para o|para a|para|ao|a)\s+{_NAME}{_NOTE}"),
    _c(rf"je\s+dois\s+{_AMT}\s+(?:a|au|a la)\s+{_NAME}{_NOTE}"),
]
_SETTLE_OWED = [  # they paid me: person, optional amount
    _c(rf"{_NAME}\s+(?:me\s+)?pagou(?:\s+{_AMT})?"),
    _c(rf"{_NAME}\s+paid\s+me(?:\s+{_AMT})?"),
    _c(rf"{_NAME}\s+heeft\s+(?:mij\s+)?(?:{_AMT}\s+)?betaald"),
    _c(rf"{_NAME}\s+m'a\s+paye(?:\s+{_AMT})?"),
    _c(rf"{_NAME}\s+hat\s+(?:mir\s+)?(?:{_AMT}\s+)?bezahlt"),
]
_SETTLE_OWE_A = [  # I paid: optional amount first, then person
    _c(rf"paguei(?:\s+{_AMT})?\s+(?:pro|pra|para o|para a|para|ao|a|o)?\s*{_NAME}"),
]
_SETTLE_OWE_B = [  # person first, then optional amount
    _c(rf"i\s+paid\s+{_NAME}(?:\s+{_AMT})?"),
    _c(rf"ik\s+heb\s+{_NAME}\s+(?:{_AMT}\s+)?betaald"),
    _c(rf"j'ai\s+paye\s+{_NAME}(?:\s+{_AMT})?"),
    _c(rf"ich\s+habe\s+{_NAME}\s+(?:{_AMT}\s+)?bezahlt"),
]
_REMIND = [
    _c(rf"(?:cobra|cobrar|lembrete\s+(?:pro|pra|para o|para a|para))\s+(?:o\s+|a\s+)?{_NAME}"),
    _c(rf"remind\s+{_NAME}"),
    _c(rf"herinner\s+{_NAME}"),
    _c(rf"rappelle\s+{_NAME}"),
    _c(rf"erinnere\s+{_NAME}"),
]
_LIST_OWED = {
    "quem me deve", "quem me deve dinheiro", "who owes me", "wie is mij geld schuldig",
    "wie is mij iets schuldig", "wie moet mij betalen", "qui me doit", "qui me doit de l'argent",
    "wer schuldet mir", "wer schuldet mir geld",
}  # fmt: skip
_LIST_OWE = {
    "o que eu devo", "quanto eu devo", "quem eu devo", "what do i owe", "who do i owe",
    "wat ben ik schuldig", "wie ben ik iets schuldig", "que dois-je", "qui je dois",
    "was schulde ich", "wem schulde ich",
}  # fmt: skip


def _dec(value: float | Decimal | None) -> Decimal:
    return Decimal(str(value or 0)).quantize(Decimal("0.01"), ROUND_HALF_UP)


def _key(name: str) -> str:
    return " ".join(strip_accents(name.lower()).replace("'", "").split())


def remaining(item: Iou) -> Decimal:
    return _dec(item.amount) - _dec(item.settled_amount)


def _fmt(value: Decimal) -> str:
    from alfred.conversation import _fmt_eur

    return _fmt_eur(float(value))


def _amount(raw: str | None) -> float | None:
    if raw is None:
        return None
    value = to_amount(raw)
    return value if value is not None and value <= MAX_AMOUNT else None


def _slice(body: str, plain: str, m: re.Match[str], group: int) -> str:
    """Group text as the member wrote it (``body`` and ``body_plain`` have the same length)."""
    start, end = m.span(group)
    raw = body[start:end] if len(body) == len(plain) else m.group(group)
    return " ".join(raw.split())


def _display(body: str, plain: str, m: re.Match[str], group: int) -> str:
    return _slice(body, plain, m, group).title()[:MAX_PERSON]


def _first(patterns: list[re.Pattern[str]], plain: str) -> re.Match[str] | None:
    for p in patterns:
        if m := p.match(plain):
            return m
    return None


async def _open(
    session: AsyncSession, member_id: uuid.UUID, direction: str | None = None
) -> list[Iou]:
    stmt = select(Iou).where(Iou.member_id == member_id, Iou.settled_at.is_(None))
    if direction:
        stmt = stmt.where(Iou.direction == direction)
    return list((await session.execute(stmt.order_by(Iou.created_at, Iou.id))).scalars())


def _people(items: list[Iou], name_key: str) -> dict[str, list[Iou]]:
    """Open items of the people ``name_key`` can mean: the exact name, else people whose first name it is."""
    by_person: dict[str, list[Iou]] = {}
    for it in items:
        by_person.setdefault(_key(it.person), []).append(it)
    if name_key in by_person:
        return {name_key: by_person[name_key]}
    return {k: v for k, v in by_person.items() if k.split()[0] == name_key}


async def handle_iou_command(
    body: str, body_plain: str, member: Member, lang: str, session: AsyncSession
) -> str | None:
    from alfred.conversation import _t

    plain = " ".join(body_plain.split()).strip(" .!?")
    if plain in _LIST_OWED:
        return await _list(member, lang, session, OWED)
    if plain in _LIST_OWE:
        return await _list(member, lang, session, OWE)

    for patterns, direction, shape in (
        (_CREATE_OWED, OWED, "pa"),
        (_CREATE_OWE_A, OWE, "pa"),
        (_CREATE_OWE_B, OWE, "ap"),
    ):
        if m := _first(patterns, plain):
            name_g, amt_g = (1, 2) if shape == "pa" else (2, 1)
            if _key(m.group(name_g)) in _NOT_NAMES:
                return None
            amount = _amount(m.group(amt_g))
            if amount is None:
                return _t("iou_bad_amount", lang)
            note = _slice(body, plain, m, 3) if m.group(3) else None
            return await _create(
                _display(body, plain, m, name_g), amount, direction, note, member, lang, session
            )

    if m := _first(_REMIND, plain):
        return await _remind(m.group(1), member, lang, session)

    for patterns, direction, shape in (
        (_SETTLE_OWED, OWED, "pa"),
        (_SETTLE_OWE_A, OWE, "ap"),
        (_SETTLE_OWE_B, OWE, "pa"),
    ):
        if m := _first(patterns, plain):
            name_g, amt_g = (1, 2) if shape == "pa" else (2, 1)
            if _key(m.group(name_g)) in _NOT_NAMES:
                return None
            raw = m.group(amt_g)
            amount = _amount(raw)
            if raw is not None and amount is None:
                return _t("iou_bad_amount", lang)
            return await _settle(m.group(name_g), amount, direction, member, lang, session)
    return None


async def _create(
    person: str,
    amount: float,
    direction: str,
    note: str | None,
    member: Member,
    lang: str,
    session: AsyncSession,
) -> str:
    from alfred.conversation import _t

    if len(await _open(session, member.id)) >= MAX_OPEN:
        return _t("iou_limit", lang, n=MAX_OPEN)
    session.add(
        Iou(
            member_id=member.id,
            person=person,
            amount=amount,
            direction=direction,
            note=(note or None) and note[:MAX_NOTE],
        )
    )
    await session.flush()
    audit(session, "iou_created", member.id, direction=direction)
    total = sum(
        (
            remaining(i)
            for i in await _open(session, member.id, direction)
            if _key(i.person) == _key(person)
        ),
        Decimal(0),
    )
    key = "iou_added_owed" if direction == OWED else "iou_added_owe"
    return _t(key, lang, person=person, amount=_fmt(_dec(amount)), total=_fmt(total))


async def _list(member: Member, lang: str, session: AsyncSession, direction: str) -> str:
    from alfred.conversation import _t

    items = await _open(session, member.id, direction)
    if not items:
        return _t("iou_empty_owed" if direction == OWED else "iou_empty_owe", lang)
    lines = []
    for it in items:
        left = remaining(it)
        extra = _t("iou_of", lang, total=_fmt(_dec(it.amount))) if left != _dec(it.amount) else ""
        note = f" · {it.note}" if it.note else ""
        lines.append(f"• {it.person}: {_fmt(left)}{extra}{note}")
    total = sum((remaining(i) for i in items), Decimal(0))
    header = _t(
        "iou_header_owed" if direction == OWED else "iou_header_owe", lang, total=_fmt(total)
    )
    return header + "\n" + "\n".join(lines)


async def _settle(
    name: str,
    amount: float | None,
    direction: str,
    member: Member,
    lang: str,
    session: AsyncSession,
) -> str | None:
    from alfred.conversation import _t

    groups = _people(await _open(session, member.id, direction), _key(name))
    if not groups:
        return None  # not ours ("paguei o aluguel"): let the other handlers see it
    if len(groups) > 1:
        names = ", ".join(sorted({v[0].person for v in groups.values()}))
        return _t("iou_ambiguous_person", lang, names=names)
    items = next(iter(groups.values()))
    person = items[0].person
    total_left = sum((remaining(i) for i in items), Decimal(0))
    if amount is None:
        if len(items) > 1:
            amounts = ", ".join(_fmt(remaining(i)) for i in items)
            return _t("iou_ambiguous_amount", lang, person=person, amounts=amounts)
        pay = remaining(items[0])
    else:
        pay = _dec(amount)
        if pay > total_left:
            return _t("iou_over", lang, person=person, total=_fmt(total_left))
        exact = [i for i in items if remaining(i) == pay]
        if exact:
            items = [exact[0]]  # a payment equal to one item's balance settles that item
    left_to_pay = pay
    now = datetime.now(UTC)
    for it in items:
        if left_to_pay <= 0:
            break
        apply = min(left_to_pay, remaining(it))
        it.settled_amount = float(_dec(it.settled_amount) + apply)
        if remaining(it) <= 0:
            it.settled_at = now
        left_to_pay -= apply
    await session.flush()
    audit(session, "iou_settled", member.id, direction=direction)
    still = sum((remaining(i) for i in items if i.settled_at is None), Decimal(0))
    if still > 0:
        return _t(
            "iou_settled_partial", lang, person=person, paid=_fmt(pay), left=_fmt(total_left - pay)
        )
    return _t("iou_settled_full", lang, person=person, paid=_fmt(pay))


async def _remind(name: str, member: Member, lang: str, session: AsyncSession) -> str | None:
    from alfred.conversation import _t

    groups = _people(await _open(session, member.id, OWED), _key(name))
    if not groups:
        return None
    if len(groups) > 1:
        return _t(
            "iou_ambiguous_person",
            lang,
            names=", ".join(sorted({v[0].person for v in groups.values()})),
        )
    items = next(iter(groups.values()))
    total = sum((remaining(i) for i in items), Decimal(0))
    return _t(
        "iou_reminder",
        lang,
        person=items[0].person,
        text=_t("iou_reminder_text", lang, person=items[0].person, amount=_fmt(total)),
    )


async def open_total(session: AsyncSession, member_id: uuid.UUID, direction: str) -> Decimal:
    """Sum still open in one direction (used by the dashboard/summary later)."""
    total = await session.scalar(
        select(func.coalesce(func.sum(Iou.amount - Iou.settled_amount), 0)).where(
            Iou.member_id == member_id, Iou.direction == direction, Iou.settled_at.is_(None)
        )
    )
    return _dec(total)


def _all(pt: str, nl: str, en: str, fr: str, de: str) -> dict[str, str | tuple[str, ...]]:
    return {"pt": pt, "nl": nl, "en": en, "fr": fr, "de": de}


STRINGS: dict[str, dict[str, str | tuple[str, ...]]] = {
    "iou_added_owed": _all(
        "Anotei: {person} te deve {amount}. Total do {person}: {total}.",
        "Genoteerd: {person} is jou {amount} schuldig. Totaal {person}: {total}.",
        "Noted: {person} owes you {amount}. {person}'s total: {total}.",
        "C'est noté : {person} te doit {amount}. Total de {person} : {total}.",
        "Notiert: {person} schuldet dir {amount}. Gesamt {person}: {total}.",
    ),
    "iou_added_owe": _all(
        "Anotei: você deve {amount} ao {person}. Total com o {person}: {total}.",
        "Genoteerd: je bent {person} {amount} schuldig. Totaal met {person}: {total}.",
        "Noted: you owe {person} {amount}. Total with {person}: {total}.",
        "C'est noté : tu dois {amount} à {person}. Total avec {person} : {total}.",
        "Notiert: du schuldest {person} {amount}. Gesamt mit {person}: {total}.",
    ),
    "iou_header_owed": _all(
        "Te devem {total}:",
        "Je krijgt nog {total}:",
        "You're owed {total}:",
        "On te doit {total} :",
        "Du bekommst noch {total}:",
    ),
    "iou_header_owe": _all(
        "Você deve {total}:",
        "Je bent {total} schuldig:",
        "You owe {total}:",
        "Tu dois {total} :",
        "Du schuldest {total}:",
    ),
    "iou_of": _all(
        " (de {total})", " (van {total})", " (of {total})", " (sur {total})", " (von {total})"
    ),
    "iou_empty_owed": _all(
        "Ninguém te deve nada por aqui.",
        "Niemand is jou iets schuldig.",
        "Nobody owes you anything here.",
        "Personne ne te doit rien ici.",
        "Dir schuldet hier niemand etwas.",
    ),
    "iou_empty_owe": _all(
        "Você não deve nada a ninguém por aqui.",
        "Je bent niemand iets schuldig.",
        "You don't owe anyone anything here.",
        "Tu ne dois rien à personne ici.",
        "Du schuldest hier niemandem etwas.",
    ),
    "iou_settled_full": _all(
        "Quitado: {person} pagou {paid}. Nada mais em aberto.",
        "Vereffend: {person} heeft {paid} betaald. Niets meer open.",
        "Settled: {person} paid {paid}. Nothing left open.",
        "Soldé : {person} a payé {paid}. Plus rien d'ouvert.",
        "Beglichen: {person} hat {paid} bezahlt. Nichts mehr offen.",
    ),
    "iou_settled_partial": _all(
        "Anotei {paid} de {person}. Ainda em aberto: {left}.",
        "{paid} van {person} genoteerd. Nog open: {left}.",
        "Noted {paid} from {person}. Still open: {left}.",
        "{paid} de {person} noté. Encore ouvert : {left}.",
        "{paid} von {person} notiert. Noch offen: {left}.",
    ),
    "iou_ambiguous_amount": _all(
        '{person} tem mais de um valor em aberto ({amounts}). Diga quanto: por exemplo, "{person} pagou 25".',
        '{person} heeft meer dan één openstaand bedrag ({amounts}). Zeg hoeveel, bijvoorbeeld "{person} heeft 25 betaald".',
        '{person} has more than one open amount ({amounts}). Say how much, for example "{person} paid 25".',
        "{person} a plusieurs montants ouverts ({amounts}). Dis combien, par exemple « {person} a payé 25 ».",
        '{person} hat mehrere offene Beträge ({amounts}). Sag, wie viel, zum Beispiel "{person} hat 25 bezahlt".',
    ),
    "iou_ambiguous_person": _all(
        "Quem exatamente? Tenho: {names}. Use o nome completo.",
        "Wie precies? Ik heb: {names}. Gebruik de volledige naam.",
        "Who exactly? I have: {names}. Use the full name.",
        "Qui exactement ? J'ai : {names}. Utilise le nom complet.",
        "Wer genau? Ich habe: {names}. Nimm den vollen Namen.",
    ),
    "iou_over": _all(
        "{person} só tem {total} em aberto. Confira o valor.",
        "{person} heeft maar {total} open staan. Controleer het bedrag.",
        "{person} only has {total} open. Check the amount.",
        "{person} n'a que {total} d'ouvert. Vérifie le montant.",
        "Bei {person} sind nur {total} offen. Prüf den Betrag.",
    ),
    "iou_bad_amount": _all(
        'Não entendi o valor. Exemplo: "Pedro me deve 25".',
        'Ik begrijp het bedrag niet. Voorbeeld: "Pedro is mij 25 schuldig".',
        'I didn\'t get the amount. Example: "Pedro owes me 25".',
        "Je n'ai pas compris le montant. Exemple : « Pedro me doit 25 ».",
        'Den Betrag habe ich nicht verstanden. Beispiel: "Pedro schuldet mir 25".',
    ),
    "iou_limit": _all(
        "Você já tem {n} valores em aberto, que é o máximo. Quite alguns antes de anotar outros.",
        "Je hebt al {n} openstaande bedragen, dat is het maximum. Vereffen er eerst een paar.",
        "You already have {n} open amounts, which is the maximum. Settle a few first.",
        "Tu as déjà {n} montants ouverts, c'est le maximum. Solde-en quelques-uns d'abord.",
        "Du hast schon {n} offene Beträge, das ist das Maximum. Begleiche zuerst einige.",
    ),
    "iou_reminder": _all(
        "Texto pronto para copiar e mandar ao {person}:\n\n{text}",
        "Tekst om te kopiëren en naar {person} te sturen:\n\n{text}",
        "Ready-to-copy text for {person}:\n\n{text}",
        "Texte à copier et envoyer à {person} :\n\n{text}",
        "Fertiger Text zum Kopieren für {person}:\n\n{text}",
    ),
    "iou_reminder_text": _all(
        "Oi {person}! Só lembrando dos {amount} que ficaram combinados. Se for mais fácil, manda um Tikkie.",
        "Hoi {person}! Even een herinnering aan de {amount} die we hadden afgesproken. Als het makkelijker is, stuur ik een Tikkie.",
        "Hi {person}! Just a reminder about the {amount} we agreed on. If it's easier, I can send a Tikkie.",
        "Salut {person} ! Petit rappel pour les {amount} dont on avait parlé. Si c'est plus simple, je t'envoie un Tikkie.",
        "Hallo {person}! Kurze Erinnerung an die {amount}, die wir vereinbart hatten. Wenn es einfacher ist, schicke ich dir einen Tikkie.",
    ),
}
