# ruff: noqa: E501
"""V2-27 — contracts of the home (energy, gas, internet): provider, monthly amount, end date.

Registered from text ("contrato energia Vattenfall 120 por mês até 31/12/2027") or from a photo/PDF
of the contract (read by ``media_input``; the model's JSON is untrusted). A draft is shown first
and kept as ``status='draft'`` for 15 minutes. The cron reminds 60 and 30 days before the end so the
member can compare offers; the module gives dates, never advice on which supplier to choose.
"""

from __future__ import annotations

import re
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from alfred.audit import audit
from alfred.models import HomeContract, Member, Message
from alfred.parsing import strip_accents, to_amount
from alfred.receipt import _EURO, _text, _total
from alfred.training import Reply
from alfred.validation import MAX_AMOUNT

MAX_ACTIVE = 20
DRAFT_TTL = timedelta(minutes=15)
MAX_YEARS_AHEAD = 10
WINDOW_HOURS = 24
KINDS = ("energy", "gas", "internet")

_KIND_RE = {
    "energy": r"energia|energie|energy|electricit(?:y|e|é)|eletricidade|electricidade|stroom|strom|luz|power|courant|électricité",
    "gas": r"g[aá]s|gaz",
    "internet": r"internet|wifi|wi-fi|fibra|fiber|fibre|glasvezel|breitband",
}
_ANY_KIND = "|".join(f"(?:{v})" for v in _KIND_RE.values())
_OF = r"(?:(?:de|do|da|of|van|von|du)\s+|d')?"
_NOUN = r"(?:contrato|contract|vertrag|contrat)"
_CREATE = re.compile(
    r"^(?:(?:guarda|guardar|registra|registrar|novo|nova|save|add|new|bewaar|voeg toe|nieuw|garde|enregistre|speichere)\s+)?"
    rf"{_NOUN}\s+{_OF}(?P<kind>{_ANY_KIND})\s+(?P<rest>.+)$",
    re.I,
)
_UNTIL = re.compile(
    r"^(?P<head>.+?)\s+(?:at[eé]|until|tot|jusqu'(?:au|à|a)|bis|vence(?:\s+em)?|ends(?:\s+on)?|eindigt(?:\s+op)?|endet(?:\s+am)?|termina(?:\s+em)?)\s+(?P<date>\S.*)$",
    re.I,
)
_AMOUNT = re.compile(
    r"^(?P<prov>.+?)\s+(?:€\s*)?(?P<amt>\d[\d.,]*)\s*(?:€|eur|euros?)?"
    r"(?:\s*(?:por\s+m[eê]s|/\s*m[eê]s|per\s+maand|/\s*maand|a\s+month|per\s+month|/\s*month|monthly|par\s+mois|/\s*mois|pro\s+monat|/\s*monat|mensais?|mensal))?$",
    re.I,
)
_DATE = re.compile(r"^(\d{1,2})[/.-](\d{1,2})(?:[/.-](\d{2,4}))?$")
_LIST = {
    "contratos", "meus contratos", "contratos da casa", "contracts", "my contracts",
    "home contracts", "contracten", "mijn contracten", "contrats", "mes contrats",
    "vertrage", "meine vertrage", "vertraege", "meine vertraege",
}  # fmt: skip
_DELETE = re.compile(
    rf"^(?:apaga|apagar|delete|remove|verwijder|supprime|loesche|loeschen)\s+(?:o |a |the |het |de |le |la |das |der )?{_NOUN}\s+{_OF}(?P<ref>.+)$"
)
_RENEW = re.compile(
    rf"^(?:renovei|renovou|renewed|renew|verlengd|verleng|renouvele|renouvelle|verlaengert|verlaengerte)\s+(?:o |a |my |mijn |mon |mein )?{_NOUN}\s+{_OF}(?P<ref>.+?)\s+(?:ate|until|tot|jusqu'au|jusqu'a|bis)\s+(?P<date>.+)$"
)
_KEYS = re.compile(r"contrat|contract|vertrag|vertrae")
_MARKUP = re.compile(r"[*_~`]")


def _t(key: str, lang: str, **kw: object) -> str:
    from alfred.conversation import _t as t

    return t(key, lang, **kw)


def _fmt(value: float) -> str:
    from alfred.conversation import _fmt_eur

    return _fmt_eur(float(value))


def _kind_of(word: str) -> str | None:
    for kind, pattern in _KIND_RE.items():
        if re.fullmatch(pattern, word.strip(), re.I):
            return kind
    return None


def _parse_end(raw: str, today: date) -> date | None:
    m = _DATE.match(raw.strip(" .!?"))
    if not m:
        return None
    d, mo, y = int(m.group(1)), int(m.group(2)), m.group(3)
    try:
        if y:
            yy = int(y)
            end = date(yy + 2000 if yy < 100 else yy, mo, d)
        else:
            end = date(today.year, mo, d)
            if end < today:
                end = date(today.year + 1, mo, d)
    except ValueError:
        return None
    if end < today or end > today + timedelta(days=365 * MAX_YEARS_AHEAD):
        return None
    return end


def _clean(value: str, limit: int) -> str | None:
    out = " ".join(_MARKUP.sub("", value).split())[:limit].strip(" -,.")
    return out or None


def parse_contract(text: str, today: date) -> dict | None:
    """Fields from the typed contract; None when kind, provider or a valid end date is missing."""
    m = _CREATE.match(unicodedata.normalize("NFC", " ".join(text.split()).strip(" !?")))
    if not m:
        return None
    kind = _kind_of(m.group("kind"))
    until = _UNTIL.match(m.group("rest"))
    if kind is None or until is None:
        return None
    ends = _parse_end(until.group("date"), today)
    if ends is None:
        return None
    head = until.group("head").strip()
    amount = None
    if am := _AMOUNT.match(head):
        amount = to_amount(am.group("amt"))
        if amount is None or amount > MAX_AMOUNT:
            return None
        amount = round(amount, 2)
        head = am.group("prov")
    provider = _clean(head, 60)
    if provider is None or not any(c.isalpha() for c in provider):
        return None
    return {"kind": kind, "provider": provider, "amount": amount, "ends_on": ends}


def contract_from_reading(reading: dict, today: date) -> dict | None:
    """Fields from what the model read on a contract; everything is checked here."""
    provider = _text(reading.get("provider"), 60)
    kind = str(reading.get("contract_type") or "").lower().strip()
    raw_end = reading.get("end_date")
    if provider is None or kind not in KINDS or not isinstance(raw_end, str):
        return None
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw_end.strip()):
        return None
    try:
        ends = date.fromisoformat(raw_end.strip())
    except ValueError:
        return None
    if ends < today or ends > today + timedelta(days=365 * MAX_YEARS_AHEAD):
        return None
    currency = reading.get("currency")
    euro = not (
        isinstance(currency, str) and currency.strip() and currency.strip().upper() not in _EURO
    )
    amount = _total(reading.get("monthly_amount")) if euro else None
    return {"kind": kind, "provider": provider, "amount": amount, "ends_on": ends}


def _kind_label(kind: str, lang: str) -> str:
    return _t(f"home_kind_{kind}", lang)


def _key(text: str) -> str:
    return strip_accents(unicodedata.normalize("NFC", text)).lower().strip()


async def _create_draft(
    fields: dict, member: Member, lang: str, session: AsyncSession, today: date
) -> Reply:
    active = await session.scalar(
        select(func.count())
        .select_from(HomeContract)
        .where(HomeContract.member_id == member.id, HomeContract.status == "active")
    )
    if (active or 0) >= MAX_ACTIVE:
        return Reply(_t("home_limit", lang, n=MAX_ACTIVE))
    await session.execute(
        delete(HomeContract).where(
            HomeContract.member_id == member.id, HomeContract.status == "draft"
        )
    )
    rec = HomeContract(
        id=uuid.uuid4(),
        member_id=member.id,
        status="draft",
        kind=fields["kind"],
        provider=fields["provider"],
        amount=fields["amount"],
        ends_on=fields["ends_on"],
    )
    session.add(rec)
    await session.flush()
    lines = [_t("home_title", lang), "• " + _line(rec, lang, today)]
    return Reply(
        "\n".join([*lines, "", _t("home_foot", lang)]),
        [
            (f"home_ok:{rec.id}", _t("btn_home_ok", lang)),
            (f"home_no:{rec.id}", _t("btn_home_no", lang)),
        ],
    )


def _line(rec: HomeContract, lang: str, today: date) -> str:
    parts = [f"{_kind_label(rec.kind, lang)}: {rec.provider}"]
    if rec.amount is not None:
        parts.append(_t("home_per_month", lang, a=_fmt(rec.amount)))
    days = (rec.ends_on - today).days
    parts.append(_t("home_ends", lang, d=f"{rec.ends_on:%d/%m/%Y}", n=days))
    return " · ".join(parts)


async def home_reply(
    reading: dict, member: Member, lang: str, session: AsyncSession, today: date
) -> Reply:
    fields = contract_from_reading(reading, today)
    if fields is None:
        return Reply(_t("home_photo_bad", lang))
    return await _create_draft(fields, member, lang, session, today)


async def _active(session: AsyncSession, member: Member) -> list[HomeContract]:
    rows = await session.execute(
        select(HomeContract)
        .where(HomeContract.member_id == member.id, HomeContract.status == "active")
        .order_by(HomeContract.ends_on, HomeContract.created_at)
        .limit(MAX_ACTIVE + 1)
    )
    return list(rows.scalars().all())


def _find(rows: list[HomeContract], ref: str) -> list[HomeContract]:
    key = _key(ref)
    word = _kind_of(key) or _kind_of(ref.strip())
    if word:
        return [r for r in rows if r.kind == word]
    return [r for r in rows if key and key in _key(r.provider)]


async def handle_home_command(
    raw: str, body_plain: str, member: Member, lang: str, session: AsyncSession, today: date
) -> Reply | None:
    """None unless the message is clearly about home contracts (cheap exit first)."""
    plain = " ".join(body_plain.split()).strip(" .!?")
    if len(plain) > 200 or (plain not in _LIST and not _KEYS.search(plain)):
        return None
    if plain in _LIST:
        return await _list(member, lang, session, today)
    if _CREATE.match(unicodedata.normalize("NFC", " ".join(raw.split()).strip(" !?"))):
        fields = parse_contract(raw, today)
        if fields is None:
            return Reply(_t("home_bad", lang))
        return await _create_draft(fields, member, lang, session, today)
    if m := _RENEW.match(plain):
        ends = _parse_end(m.group("date"), today)
        if ends is None:
            return Reply(_t("home_bad", lang))
        found = _find(await _active(session, member), m.group("ref"))
        if not found:
            return Reply(_t("home_not_found", lang))
        if len(found) > 1:
            return Reply(_t("home_ambiguous", lang, names=_names(found, lang)))
        rec = found[0]
        rec.ends_on, rec.sent_60, rec.sent_30 = ends, False, False
        session.add(rec)
        audit(session, "home_contract_renewed", member.id)
        return Reply(_t("home_renewed", lang, line=_line(rec, lang, today)))
    if m := _DELETE.match(plain):
        found = _find(await _active(session, member), m.group("ref"))
        if not found:
            return Reply(_t("home_not_found", lang))
        if len(found) > 1:
            return Reply(_t("home_ambiguous", lang, names=_names(found, lang)))
        label = f"{_kind_label(found[0].kind, lang)} ({found[0].provider})"
        await session.delete(found[0])
        audit(session, "home_contract_deleted", member.id)
        return Reply(_t("home_deleted", lang, what=label))
    return None


def _names(rows: list[HomeContract], lang: str) -> str:
    return ", ".join(f"{_kind_label(r.kind, lang)} ({r.provider})" for r in rows[:5])


async def _list(member: Member, lang: str, session: AsyncSession, today: date) -> Reply:
    rows = await _active(session, member)
    if not rows:
        return Reply(_t("home_list_empty", lang))
    lines = [_t("home_list_title", lang) + "\n"]
    lines += [f"• {_line(r, lang, today)}" for r in rows[:MAX_ACTIVE]]
    lines.append("\n" + _t("home_list_hint", lang))
    return Reply("\n".join(lines))


async def handle_home_button(
    action: str, raw_id: str, member: Member, lang: str, session: AsyncSession, today: date
) -> Reply:
    try:
        rec_id = uuid.UUID(raw_id)
    except ValueError:
        return Reply(_t("button_gone", lang))
    rec = await session.scalar(
        select(HomeContract)
        .where(HomeContract.id == rec_id, HomeContract.member_id == member.id)
        .with_for_update()
    )
    if rec is None or rec.status != "draft":
        return Reply(_t("button_gone", lang))
    if action == "home_no":
        await session.delete(rec)
        return Reply(_t("home_cancelled", lang))
    if rec.created_at < datetime.now(UTC) - DRAFT_TTL:
        await session.delete(rec)
        return Reply(_t("home_expired", lang))
    await session.execute(
        delete(HomeContract).where(
            HomeContract.member_id == member.id,
            HomeContract.kind == rec.kind,
            HomeContract.status == "active",
        )
    )
    rec.status = "active"
    session.add(rec)
    audit(session, "home_contract_saved", member.id)
    return Reply(_t("home_saved", lang, line=_line(rec, lang, today)))


# ── cron side ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ContractReminder:
    record_id: uuid.UUID
    which: str  # d60 | d30
    wa_phone: str
    lang: str
    text: str
    in_window: bool


async def due_reminders(
    session: AsyncSession, today: date, now: datetime
) -> list[ContractReminder]:
    rows = (
        await session.execute(
            select(HomeContract, Member.wa_phone, Member.language)
            .join(Member, HomeContract.member_id == Member.id)
            .where(HomeContract.status == "active", Member.consent_state == "accepted")
        )
    ).all()
    out: list[ContractReminder] = []
    cutoff = now - timedelta(hours=WINDOW_HOURS)
    for rec, phone, language in rows:
        days = (rec.ends_on - today).days
        if 0 <= days <= 30 and not rec.sent_30:
            which = "d30"
        elif 30 < days <= 60 and not rec.sent_60:
            which = "d60"
        else:
            continue
        lang = language or "en"
        last_in = await session.scalar(
            select(func.max(Message.created_at)).where(
                Message.author_id == rec.member_id, Message.direction == "inbound"
            )
        )
        text = _t(
            "home_rem",
            lang,
            kind=_kind_label(rec.kind, lang),
            provider=rec.provider,
            d=f"{rec.ends_on:%d/%m/%Y}",
            n=days,
        )
        in_window = bool(last_in and last_in.astimezone(now.tzinfo) >= cutoff)
        out.append(ContractReminder(rec.id, which, phone, lang, text, in_window))
    return out


async def mark_sent(session: AsyncSession, record_id: uuid.UUID, which: str) -> None:
    rec = await session.get(HomeContract, record_id)
    if rec is None:
        return
    if which == "d30":
        rec.sent_30 = rec.sent_60 = True
    else:
        rec.sent_60 = True
    session.add(rec)


def _all5(pt: str, nl: str, en: str, fr: str, de: str) -> dict[str, str]:
    return {"pt": pt, "nl": nl, "en": en, "fr": fr, "de": de}


STRINGS: dict[str, dict[str, str]] = {
    "btn_home_ok": _all5("Confirmar", "Bevestigen", "Confirm", "Confirmer", "Bestätigen"),
    "btn_home_no": _all5("Cancelar", "Annuleren", "Cancel", "Annuler", "Abbrechen"),
    "home_kind_energy": _all5("Energia", "Energie", "Energy", "Énergie", "Strom"),
    "home_kind_gas": _all5("Gás", "Gas", "Gas", "Gaz", "Gas"),
    "home_kind_internet": _all5("Internet", "Internet", "Internet", "Internet", "Internet"),
    "home_per_month": _all5(
        "{a} por mês", "{a} per maand", "{a} a month", "{a} par mois", "{a} im Monat"
    ),
    "home_ends": _all5(
        "termina em {d} (faltam {n} dias)",
        "eindigt op {d} (nog {n} dagen)",
        "ends {d} ({n} days left)",
        "se termine le {d} (encore {n} jours)",
        "endet am {d} (noch {n} Tage)",
    ),
    "home_title": _all5(
        "Entendi este contrato:",
        "Dit contract heb ik begrepen:",
        "Here is the contract I understood:",
        "Voici le contrat compris :",
        "Das habe ich verstanden:",
    ),
    "home_foot": _all5(
        "Ainda não gravei nada. Se existir outro contrato do mesmo tipo, este o substitui. Para corrigir, mande de novo.",
        "Ik heb nog niets opgeslagen. Een bestaand contract van hetzelfde type wordt vervangen. Om te corrigeren, stuur het opnieuw.",
        "Nothing is saved yet. An existing contract of the same type is replaced. To correct something, send it again.",
        "Rien n'est encore enregistré. Un contrat existant du même type est remplacé. Pour corriger, renvoie-le.",
        "Noch nichts gespeichert. Ein bestehender Vertrag derselben Art wird ersetzt. Zum Korrigieren sende es neu.",
    ),
    "home_saved": _all5(
        "Guardado: {line}. Aviso 60 e 30 dias antes do fim, para você comparar ofertas.",
        "Opgeslagen: {line}. Ik waarschuw je 60 en 30 dagen voor het einde, zodat je aanbiedingen kunt vergelijken.",
        "Saved: {line}. I'll remind you 60 and 30 days before the end so you can compare offers.",
        "Enregistré : {line}. Je te préviens 60 et 30 jours avant la fin pour comparer les offres.",
        "Gespeichert: {line}. Ich erinnere dich 60 und 30 Tage vor dem Ende, damit du Angebote vergleichen kannst.",
    ),
    "home_cancelled": _all5(
        "Cancelado, nada foi guardado.",
        "Geannuleerd, niets opgeslagen.",
        "Cancelled, nothing was saved.",
        "Annulé, rien n'a été enregistré.",
        "Abgebrochen, nichts gespeichert.",
    ),
    "home_expired": _all5(
        "Esse rascunho expirou. Mande o contrato de novo.",
        "Dit concept is verlopen. Stuur het contract opnieuw.",
        "That draft expired. Please send the contract again.",
        "Ce brouillon a expiré. Renvoie le contrat.",
        "Dieser Entwurf ist abgelaufen. Sende den Vertrag bitte neu.",
    ),
    "home_limit": _all5(
        "Você já tem {n} contratos guardados, o máximo. Apague algum antes de guardar outro.",
        "Je hebt al {n} contracten opgeslagen, het maximum. Verwijder er eerst een.",
        "You already have {n} contracts saved, the maximum. Delete one first.",
        "Tu as déjà {n} contrats enregistrés, le maximum. Supprime-en un d'abord.",
        "Du hast bereits {n} Verträge gespeichert, das Maximum. Lösche zuerst einen.",
    ),
    "home_bad": _all5(
        "Não entendi. Preciso do tipo (energia, gás ou internet), do fornecedor e da data de fim, por exemplo *contrato energia Vattenfall 120 por mês até 31/12/2027*.",
        "Dat begreep ik niet. Ik heb het type (energie, gas of internet), de leverancier en de einddatum nodig, bijvoorbeeld *contract energie Vattenfall 120 per maand tot 31/12/2027*.",
        "I didn't get that. I need the type (energy, gas or internet), the provider and the end date, for example *contract energy Vattenfall 120 a month until 31/12/2027*.",
        "Je n'ai pas compris. Il me faut le type (énergie, gaz ou internet), le fournisseur et la date de fin, par exemple *contrat énergie Vattenfall 120 par mois jusqu'au 31/12/2027*.",
        "Das habe ich nicht verstanden. Ich brauche die Art (Strom, Gas oder Internet), den Anbieter und das Enddatum, zum Beispiel *vertrag strom Vattenfall 120 im Monat bis 31/12/2027*.",
    ),
    "home_photo_bad": _all5(
        "Não consegui ler nesse arquivo o tipo, o fornecedor e a data de fim do contrato. Mande uma foto mais nítida ou escreva, por exemplo *contrato energia Vattenfall 120 por mês até 31/12/2027*.",
        "Ik kon in dit bestand het type, de leverancier en de einddatum van het contract niet lezen. Stuur een scherpere foto of typ bijvoorbeeld *contract energie Vattenfall 120 per maand tot 31/12/2027*.",
        "I couldn't read the type, provider and end date of the contract in that file. Send a sharper photo or type, for example, *contract energy Vattenfall 120 a month until 31/12/2027*.",
        "Je n'ai pas pu lire le type, le fournisseur et la date de fin du contrat. Envoie une photo plus nette ou écris par exemple *contrat énergie Vattenfall 120 par mois jusqu'au 31/12/2027*.",
        "Ich konnte Art, Anbieter und Enddatum des Vertrags nicht lesen. Sende ein schärferes Foto oder tippe zum Beispiel *vertrag strom Vattenfall 120 im Monat bis 31/12/2027*.",
    ),
    "home_list_title": _all5(
        "Contratos da casa:",
        "Contracten van het huis:",
        "Home contracts:",
        "Contrats de la maison :",
        "Verträge des Hauses:",
    ),
    "home_list_hint": _all5(
        "Para renovar: *renovei contrato energia até 31/12/2028*. Para apagar: *apaga contrato energia*.",
        "Verlengen: *verlengd contract energie tot 31/12/2028*. Verwijderen: *verwijder contract energie*.",
        "To renew: *renewed contract energy until 31/12/2028*. To delete: *delete contract energy*.",
        "Pour renouveler : *renouvelé contrat énergie jusqu'au 31/12/2028*. Pour supprimer : *supprime contrat énergie*.",
        "Verlängern: *verlängert vertrag strom bis 31/12/2028*. Löschen: *lösche vertrag strom*.",
    ),
    "home_list_empty": _all5(
        "Você não tem contratos guardados. Para guardar: *contrato energia Vattenfall 120 por mês até 31/12/2027* ou mande uma foto do contrato.",
        "Je hebt geen opgeslagen contracten. Opslaan: *contract energie Vattenfall 120 per maand tot 31/12/2027* of stuur een foto van het contract.",
        "You have no saved contracts. To save one: *contract energy Vattenfall 120 a month until 31/12/2027* or send a photo of the contract.",
        "Tu n'as aucun contrat enregistré. Pour en ajouter : *contrat énergie Vattenfall 120 par mois jusqu'au 31/12/2027* ou envoie une photo du contrat.",
        "Du hast keine gespeicherten Verträge. Zum Speichern: *vertrag strom Vattenfall 120 im Monat bis 31/12/2027* oder sende ein Foto des Vertrags.",
    ),
    "home_renewed": _all5(
        "Renovado: {line}. Os avisos recomeçam para a nova data.",
        "Verlengd: {line}. De herinneringen starten opnieuw voor de nieuwe datum.",
        "Renewed: {line}. Reminders restart for the new date.",
        "Renouvelé : {line}. Les rappels repartent pour la nouvelle date.",
        "Verlängert: {line}. Die Erinnerungen starten für das neue Datum neu.",
    ),
    "home_not_found": _all5(
        "Não achei esse contrato. Escreva *contratos* para ver a lista.",
        "Ik vond dat contract niet. Schrijf *contracten* voor de lijst.",
        "I couldn't find that contract. Write *contracts* for the list.",
        "Je n'ai pas trouvé ce contrat. Écris *contrats* pour la liste.",
        "Ich habe diesen Vertrag nicht gefunden. Schreib *verträge* für die Liste.",
    ),
    "home_ambiguous": _all5(
        "Achei mais de um: {names}. Diga o tipo, por exemplo energia, gás ou internet.",
        "Ik vond er meer dan één: {names}. Noem het type, bijvoorbeeld energie, gas of internet.",
        "I found more than one: {names}. Tell me the type, for example energy, gas or internet.",
        "J'en ai trouvé plusieurs : {names}. Précise le type, par exemple énergie, gaz ou internet.",
        "Ich habe mehrere gefunden: {names}. Nenne die Art, zum Beispiel Strom, Gas oder Internet.",
    ),
    "home_panel_hint": _all5(
        "Nenhum contrato guardado ainda. No chat, escreva: contrato energia Vattenfall 120 por mês até 31/12/2027.",
        "Nog geen contracten opgeslagen. Schrijf in de chat: contract energie Vattenfall 120 per maand tot 31/12/2027.",
        "No contracts saved yet. In the chat write: contract energy Vattenfall 120 a month until 31/12/2027.",
        "Aucun contrat enregistré. Dans le chat, écris : contrat énergie Vattenfall 120 par mois jusqu'au 31/12/2027.",
        "Noch keine Verträge gespeichert. Schreibe im Chat: vertrag strom Vattenfall 120 im Monat bis 31/12/2027.",
    ),
    "home_deleted": _all5(
        "Apaguei: {what}.",
        "Verwijderd: {what}.",
        "Deleted: {what}.",
        "Supprimé : {what}.",
        "Gelöscht: {what}.",
    ),
    "home_rem": _all5(
        "Aviso: o contrato de {kind} com {provider} termina em {d} (faltam {n} dias). É um bom momento para comparar ofertas ou renegociar. Confira no seu contrato o prazo de aviso.",
        "Let op: het contract voor {kind} bij {provider} eindigt op {d} (nog {n} dagen). Een goed moment om aanbiedingen te vergelijken of te heronderhandelen. Controleer de opzegtermijn in je contract.",
        "Heads up: your {kind} contract with {provider} ends on {d} ({n} days left). A good time to compare offers or renegotiate. Check the notice period in your contract.",
        "Attention : ton contrat {kind} chez {provider} se termine le {d} (encore {n} jours). Bon moment pour comparer les offres ou renégocier. Vérifie le préavis dans ton contrat.",
        "Achtung: dein {kind}-Vertrag bei {provider} endet am {d} (noch {n} Tage). Ein guter Zeitpunkt, Angebote zu vergleichen oder neu zu verhandeln. Prüfe die Kündigungsfrist in deinem Vertrag.",
    ),
}


# ── panel (the "Casa" tab) ───────────────────────────────────────────────────


async def panel_cards(
    session: AsyncSession, member: Member, lang: str, today: date
) -> list[dict[str, object]]:
    """Contracts of the home and their monthly cost; for everyone, with or without a partner."""
    rows = await _active(session, member)
    items = [
        {
            "kind": r.kind,
            "label": _kind_label(r.kind, lang),
            "provider": r.provider,
            "amount": float(r.amount) if r.amount is not None else None,
            "ends_on": r.ends_on.isoformat(),
            "days": (r.ends_on - today).days,
        }
        for r in rows
    ]
    contracts: dict[str, object] = {
        "id": "home_contracts",
        "empty": not items,
        "values": {"count": len(items)},
        "items": items,
        "phrase": None,
    }
    if not items:
        contracts["hint"] = {
            "key": "home_contracts",
            "text": _t("home_panel_hint", lang),
            "chat": None,
            "severity": "info",
        }
    cards = [contracts]
    priced = [i for i in items if i["amount"] is not None]
    if priced:
        total = sum(float(i["amount"]) for i in priced)  # type: ignore[arg-type]
        cards.append(
            {
                "id": "home_cost",
                "empty": False,
                "values": {"monthly": round(total, 2), "yearly": round(total * 12, 2)},
                "items": [
                    {"label": i["label"], "provider": i["provider"], "amount": i["amount"]}
                    for i in priced
                ],
                "phrase": None,
            }
        )
    return cards
