# ruff: noqa: E501
"""V2-35 — weekly training plan with loads and the evolution of each exercise.

Rule-based, no LLM. What is understood:

* a plan, written as lines ``Segunda - Peito: Supino 4x10 60kg, Crucifixo 3x12 14kg`` after a
  header such as "plano de treino:"; Alfred answers with a preview and two buttons, and only the
  confirmation writes anything (the previous plan is archived, not deleted);
* "treino de hoje" · "meu plano de treino";
* a load actually used: "carga supino 62 kg" · "usei 62 kg no supino";
* "evolução do supino": the last loads and the change since the first one.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from alfred.audit import audit
from alfred.clock import now_local, today_local
from alfred.models import (
    Member,
    PendingAction,
    WorkoutLoad,
    WorkoutPlan,
    WorkoutPlanDay,
    WorkoutPlanItem,
)
from alfred.parsing import strip_accents

KIND = "workout_plan"
DRAFT_MINUTES = 15
MAX_DAYS = 7
MAX_ITEMS_PER_DAY = 12
MAX_EXERCISE = 80
MAX_KG = 500.0
HISTORY_SHOWN = 6

WEEKDAYS: dict[str, int] = {}
for _i, _names in enumerate(
    (
        "segunda seg monday mon maandag ma lundi lun montag mo",
        "terca ter tuesday tue dinsdag di mardi mar dienstag",
        "quarta qua wednesday wed woensdag wo mercredi mer mittwoch mi",
        "quinta qui thursday thu donderdag do jeudi jeu donnerstag",
        "sexta sex friday fri vrijdag vr vendredi ven freitag fr",
        "sabado sab saturday sat zaterdag za samedi sam samstag sa",
        "domingo dom sunday sun zondag zo dimanche dim sonntag so",
    )
):
    for _n in _names.split():
        WEEKDAYS[_n] = _i

_HEADERS = (
    "meu plano de treino", "plano de treino", "meu treino semanal", "my training plan",
    "training plan", "my workout plan", "workout plan", "mijn trainingsschema", "trainingsschema",
    "mon plan d'entrainement", "plan d'entrainement", "mein trainingsplan", "trainingsplan",
)  # fmt: skip
_TODAY = {
    "treino de hoje", "meu treino de hoje", "qual o treino de hoje", "qual e o treino de hoje",
    "o que treino hoje", "workout today", "my workout today", "todays workout",
    "whats my workout today", "what is my workout today", "training vandaag",
    "mijn training van vandaag", "training van vandaag", "entrainement du jour",
    "mon entrainement daujourdhui", "entrainement daujourdhui", "training heute",
    "mein training heute", "heutiges training",
}  # fmt: skip

_KG = r"(\d{1,3}(?:[.,]\d{1,2})?)\s*kg"
_LOAD = [  # (regex, exercise group, kg group)
    (re.compile(rf"^(?:carga|peso)\s+(?:do\s+|da\s+|de\s+|no\s+|na\s+)?(.+?)\s+{_KG}$"), 1, 2),
    (re.compile(rf"^usei\s+{_KG}\s+(?:no|na|em|de|do|da)\s+(.+)$"), 2, 1),
    (re.compile(rf"^load\s+(.+?)\s+{_KG}$"), 1, 2),
    (re.compile(rf"^i\s+used\s+{_KG}\s+(?:on|for|in)\s+(.+)$"), 2, 1),
    (re.compile(rf"^gewicht\s+(.+?)\s+{_KG}$"), 1, 2),
    (re.compile(rf"^ik\s+gebruikte\s+{_KG}\s+(?:voor|bij|op)\s+(.+)$"), 2, 1),
    (re.compile(rf"^charge\s+(.+?)\s+{_KG}$"), 1, 2),
    (re.compile(rf"^jai\s+utilise\s+{_KG}\s+(?:pour|sur|au|aux|a)\s+(.+)$"), 2, 1),
    (re.compile(rf"^ich\s+habe\s+{_KG}\s+(?:bei|fur|beim|auf)\s+(.+)$"), 2, 1),
]
_EVOLUTION = [
    re.compile(r"^(?:evolucao|progresso)\s+(?:do\s+|da\s+|de\s+|no\s+|na\s+)?(.+)$"),
    re.compile(r"^(?:progress|progression)\s+(?:on\s+|of\s+|for\s+)?(.+)$"),
    re.compile(r"^voortgang\s+(?:van\s+|bij\s+)?(.+)$"),
    re.compile(r"^evolution\s+(?:du\s+|de\s+la\s+|de\s+l\s*|des\s+|de\s+)?(.+)$"),
    re.compile(r"^(?:fortschritt|entwicklung)\s+(?:bei\s+|beim\s+|von\s+|im\s+)?(.+)$"),
]
_ITEM = re.compile(
    r"^(?P<name>.+?)\s+(?P<sets>\d{1,2})\s*[x×]\s*(?P<reps>\d{1,3}(?:\s*-\s*\d{1,3})?)"
    rf"(?:\s*(?:@|com|with|met|avec|mit)?\s*{_KG})?$",
    re.I,
)
_ITEM_KG = re.compile(rf"^(?P<name>.+?)\s+{_KG}$", re.I)
_DAY_LINE = re.compile(r"^\s*([A-Za-zÀ-ÿ]{2,12})\s*(?:[-–—]\s*([^:]{1,40}?))?\s*:\s*(.+)$")


def key_of(name: str) -> str:
    return " ".join(strip_accents(name.lower()).replace("'", "").split())[:MAX_EXERCISE]


def _all(pt: str, nl: str, en: str, fr: str, de: str) -> dict[str, str | tuple[str, ...]]:
    return {"pt": pt, "nl": nl, "en": en, "fr": fr, "de": de}


@dataclass
class Reply:
    text: str
    buttons: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class PlanItem:
    exercise: str
    sets: int | None = None
    reps: str | None = None
    load_kg: float | None = None


@dataclass
class PlanDay:
    weekday: int
    title: str
    items: list[PlanItem]


def _kg(raw: str) -> float | None:
    try:
        value = float(raw.replace(",", "."))
    except ValueError:
        return None
    return value if 0 < value <= MAX_KG else None


def fmt_kg(value: float, lang: str) -> str:
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    if lang != "en":
        text = text.replace(".", ",")
    return f"{text} kg"


def weekday_name(index: int, lang: str) -> str:
    from alfred.panel_phrases import WEEKDAYS as NAMES

    return NAMES[lang][index]


def _clean(text: str) -> str:
    return " ".join(text.split())


_DAY_ONLY = re.compile(r"^\s*([A-Za-zÀ-ÿ]{2,12})\s*(?:[-–—:]\s*([^:]{1,40}?))?\s*:?\s*$")
_BULLET = re.compile(r"^\s*[-•*·]\s*")


def _starts_a_day(line: str) -> bool:
    m = re.match(r"^\s*([A-Za-zÀ-ÿ]{2,12})\b", line)
    return bool(m and WEEKDAYS.get(strip_accents(m.group(1).lower())) is not None)


def _fold_lines(lines: list[str]) -> list[tuple[int, str]]:
    """Fold the natural layout into one line per day: "Segunda: peito" followed by one exercise
    per line becomes "Segunda - peito: supino 4x10 60kg, crucifixo 3x12" (numbered by its first
    line, so an error still points at the line the member wrote)."""
    folded: list[tuple[int, str]] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        j = i + 1
        extra: list[str] = []
        while j < len(lines) and not _starts_a_day(lines[j]) and not _DAY_LINE.match(lines[j]):
            extra.append(_BULLET.sub("", lines[j]))
            j += 1
        title_only = not re.search(r"\d", line.partition(":")[2])
        if extra and title_only and (m := _DAY_ONLY.match(line)) and _starts_a_day(line):
            title = _clean(m.group(2) or "")
            line = (
                f"{m.group(1)} - {title}: " + ", ".join(extra)
                if title
                else (f"{m.group(1)}: " + ", ".join(extra))
            )
        elif extra and title_only:
            line = line + ", " + ", ".join(extra)
        elif extra:
            j = i + 1  # the day line is complete: what follows is its own (maybe bad) line
        folded.append((i + 1, line))
        i = j
    return folded


def parse_plan(text: str) -> list[PlanDay] | int:
    """Days of the plan, or the 1-based number of the first line that does not parse."""
    days: list[PlanDay] = []
    seen: set[int] = set()
    lines = [ln for ln in (_clean(x) for x in text.replace("\r", "").split("\n")) if ln]
    if not lines:
        return 1
    for number, line in _fold_lines(lines):
        m = _DAY_LINE.match(line)
        if not m:
            return number
        weekday = WEEKDAYS.get(strip_accents(m.group(1).lower()))
        if weekday is None or weekday in seen:
            return number
        items: list[PlanItem] = []
        for raw in re.split(r"[;,](?!\d)", m.group(3)):
            raw = raw.strip()
            if not raw:
                continue
            item = _ITEM.match(raw)
            if item:
                kg = _kg(item.group(4)) if item.group(4) else None
                if item.group(4) and kg is None:
                    return number
                parsed = PlanItem(
                    _clean(item.group("name")),
                    int(item.group("sets")),
                    item.group("reps").replace(" ", ""),
                    kg,
                )
            elif plain_kg := _ITEM_KG.match(raw):
                kg = _kg(plain_kg.group(2))
                if kg is None:
                    return number
                parsed = PlanItem(_clean(plain_kg.group("name")), None, None, kg)
            else:
                parsed = PlanItem(_clean(raw))
            if (
                not parsed.exercise
                or len(parsed.exercise) > MAX_EXERCISE
                or not parsed.sets
                and parsed.exercise.isdigit()
            ):
                return number
            items.append(parsed)
        if not items or len(items) > MAX_ITEMS_PER_DAY:
            return number
        seen.add(weekday)
        days.append(PlanDay(weekday, _clean(m.group(2) or "")[:80], items))
        if len(days) > MAX_DAYS:
            return number
    return sorted(days, key=lambda d: d.weekday)


def _item_text(item: PlanItem, lang: str) -> str:
    out = item.exercise
    if item.sets and item.reps:
        out += f" {item.sets}x{item.reps}"
    if item.load_kg:
        out += f" · {fmt_kg(item.load_kg, lang)}"
    return out


def _preview(days: list[PlanDay], lang: str) -> str:
    from alfred.conversation import _t

    blocks = []
    for d in days:
        head = weekday_name(d.weekday, lang) + (f" · {d.title}" if d.title else "")
        blocks.append("\n".join([f"*{head}*", *(f"• {_item_text(i, lang)}" for i in d.items)]))
    return _t("plan_preview", lang) + "\n\n" + "\n\n".join(blocks) + "\n\n" + _t("plan_ask", lang)


def _buttons(draft_id: uuid.UUID, lang: str) -> list[tuple[str, str]]:
    from alfred.conversation import _t

    return [
        (f"plan_ok:{draft_id}", _t("plan_btn_ok", lang)),
        (f"plan_cancel:{draft_id}", _t("plan_btn_cancel", lang)),
    ]


def _norm_text(plain: str) -> str:
    return " ".join(plain.replace("'", "").split()).strip(" .!?")


_HEADER_RE = re.compile(
    r"^\s*(?:"
    + "|".join(
        re.escape(h).replace("\\'", "'?").replace("'", "'?")
        for h in sorted(_HEADERS, key=len, reverse=True)
    )
    + r")(?=[:\s]|$)\s*:?\s*"
)


def _split_header(body: str, body_plain: str) -> str | None:
    """Text after the plan header as the member wrote it, or None when there is no header."""
    m = _HEADER_RE.match(body_plain)
    if m is None:
        return None
    return (body if len(body) == len(body_plain) else body_plain)[m.end() :]


async def _active_plan(session: AsyncSession, member_id: uuid.UUID) -> WorkoutPlan | None:
    return await session.scalar(
        select(WorkoutPlan)
        .where(WorkoutPlan.member_id == member_id, WorkoutPlan.active.is_(True))
        .order_by(WorkoutPlan.created_at.desc())
    )


async def _plan_days(
    session: AsyncSession, plan: WorkoutPlan
) -> list[tuple[WorkoutPlanDay, list[WorkoutPlanItem]]]:
    days = list(
        (
            await session.execute(
                select(WorkoutPlanDay)
                .where(WorkoutPlanDay.plan_id == plan.id)
                .order_by(WorkoutPlanDay.weekday)
            )
        ).scalars()
    )
    out = []
    for d in days:
        items = list(
            (
                await session.execute(
                    select(WorkoutPlanItem)
                    .where(WorkoutPlanItem.day_id == d.id)
                    .order_by(WorkoutPlanItem.position)
                )
            ).scalars()
        )
        out.append((d, items))
    return out


async def last_loads(
    session: AsyncSession, member_id: uuid.UUID, keys: list[str]
) -> dict[str, WorkoutLoad]:
    """Most recent load of each exercise key."""
    out: dict[str, WorkoutLoad] = {}
    if not keys:
        return out
    rows = (
        await session.execute(
            select(WorkoutLoad)
            .where(WorkoutLoad.member_id == member_id, WorkoutLoad.exercise_key.in_(keys))
            .order_by(WorkoutLoad.day.desc(), WorkoutLoad.created_at.desc())
        )
    ).scalars()
    for row in rows:
        out.setdefault(row.exercise_key, row)
    return out


def _date(day: date) -> str:
    return day.strftime("%d/%m")


async def handle_training_command(
    body: str, body_plain: str, member: Member, lang: str, session: AsyncSession
) -> Reply | None:
    from alfred.conversation import _t

    flat = _norm_text(body_plain)

    if flat in _TODAY:
        return await _today(member, lang, session)

    rest = _split_header(body, body_plain)
    if rest is not None:
        if not rest.strip():
            return await _show_plan(member, lang, session)
        return await _draft(rest, member, lang, session)

    for pattern, ex_g, kg_g in _LOAD:
        if m := pattern.match(flat):
            kg = _kg(m.group(kg_g))
            if kg is None:
                return Reply(_t("load_bad", lang))
            name = _display_name(body, flat, m, ex_g)
            return Reply(await _log_load(name, kg, member, lang, session))
    for pattern in _EVOLUTION:
        if m := pattern.match(flat):
            return Reply(await _evolution(_display_name(body, flat, m, 1), member, lang, session))
    return None


def _display_name(body: str, flat: str, m: re.Match[str], group: int) -> str:
    start, end = m.span(group)
    raw = " ".join(body.split())
    original = raw[start:end] if len(raw) == len(flat) else m.group(group)
    return (
        _clean(original)[:MAX_EXERCISE].capitalize()
        if original.islower()
        else _clean(original)[:MAX_EXERCISE]
    )


async def _draft(text: str, member: Member, lang: str, session: AsyncSession) -> Reply:
    from alfred.conversation import _t

    parsed = parse_plan(text)
    if isinstance(parsed, int):
        return Reply(_t("plan_bad_line", lang, n=parsed))
    await session.execute(
        delete(PendingAction).where(
            PendingAction.member_id == member.id, PendingAction.kind == KIND
        )
    )
    draft = PendingAction(
        member_id=member.id,
        kind=KIND,
        payload={
            "days": [
                {
                    "weekday": d.weekday,
                    "title": d.title,
                    "items": [
                        {
                            "exercise": i.exercise,
                            "sets": i.sets,
                            "reps": i.reps,
                            "load_kg": i.load_kg,
                        }
                        for i in d.items
                    ],
                }
                for d in parsed
            ]
        },
        expires_at=now_local() + timedelta(minutes=DRAFT_MINUTES),
    )
    session.add(draft)
    await session.flush()
    return Reply(_preview(parsed, lang), _buttons(draft.id, lang))


async def handle_training_button(
    action: str, raw_id: str, member: Member, lang: str, session: AsyncSession
) -> Reply:
    from alfred.conversation import _t

    try:
        draft_id = uuid.UUID(raw_id)
    except ValueError:
        return Reply(_t("plan_gone", lang))
    # atomic take: a second tap finds nothing
    taken = (
        await session.execute(
            delete(PendingAction)
            .where(
                PendingAction.id == draft_id,
                PendingAction.member_id == member.id,
                PendingAction.kind == KIND,
            )
            .returning(PendingAction.payload, PendingAction.expires_at)
        )
    ).first()
    if taken is None:
        return Reply(_t("plan_gone", lang))
    payload, expires_at = taken
    if expires_at < now_local():
        return Reply(_t("plan_expired", lang))
    if action == "plan_cancel":
        return Reply(_t("plan_cancelled", lang))
    await session.execute(
        update(WorkoutPlan)
        .where(WorkoutPlan.member_id == member.id, WorkoutPlan.active.is_(True))
        .values(active=False)
    )
    plan = WorkoutPlan(member_id=member.id, name="", active=True)
    session.add(plan)
    await session.flush()
    for d in payload["days"]:
        day = WorkoutPlanDay(
            member_id=member.id, plan_id=plan.id, weekday=d["weekday"], title=d["title"]
        )
        session.add(day)
        await session.flush()
        for pos, i in enumerate(d["items"]):
            session.add(
                WorkoutPlanItem(
                    member_id=member.id,
                    day_id=day.id,
                    position=pos,
                    exercise=i["exercise"],
                    sets=i["sets"],
                    reps=i["reps"],
                    load_kg=i["load_kg"],
                )
            )
    await session.flush()
    audit(session, "workout_plan_saved", member.id, days=len(payload["days"]))
    return Reply(_t("plan_saved", lang, n=len(payload["days"])))


async def _show_plan(member: Member, lang: str, session: AsyncSession) -> Reply:
    from alfred.conversation import _t

    plan = await _active_plan(session, member.id)
    if plan is None:
        return Reply(_t("plan_none", lang))
    blocks = []
    for d, items in await _plan_days(session, plan):
        head = weekday_name(d.weekday, lang) + (f" · {d.title}" if d.title else "")
        lines = [_item_text(PlanItem(i.exercise, i.sets, i.reps, i.load_kg), lang) for i in items]
        blocks.append("\n".join([f"*{head}*", *(f"• {ln}" for ln in lines)]))
    return Reply(_t("plan_header", lang) + "\n\n" + "\n\n".join(blocks))


async def _today(member: Member, lang: str, session: AsyncSession) -> Reply:
    from alfred.conversation import _t

    plan = await _active_plan(session, member.id)
    if plan is None:
        return Reply(_t("plan_none", lang))
    weekday = today_local().weekday()
    for d, items in await _plan_days(session, plan):
        if d.weekday != weekday:
            continue
        last = await last_loads(session, member.id, [key_of(i.exercise) for i in items])
        lines = []
        for i in items:
            text = _item_text(PlanItem(i.exercise, i.sets, i.reps, None), lang)
            prior = last.get(key_of(i.exercise))
            if prior is not None:
                text += " · " + _t(
                    "today_last", lang, kg=fmt_kg(float(prior.load_kg), lang), day=_date(prior.day)
                )
            elif i.load_kg:
                text += f" · {fmt_kg(float(i.load_kg), lang)}"
            lines.append(f"• {text}")
        head = _t("today_header", lang, day=weekday_name(weekday, lang)) + (
            f" · {d.title}" if d.title else ""
        )
        return Reply(head + "\n" + "\n".join(lines))
    return Reply(_t("today_rest", lang, day=weekday_name(weekday, lang)))


async def _match_exercise(
    session: AsyncSession, member_id: uuid.UUID, typed: str
) -> tuple[str, str]:
    """(display name, key): the plan's own exercise when the typed name is exact or a unique match."""
    key = key_of(typed)
    plan = await _active_plan(session, member_id)
    if plan is not None:
        names = [i.exercise for _d, items in await _plan_days(session, plan) for i in items]
        exact = [n for n in names if key_of(n) == key]
        if exact:
            return exact[0], key_of(exact[0])
        partial = sorted({n for n in names if key and (key in key_of(n) or key_of(n) in key)})
        if len(partial) == 1:
            return partial[0], key_of(partial[0])
    return typed, key


async def _log_load(name: str, kg: float, member: Member, lang: str, session: AsyncSession) -> str:
    from alfred.conversation import _t

    display, key = await _match_exercise(session, member.id, name)
    if not key:
        return _t("load_bad", lang)
    today = today_local()
    previous = (await last_loads(session, member.id, [key])).get(key)
    same_day = await session.scalar(
        select(WorkoutLoad).where(
            WorkoutLoad.member_id == member.id,
            WorkoutLoad.exercise_key == key,
            WorkoutLoad.day == today,
        )
    )
    if same_day is not None:
        same_day.load_kg = kg  # saying it twice on one day corrects it, it does not duplicate
        before = None
        others = (
            (
                await session.execute(
                    select(WorkoutLoad)
                    .where(
                        WorkoutLoad.member_id == member.id,
                        WorkoutLoad.exercise_key == key,
                        WorkoutLoad.day < today,
                    )
                    .order_by(WorkoutLoad.day.desc())
                )
            )
            .scalars()
            .first()
        )
        before = others
    else:
        session.add(
            WorkoutLoad(
                member_id=member.id, exercise=display, exercise_key=key, day=today, load_kg=kg
            )
        )
        before = previous
    await session.flush()
    audit(session, "workout_load_logged", member.id)
    if before is None:
        return _t("load_first", lang, name=display, kg=fmt_kg(kg, lang))
    diff = round(kg - float(before.load_kg), 2)
    if diff == 0:
        return _t("load_same", lang, name=display, kg=fmt_kg(kg, lang))
    sign = "+" if diff > 0 else "−"
    return _t(
        "load_changed",
        lang,
        name=display,
        kg=fmt_kg(kg, lang),
        before=fmt_kg(float(before.load_kg), lang),
        delta=f"{sign}{fmt_kg(abs(diff), lang)}",
    )


async def _evolution(name: str, member: Member, lang: str, session: AsyncSession) -> str:
    from alfred.conversation import _t

    display, key = await _match_exercise(session, member.id, name)
    rows = list(
        (
            await session.execute(
                select(WorkoutLoad)
                .where(WorkoutLoad.member_id == member.id, WorkoutLoad.exercise_key == key)
                .order_by(WorkoutLoad.day)
            )
        ).scalars()
    )
    if not rows:
        return _t("evo_none", lang, name=display)
    shown = rows[-HISTORY_SHOWN:]
    lines = "\n".join(f"• {_date(r.day)}: {fmt_kg(float(r.load_kg), lang)}" for r in shown)
    head = _t("evo_header", lang, name=display)
    if len(rows) < 2:
        return f"{head}\n{lines}\n{_t('evo_one', lang)}"
    diff = round(float(rows[-1].load_kg) - float(rows[0].load_kg), 2)
    sign = "+" if diff > 0 else "−" if diff < 0 else ""
    return f"{head}\n{lines}\n{_t('evo_delta', lang, delta=sign + fmt_kg(abs(diff), lang), since=_date(rows[0].day))}"


STRINGS: dict[str, dict[str, str | tuple[str, ...]]] = {
    "plan_preview": _all(
        "Este é o plano que entendi:",
        "Dit is het schema dat ik begrepen heb:",
        "This is the plan I understood:",
        "Voici le plan que j'ai compris :",
        "Das ist der Plan, den ich verstanden habe:",
    ),
    "plan_ask": _all(
        "Confirma para eu guardar. Nada foi salvo ainda.",
        "Bevestig, dan sla ik het op. Er is nog niets opgeslagen.",
        "Confirm and I'll save it. Nothing is saved yet.",
        "Confirme et je l'enregistre. Rien n'est encore enregistré.",
        "Bestätige, dann speichere ich ihn. Noch ist nichts gespeichert.",
    ),
    "plan_btn_ok": _all("Confirmar", "Bevestigen", "Confirm", "Confirmer", "Bestätigen"),
    "plan_btn_cancel": _all("Cancelar", "Annuleren", "Cancel", "Annuler", "Abbrechen"),
    "plan_saved": _all(
        "Plano salvo ({n} dias). Diga “treino de hoje” quando quiser vê-lo.",
        "Schema opgeslagen ({n} dagen). Zeg “training vandaag” om het te zien.",
        "Plan saved ({n} days). Say “workout today” to see it.",
        "Plan enregistré ({n} jours). Dis « entraînement du jour » pour le voir.",
        "Plan gespeichert ({n} Tage). Sag „training heute“, um ihn zu sehen.",
    ),
    "plan_cancelled": _all(
        "Cancelado. Seu plano continua como estava.",
        "Geannuleerd. Je schema blijft zoals het was.",
        "Cancelled. Your plan stays as it was.",
        "Annulé. Ton plan reste comme avant.",
        "Abgebrochen. Dein Plan bleibt, wie er war.",
    ),
    "plan_gone": _all(
        "Não há plano pendente para confirmar.",
        "Er is geen schema om te bevestigen.",
        "There's no pending plan to confirm.",
        "Il n'y a aucun plan en attente.",
        "Es gibt keinen Plan zum Bestätigen.",
    ),
    "plan_expired": _all(
        "Esse rascunho expirou. Mande o plano de novo.",
        "Dat concept is verlopen. Stuur het schema opnieuw.",
        "That draft expired. Send the plan again.",
        "Ce brouillon a expiré. Renvoie le plan.",
        "Dieser Entwurf ist abgelaufen. Schick den Plan noch einmal.",
    ),
    "plan_bad_line": _all(
        "Não entendi a linha {n}. Use um dia por linha, por exemplo: “Segunda - Peito: Supino 4x10 60kg, Crucifixo 3x12”.",
        "Ik begrijp regel {n} niet. Gebruik één dag per regel, bijvoorbeeld: “Maandag - Borst: Bankdrukken 4x10 60kg, Flyes 3x12”.",
        "I didn't understand line {n}. Use one day per line, for example: “Monday - Chest: Bench press 4x10 60kg, Flyes 3x12”.",
        "Je n'ai pas compris la ligne {n}. Un jour par ligne, par exemple : « Lundi - Pectoraux : Développé couché 4x10 60kg, Écartés 3x12 ».",
        "Ich habe Zeile {n} nicht verstanden. Ein Tag pro Zeile, zum Beispiel: „Montag - Brust: Bankdrücken 4x10 60kg, Fliegende 3x12“.",
    ),
    "plan_none": _all(
        "Você ainda não tem plano de treino. Mande “plano de treino:” seguido de um dia por linha.",
        "Je hebt nog geen trainingsschema. Stuur “trainingsschema:” gevolgd door één dag per regel.",
        "You don't have a training plan yet. Send “training plan:” followed by one day per line.",
        "Tu n'as pas encore de plan d'entraînement. Envoie « plan d'entraînement : » suivi d'un jour par ligne.",
        "Du hast noch keinen Trainingsplan. Schick „trainingsplan:“ gefolgt von einem Tag pro Zeile.",
    ),
    "plan_header": _all(
        "Seu plano de treino:",
        "Je trainingsschema:",
        "Your training plan:",
        "Ton plan d'entraînement :",
        "Dein Trainingsplan:",
    ),
    "today_header": _all(
        "Treino de hoje ({day}):",
        "Training van vandaag ({day}):",
        "Today's workout ({day}):",
        "Entraînement du jour ({day}) :",
        "Training heute ({day}):",
    ),
    "today_last": _all(
        "última vez {kg} em {day}",
        "vorige keer {kg} op {day}",
        "last time {kg} on {day}",
        "dernière fois {kg} le {day}",
        "letztes Mal {kg} am {day}",
    ),
    "today_rest": _all(
        "Hoje ({day}) não tem treino no seu plano. Descanse.",
        "Vandaag ({day}) staat er geen training in je schema. Rust uit.",
        "There's no workout in your plan for today ({day}). Rest up.",
        "Pas d'entraînement prévu aujourd'hui ({day}). Repose-toi.",
        "Für heute ({day}) steht kein Training in deinem Plan. Ruh dich aus.",
    ),
    "load_bad": _all(
        "Não entendi a carga. Exemplo: “carga supino 62 kg”.",
        "Ik begrijp het gewicht niet. Voorbeeld: “gewicht bankdrukken 62 kg”.",
        "I didn't understand the load. Example: “load bench press 62 kg”.",
        "Je n'ai pas compris la charge. Exemple : « charge développé 62 kg ».",
        "Ich habe das Gewicht nicht verstanden. Beispiel: „gewicht bankdrücken 62 kg“.",
    ),
    "load_first": _all(
        "Anotei: {name} com {kg}.",
        "Genoteerd: {name} met {kg}.",
        "Noted: {name} at {kg}.",
        "C'est noté : {name} à {kg}.",
        "Notiert: {name} mit {kg}.",
    ),
    "load_same": _all(
        "Anotei: {name} com {kg}, a mesma carga de antes.",
        "Genoteerd: {name} met {kg}, hetzelfde als eerder.",
        "Noted: {name} at {kg}, same as before.",
        "C'est noté : {name} à {kg}, comme avant.",
        "Notiert: {name} mit {kg}, wie zuvor.",
    ),
    "load_changed": _all(
        "Anotei: {name} com {kg} (antes {before}, {delta}).",
        "Genoteerd: {name} met {kg} (eerder {before}, {delta}).",
        "Noted: {name} at {kg} (before {before}, {delta}).",
        "C'est noté : {name} à {kg} (avant {before}, {delta}).",
        "Notiert: {name} mit {kg} (vorher {before}, {delta}).",
    ),
    "evo_none": _all(
        "Ainda não tenho cargas anotadas para {name}. Diga, por exemplo, “carga {name} 60 kg”.",
        "Ik heb nog geen gewichten voor {name}. Zeg bijvoorbeeld “gewicht {name} 60 kg”.",
        "I have no loads for {name} yet. Say, for example, “load {name} 60 kg”.",
        "Je n'ai pas encore de charges pour {name}. Dis par exemple « charge {name} 60 kg ».",
        "Ich habe noch keine Gewichte für {name}. Sag zum Beispiel „gewicht {name} 60 kg“.",
    ),
    "evo_header": _all(
        "Evolução do {name}:",
        "Voortgang {name}:",
        "Progress on {name}:",
        "Évolution de {name} :",
        "Fortschritt bei {name}:",
    ),
    "evo_one": _all(
        "Com mais um registro eu mostro a evolução.",
        "Met nog één registratie laat ik de voortgang zien.",
        "One more entry and I can show the progress.",
        "Encore un relevé et je peux montrer la progression.",
        "Mit einem weiteren Eintrag zeige ich den Fortschritt.",
    ),
    "evo_delta": _all(
        "Desde {since}: {delta}.",
        "Sinds {since}: {delta}.",
        "Since {since}: {delta}.",
        "Depuis le {since} : {delta}.",
        "Seit {since}: {delta}.",
    ),
}
