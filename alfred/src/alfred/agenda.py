"""V2-06 — agenda: appointments with a date and time, a reminder before, and a view of what is next.

Dates and times are resolved by rules (``parse_when``, accent-free lower-case input, five
languages); no LLM. A create needs BOTH an explicit date word and a time of day, so ordinary
messages ("corri às 7h", "pizza 18 ontem") never turn into appointments by accident.
Reminders are sent by the cron (``pending_reminders``): inside the 24 h window as text, outside
only with the approved template ``alfred_appointment_reminder`` once its flag is on.
"""

# ruff: noqa: E501
from __future__ import annotations

import calendar
import re
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from alfred.audit import audit
from alfred.clock import _MONTHS, local_tz, now_local, to_local, weekday_abbr
from alfred.models import Appointment, Member, Message
from alfred.parsing import has_money, strip_accents

DEFAULT_REMIND_MINUTES = 60
MAX_REMIND_MINUTES = 7 * 24 * 60
MAX_ACTIVE = 200
LIST_LIMIT = 15
WINDOW_HOURS = 24
CONFLICT_MINUTES = 59

# ── date / time rules ────────────────────────────────────────────────────────

_WEEKDAYS: dict[str, int] = {}
for _i, _names in enumerate(
    (
        ("segunda", "segunda-feira", "monday", "mon", "maandag", "lundi", "montag"),
        ("terca", "terca-feira", "tuesday", "tue", "dinsdag", "mardi", "dienstag"),
        ("quarta", "quarta-feira", "wednesday", "wed", "woensdag", "mercredi", "mittwoch"),
        ("quinta", "quinta-feira", "thursday", "thu", "donderdag", "jeudi", "donnerstag"),
        ("sexta", "sexta-feira", "friday", "fri", "vrijdag", "vendredi", "freitag"),
        ("sabado", "saturday", "sat", "zaterdag", "samedi", "samstag"),
        ("domingo", "sunday", "sun", "zondag", "dimanche", "sonntag"),
    )
):
    for _n in _names:
        _WEEKDAYS[_n] = _i

_MONTH_NUM: dict[str, int] = {}
for _lang_months in _MONTHS.values():
    for _idx, _name in enumerate(_lang_months, 1):
        _MONTH_NUM[strip_accents(_name)] = _idx

_REL_DAYS: dict[str, int] = {
    "hoje": 0, "today": 0, "vandaag": 0, "aujourd'hui": 0, "aujourdhui": 0, "heute": 0,
    "amanha": 1, "tomorrow": 1, "morgen": 1, "demain": 1,
    "depois de amanha": 2, "day after tomorrow": 2, "overmorgen": 2, "apres-demain": 2,
    "apres demain": 2, "uebermorgen": 2, "ubermorgen": 2,
}  # fmt: skip

_UNITS = r"(?:h|hs|hr|hrs|hora|horas|hour|hours|uur|uren|heure|heures|stunde|stunden|std|min|mins|minuto|minutos|minute|minutes|minuten)"
_REMIND_RE = re.compile(
    r"(?:(?:me\s+)?(?:avisa|avise|avisar|lembra|lembre|lembrar|remind(?:\s+me)?|herinner(?:\s+me)?|"
    r"previens-moi|previens\s+moi|prevenez-moi|erinnere\s+mich|erinner\s+mich)\s+)?"
    r"(\d{1,3})\s*("
    + _UNITS
    + r")\s+(?:antes|before|ervoor|eerder|van\s+tevoren|vorher|avant|zuvor)\b"
)
_T_COLON = re.compile(
    r"(?:\b(?:as|a|at|om|um|a\s+las)\s+)?\b([01]?\d|2[0-3])[:hu]([0-5]\d)\b"
)  # "14:30", "14h30", Dutch "14u30"
_T_HOUR = re.compile(
    r"(?:\b(?:as|a|at|om|um)\s+)?\b([01]?\d|2[0-3])(?:\s*(?:h|hs|hrs|uur|uhr|heures?)|u)\b(?!\s*[:\d])"
)
_T_AMPM = re.compile(r"\b(1[0-2]|0?[1-9])(?::([0-5]\d))?\s*(am|pm)\b")
_T_LEAD = re.compile(
    r"\b(?:as|at|om|um)\s+([01]?\d|2[0-3])\b(?!\s*(?:[:h]|euros?|€|km|min|x|%|\d|/\s*10|(?:em|de|von|van|sur|of|out\s+of)\s+10\b))"
)
_D_DAYNUM = re.compile(
    r"\b(?:dia|day|the|op\s+de|op\s+den|le|am|den)\s+(\d{1,2})(?:st|nd|rd|th|e|ste|de|er|o)?\b(?!\s*[:h/]|\s*(?:euros?|€))"
)
_D_NUMERIC = re.compile(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b")
_D_MONTHNAME = re.compile(
    r"\b(\d{1,2})(?:st|nd|rd|th|e|er|ste|de|o)?\s+(?:de\s+|of\s+)?(" + "|".join(sorted(_MONTH_NUM, key=len, reverse=True)) + r")\b"
)  # fmt: skip
_D_WEEKDAY = re.compile(
    r"\b(?:na\s+|no\s+|on\s+|op\s+|am\s+|ce\s+|this\s+|next\s+|proxima\s+|proximo\s+)?("
    + "|".join(sorted(_WEEKDAYS, key=len, reverse=True))
    + r")\b"
)
_D_REL = re.compile(
    r"\b("
    + "|".join(sorted((re.escape(k) for k in _REL_DAYS), key=len, reverse=True))
    + r")(?![a-z])"
)


@dataclass
class When:
    day: date | None = None
    hhmm: tuple[int, int] | None = None
    remind_minutes: int | None = None
    spans: list[tuple[int, int]] = field(default_factory=list)


def _unit_minutes(value: int, unit: str) -> int:
    return value if unit.startswith("min") else value * 60


def _clamp(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def parse_when(plain: str, now: datetime) -> When:
    """Pick the reminder lead, time of day and date out of ``plain``; ``spans`` are what to cut
    from the title. Anything not found stays None."""
    w = When()
    taken: list[tuple[int, int]] = []

    def free(a: int, b: int) -> bool:
        return all(b <= s or a >= e for s, e in taken)

    m = _REMIND_RE.search(plain)
    if m:
        minutes = _unit_minutes(int(m.group(1)), m.group(2))
        w.remind_minutes = min(max(minutes, 0), MAX_REMIND_MINUTES)
        taken.append(m.span())
    for rx in (_T_COLON, _T_AMPM, _T_HOUR, _T_LEAD):
        for m in rx.finditer(plain):
            if not free(*m.span()):
                continue
            if rx is _T_AMPM:
                h = int(m.group(1)) % 12 + (12 if m.group(3) == "pm" else 0)
                mi = int(m.group(2) or 0)
            elif rx is _T_LEAD or rx is _T_HOUR:
                h, mi = int(m.group(1)), 0
            else:
                h, mi = int(m.group(1)), int(m.group(2))
            w.hhmm = (h, mi)
            taken.append(m.span())
            break
        if w.hhmm:
            break
    today = now.date()
    day: date | None = None
    for rx, kind in (
        (_D_REL, "rel"),
        (_D_NUMERIC, "num"),
        (_D_MONTHNAME, "month"),
        (_D_DAYNUM, "daynum"),
        (_D_WEEKDAY, "wd"),
    ):
        for m in rx.finditer(plain):
            if not free(*m.span()):
                continue
            if kind == "rel":
                day = today + timedelta(days=_REL_DAYS[m.group(1)])
            elif kind == "num":
                d, mo = int(m.group(1)), int(m.group(2))
                yr = today.year
                if m.group(3):
                    yr = int(m.group(3)) + (2000 if int(m.group(3)) < 100 else 0)
                cand = _clamp(yr, mo, d)
                if cand and not m.group(3) and cand < today:
                    cand = _clamp(yr + 1, mo, d)
                day = cand
            elif kind == "month":
                d, mo = int(m.group(1)), _MONTH_NUM[m.group(2)]
                cand = _clamp(today.year, mo, d)
                if cand and cand < today:
                    cand = _clamp(today.year + 1, mo, d)
                day = cand
            elif kind == "daynum":
                d = int(m.group(1))
                if 1 <= d <= 31:
                    y, mo = today.year, today.month
                    cand = _clamp(y, mo, d)
                    if cand is None or cand < today:
                        mo2 = mo % 12 + 1
                        y2 = y + (1 if mo == 12 else 0)
                        cand = _clamp(y2, mo2, min(d, calendar.monthrange(y2, mo2)[1]))
                    day = cand
            else:
                wd = _WEEKDAYS[m.group(1)]
                delta = (wd - today.weekday()) % 7
                if (
                    delta == 0
                    and w.hhmm is not None
                    and time(*w.hhmm) <= now.timetz().replace(tzinfo=None)
                ):
                    delta = 7
                day = today + timedelta(days=delta)
            if day is not None:
                taken.append(m.span())
                break
        if day is not None:
            break
    w.day = day
    w.spans = sorted(taken)
    return w


def to_datetime(day: date, hhmm: tuple[int, int]) -> datetime:
    return datetime.combine(day, time(*hhmm), tzinfo=local_tz())


# ── creating ─────────────────────────────────────────────────────────────────

_LEAD_VERBS = re.compile(
    r"^(?:marca|marcar|marque|agenda|agendar|agende|adiciona|adicionar|add|schedule|book|plan|plant|zet|voeg\s+toe|ajoute|plane|nova|novo|tenho|tem)\s+"
)
# "remind me to X" / "herinner me eraan X" / "rappelle-moi d'X" / "erinnere mich daran, X": the command
# is not part of the title of what is being remembered.
_REMIND_LEAD = re.compile(
    r"^(?:me\s+lembra(?:r)?|lembre-me|lembra-me|remind\s+me|herinner\s+me|"
    r"rappelle[-\s]moi|rappeler|erinnere\s+mich)\s*(?:(?:eraan|daran|to|de|que|zu)(?![\w])|d')?[\s,]*"
)
# "lembrete: X" / "reminder: X": a label in front of the thing, not part of its title (defect 9).
_REMIND_LABEL = re.compile(r"^(?:lembrete|reminder|herinnering|rappel|erinnerung)\s*:\s*")
_EDGE_WORDS = {
    "na", "no", "em", "de", "do", "da", "a", "as", "para", "pra", "um", "uma", "dia", "com", "e",
    "at", "on", "for", "the", "an", "om", "op", "voor", "een", "le", "la", "au", "pour", "am", "fur", "für",
    "to", "den", "dem", "zu",
}  # fmt: skip
# Past-tense / logging verbs: "corri às 7h", "gastei 12 hoje às 3" are records, not appointments.
_PAST_VERBS = re.compile(
    r"^(?:gastei|paguei|comprei|corri|treinei|dormi|acordei|fui|caminhei|nadei|pedalei|medi|tomei|"
    r"spent|paid|bought|ran|slept|woke|went|walked|swam|took|cycled|"
    r"betaalde|kocht|rende|sliep|ging|"
    r"depense|ai\s+paye|ai\s+achete|bezahlt|gekauft|bin|habe)\b"
)


def clean_title(body: str, spans: list[tuple[int, int]]) -> str:
    out, pos = [], 0
    for a, b in spans:
        out.append(body[pos:a])
        pos = b
    out.append(body[pos:])
    text = re.sub(r"[,;]+", " ", " ".join(out))
    text = re.sub(r"\s+", " ", text).strip(" .-–—:")
    text = _REMIND_LEAD.sub("", _LEAD_VERBS.sub("", _REMIND_LABEL.sub("", text)))
    words = text.split()
    while words and strip_accents(words[0]) in _EDGE_WORDS:
        words.pop(0)
    while words and strip_accents(words[-1]) in _EDGE_WORDS:
        words.pop()
    title = " ".join(words).strip()
    return (title[0].upper() + title[1:])[:160] if title else ""


@dataclass(frozen=True)
class NewAppointment:
    title: str
    starts_at: datetime
    remind_minutes: int


def parse_appointment(body: str, body_plain: str, now: datetime) -> NewAppointment | str | None:
    """A new appointment, ``"past"`` when its moment is gone, or None when it is not one."""
    plain = body_plain.strip()
    if has_money(plain) or "?" in plain or _PAST_VERBS.match(plain):
        return None
    w = parse_when(plain, now)
    if w.day is None or w.hhmm is None:
        return None
    title = clean_title(body, w.spans)
    if not title or len(title) < 2:
        return None
    starts = to_datetime(w.day, w.hhmm)
    if starts <= now:
        return "past"
    return NewAppointment(
        title, starts, w.remind_minutes if w.remind_minutes is not None else DEFAULT_REMIND_MINUTES
    )


# ── other commands ───────────────────────────────────────────────────────────

_LIST_RE = re.compile(
    r"^(?:(?:a\s+|o\s+|minha\s+|meu\s+|my\s+|mijn\s+|mon\s+|ma\s+|mein\s+|meine\s+|de\s+|het\s+)*)"
    r"(?:agenda|calendar|calendario|kalender|agenda\s+de|compromissos|appointments|afspraken|rendez-vous|termine)"
    r"(?:\s+(?:de\s+|do\s+|da\s+|for\s+|voor\s+|pour\s+|fuer\s+|für\s+)?(?P<per>.+?))?\s*[.!?]*$"
)  # fmt: skip
_CANCEL_RE = re.compile(
    r"^(?:cancela|cancele|cancelar|desmarca|desmarque|desmarcar|apaga|apague|remove|remova|cancel|delete|"
    r"annuleer|verwijder|annule|supprime|loesche|sage\s+ab)\s+"
    r"(?:(?:a|o|the|het|de|le|la|das|die|der|my|meu|minha|mijn|mon|mein)\s+)?(?:(?:consulta|compromisso|appointment|afspraak)\s+)?(?P<name>[a-z][a-z0-9 '\-]{1,60}?)\s*[.!]*$"
)  # fmt: skip
_MOVE_RE = re.compile(
    r"^(?:muda|mude|mudar|altera|altere|remarca|remarque|reagenda|reagende|move|mova|reschedule|change|"
    r"verplaats|verzet|deplace|reporte|verschiebe)\s+"
    r"(?:(?:a|o|the|het|de|le|la|das|die|der|my|meu|minha|mijn|mon|mein)\s+)?(?P<name>[a-z][a-z0-9 '\-]{1,60}?)\s+"
    r"(?:para|to|naar|a|zu|auf|pour|pra)\s+(?P<rest>.+?)\s*[.!]*$"
)  # fmt: skip


def _tokens(text: str) -> set[str]:
    stop = {
        "de",
        "do",
        "da",
        "o",
        "a",
        "os",
        "as",
        "the",
        "het",
        "le",
        "la",
        "meu",
        "minha",
        "my",
        "mijn",
        "mon",
        "mein",
    }
    return {t for t in re.split(r"[^a-z0-9]+", strip_accents(text.lower())) if t and t not in stop}


def _match(name: str, items: list[Appointment]) -> list[Appointment]:
    want = _tokens(name)
    if not want:
        return []
    exact = [a for a in items if _tokens(a.title) == want]
    return exact or [a for a in items if want <= _tokens(a.title)]


async def _upcoming(
    session: AsyncSession, member_id: uuid.UUID, now: datetime
) -> list[Appointment]:
    return list(
        (
            await session.execute(
                select(Appointment)
                .where(
                    Appointment.member_id == member_id,
                    Appointment.status == "active",
                    Appointment.starts_at >= now - timedelta(hours=1),
                )
                .order_by(Appointment.starts_at)
            )
        )
        .scalars()
        .all()
    )


def _fmt_when(dt: datetime, lang: str) -> str:
    d = to_local(dt)
    return f"{weekday_abbr(d.date(), lang)} {d.day:02d}/{d.month:02d} {d.hour:02d}:{d.minute:02d}"


def _fmt_lead(minutes: int) -> str:
    if minutes % 60 == 0 and minutes >= 60:
        return f"{minutes // 60}h"
    return f"{minutes} min"


@dataclass(frozen=True)
class AgendaReply:
    text: str
    buttons: list[tuple[str, str]] | None = None


async def handle_agenda_command(
    body: str, body_plain: str, member: Member, lang: str, session: AsyncSession
) -> AgendaReply | None:
    """Reply (maybe with buttons) for create / list / cancel / move, or None."""
    from alfred.conversation import _t

    now = now_local()
    plain = re.sub(r"\s+", " ", body_plain).strip(" .!?")

    m = _LIST_RE.match(plain)
    if m:
        return AgendaReply(await _list(member, lang, session, now, m.group("per")))

    m_move = _MOVE_RE.match(plain)
    if m_move:
        items = await _upcoming(session, member.id, now)
        hits = _match(m_move.group("name"), items)
        if hits:
            if len(hits) > 1:
                return AgendaReply(
                    _t("agenda_ambiguous", lang, names=", ".join(h.title for h in hits))
                )
            w = parse_when(m_move.group("rest"), now)
            if w.day is None and w.hhmm is None:
                return AgendaReply(_t("agenda_move_what", lang))
            cur = to_local(hits[0].starts_at)
            new = to_datetime(w.day or cur.date(), w.hhmm or (cur.hour, cur.minute))
            if new <= now:
                return AgendaReply(_t("agenda_past", lang))
            hits[0].starts_at = new
            hits[0].reminded_at = None
            session.add(hits[0])
            await session.flush()
            audit(session, "appointment_moved", member.id)
            return AgendaReply(
                _t("agenda_moved", lang, title=hits[0].title, when=_fmt_when(new, lang))
            )

    m_cancel = _CANCEL_RE.match(plain)
    if m_cancel:
        items = await _upcoming(session, member.id, now)
        hits = _match(m_cancel.group("name"), items)
        if len(hits) == 1:
            hits[0].status = "cancelled"
            session.add(hits[0])
            audit(session, "appointment_cancelled", member.id)
            return AgendaReply(_t("agenda_cancelled", lang, title=hits[0].title))
        if len(hits) > 1:
            return AgendaReply(_t("agenda_ambiguous", lang, names=", ".join(h.title for h in hits)))
        return None

    parsed = parse_appointment(body, body_plain, now)
    if parsed is None:
        return None
    if parsed == "past":
        return AgendaReply(_t("agenda_past", lang))
    return await _create(parsed, member, lang, session, now)


async def _create(
    a: NewAppointment, member: Member, lang: str, session: AsyncSession, now: datetime
) -> AgendaReply:
    from alfred.conversation import _t

    active = await session.scalar(
        select(func.count())
        .select_from(Appointment)
        .where(
            Appointment.member_id == member.id,
            Appointment.status == "active",
            Appointment.starts_at >= now,
        )
    )
    if (active or 0) >= MAX_ACTIVE:
        return AgendaReply(_t("agenda_limit", lang, n=MAX_ACTIVE))
    clash = await session.scalar(
        select(Appointment)
        .where(
            Appointment.member_id == member.id,
            Appointment.status == "active",
            Appointment.starts_at > a.starts_at - timedelta(minutes=CONFLICT_MINUTES + 1),
            Appointment.starts_at < a.starts_at + timedelta(minutes=CONFLICT_MINUTES + 1),
        )
        .order_by(Appointment.starts_at)
        .limit(1)
    )
    if clash is not None and clash.starts_at == a.starts_at:
        want, have = _tokens(a.title), _tokens(clash.title)
        if want and have and (want <= have or have <= want):  # the same thing, said twice
            return AgendaReply(
                _t(
                    "agenda_duplicate",
                    lang,
                    title=clash.title,
                    when=_fmt_when(clash.starts_at, lang),
                )
            )
    appt = Appointment(
        id=uuid.uuid4(),
        member_id=member.id,
        household_id=member.household_id,
        title=a.title,
        starts_at=a.starts_at,
        remind_before_minutes=a.remind_minutes,
    )
    session.add(appt)
    await session.flush()
    audit(session, "appointment_created", member.id)
    text = _t(
        "agenda_created",
        lang,
        title=a.title,
        when=_fmt_when(a.starts_at, lang),
        lead=_fmt_lead(a.remind_minutes),
    )
    if clash is not None:
        text += _t(
            "agenda_conflict",
            lang,
            title=clash.title,
            time=to_local(clash.starts_at).strftime("%H:%M"),
        )
    return AgendaReply(
        text,
        [
            (f"appt_ok:{appt.id}", _t("btn_ok", lang)),
            (f"appt_undo:{appt.id}", _t("btn_undo", lang)),
        ],
    )


async def _list(
    member: Member, lang: str, session: AsyncSession, now: datetime, per: str | None
) -> str:
    from alfred.conversation import _t

    start, end = now, now + timedelta(days=14)
    per_plain = (per or "").strip()
    if per_plain:
        w = parse_when(per_plain, now)
        if w.day is not None:
            start = datetime.combine(w.day, time.min, tzinfo=local_tz())
            end = start + timedelta(days=1)
        elif re.search(r"semana|week|woche|semaine", per_plain):
            end = now + timedelta(days=7)
        else:
            return _t("agenda_list_help", lang)
    rows = list(
        (
            await session.execute(
                select(Appointment)
                .where(
                    Appointment.member_id == member.id,
                    Appointment.status == "active",
                    Appointment.starts_at >= max(start, now - timedelta(hours=1))
                    if per_plain and start > now
                    else Appointment.starts_at >= start,
                    Appointment.starts_at < end,
                )
                .order_by(Appointment.starts_at)
                .limit(LIST_LIMIT + 1)
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        return _t("agenda_list_empty", lang)
    lines = [_t("agenda_list_header", lang, n=min(len(rows), LIST_LIMIT))]
    for r in rows[:LIST_LIMIT]:
        lines.append(_t("agenda_list_row", lang, when=_fmt_when(r.starts_at, lang), title=r.title))
    if len(rows) > LIST_LIMIT:
        lines.append(_t("agenda_list_more", lang))
    return "\n".join(lines)


async def handle_button(
    action: str, raw_id: str, member: Member, lang: str, session: AsyncSession
) -> str:
    """Taps on [It's right] / [Undo] under an appointment confirmation."""
    from alfred.conversation import _t

    try:
        appt_id = uuid.UUID(raw_id)
    except ValueError:
        return _t("button_gone", lang)
    appt = await session.scalar(
        select(Appointment).where(Appointment.id == appt_id, Appointment.member_id == member.id)
    )
    if appt is None or appt.status == "cancelled":
        return _t("button_gone", lang)
    if action == "appt_undo":
        appt.status = "cancelled"
        session.add(appt)
        audit(session, "appointment_cancelled", member.id)
        return _t("agenda_cancelled", lang, title=appt.title)
    return _t("button_ok_reply", lang)


# ── cron side ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class AppointmentReminder:
    appointment_id: uuid.UUID
    wa_phone: str
    lang: str
    text: str
    in_window: bool


async def pending_reminders(session: AsyncSession, now: datetime) -> list[AppointmentReminder]:
    """Appointments whose reminder time has come (and that have not started) and not yet reminded."""
    from alfred.conversation import _t

    rows = (
        await session.execute(
            select(Appointment, Member.wa_phone, Member.language)
            .join(Member, Appointment.member_id == Member.id)
            .where(
                Appointment.status == "active",
                Appointment.reminded_at.is_(None),
                Appointment.starts_at > now,
                Member.consent_state == "accepted",
            )
        )
    ).all()
    out: list[AppointmentReminder] = []
    cutoff = now - timedelta(hours=WINDOW_HOURS)
    for appt, phone, language in rows:
        if now < appt.starts_at - timedelta(minutes=appt.remind_before_minutes):
            continue
        lang = language or "en"
        last_in = await session.scalar(
            select(func.max(Message.created_at)).where(
                Message.author_id == appt.member_id, Message.direction == "inbound"
            )
        )
        local = to_local(appt.starts_at)
        out.append(
            AppointmentReminder(
                appointment_id=appt.id,
                wa_phone=phone,
                lang=lang,
                text=_t("agenda_reminder", lang, title=appt.title, when=_fmt_when(local, lang)),
                in_window=bool(last_in and last_in.astimezone(now.tzinfo) >= cutoff),
            )
        )
    return out


async def mark_reminded(session: AsyncSession, appointment_id: uuid.UUID, when: datetime) -> None:
    appt = await session.get(Appointment, appointment_id)
    if appt is not None:
        appt.reminded_at = when
        session.add(appt)


STRINGS: dict[str, dict[str, str | tuple[str, ...]]] = {
    "agenda_created": {
        "pt": ("Anotado: {title}, {when}. Aviso {lead} antes.", "Agendado: {title}, {when}. Eu te aviso {lead} antes."),
        "nl": ("Genoteerd: {title}, {when}. Ik herinner je {lead} van tevoren.", "Ingepland: {title}, {when}. Ik waarschuw je {lead} eerder."),
        "en": ("Noted: {title}, {when}. I'll remind you {lead} before.", "Scheduled: {title}, {when}. I'll ping you {lead} before."),
        "fr": ("C'est noté : {title}, {when}. Je te préviens {lead} avant.", "Planifié : {title}, {when}. Je te préviens {lead} avant."),
        "de": ("Notiert: {title}, {when}. Ich erinnere dich {lead} vorher.", "Eingetragen: {title}, {when}. Ich melde mich {lead} vorher."),
    },
    "agenda_duplicate": {
        "pt": "Isso já está na agenda: {title}, {when}. Não criei outro.",
        "nl": "Dat staat al in je agenda: {title}, {when}. Ik heb geen tweede gemaakt.",
        "en": "That is already in your agenda: {title}, {when}. I did not add another.",
        "fr": "C'est déjà dans ton agenda : {title}, {when}. Je n'en ai pas créé un autre.",
        "de": "Das steht schon in deinem Kalender: {title}, {when}. Ich habe keinen zweiten angelegt.",
    },
    "agenda_conflict": {
        "pt": " Atenção: você já tem {title} às {time}.",
        "nl": " Let op: je hebt al {title} om {time}.",
        "en": " Heads up: you already have {title} at {time}.",
        "fr": " Attention : tu as déjà {title} à {time}.",
        "de": " Achtung: du hast schon {title} um {time}.",
    },
    "agenda_past": {
        "pt": "Essa data e hora já passaram. Diga de novo com uma data futura, por exemplo “dentista amanhã às 14h”.",
        "nl": "Dat moment is al voorbij. Geef een datum in de toekomst, bijvoorbeeld “tandarts morgen om 14:00”.",
        "en": "That date and time have passed. Try again with a future date, for example “dentist tomorrow at 2pm”.",
        "fr": "Cette date est déjà passée. Redis-le avec une date à venir, par exemple « dentiste demain à 14h ».",
        "de": "Dieser Zeitpunkt ist schon vorbei. Nenne ein Datum in der Zukunft, zum Beispiel „Zahnarzt morgen um 14 Uhr“.",
    },
    "agenda_cancelled": {
        "pt": "Compromisso cancelado: {title}.",
        "nl": "Afspraak geannuleerd: {title}.",
        "en": "Appointment cancelled: {title}.",
        "fr": "Rendez-vous annulé : {title}.",
        "de": "Termin abgesagt: {title}.",
    },
    "agenda_moved": {
        "pt": "Remarcado: {title}, {when}.",
        "nl": "Verplaatst: {title}, {when}.",
        "en": "Rescheduled: {title}, {when}.",
        "fr": "Déplacé : {title}, {when}.",
        "de": "Verschoben: {title}, {when}.",
    },
    "agenda_move_what": {
        "pt": "Para quando? Por exemplo “muda o dentista para sexta às 15h”.",
        "nl": "Naar wanneer? Bijvoorbeeld “verplaats tandarts naar vrijdag 15:00”.",
        "en": "To when? For example “move dentist to Friday at 3pm”.",
        "fr": "Pour quand ? Par exemple « déplace dentiste à vendredi 15h ».",
        "de": "Auf wann? Zum Beispiel „verschiebe Zahnarzt auf Freitag 15 Uhr“.",
    },
    "agenda_ambiguous": {
        "pt": "Mais de um compromisso combina: {names}. Diga o nome completo.",
        "nl": "Meerdere afspraken passen: {names}. Geef de volledige naam.",
        "en": "More than one appointment matches: {names}. Please use the full name.",
        "fr": "Plusieurs rendez-vous correspondent : {names}. Donne le nom complet.",
        "de": "Mehrere Termine passen: {names}. Bitte den vollen Namen nennen.",
    },
    "agenda_limit": {
        "pt": "Você já tem {n} compromissos futuros, que é o limite.",
        "nl": "Je hebt al {n} toekomstige afspraken, dat is het maximum.",
        "en": "You already have {n} upcoming appointments, which is the limit.",
        "fr": "Tu as déjà {n} rendez-vous à venir, c'est la limite.",
        "de": "Du hast schon {n} kommende Termine, das ist das Maximum.",
    },
    "agenda_list_empty": {
        "pt": "Nada na agenda nesse período. Para marcar, diga por exemplo “dentista quinta às 14h”.",
        "nl": "Niets in je agenda voor deze periode. Plan er een met bijvoorbeeld “tandarts donderdag om 14:00”.",
        "en": "Nothing on your agenda for that period. Add one, for example “dentist Thursday at 2pm”.",
        "fr": "Rien dans ton agenda pour cette période. Ajoutes-en un, par exemple « dentiste jeudi à 14h ».",
        "de": "Nichts in deinem Kalender für diesen Zeitraum. Trage etwas ein, zum Beispiel „Zahnarzt Donnerstag um 14 Uhr“.",
    },
    "agenda_list_help": {
        "pt": "Posso mostrar “minha agenda”, “agenda de hoje”, “agenda de amanhã”, “agenda da semana” ou de um dia da semana.",
        "nl": "Ik kan “mijn agenda”, “agenda van vandaag”, “agenda van morgen” of “agenda van de week” laten zien.",
        "en": "I can show “my agenda”, “agenda for today”, “agenda for tomorrow” or “agenda for the week”.",
        "fr": "Je peux montrer « mon agenda », « agenda d'aujourd'hui », « agenda de demain » ou « agenda de la semaine ».",
        "de": "Ich kann „meinen Kalender“, „Kalender für heute“, „für morgen“ oder „für die Woche“ zeigen.",
    },
    "agenda_list_header": {
        "pt": "Sua agenda ({n}):",
        "nl": "Je agenda ({n}):",
        "en": "Your agenda ({n}):",
        "fr": "Ton agenda ({n}) :",
        "de": "Dein Kalender ({n}):",
    },
    "agenda_list_row": {
        "pt": "• {when} — {title}",
        "nl": "• {when} — {title}",
        "en": "• {when} — {title}",
        "fr": "• {when} — {title}",
        "de": "• {when} — {title}",
    },
    "agenda_list_more": {
        "pt": "… e mais. Peça um dia específico para ver o resto.",
        "nl": "… en meer. Vraag een specifieke dag om de rest te zien.",
        "en": "… and more. Ask for a specific day to see the rest.",
        "fr": "… et plus. Demande un jour précis pour voir la suite.",
        "de": "… und mehr. Frag nach einem bestimmten Tag, um den Rest zu sehen.",
    },
    "agenda_reminder": {
        "pt": "⏰ {title} — {when}.",
        "nl": "⏰ {title} — {when}.",
        "en": "⏰ {title} — {when}.",
        "fr": "⏰ {title} — {when}.",
        "de": "⏰ {title} — {when}.",
    },
}  # fmt: skip
