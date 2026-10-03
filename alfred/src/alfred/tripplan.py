# ruff: noqa: E501
"""V2-35 — itinerary, packing list and planned budget per category for a trip.

Rule-based, no LLM. All of it attaches to "the" trip: the active one, else the next upcoming one
(the same rule the panel uses). Without such a trip the member is told how to create one.

* "roteiro 12/10 10:00 Museu do Fado" adds an itinerary entry · "roteiro" lists them
* "bagagem: passaporte, carregador" adds items · "peguei o passaporte" ticks one (only when it is
  on the list, so "peguei um uber" is never ours) · "bagagem" lists them
* "orçamento da viagem hospedagem 300" sets the planned amount of a category · "orçamento da
  viagem" compares planned with what was spent
"""

from __future__ import annotations

import re
import uuid
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from alfred.audit import audit
from alfred.budgets import resolve_category
from alfred.clock import today_local
from alfred.labels import category_label
from alfred.models import SETTLED, Expense, Member, Trip, TripBudgetLine, TripItem, TripPackItem
from alfred.parsing import strip_accents, to_amount
from alfred.validation import MAX_AMOUNT

MAX_ITEMS = 200
MAX_PACK = 150
MAX_TEXT = 160
MAX_PACK_NAME = 120

_DATE = r"(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?"
_TIME = r"(?:\s+(?:as\s+|at\s+|um\s+|om\s+|a\s+|à\s+)?(\d{1,2})(?::|h)(\d{2})?(?:\s*h)?)?"
_ITIN_ADD = [
    re.compile(rf"^roteiro\s+(?:dia\s+)?{_DATE}{_TIME}\s+(.+)$"),
    re.compile(rf"^itinerary\s+(?:on\s+)?{_DATE}{_TIME}\s+(.+)$"),
    re.compile(rf"^reisschema\s+(?:op\s+)?{_DATE}{_TIME}\s+(.+)$"),
    re.compile(rf"^itineraire\s+(?:le\s+)?{_DATE}{_TIME}\s+(.+)$"),
    re.compile(rf"^reiseplan\s+(?:am\s+)?{_DATE}{_TIME}\s+(.+)$"),
]
_ITIN_LIST = {
    "roteiro", "meu roteiro", "roteiro da viagem", "itinerary", "my itinerary", "trip itinerary",
    "reisschema", "mijn reisschema", "itineraire", "mon itineraire", "reiseplan", "mein reiseplan",
}  # fmt: skip
_PACK_ADD = [
    re.compile(r"^(?:bagagem|lista de bagagem|mala)\s*[:\-]\s*(.+)$"),
    re.compile(r"^(?:packing|packing list)\s*[:\-]\s*(.+)$"),
    re.compile(r"^(?:bagage|paklijst)\s*[:\-]\s*(.+)$"),
    re.compile(r"^(?:bagages?|valise)\s*:\s*(.+)$"),
    re.compile(r"^(?:gepack|packliste)\s*:\s*(.+)$"),
]
_PACK_LIST = {
    "bagagem", "minha bagagem", "lista de bagagem", "minha mala", "packing list", "my packing list",
    "packing", "paklijst", "mijn paklijst", "bagage", "liste de bagages", "ma liste de bagages",
    "packliste", "meine packliste",
}  # fmt: skip
_PACKED = [
    re.compile(
        r"^(?:peguei|botei na mala|coloquei na mala|guardei na mala|ja coloquei na mala)\s+(?:o\s+|a\s+|os\s+|as\s+)?(.+)$"
    ),
    re.compile(r"^packed\s+(?:the\s+|my\s+)?(.+)$"),
    re.compile(r"^(?:ingepakt|ik heb ingepakt)\s*:?\s+(?:de\s+|het\s+)?(.+)$"),
    re.compile(r"^jai mis\s+(?:le\s+|la\s+|les\s+|l)?(.+?)\s+dans la valise$"),
    re.compile(r"^(?:eingepackt|ich habe eingepackt)\s*:?\s+(?:den\s+|die\s+|das\s+)?(.+)$"),
]
_BUDGET_WORDS = (
    r"(?:orcamento da viagem|trip budget|reisbudget|budget voyage|budget du voyage|reisebudget)"
)
_BUDGET_SET = re.compile(rf"^{_BUDGET_WORDS}\s+(.+?)\s+(?:€\s*)?(\d[\d.,]*)\s*(?:€|eur|euros?)?$")
_BUDGET_LIST = re.compile(rf"^{_BUDGET_WORDS}$")


def _all(pt: str, nl: str, en: str, fr: str, de: str) -> dict[str, str | tuple[str, ...]]:
    return {"pt": pt, "nl": nl, "en": en, "fr": fr, "de": de}


def _fmt(value: float) -> str:
    from alfred.conversation import _fmt_eur

    return _fmt_eur(value)


def main_trip(trips: list[Trip], today: date) -> Trip | None:
    """The active trip, else the nearest upcoming one (what the panel shows too)."""
    active = [t for t in trips if t.active and t.started_at <= today]
    if active:
        return active[0]
    upcoming = sorted((t for t in trips if t.started_at > today), key=lambda t: t.started_at)
    return upcoming[0] if upcoming else None


async def target_trip(session: AsyncSession, member_id: uuid.UUID) -> Trip | None:
    trips = list(
        (
            await session.execute(
                select(Trip)
                .where(Trip.member_id == member_id)
                .order_by(Trip.started_at.desc(), Trip.created_at.desc())
                .limit(40)
            )
        ).scalars()
    )
    return main_trip(trips, today_local())


def _clean(text: str) -> str:
    return " ".join(text.split())


def _original(body: str, plain: str, m: re.Match[str], group: int) -> str:
    start, end = m.span(group)
    raw = " ".join(body.split())
    flat = " ".join(plain.split())
    return _clean(raw[start:end] if len(raw) == len(flat) else m.group(group))


def _day(d: str, mo: str, y: str | None, trip: Trip) -> date | None:
    year = int(y) + (2000 if y and len(y) == 2 else 0) if y else trip.started_at.year
    try:
        day = date(year, int(mo), int(d))
    except ValueError:
        return None
    if y is None and day < trip.started_at:
        try:
            day = date(year + 1, int(mo), int(d))
        except ValueError:
            return None
    return day


def _time(h: str | None, mi: str | None) -> str | None | bool:
    if h is None:
        return None
    hour, minute = int(h), int(mi or 0)
    if hour > 23 or minute > 59:
        return False
    return f"{hour:02d}:{minute:02d}"


async def handle_tripplan_command(
    body: str, body_plain: str, member: Member, lang: str, session: AsyncSession
) -> str | None:
    flat = " ".join(body_plain.replace("'", "").split()).strip(" .!?")
    plain_keep = " ".join(body_plain.split()).strip(" .!?")  # apostrophes kept, for the originals

    if flat in _ITIN_LIST:
        return await _list_itinerary(member, lang, session)
    if flat in _PACK_LIST:
        return await _list_pack(member, lang, session)
    if _BUDGET_LIST.match(flat):
        return await _list_budget(member, lang, session)

    for pattern in _ITIN_ADD:
        if m := pattern.match(flat):
            return await _add_itinerary(body, flat, m, member, lang, session)
    for pattern in _PACK_ADD:
        if m := pattern.match(flat):
            names = [
                _clean(x)[:MAX_PACK_NAME]
                for x in re.split(
                    r"[;,]", _original(body, plain_keep, pattern.match(plain_keep) or m, 1)
                )
                if x.strip()
            ]
            return await _add_pack(names, member, lang, session)
    if m := _BUDGET_SET.match(flat):
        return await _set_budget(m.group(1), m.group(2), member, lang, session)
    for pattern in _PACKED:
        if m := pattern.match(flat):
            return await _tick(m.group(1), member, lang, session)
    return None


async def _need_trip(
    member: Member, lang: str, session: AsyncSession
) -> tuple[Trip | None, str | None]:
    from alfred.conversation import _t

    trip = await target_trip(session, member.id)
    return (trip, None) if trip else (None, _t("tp_no_trip", lang))


async def _add_itinerary(
    body: str, flat: str, m: re.Match[str], member: Member, lang: str, session: AsyncSession
) -> str:
    from alfred.conversation import _t

    trip, msg = await _need_trip(member, lang, session)
    if trip is None:
        return msg or ""
    day = _day(m.group(1), m.group(2), m.group(3), trip)
    at = _time(m.group(4), m.group(5))
    title = _original(body, flat, m, 6)[:MAX_TEXT]
    if day is None or at is False or not title:
        return _t("tp_itin_bad", lang)
    count = await session.scalar(
        select(func.count()).select_from(TripItem).where(TripItem.trip_id == trip.id)
    )
    if (count or 0) >= MAX_ITEMS:
        return _t("tp_limit", lang, n=MAX_ITEMS)
    session.add(
        TripItem(member_id=member.id, trip_id=trip.id, day=day, at_time=at or None, title=title)
    )
    await session.flush()
    audit(session, "trip_item_added", member.id)
    when = day.strftime("%d/%m") + (f" {at}" if at else "")
    return _t("tp_itin_added", lang, when=when, title=title, dest=trip.destination)


async def _list_itinerary(member: Member, lang: str, session: AsyncSession) -> str:
    from alfred.conversation import _t

    trip, msg = await _need_trip(member, lang, session)
    if trip is None:
        return msg or ""
    rows = list(
        (
            await session.execute(
                select(TripItem)
                .where(TripItem.trip_id == trip.id)
                .order_by(TripItem.day, TripItem.at_time.nulls_first(), TripItem.created_at)
            )
        ).scalars()
    )
    if not rows:
        return _t("tp_itin_empty", lang, dest=trip.destination)
    lines = [
        f"• {r.day.strftime('%d/%m')}{' ' + r.at_time if r.at_time else ''} · {r.title}"
        for r in rows
    ]
    return _t("tp_itin_header", lang, dest=trip.destination) + "\n" + "\n".join(lines)


async def _pack_rows(session: AsyncSession, trip_id: uuid.UUID) -> list[TripPackItem]:
    return list(
        (
            await session.execute(
                select(TripPackItem)
                .where(TripPackItem.trip_id == trip_id)
                .order_by(TripPackItem.created_at, TripPackItem.id)
            )
        ).scalars()
    )


def _pkey(name: str) -> str:
    return " ".join(strip_accents(name.lower()).replace("'", "").split())


async def _add_pack(names: list[str], member: Member, lang: str, session: AsyncSession) -> str:
    from alfred.conversation import _t

    trip, msg = await _need_trip(member, lang, session)
    if trip is None:
        return msg or ""
    rows = await _pack_rows(session, trip.id)
    have = {_pkey(r.name) for r in rows}
    added: list[str] = []
    for name in names:
        if _pkey(name) in have or not _pkey(name):
            continue
        if len(rows) + len(added) >= MAX_PACK:
            return _t("tp_limit", lang, n=MAX_PACK)
        session.add(TripPackItem(member_id=member.id, trip_id=trip.id, name=name))
        have.add(_pkey(name))
        added.append(name)
    await session.flush()
    if not added:
        return _t("tp_pack_nothing_new", lang)
    audit(session, "trip_pack_added", member.id, n=len(added))
    return _t("tp_pack_added", lang, n=len(added), dest=trip.destination)


async def _list_pack(member: Member, lang: str, session: AsyncSession) -> str:
    from alfred.conversation import _t

    trip, msg = await _need_trip(member, lang, session)
    if trip is None:
        return msg or ""
    rows = await _pack_rows(session, trip.id)
    if not rows:
        return _t("tp_pack_empty", lang, dest=trip.destination)
    done = sum(1 for r in rows if r.packed)
    lines = [f"{'✓' if r.packed else '○'} {r.name}" for r in rows]
    return (
        _t("tp_pack_header", lang, done=done, total=len(rows), dest=trip.destination)
        + "\n"
        + "\n".join(lines)
    )


async def _tick(raw: str, member: Member, lang: str, session: AsyncSession) -> str | None:
    from alfred.conversation import _t

    trip = await target_trip(session, member.id)
    if trip is None:
        return None
    rows = await _pack_rows(session, trip.id)
    key = _pkey(raw)
    hit = [r for r in rows if _pkey(r.name) == key] or [
        r for r in rows if key and key in _pkey(r.name)
    ]
    if len(hit) != 1:
        return None  # not on the list (or ambiguous): "peguei um uber" is somebody else's
    row = hit[0]
    row.packed = True
    await session.flush()
    left = sum(1 for r in rows if not r.packed)
    return _t("tp_packed", lang, name=row.name, left=left)


async def _set_budget(
    raw_cat: str, raw_amount: str, member: Member, lang: str, session: AsyncSession
) -> str:
    from alfred.conversation import _t

    trip, msg = await _need_trip(member, lang, session)
    if trip is None:
        return msg or ""
    category = resolve_category(raw_cat)
    if category is None:
        return _t("tp_budget_unknown", lang)
    amount = to_amount(raw_amount)
    if amount is None or amount <= 0 or amount > MAX_AMOUNT:
        return _t("tp_budget_bad", lang)
    line = await session.scalar(
        select(TripBudgetLine).where(
            TripBudgetLine.trip_id == trip.id, TripBudgetLine.category == category
        )
    )
    if line is None:
        session.add(
            TripBudgetLine(member_id=member.id, trip_id=trip.id, category=category, amount=amount)
        )
    else:
        line.amount = amount
    await session.flush()
    audit(session, "trip_budget_set", member.id)
    return _t(
        "tp_budget_set",
        lang,
        cat=category_label(category, lang),
        amount=_fmt(amount),
        dest=trip.destination,
    )


async def _list_budget(member: Member, lang: str, session: AsyncSession) -> str:
    from alfred.conversation import _t

    trip, msg = await _need_trip(member, lang, session)
    if trip is None:
        return msg or ""
    lines_db = list(
        (
            await session.execute(
                select(TripBudgetLine)
                .where(TripBudgetLine.trip_id == trip.id)
                .order_by(TripBudgetLine.category)
            )
        ).scalars()
    )
    if not lines_db:
        return _t("tp_budget_empty", lang, dest=trip.destination)
    cat = func.coalesce(Expense.category, "overig")
    spent = dict(
        (
            await session.execute(
                select(cat, func.sum(Expense.amount))
                .where(
                    Expense.member_id == member.id,
                    Expense.trip_id == trip.id,
                    Expense.transaction_type == "expense",
                    Expense.status.in_(SETTLED),
                )
                .group_by(cat)
            )
        ).all()
    )
    out = []
    total_plan = total_spent = 0.0
    for ln in lines_db:
        s = float(spent.get(ln.category) or 0)
        total_plan += float(ln.amount)
        total_spent += s
        out.append(f"• {category_label(ln.category, lang)}: {_fmt(s)} / {_fmt(float(ln.amount))}")
    return (
        _t(
            "tp_budget_header",
            lang,
            dest=trip.destination,
            spent=_fmt(total_spent),
            plan=_fmt(total_plan),
        )
        + "\n"
        + "\n".join(out)
    )


STRINGS: dict[str, dict[str, str | tuple[str, ...]]] = {
    "tp_no_trip": _all(
        "Você não tem uma viagem ativa nem próxima. Crie uma primeiro, por exemplo “criar viagem Lisboa”.",
        "Je hebt geen actieve of komende reis. Maak er eerst een, bijvoorbeeld “start reis Lissabon”.",
        "You have no active or upcoming trip. Create one first, for example “start trip Lisbon”.",
        "Tu n'as aucun voyage actif ou à venir. Crée-en un d'abord, par exemple « créer voyage Lisbonne ».",
        "Du hast keine aktive oder kommende Reise. Lege zuerst eine an, zum Beispiel „reise Lissabon starten“.",
    ),
    "tp_limit": _all(
        "Esta viagem já atingiu o limite de {n} registros.",
        "Je zit aan de limiet van {n} items voor deze reis.",
        "You've reached the limit of {n} items for this trip.",
        "Tu as atteint la limite de {n} éléments pour ce voyage.",
        "Du hast das Limit von {n} Einträgen für diese Reise erreicht.",
    ),
    "tp_itin_bad": _all(
        "Não entendi. Exemplo: “roteiro 12/10 10:00 Museu do Fado”.",
        "Ik begrijp het niet. Voorbeeld: “reisschema 12/10 10:00 Fadomuseum”.",
        "I didn't understand. Example: “itinerary 12/10 10:00 Fado Museum”.",
        "Je n'ai pas compris. Exemple : « itinéraire 12/10 10:00 Musée du Fado ».",
        "Ich habe das nicht verstanden. Beispiel: „reiseplan 12/10 10:00 Fado-Museum“.",
    ),
    "tp_itin_added": _all(
        "Anotei no roteiro de {dest}: {when} · {title}.",
        "Toegevoegd aan het reisschema van {dest}: {when} · {title}.",
        "Added to the {dest} itinerary: {when} · {title}.",
        "Ajouté à l'itinéraire de {dest} : {when} · {title}.",
        "Zum Reiseplan für {dest} hinzugefügt: {when} · {title}.",
    ),
    "tp_itin_header": _all(
        "Roteiro de {dest}:",
        "Reisschema {dest}:",
        "Itinerary for {dest}:",
        "Itinéraire de {dest} :",
        "Reiseplan für {dest}:",
    ),
    "tp_itin_empty": _all(
        "O roteiro de {dest} está vazio. Exemplo: “roteiro 12/10 10:00 Museu do Fado”.",
        "Het reisschema voor {dest} is leeg. Voorbeeld: “reisschema 12/10 10:00 Fadomuseum”.",
        "The {dest} itinerary is empty. Example: “itinerary 12/10 10:00 Fado Museum”.",
        "L'itinéraire de {dest} est vide. Exemple : « itinéraire 12/10 10:00 Musée du Fado ».",
        "Der Reiseplan für {dest} ist leer. Beispiel: „reiseplan 12/10 10:00 Fado-Museum“.",
    ),
    "tp_pack_added": _all(
        "Adicionei {n} à bagagem de {dest}.",
        "{n} toegevoegd aan de paklijst voor {dest}.",
        "Added {n} to the packing list for {dest}.",
        "Liste de bagages pour {dest} mise à jour : {n} de plus.",
        "{n} zur Packliste für {dest} hinzugefügt.",
    ),
    "tp_pack_nothing_new": _all(
        "Isso já estava na bagagem.",
        "Die items stonden al op de paklijst.",
        "Those items were already on the list.",
        "Ces éléments étaient déjà sur la liste.",
        "Diese Dinge standen schon auf der Liste.",
    ),
    "tp_pack_header": _all(
        "Bagagem de {dest}: {done} de {total} na mala.",
        "Paklijst {dest}: {done} van {total} ingepakt.",
        "Packing list for {dest}: {done} of {total} packed.",
        "Bagages pour {dest} : {done} sur {total} dans la valise.",
        "Packliste für {dest}: {done} von {total} gepackt.",
    ),
    "tp_pack_empty": _all(
        "A bagagem de {dest} está vazia. Exemplo: “bagagem: passaporte, carregador”.",
        "De paklijst voor {dest} is leeg. Voorbeeld: “paklijst: paspoort, oplader”.",
        "The packing list for {dest} is empty. Example: “packing: passport, charger”.",
        "La liste de bagages pour {dest} est vide. Exemple : « bagages : passeport, chargeur ».",
        "Die Packliste für {dest} ist leer. Beispiel: „packliste: Reisepass, Ladegerät“.",
    ),
    "tp_packed": _all(
        "Marquei “{name}” como na mala. Faltam {left}.",
        "“{name}” staat als ingepakt. Nog {left} te gaan.",
        "Marked “{name}” as packed. {left} to go.",
        "« {name} » est dans la valise. Il en reste {left}.",
        "„{name}“ ist als gepackt markiert. Noch {left} offen.",
    ),
    "tp_budget_unknown": _all(
        "Não reconheci essa categoria. Exemplo: “orçamento da viagem hospedagem 300”.",
        "Die categorie ken ik niet. Voorbeeld: “reisbudget wonen 300”.",
        "I don't know that category. Example: “trip budget housing 300”.",
        "Je ne connais pas cette catégorie. Exemple : « budget voyage logement 300 ».",
        "Diese Kategorie kenne ich nicht. Beispiel: „reisebudget wohnen 300“.",
    ),
    "tp_budget_bad": _all(
        "Esse valor não parece certo. Exemplo: “orçamento da viagem hospedagem 300”.",
        "Dat bedrag klopt niet. Voorbeeld: “reisbudget wonen 300”.",
        "That amount doesn't look right. Example: “trip budget housing 300”.",
        "Ce montant ne semble pas correct. Exemple : « budget voyage logement 300 ».",
        "Dieser Betrag passt nicht. Beispiel: „reisebudget wohnen 300“.",
    ),
    "tp_budget_set": _all(
        "Orçamento planejado de {cat} em {dest}: {amount}.",
        "Geplande begroting voor {cat} bij {dest}: {amount}.",
        "Planned budget for {cat} on {dest}: {amount}.",
        "Budget prévu pour {cat} à {dest} : {amount}.",
        "Geplantes Budget für {cat} bei {dest}: {amount}.",
    ),
    "tp_budget_header": _all(
        "Orçamento de {dest}: gasto {spent} de {plan} planejados.",
        "Begroting {dest}: uitgegeven {spent} van {plan} gepland.",
        "Budget for {dest}: spent {spent} of {plan} planned.",
        "Budget de {dest} : dépensé {spent} sur {plan} prévus.",
        "Budget für {dest}: ausgegeben {spent} von {plan} geplant.",
    ),
    "tp_budget_empty": _all(
        "Ainda não há orçamento por categoria em {dest}. Exemplo: “orçamento da viagem hospedagem 300”.",
        "Er is nog geen begroting per categorie voor {dest}. Voorbeeld: “reisbudget wonen 300”.",
        "There's no budget by category for {dest} yet. Example: “trip budget housing 300”.",
        "Pas encore de budget par catégorie pour {dest}. Exemple : « budget voyage logement 300 ».",
        "Für {dest} gibt es noch kein Budget je Kategorie. Beispiel: „reisebudget wohnen 300“.",
    ),
}
