# ruff: noqa: E501
"""V2-21 — Dutch calendar: the yearly deadlines a person in the Netherlands should not miss.

Dates were checked against official pages on 2026-10-07:

- Health insurance (rijksoverheid.nl, "Overstappen zorgverzekeraar"): sign the new policy by
  31 December and the new insurer cancels the old one by itself; someone who ends up without a
  policy can still take one until 1 February, insured from 1 January.
- Income tax return (belastingdienst.nl, "Wanneer moet mijn aangifte inkomstenbelasting binnen
  zijn?"): the deadline is the date in the letter, "vaak 1 mei"; an extension has to be requested
  before that date and gives 4 months.
- Quarterly BTW (belastingdienst.nl, "Wanneer moeten mijn btw-aangifte en mijn betaling binnen
  zijn?"): return and payment by the end of the month after the quarter (31/1, 30/4, 31/7, 31/10).
- Tikkie: no official expiry; the app only nudges the sender after one week.

The module gives dates and a pointer to the official source, never advice. Reminders are opt-in
("ligar avisos de prazos"); the cron sends them as plain text inside the 24 h window, otherwise
through the template ``alfred_calendar_reminder`` behind a flag (default off).
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from alfred.audit import audit
from alfred.models import CalendarOptIn, Member, Message
from alfred.training import Reply

WINDOW_HOURS = 24
BTW_LEAD_DAYS = 10
KEEP_KEYS = 12

_INTENT = r"(?:prazo|prazos|quando|ate quando|deadline|deadlines|when|until when|termijn|wanneer|tot wanneer|echeance|quand|jusqu'a quand|frist|wann|bis wann|trocar|mudar|switch|change|overstap\w*|wissel\w*|changer|wechsel\w*|data|date|datum)"
_TOPIC = {
    "health": re.compile(
        r"seguro[- ]saude|zorgverzekering|health insurance|assurance maladie|krankenversicherung|zorgverzekeraar"
    ),
    "tax": re.compile(
        r"declaracao (?:de |do )?imposto|imposto de renda|belastingaangifte|aangifte inkomstenbelasting|tax return|income tax|declaration d'impots?|declaration de revenus|steuererklaerung|einkommensteuer"
    ),
    "btw": re.compile(r"\bbtw\b|\btva\b|\bmwst\b|\bvat\b|\bumsatzsteuer\b"),
}
_LIST = {
    "prazos", "prazos holandeses", "prazos da holanda", "prazos nl", "calendario holandes",
    "calendario da holanda", "datas importantes", "deadlines", "deadlines nl", "dutch deadlines",
    "dutch calendar", "important dates", "termijnen", "belangrijke data", "belangrijke datums",
    "nederlandse deadlines", "echeances", "echeances pays-bas", "dates importantes",
    "fristen", "niederlande fristen", "wichtige termine", "wichtige fristen",
}  # fmt: skip
_REM = re.compile(
    r"(?:avisos?|lembretes?|reminders?|herinneringen?|rappels?|erinnerungen?)\s+(?:de |of |van |des |der |von |do |dos )?(?:prazos|deadlines|termijnen|echeances|fristen)"
)
_ON = re.compile(
    r"\b(?:ligar|liga|ativar|ativa|activar|turn on|enable|activate|zet aan|aanzetten|activeer|activer|active|einschalten|aktiviere|schalte ein)\b"
)
_OFF = re.compile(
    r"\b(?:desligar|desliga|desativar|desativa|desactivar|turn off|disable|deactivate|zet uit|uitzetten|deactiveer|desactiver|desactive|ausschalten|deaktiviere|schalte aus)\b"
)
_TIKKIE = re.compile(r"\btikkie\b")


def _t(key: str, lang: str, **kw: object) -> str:
    from alfred.conversation import _t as t

    return t(key, lang, **kw)


# ── pure date rules ───────────────────────────────────────────────────────────


def health_deadline(today: date) -> date:
    """The 31 December on or after ``today`` (the day to have signed the new policy)."""
    return date(today.year, 12, 31)


def tax_deadline(today: date) -> date:
    """The usual 1 May: this year's until it has passed, then next year's."""
    this = date(today.year, 5, 1)
    return this if today <= this else date(today.year + 1, 5, 1)


def btw_deadline(today: date) -> date:
    """Next quarterly return deadline: end of the month after the quarter (31/1, 30/4, 31/7, 31/10)."""
    for y in (today.year, today.year + 1):
        for month, day in ((1, 31), (4, 30), (7, 31), (10, 31)):
            d = date(y, month, day)
            if d >= today:
                return d
    raise AssertionError("unreachable")  # pragma: no cover


@dataclass(frozen=True)
class Event:
    key: str  # unique per occurrence, stored once sent
    topic: str  # health | tax | btw
    deadline: date
    stage: str  # nov | dec | tax | btw


def events_due(today: date) -> list[Event]:
    """Reminders that apply today (each ``key`` is sent at most once per person)."""
    y = today.year
    out: list[Event] = []
    if date(y, 11, 1) <= today <= date(y, 11, 30):
        out.append(Event(f"health-{y}-nov", "health", date(y, 12, 31), "nov"))
    if date(y, 12, 15) <= today <= date(y, 12, 31):
        out.append(Event(f"health-{y}-dec", "health", date(y, 12, 31), "dec"))
    if date(y, 4, 15) <= today <= date(y, 4, 30):
        out.append(Event(f"tax-{y}", "tax", date(y, 5, 1), "tax"))
    for month, day in ((1, 31), (4, 30), (7, 31), (10, 31)):
        d = date(y, month, day)
        if d - timedelta(days=BTW_LEAD_DAYS) <= today <= d:
            out.append(Event(f"btw-{d.isoformat()}", "btw", d, "btw"))
    return out


def _days(deadline: date, today: date) -> int:
    return (deadline - today).days


def _d(value: date) -> str:
    return f"{value:%d/%m/%Y}"


# ── replies ───────────────────────────────────────────────────────────────────


def topic_text(topic: str, lang: str, today: date) -> str:
    if topic == "health":
        d = health_deadline(today)
        return _t("cal_health", lang, d=_d(d), n=_days(d, today))
    if topic == "tax":
        d = tax_deadline(today)
        return _t("cal_tax", lang, d=_d(d), n=_days(d, today))
    d = btw_deadline(today)
    return _t("cal_btw", lang, d=_d(d), n=_days(d, today))


def overview(lang: str, today: date) -> str:
    parts = [_t("cal_title", lang)]
    parts += [f"• {topic_text(t, lang, today)}" for t in ("health", "tax", "btw")]
    parts.append(f"• {_t('cal_tikkie', lang)}")
    parts.append("\n" + _t("cal_hint", lang))
    return "\n".join(parts)


def _event_text(ev: Event, lang: str, today: date) -> str:
    n = max(_days(ev.deadline, today), 0)
    return _t(f"cal_rem_{ev.stage}", lang, d=_d(ev.deadline), n=n)


def _topic_of(plain: str) -> str | None:
    if not re.search(_INTENT, plain):
        return None
    for topic, rx in _TOPIC.items():
        if rx.search(plain):
            return topic
    return None


async def _optin(session: AsyncSession, member: Member) -> CalendarOptIn | None:
    return await session.scalar(select(CalendarOptIn).where(CalendarOptIn.member_id == member.id))


async def handle_calendar_command(
    body_plain: str, member: Member, lang: str, session: AsyncSession, today: date
) -> Reply | None:
    """None unless the message is clearly about Dutch deadlines (cheap exit first)."""
    plain = " ".join(body_plain.split()).strip(" .!?")
    if not plain or len(plain) > 120:
        return None
    if _REM.search(plain):
        row = await _optin(session, member)
        if _OFF.search(plain):
            if row is not None:
                await session.delete(row)
                audit(session, "calendar_optout", member.id)
            return Reply(_t("cal_off", lang))
        if _ON.search(plain):
            if row is None:
                session.add(CalendarOptIn(member_id=member.id, sent_keys=""))
                audit(session, "calendar_optin", member.id)
            return Reply(_t("cal_on", lang))
        return Reply(_t("cal_status_on" if row is not None else "cal_status_off", lang))
    if plain in _LIST:
        return Reply(overview(lang, today))
    topic = _topic_of(plain)
    if topic is not None:
        return Reply(topic_text(topic, lang, today) + "\n\n" + _t("cal_hint", lang))
    if _TIKKIE.search(plain) and re.search(_INTENT, plain):
        return Reply(_t("cal_tikkie", lang))
    return None


# ── reminders (cron) ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CalendarReminder:
    member_id: uuid.UUID
    key: str
    wa_phone: str
    lang: str
    text: str
    in_window: bool


async def due_reminders(
    session: AsyncSession, today: date, now: datetime
) -> list[CalendarReminder]:
    events = events_due(today)
    if not events:
        return []
    rows = (
        await session.execute(
            select(CalendarOptIn, Member.wa_phone, Member.language)
            .join(Member, CalendarOptIn.member_id == Member.id)
            .where(Member.consent_state == "accepted")
        )
    ).all()
    out: list[CalendarReminder] = []
    cutoff = now - timedelta(hours=WINDOW_HOURS)
    for opt, phone, language in rows:
        sent = set(filter(None, opt.sent_keys.split(",")))
        lang = language or "en"
        last_in = await session.scalar(
            select(func.max(Message.created_at)).where(
                Message.author_id == opt.member_id, Message.direction == "inbound"
            )
        )
        in_window = bool(last_in and last_in.astimezone(now.tzinfo) >= cutoff)
        for ev in events:
            if ev.key in sent:
                continue
            out.append(
                CalendarReminder(
                    opt.member_id, ev.key, phone, lang, _event_text(ev, lang, today), in_window
                )
            )
    return out


async def mark_sent(session: AsyncSession, member_id: uuid.UUID, key: str) -> None:
    opt = await session.scalar(select(CalendarOptIn).where(CalendarOptIn.member_id == member_id))
    if opt is None:
        return
    keys = [k for k in opt.sent_keys.split(",") if k]
    if key not in keys:
        keys.append(key)
    opt.sent_keys = ",".join(keys[-KEEP_KEYS:])
    session.add(opt)


def _all5(pt: str, nl: str, en: str, fr: str, de: str) -> dict[str, str]:
    return {"pt": pt, "nl": nl, "en": en, "fr": fr, "de": de}


STRINGS: dict[str, dict[str, str]] = {
    "cal_title": _all5(
        "Prazos que costumam importar nos Países Baixos:",
        "Deadlines die in Nederland vaak tellen:",
        "Deadlines that often matter in the Netherlands:",
        "Échéances qui comptent souvent aux Pays-Bas :",
        "Fristen, die in den Niederlanden oft wichtig sind:",
    ),
    "cal_health": _all5(
        "Seguro-saúde: para valer em 1º de janeiro, contrate o novo até {d} (faltam {n} dias); o novo seguro cancela o antigo sozinho. Quem ficar sem seguro ainda pode contratar até 1º de fevereiro, com cobertura desde 1º de janeiro (confira no site do governo).",
        "Zorgverzekering: sluit uiterlijk op {d} een nieuwe af (nog {n} dagen) om per 1 januari over te stappen; de nieuwe verzekeraar zegt de oude zelf op. Wie zonder verzekering blijft, kan tot 1 februari nog een polis nemen, met dekking vanaf 1 januari (controleer op rijksoverheid.nl).",
        "Health insurance: to switch from 1 January, sign the new policy by {d} ({n} days left); the new insurer cancels the old one for you. If you end up uninsured you can still take a policy until 1 February, covered from 1 January (check the government site).",
        "Assurance maladie : pour changer au 1er janvier, souscris la nouvelle au plus tard le {d} (encore {n} jours) ; le nouvel assureur résilie l'ancienne pour toi. Sans assurance, on peut encore en prendre une jusqu'au 1er février, avec couverture depuis le 1er janvier (vérifie sur le site du gouvernement).",
        "Krankenversicherung: Für einen Wechsel zum 1. Januar schließt du die neue bis spätestens {d} ab (noch {n} Tage); der neue Versicherer kündigt die alte für dich. Wer ohne Versicherung bleibt, kann bis 1. Februar noch eine abschließen, mit Schutz ab 1. Januar (bitte auf der Regierungsseite prüfen).",
    ),
    "cal_tax": _all5(
        "Declaração de imposto de renda: o prazo costuma ser 1º de maio ({d}, faltam {n} dias), mas vale a data da carta da Belastingdienst. Dá para pedir adiamento antes do prazo (costuma dar 4 meses a mais).",
        "Aangifte inkomstenbelasting: de datum is vaak 1 mei ({d}, nog {n} dagen), maar de datum in je brief van de Belastingdienst geldt. Uitstel vraag je aan vóór die datum (meestal 4 maanden extra).",
        "Income tax return: the deadline is often 1 May ({d}, {n} days left), but the date in your Belastingdienst letter is the one that counts. An extension has to be requested before it (usually 4 extra months).",
        "Déclaration d'impôt sur le revenu : l'échéance est souvent le 1er mai ({d}, encore {n} jours), mais c'est la date de ta lettre de la Belastingdienst qui compte. Un report se demande avant cette date (en général 4 mois de plus).",
        "Einkommensteuererklärung: Die Frist ist oft der 1. Mai ({d}, noch {n} Tage); maßgeblich ist aber das Datum in deinem Brief der Belastingdienst. Aufschub muss vor diesem Datum beantragt werden (meist 4 Monate mehr).",
    ),
    "cal_btw": _all5(
        "BTW trimestral (para quem tem empresa): declaração e pagamento até o fim do mês seguinte ao trimestre. Próximo prazo: {d} (faltam {n} dias). Quem declara por mês tem o fim de cada mês seguinte.",
        "Kwartaal-btw (voor ondernemers): aangifte en betaling uiterlijk aan het einde van de maand na het kwartaal. Volgende datum: {d} (nog {n} dagen). Wie per maand aangifte doet, heeft het einde van de volgende maand.",
        "Quarterly BTW (for business owners): return and payment by the end of the month after the quarter. Next deadline: {d} ({n} days left). Monthly filers have the end of each following month.",
        "TVA (BTW) trimestrielle (pour les entrepreneurs) : déclaration et paiement avant la fin du mois suivant le trimestre. Prochaine échéance : {d} (encore {n} jours). Les déclarants mensuels ont la fin de chaque mois suivant.",
        "Quartals-BTW (für Unternehmer): Erklärung und Zahlung bis zum Ende des Monats nach dem Quartal. Nächste Frist: {d} (noch {n} Tage). Monatliche Melder haben das Ende des jeweils folgenden Monats.",
    ),
    "cal_tikkie": _all5(
        "Tikkie: não há prazo oficial; o app só lembra quem pediu depois de 1 semana sem resposta. Diga 'quem me deve' e eu preparo o texto para você copiar.",
        "Tikkie: er is geen officiële termijn; de app geeft de afzender pas na 1 week een seintje als niemand heeft betaald. Zeg 'wie moet mij nog betalen' en ik maak de tekst om te kopiëren.",
        "Tikkie: there is no official deadline; the app only nudges the sender after 1 week without a response. Say 'who owes me' and I will prepare the text for you to copy.",
        "Tikkie : il n'y a pas de délai officiel ; l'appli ne relance l'expéditeur qu'après 1 semaine sans réponse. Dis 'qui me doit' et je prépare le texte à copier.",
        "Tikkie: Es gibt keine offizielle Frist; die App erinnert den Absender erst nach 1 Woche ohne Antwort. Sag 'wer schuldet mir', dann bereite ich den Text zum Kopieren vor.",
    ),
    "cal_hint": _all5(
        "Quer receber avisos? Diga 'ligar avisos de prazos'. São datas de fontes oficiais; confira na fonte antes de decidir. Não é aconselhamento.",
        "Wil je herinneringen? Zeg 'zet herinneringen deadlines aan'. Dit zijn data uit officiële bronnen; controleer ze bij de bron. Geen advies.",
        "Want reminders? Say 'turn on deadline reminders'. These are dates from official sources; check the source before deciding. Not advice.",
        "Des rappels ? Dis 'activer rappels échéances'. Ce sont des dates de sources officielles ; vérifie à la source. Ce n'est pas un conseil.",
        "Erinnerungen? Sag 'Erinnerungen Fristen einschalten'. Das sind Daten aus offiziellen Quellen; bitte an der Quelle prüfen. Keine Beratung.",
    ),
    "cal_on": _all5(
        "Pronto: vou avisar sobre seguro-saúde (novembro e dezembro), declaração de imposto (fim de abril) e BTW trimestral (10 dias antes). Para desligar, diga 'desligar avisos de prazos'.",
        "Klaar: ik waarschuw voor de zorgverzekering (november en december), de belastingaangifte (eind april) en kwartaal-btw (10 dagen vooraf). Uitzetten: 'zet herinneringen deadlines uit'.",
        "Done: I will remind you about health insurance (November and December), the tax return (end of April) and quarterly BTW (10 days before). To stop, say 'turn off deadline reminders'.",
        "C'est fait : je te préviendrai pour l'assurance maladie (novembre et décembre), la déclaration d'impôt (fin avril) et la TVA trimestrielle (10 jours avant). Pour arrêter : 'désactiver rappels échéances'.",
        "Erledigt: Ich erinnere dich an die Krankenversicherung (November und Dezember), die Steuererklärung (Ende April) und die Quartals-BTW (10 Tage vorher). Zum Beenden: 'Erinnerungen Fristen ausschalten'.",
    ),
    "cal_off": _all5(
        "Combinado, não vou mais enviar avisos de prazos. Você ainda pode perguntar 'prazos' quando quiser.",
        "Prima, ik stuur geen deadline-herinneringen meer. Je kunt altijd 'deadlines' vragen.",
        "Done, no more deadline reminders. You can still ask 'deadlines' any time.",
        "C'est noté, plus de rappels d'échéances. Tu peux toujours demander 'échéances'.",
        "Erledigt, keine Fristen-Erinnerungen mehr. Du kannst jederzeit 'Fristen' fragen.",
    ),
    "cal_status_on": _all5(
        "Os avisos de prazos estão ligados. Para desligar, diga 'desligar avisos de prazos'.",
        "De deadline-herinneringen staan aan. Uitzetten: 'zet herinneringen deadlines uit'.",
        "Deadline reminders are on. To stop, say 'turn off deadline reminders'.",
        "Les rappels d'échéances sont activés. Pour arrêter : 'désactiver rappels échéances'.",
        "Die Fristen-Erinnerungen sind eingeschaltet. Zum Beenden: 'Erinnerungen Fristen ausschalten'.",
    ),
    "cal_status_off": _all5(
        "Os avisos de prazos estão desligados. Para ligar, diga 'ligar avisos de prazos'.",
        "De deadline-herinneringen staan uit. Aanzetten: 'zet herinneringen deadlines aan'.",
        "Deadline reminders are off. To start, say 'turn on deadline reminders'.",
        "Les rappels d'échéances sont désactivés. Pour les activer : 'activer rappels échéances'.",
        "Die Fristen-Erinnerungen sind ausgeschaltet. Zum Einschalten: 'Erinnerungen Fristen einschalten'.",
    ),
    "cal_rem_nov": _all5(
        "Aviso: é época de revisar o seguro-saúde. Para trocar em 1º de janeiro, contrate o novo até {d}. As datas vêm do site do governo; confira lá. Para parar: 'desligar avisos de prazos'.",
        "Herinnering: tijd om je zorgverzekering te bekijken. Wil je per 1 januari overstappen, sluit dan uiterlijk op {d} een nieuwe af. Data van de overheid; controleer ze daar. Stoppen: 'zet herinneringen deadlines uit'.",
        "Reminder: time to review your health insurance. To switch from 1 January, sign the new policy by {d}. Dates come from the government site; check there. To stop: 'turn off deadline reminders'.",
        "Rappel : il est temps de revoir ton assurance maladie. Pour changer au 1er janvier, souscris la nouvelle avant le {d}. Dates du site du gouvernement ; vérifie-les là-bas. Pour arrêter : 'désactiver rappels échéances'.",
        "Erinnerung: Zeit, die Krankenversicherung zu prüfen. Für einen Wechsel zum 1. Januar schließt du die neue bis {d} ab. Die Daten stammen von der Regierungsseite; bitte dort prüfen. Beenden: 'Erinnerungen Fristen ausschalten'.",
    ),
    "cal_rem_dec": _all5(
        "Aviso: faltam {n} dias ({d}) para contratar um novo seguro-saúde que valha a partir de 1º de janeiro. Se não quer trocar, não precisa fazer nada. Confira no site do governo.",
        "Herinnering: nog {n} dagen ({d}) om een nieuwe zorgverzekering af te sluiten die per 1 januari ingaat. Wil je niet overstappen, dan hoef je niets te doen. Controleer op rijksoverheid.nl.",
        "Reminder: {n} days left ({d}) to sign a new health insurance that starts on 1 January. If you do not want to switch, you do not need to do anything. Check the government site.",
        "Rappel : encore {n} jours ({d}) pour souscrire une nouvelle assurance maladie valable au 1er janvier. Si tu ne veux pas changer, rien à faire. Vérifie sur le site du gouvernement.",
        "Erinnerung: noch {n} Tage ({d}), um eine neue Krankenversicherung ab 1. Januar abzuschließen. Wenn du nicht wechseln willst, musst du nichts tun. Bitte auf der Regierungsseite prüfen.",
    ),
    "cal_rem_tax": _all5(
        "Aviso: a declaração de imposto de renda costuma vencer em 1º de maio. Veja a data na carta da Belastingdienst; quem precisar de mais tempo pede adiamento antes do prazo.",
        "Herinnering: de aangifte inkomstenbelasting moet vaak vóór 1 mei binnen zijn. Kijk naar de datum in je brief van de Belastingdienst; uitstel vraag je vóór de datum aan.",
        "Reminder: the income tax return is often due on 1 May. Check the date in your Belastingdienst letter; if you need more time, request an extension before the deadline.",
        "Rappel : la déclaration d'impôt est souvent due le 1er mai. Vérifie la date dans ta lettre de la Belastingdienst ; pour plus de temps, demande un report avant l'échéance.",
        "Erinnerung: Die Einkommensteuererklärung ist oft bis 1. Mai fällig. Prüf das Datum in deinem Brief der Belastingdienst; für mehr Zeit beantragst du Aufschub vor der Frist.",
    ),
    "cal_rem_btw": _all5(
        "Aviso: o BTW trimestral vence em {d} (faltam {n} dias), declaração e pagamento. Vale para quem declara por trimestre.",
        "Herinnering: de kwartaal-btw moet uiterlijk {d} binnen zijn (nog {n} dagen), aangifte en betaling. Geldt voor wie per kwartaal aangifte doet.",
        "Reminder: the quarterly BTW is due on {d} ({n} days left), return and payment. It applies if you file per quarter.",
        "Rappel : la TVA (BTW) trimestrielle est due le {d} (encore {n} jours), déclaration et paiement. Valable si tu déclares par trimestre.",
        "Erinnerung: die Quartals-BTW ist am {d} fällig (noch {n} Tage), Erklärung und Zahlung. Gilt, wenn du vierteljährlich meldest.",
    ),
}
