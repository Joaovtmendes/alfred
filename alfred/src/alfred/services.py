# ruff: noqa: E501
"""V2-36 — services and deadlines: note a service you paid for and keep the dates that matter.

Registered from a photo/PDF of the invoice (read by ``media_input``, the model's JSON is untrusted)
or from text ("guarda serviço: encanador, 180 €, garantia 12 meses"). A draft is shown first and
stored only as ``status='draft'`` for 15 minutes; the member confirms with a button. Dates are
computed by code. The module gives facts and dates, never legal advice; the image is never kept.
"""

from __future__ import annotations

import re
import unicodedata
import uuid
from calendar import monthrange
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from alfred.audit import audit
from alfred.clock import now_local
from alfred.models import Expense, Member, Message, ServiceRecord
from alfred.parsing import strip_accents, to_amount
from alfred.receipt import _EURO, _text, _total
from alfred.training import Reply
from alfred.validation import MAX_AMOUNT

MAX_ACTIVE = 50
DRAFT_TTL = timedelta(minutes=15)
WITHDRAWAL_DAYS = 14
MAX_WARRANTY_MONTHS = 120
WINDOW_HOURS = 24

_KEYS = re.compile(r"garanti|warrant|gewahr|servi|dienst")
_ONE = r"(?:servi[cç]o|service|dienst|garantia|warranty|garantie|gew[aä]hrleistung)"
_CREATE = re.compile(
    r"^(?:guarda|guardar|salva|salvar|registra|registrar|anota|save|store|add|bewaar|sla op|voeg toe|garde|enregistre|speichere|merke|trage ein)\s+"
    rf"{_ONE}\s*[:\-]?\s*(?P<rest>.+)$",
    re.I,
)
_LIST = {
    "garantias", "minhas garantias", "servicos", "meus servicos", "garantias ativas",
    "warranties", "my warranties", "services", "my services",
    "garanties", "mes garanties", "mes services", "garantien", "meine garantien",
    "diensten", "mijn diensten", "dienste", "meine dienste",
}  # fmt: skip
_DETAIL = re.compile(
    r"^(?:garantia|warranty|garantie|gewahrleistung|servico|service|dienst)\s+(?:do |da |de |of |van |von |du |des |d')?(?P<ref>[^?,]{2,40})$"
)
_DELETE = re.compile(
    rf"^(?:apaga|apagar|delete|remove|verwijder|supprime|loesche|loeschen)\s+(?:a |o |the |de |het |la |le |die |der |das )?{_ONE}\s+(?:do |da |de |of |van |von |du |des )?(?P<ref>.+)$"
)
_UNIT = r"(?P<unit>mes(?:es)?|months?|maand(?:en)?|mois|monat(?:e|en)?|anos?|years?|jaar|jaren|ans?|jahr(?:e|en)?)"
_WARRANTY = (
    re.compile(
        r"^(?:garantia|warranty|garantie|gewahrleistung)(?:\s+(?:de|of|van|von))?\s+(?P<n>\d{1,4})\s*"
        + _UNIT
        + "$"
    ),
    re.compile(
        r"^(?P<n>\d{1,4})\s*"
        + _UNIT
        + r"(?:\s+(?:de|of|van|von))?\s+(?:garantia|warranty|garantie|gewahrleistung)$"
    ),
)
_DATE = re.compile(
    r"^(?:dia\s+|op\s+|le\s+|am\s+|on\s+)?(\d{1,2})[/.-](\d{1,2})(?:[/.-](\d{2,4}))?$"
)
_AMOUNT = re.compile(r"^(?:€\s*)?(\d[\d.,]*)\s*(?:€|eur|euros?)?$")
_SPLIT = re.compile(r"\s*;\s*|,(?=\s|\D)")
_MARKUP = re.compile(r"[*_~`]")


def _t(key: str, lang: str, **kw: object) -> str:
    from alfred.conversation import _t as t

    return t(key, lang, **kw)


def _fmt(value: float) -> str:
    from alfred.conversation import _fmt_eur

    return _fmt_eur(float(value))


def add_months(day: date, months: int) -> date:
    """``day`` plus whole months, the day clamped to the end of a shorter month."""
    index = day.year * 12 + day.month - 1 + months
    year, month = divmod(index, 12)
    return date(year, month + 1, min(day.day, monthrange(year, month + 1)[1]))


def _clean(value: str, limit: int) -> str | None:
    out = " ".join(_MARKUP.sub("", value).split())[:limit].strip(" -,.")
    return out or None


def _months(n: str, unit: str) -> int | None:
    value = int(n) * (12 if unit.startswith(("a", "y", "j")) and not unit.startswith("ma") else 1)
    return value if 1 <= value <= MAX_WARRANTY_MONTHS else None


def _day(d: int, m: int, y: str | None, today: date) -> date | None:
    try:
        if y:
            yy = int(y)
            day = date(yy + 2000 if yy < 100 else yy, m, d)
        else:
            day = date(today.year, m, d)
            if day > today:
                day = date(today.year - 1, m, d)
    except ValueError:
        return None
    return day if day <= today and (today - day).days <= 3650 else None


def parse_register(rest: str, today: date) -> dict | None:
    """Fields from "provider, description, 180 €, garantia 12 meses, 10/09"; None when unusable."""
    texts: list[str] = []
    out: dict = {"amount": None, "warranty_months": None, "service_date": today}
    for part in _SPLIT.split(unicodedata.normalize("NFC", rest)):
        part = part.strip(" .!?")
        if not part:
            continue
        key = strip_accents(part).lower()
        warranty = next((m for p in _WARRANTY if (m := p.match(key))), None)
        if warranty:
            months = _months(warranty.group("n"), warranty.group("unit"))
            if months is None:
                return None
            out["warranty_months"] = months
        elif m := _DATE.match(key):
            day = _day(int(m.group(1)), int(m.group(2)), m.group(3), today)
            if day is None:
                return None
            out["service_date"] = day
        elif m := _AMOUNT.match(key):
            amount = to_amount(m.group(1))
            if amount is None or amount > MAX_AMOUNT:
                return None
            out["amount"] = round(amount, 2)
        else:
            texts.append(part)
    provider = _clean(texts[0], 60) if texts else None
    if provider is None:
        return None
    out["provider"] = provider
    out["description"] = _clean(texts[1], 80) if len(texts) > 1 else None
    return out


def record_from_reading(reading: dict, today: date) -> dict | None:
    """Fields from what the model read on an invoice; everything is checked here."""
    provider = _text(reading.get("provider"), 60)
    if provider is None:
        return None
    currency = reading.get("currency")
    euro = not (
        isinstance(currency, str) and currency.strip() and currency.strip().upper() not in _EURO
    )
    amount = _total(reading.get("total")) if euro else None
    day = today
    raw_day = reading.get("service_date")
    if isinstance(raw_day, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw_day.strip()):
        try:
            parsed = date.fromisoformat(raw_day.strip())
            if parsed <= today and (today - parsed).days <= 3650:
                day = parsed
        except ValueError:
            pass
    raw_months = reading.get("warranty_months")
    months = None
    if isinstance(raw_months, bool):
        raw_months = None
    if isinstance(raw_months, str) and raw_months.strip().isdigit():
        raw_months = int(raw_months.strip())
    if isinstance(raw_months, int) and 1 <= raw_months <= MAX_WARRANTY_MONTHS:
        months = raw_months
    return {
        "provider": provider,
        "description": _text(reading.get("description"), 80),
        "amount": amount,
        "service_date": day,
        "warranty_months": months,
    }


def _what(rec: ServiceRecord) -> str:
    return f"{rec.provider} ({rec.description})" if rec.description else rec.provider


def _key(text: str) -> str:
    return strip_accents(unicodedata.normalize("NFC", text)).lower().strip()


def _lines(rec: ServiceRecord, lang: str, today: date, *, left: bool = False) -> list[str]:
    out = [f"• {_what(rec)}", "• " + _t("svc_l_date", lang, d=f"{rec.service_date:%d/%m/%Y}")]
    if rec.amount is not None:
        out.append("• " + _t("svc_l_amount", lang, a=_fmt(rec.amount)))
    if rec.warranty_until:
        line = _t("svc_l_warranty", lang, d=f"{rec.warranty_until:%d/%m/%Y}", n=rec.warranty_months)
        if left:
            days = (rec.warranty_until - today).days
            line += " " + (_t("svc_l_left", lang, n=days) if days >= 0 else _t("svc_l_over", lang))
        out.append("• " + line)
    else:
        out.append("• " + _t("svc_l_nowarranty", lang))
    if rec.withdrawal_until:
        out.append("• " + _t("svc_l_wd", lang, d=f"{rec.withdrawal_until:%d/%m/%Y}"))
    return out


async def _create_draft(
    fields: dict, member: Member, lang: str, session: AsyncSession, today: date
) -> Reply:
    active = await session.scalar(
        select(func.count())
        .select_from(ServiceRecord)
        .where(ServiceRecord.member_id == member.id, ServiceRecord.status == "active")
    )
    if (active or 0) >= MAX_ACTIVE:
        return Reply(_t("svc_limit", lang, n=MAX_ACTIVE))
    await session.execute(
        delete(ServiceRecord).where(
            ServiceRecord.member_id == member.id, ServiceRecord.status == "draft"
        )
    )
    months = fields["warranty_months"]
    rec = ServiceRecord(
        id=uuid.uuid4(),
        member_id=member.id,
        kind="service",
        status="draft",
        provider=fields["provider"],
        description=fields["description"],
        service_date=fields["service_date"],
        amount=fields["amount"],
        warranty_months=months,
        warranty_until=add_months(fields["service_date"], months) if months else None,
    )
    session.add(rec)
    await session.flush()
    text = "\n".join([_t("svc_title", lang), *_lines(rec, lang, today), "", _t("svc_foot", lang)])
    buttons = [(f"svc_ok:{rec.id}", _t("btn_svc_ok", lang))]
    if rec.amount is not None:
        buttons.append((f"svc_exp:{rec.id}", _t("btn_svc_exp", lang)))
    buttons.append((f"svc_no:{rec.id}", _t("btn_svc_no", lang)))
    return Reply(text, buttons)


async def service_reply(
    reading: dict, member: Member, lang: str, session: AsyncSession, today: date
) -> Reply:
    fields = record_from_reading(reading, today)
    if fields is None:
        return Reply(_t("svc_no_provider", lang))
    return await _create_draft(fields, member, lang, session, today)


async def _active(session: AsyncSession, member: Member) -> list[ServiceRecord]:
    rows = await session.execute(
        select(ServiceRecord)
        .where(ServiceRecord.member_id == member.id, ServiceRecord.status == "active")
        .order_by(ServiceRecord.warranty_until.asc().nulls_last(), ServiceRecord.created_at)
        .limit(MAX_ACTIVE + 1)
    )
    return list(rows.scalars().all())


def _match(rows: list[ServiceRecord], ref: str) -> list[ServiceRecord]:
    key = _key(ref)
    exact = [r for r in rows if _key(r.provider) == key]
    if exact:
        return exact
    return [
        r
        for r in rows
        if key and (key in _key(r.provider) or (r.description and key in _key(r.description)))
    ]


async def handle_service_command(
    raw: str, body_plain: str, member: Member, lang: str, session: AsyncSession, today: date
) -> Reply | None:
    """None unless the message is clearly about service records (cheap exit first)."""
    plain = " ".join(body_plain.split()).strip(" .!?")
    if len(plain) > 200 or (plain not in _LIST and not _KEYS.search(plain)):
        return None
    raw_norm = unicodedata.normalize("NFC", " ".join(raw.split()).strip(" !?"))

    if plain in _LIST:
        return await _list(member, lang, session, today)
    if m := _CREATE.match(raw_norm):
        fields = parse_register(m.group("rest"), today)
        if fields is None:
            return Reply(_t("svc_bad", lang))
        return await _create_draft(fields, member, lang, session, today)
    if m := _DELETE.match(plain):
        rows = await _active(session, member)
        found = _match(rows, m.group("ref"))
        if not found:
            return Reply(_t("svc_not_found", lang))
        if len(found) > 1:
            return Reply(_t("svc_ambiguous", lang, names=", ".join(_what(r) for r in found[:5])))
        await session.delete(found[0])
        audit(session, "service_deleted", member.id)
        return Reply(_t("svc_deleted", lang, what=_what(found[0])))
    if m := _DETAIL.match(plain):
        found = _match(await _active(session, member), m.group("ref"))
        if not found:
            return None  # an ordinary sentence about warranties: not ours
        if len(found) > 1:
            return Reply(_t("svc_ambiguous", lang, names=", ".join(_what(r) for r in found[:5])))
        text = "\n".join([*_lines(found[0], lang, today, left=True), "", _t("svc_note", lang)])
        return Reply(text)
    return None


async def _list(member: Member, lang: str, session: AsyncSession, today: date) -> Reply:
    rows = await _active(session, member)
    if not rows:
        return Reply(_t("svc_list_empty", lang))
    lines = [_t("svc_list_title", lang) + "\n"]
    for r in rows[:MAX_ACTIVE]:
        if r.warranty_until:
            days = (r.warranty_until - today).days
            tail = (
                _t("svc_list_until", lang, d=f"{r.warranty_until:%d/%m/%Y}", n=days)
                if days >= 0
                else _t("svc_l_over", lang)
            )
        else:
            tail = _t("svc_l_nowarranty", lang)
        lines.append(f"• {_what(r)}: {tail}")
    lines.append("\n" + _t("svc_list_hint", lang))
    return Reply("\n".join(lines))


async def handle_service_button(
    action: str, raw_id: str, member: Member, lang: str, session: AsyncSession, today: date
) -> Reply:
    try:
        rec_id = uuid.UUID(raw_id)
    except ValueError:
        return Reply(_t("button_gone", lang))
    rec = await session.scalar(
        select(ServiceRecord)
        .where(ServiceRecord.id == rec_id, ServiceRecord.member_id == member.id)
        .with_for_update()
    )
    if rec is None:
        return Reply(_t("button_gone", lang))
    if action in ("svc_wd_yes", "svc_wd_no"):
        if rec.status != "active":
            return Reply(_t("button_gone", lang))
        if action == "svc_wd_no":
            return Reply(_t("svc_wd_skip", lang))
        rec.withdrawal_until = rec.service_date + timedelta(days=WITHDRAWAL_DAYS)
        session.add(rec)
        return Reply(_t("svc_wd_set", lang, d=f"{rec.withdrawal_until:%d/%m/%Y}"))
    if rec.status != "draft":
        return Reply(_t("button_gone", lang))
    if action == "svc_no":
        await session.delete(rec)
        return Reply(_t("svc_cancelled", lang))
    if rec.created_at < datetime.now(UTC) - DRAFT_TTL:
        await session.delete(rec)
        return Reply(_t("svc_expired", lang))
    rec.status = "active"
    session.add(rec)
    if action == "svc_exp" and rec.amount is not None:
        session.add(
            Expense(
                id=uuid.uuid4(),
                member_id=member.id,
                household_id=member.household_id,
                transaction_type="expense",
                amount=rec.amount,
                currency="EUR",
                merchant=rec.provider,
                category="overig",
                description=rec.description,
                expense_date=now_local() - timedelta(days=max((today - rec.service_date).days, 0)),
            )
        )
    audit(session, "service_saved", member.id)
    text = _t("svc_saved", lang, what=_what(rec))
    if (today - rec.service_date).days <= WITHDRAWAL_DAYS:
        return Reply(
            text + "\n\n" + _t("svc_wd_ask", lang),
            [
                (f"svc_wd_yes:{rec.id}", _t("btn_svc_wd_yes", lang)),
                (f"svc_wd_no:{rec.id}", _t("btn_svc_wd_no", lang)),
            ],
        )
    return Reply(text)


# ── cron side ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ServiceReminder:
    record_id: uuid.UUID
    which: str  # w30 | w7 | wd
    wa_phone: str
    lang: str
    text: str
    in_window: bool


async def due_reminders(session: AsyncSession, today: date, now: datetime) -> list[ServiceReminder]:
    rows = (
        await session.execute(
            select(ServiceRecord, Member.wa_phone, Member.language)
            .join(Member, ServiceRecord.member_id == Member.id)
            .where(ServiceRecord.status == "active", Member.consent_state == "accepted")
        )
    ).all()
    out: list[ServiceReminder] = []
    cutoff = now - timedelta(hours=WINDOW_HOURS)
    for rec, phone, language in rows:
        lang = language or "en"
        due: list[tuple[str, str, date]] = []
        if rec.warranty_until:
            days = (rec.warranty_until - today).days
            if 0 <= days <= 7 and not rec.sent_w7:
                due.append(("w7", "svc_rem_warranty", rec.warranty_until))
            elif 7 < days <= 30 and not rec.sent_w30:
                due.append(("w30", "svc_rem_warranty", rec.warranty_until))
        if rec.withdrawal_until and not rec.sent_wd:
            if 0 <= (rec.withdrawal_until - today).days <= 3:
                due.append(("wd", "svc_rem_withdrawal", rec.withdrawal_until))
        if not due:
            continue
        last_in = await session.scalar(
            select(func.max(Message.created_at)).where(
                Message.author_id == rec.member_id, Message.direction == "inbound"
            )
        )
        in_window = bool(last_in and last_in.astimezone(now.tzinfo) >= cutoff)
        for which, key, when in due:
            text = _t(key, lang, what=_what(rec), d=f"{when:%d/%m/%Y}", n=(when - today).days)
            out.append(ServiceReminder(rec.id, which, phone, lang, text, in_window))
    return out


async def mark_sent(session: AsyncSession, record_id: uuid.UUID, which: str) -> None:
    rec = await session.get(ServiceRecord, record_id)
    if rec is None:
        return
    if which == "w7":
        rec.sent_w7 = rec.sent_w30 = True
    elif which == "w30":
        rec.sent_w30 = True
    elif which == "wd":
        rec.sent_wd = True
    session.add(rec)


def _all5(pt: str, nl: str, en: str, fr: str, de: str) -> dict[str, str]:
    return {"pt": pt, "nl": nl, "en": en, "fr": fr, "de": de}


STRINGS: dict[str, dict[str, str]] = {
    "btn_svc_ok": _all5("Confirmar", "Bevestigen", "Confirm", "Confirmer", "Bestätigen"),
    "btn_svc_exp": _all5(
        "Confirmar + gasto",
        "Bevestig + uitgave",
        "Confirm + expense",
        "Confirmer + dépense",
        "Bestät. + Ausgabe",
    ),
    "btn_svc_no": _all5("Cancelar", "Annuleren", "Cancel", "Annuler", "Abbrechen"),
    "btn_svc_wd_yes": _all5(
        "Sim, online/fora",
        "Ja, online/buiten",
        "Yes, online/away",
        "Oui, en ligne/hors",
        "Ja, online/extern",
    ),
    "btn_svc_wd_no": _all5("Não", "Nee", "No", "Non", "Nein"),
    "svc_title": _all5(
        "Entendi este serviço:",
        "Dit heb ik begrepen:",
        "Here is the service I understood:",
        "Voici le service compris :",
        "Das habe ich verstanden:",
    ),
    "svc_foot": _all5(
        "Ainda não gravei nada. Para corrigir algo, mande de novo com os dados certos.",
        "Ik heb nog niets opgeslagen. Om iets te corrigeren, stuur het opnieuw met de juiste gegevens.",
        "Nothing is saved yet. To correct something, send it again with the right details.",
        "Rien n'est encore enregistré. Pour corriger, renvoie-le avec les bonnes informations.",
        "Noch nichts gespeichert. Zum Korrigieren sende es einfach neu mit den richtigen Angaben.",
    ),
    "svc_l_date": _all5("Data: {d}", "Datum: {d}", "Date: {d}", "Date : {d}", "Datum: {d}"),
    "svc_l_amount": _all5(
        "Valor: {a}", "Bedrag: {a}", "Amount: {a}", "Montant : {a}", "Betrag: {a}"
    ),
    "svc_l_warranty": _all5(
        "Garantia prometida até {d} ({n} meses)",
        "Beloofde garantie tot {d} ({n} maanden)",
        "Promised warranty until {d} ({n} months)",
        "Garantie promise jusqu'au {d} ({n} mois)",
        "Zugesagte Garantie bis {d} ({n} Monate)",
    ),
    "svc_l_nowarranty": _all5(
        "Sem garantia informada",
        "Geen garantie opgegeven",
        "No warranty given",
        "Aucune garantie indiquée",
        "Keine Garantie angegeben",
    ),
    "svc_l_left": _all5(
        "— faltam {n} dias",
        "— nog {n} dagen",
        "— {n} days left",
        "— encore {n} jours",
        "— noch {n} Tage",
    ),
    "svc_l_over": _all5(
        "— já terminou", "— is verlopen", "— has ended", "— est terminée", "— ist abgelaufen"
    ),
    "svc_l_wd": _all5(
        "Prazo de arrependimento de 14 dias até {d}",
        "Bedenktijd van 14 dagen tot {d}",
        "14-day cooling-off period until {d}",
        "Délai de rétractation de 14 jours jusqu'au {d}",
        "14 Tage Widerrufsfrist bis {d}",
    ),
    "svc_saved": _all5(
        "Guardado: {what}.",
        "Opgeslagen: {what}.",
        "Saved: {what}.",
        "Enregistré : {what}.",
        "Gespeichert: {what}.",
    ),
    "svc_wd_ask": _all5(
        "Você contratou online ou fora de uma loja? Nesses casos costuma haver 14 dias para desistir, e eu aviso 3 dias antes de acabar. Confira as condições do seu contrato.",
        "Heb je dit online of buiten een winkel afgesloten? Dan is er meestal 14 dagen bedenktijd en ik waarschuw je 3 dagen voor het einde. Controleer de voorwaarden van je contract.",
        "Did you book this online or outside a shop? Then there is usually a 14-day cooling-off period, and I'll remind you 3 days before it ends. Check your contract's terms.",
        "As-tu souscrit en ligne ou hors d'un magasin ? Il y a alors en général 14 jours pour se rétracter et je te préviens 3 jours avant la fin. Vérifie les conditions de ton contrat.",
        "Hast du das online oder außerhalb eines Geschäfts abgeschlossen? Dann gibt es meist 14 Tage Widerrufsfrist, und ich erinnere dich 3 Tage vor dem Ende. Prüfe die Bedingungen deines Vertrags.",
    ),
    "svc_wd_set": _all5(
        "Combinado. Aviso 3 dias antes de {d}.",
        "Afgesproken. Ik waarschuw je 3 dagen voor {d}.",
        "Done. I'll remind you 3 days before {d}.",
        "C'est noté. Je te préviens 3 jours avant le {d}.",
        "Alles klar. Ich erinnere dich 3 Tage vor dem {d}.",
    ),
    "svc_wd_skip": _all5(
        "Certo, sem prazo de arrependimento.",
        "Oké, zonder bedenktijd.",
        "OK, no cooling-off period tracked.",
        "D'accord, sans délai de rétractation.",
        "Okay, ohne Widerrufsfrist.",
    ),
    "svc_cancelled": _all5(
        "Cancelado, nada foi guardado.",
        "Geannuleerd, niets opgeslagen.",
        "Cancelled, nothing was saved.",
        "Annulé, rien n'a été enregistré.",
        "Abgebrochen, nichts gespeichert.",
    ),
    "svc_expired": _all5(
        "Esse rascunho expirou. Mande o serviço de novo.",
        "Dit concept is verlopen. Stuur de dienst opnieuw.",
        "That draft expired. Please send the service again.",
        "Ce brouillon a expiré. Renvoie le service.",
        "Dieser Entwurf ist abgelaufen. Sende den Service bitte neu.",
    ),
    "svc_limit": _all5(
        "Você já tem {n} serviços guardados, o máximo. Apague algum antes de guardar outro.",
        "Je hebt al {n} diensten opgeslagen, het maximum. Verwijder er eerst een.",
        "You already have {n} services saved, the maximum. Delete one first.",
        "Tu as déjà {n} services enregistrés, le maximum. Supprime-en un d'abord.",
        "Du hast bereits {n} Services gespeichert, das Maximum. Lösche zuerst einen.",
    ),
    "svc_bad": _all5(
        "Não entendi. Exemplo: *guarda serviço: encanador, 180 €, garantia 12 meses* (a data é hoje; para outra, acrescente *10/09*).",
        "Dat begreep ik niet. Voorbeeld: *bewaar dienst: loodgieter, 180 €, garantie 12 maanden* (datum is vandaag; voeg anders *10/09* toe).",
        "I didn't get that. Example: *save service: plumber, 180 €, warranty 12 months* (date is today; add *10/09* for another).",
        "Je n'ai pas compris. Exemple : *garde service : plombier, 180 €, garantie 12 mois* (la date est aujourd'hui ; ajoute *10/09* pour une autre).",
        "Das habe ich nicht verstanden. Beispiel: *speichere service: Klempner, 180 €, garantie 12 monate* (Datum ist heute; für ein anderes *10/09* ergänzen).",
    ),
    "svc_no_provider": _all5(
        "Não consegui ver quem prestou o serviço nesse arquivo. Mande uma foto mais nítida ou escreva, por exemplo *guarda serviço: encanador, 180 €, garantia 12 meses*.",
        "Ik kon niet zien wie de dienst leverde. Stuur een scherpere foto of typ bijvoorbeeld *bewaar dienst: loodgieter, 180 €, garantie 12 maanden*.",
        "I couldn't see who provided the service in that file. Send a sharper photo or type, for example, *save service: plumber, 180 €, warranty 12 months*.",
        "Je n'ai pas vu qui a fourni le service. Envoie une photo plus nette ou écris par exemple *garde service : plombier, 180 €, garantie 12 mois*.",
        "Ich konnte nicht erkennen, wer die Leistung erbracht hat. Sende ein schärferes Foto oder tippe zum Beispiel *speichere service: Klempner, 180 €, garantie 12 monate*.",
    ),
    "svc_list_title": _all5(
        "Seus serviços e garantias:",
        "Je diensten en garanties:",
        "Your services and warranties:",
        "Tes services et garanties :",
        "Deine Services und Garantien:",
    ),
    "svc_list_until": _all5(
        "garantia até {d} ({n} dias)",
        "garantie tot {d} ({n} dagen)",
        "warranty until {d} ({n} days)",
        "garantie jusqu'au {d} ({n} jours)",
        "Garantie bis {d} ({n} Tage)",
    ),
    "svc_list_hint": _all5(
        "Para ver o detalhe: *garantia encanador*. Para apagar: *apaga garantia encanador*.",
        "Detail bekijken: *garantie loodgieter*. Verwijderen: *verwijder garantie loodgieter*.",
        "For details: *warranty plumber*. To delete: *delete warranty plumber*.",
        "Pour le détail : *garantie plombier*. Pour supprimer : *supprime garantie plombier*.",
        "Details: *garantie Klempner*. Löschen: *lösche garantie Klempner*.",
    ),
    "svc_list_empty": _all5(
        "Você não tem serviços guardados. Para guardar: *guarda serviço: encanador, 180 €, garantia 12 meses* ou mande uma foto da nota.",
        "Je hebt geen opgeslagen diensten. Opslaan: *bewaar dienst: loodgieter, 180 €, garantie 12 maanden* of stuur een foto van de factuur.",
        "You have no saved services. To save one: *save service: plumber, 180 €, warranty 12 months* or send a photo of the invoice.",
        "Tu n'as aucun service enregistré. Pour en ajouter : *garde service : plombier, 180 €, garantie 12 mois* ou envoie une photo de la facture.",
        "Du hast keine gespeicherten Services. Zum Speichern: *speichere service: Klempner, 180 €, garantie 12 monate* oder sende ein Foto der Rechnung.",
    ),
    "svc_not_found": _all5(
        "Não achei esse serviço. Escreva *garantias* para ver a lista.",
        "Ik vond die dienst niet. Schrijf *garanties* voor de lijst.",
        "I couldn't find that service. Write *warranties* for the list.",
        "Je n'ai pas trouvé ce service. Écris *garanties* pour la liste.",
        "Ich habe diesen Service nicht gefunden. Schreib *garantien* für die Liste.",
    ),
    "svc_ambiguous": _all5(
        "Achei mais de um: {names}. Diga o nome completo.",
        "Ik vond er meer dan één: {names}. Geef de volledige naam.",
        "I found more than one: {names}. Tell me the full name.",
        "J'en ai trouvé plusieurs : {names}. Donne le nom complet.",
        "Ich habe mehrere gefunden: {names}. Nenne den vollständigen Namen.",
    ),
    "svc_deleted": _all5(
        "Apaguei: {what}.",
        "Verwijderd: {what}.",
        "Deleted: {what}.",
        "Supprimé : {what}.",
        "Gelöscht: {what}.",
    ),
    "svc_note": _all5(
        "Se algo der errado, avise a empresa logo que perceber o problema e guarde a nota original. Informação geral, não é aconselhamento jurídico.",
        "Gaat er iets mis, meld het dan zo snel mogelijk bij het bedrijf en bewaar de originele factuur. Algemene informatie, geen juridisch advies.",
        "If something goes wrong, tell the company as soon as you notice the problem and keep the original invoice. General information, not legal advice.",
        "Si quelque chose ne va pas, préviens l'entreprise dès que tu le remarques et garde la facture originale. Information générale, pas un conseil juridique.",
        "Wenn etwas schiefgeht, melde es dem Unternehmen, sobald du das Problem bemerkst, und bewahre die Originalrechnung auf. Allgemeine Information, keine Rechtsberatung.",
    ),
    "svc_rem_warranty": _all5(
        "Aviso: a garantia prometida de {what} termina em {d} (faltam {n} dias). Se houver algum problema, é a hora de falar com a empresa. Guarde a nota original.",
        "Let op: de beloofde garantie van {what} loopt af op {d} (nog {n} dagen). Is er een probleem, neem dan nu contact op met het bedrijf. Bewaar de originele factuur.",
        "Heads up: the promised warranty on {what} ends on {d} ({n} days left). If there is a problem, now is the time to contact the company. Keep the original invoice.",
        "Attention : la garantie promise pour {what} se termine le {d} (encore {n} jours). En cas de problème, c'est le moment de contacter l'entreprise. Garde la facture originale.",
        "Achtung: die zugesagte Garantie für {what} endet am {d} (noch {n} Tage). Bei einem Problem melde dich jetzt beim Unternehmen. Bewahre die Originalrechnung auf.",
    ),
    "svc_rem_withdrawal": _all5(
        "Aviso: o prazo de 14 dias para desistir de {what} termina em {d} (faltam {n} dias). Se quiser desistir, é agora. Confira as condições do seu contrato.",
        "Let op: de bedenktijd van 14 dagen voor {what} eindigt op {d} (nog {n} dagen). Wil je annuleren, dan is dat nu. Controleer de voorwaarden van je contract.",
        "Heads up: the 14-day cooling-off period for {what} ends on {d} ({n} days left). If you want to cancel, now is the time. Check your contract's terms.",
        "Attention : le délai de rétractation de 14 jours pour {what} se termine le {d} (encore {n} jours). Si tu veux te rétracter, c'est maintenant. Vérifie les conditions de ton contrat.",
        "Achtung: die 14-tägige Widerrufsfrist für {what} endet am {d} (noch {n} Tage). Wenn du widerrufen willst, ist jetzt der Zeitpunkt. Prüfe die Bedingungen deines Vertrags.",
    ),
}
