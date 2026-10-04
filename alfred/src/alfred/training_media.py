# ruff: noqa: E501
"""V2-35b — a training plan read from a photo or a PDF (see ``media_input`` for the intake).

The model's JSON is untrusted: every field is cleaned and capped here before it becomes the same
``PlanDay`` list a typed plan produces, and the same preview with Confirm/Cancel follows.
"""

from __future__ import annotations

import re

from sqlalchemy.ext.asyncio import AsyncSession

from alfred.models import Member
from alfred.parsing import strip_accents
from alfred.training import (
    MAX_DAYS,
    MAX_EXERCISE,
    MAX_ITEMS_PER_DAY,
    MAX_KG,
    WEEKDAYS,
    PlanDay,
    PlanItem,
    Reply,
    draft_days,
)

_REPS = re.compile(r"^\d{1,3}(?:-\d{1,3})?$")
_MARKUP = re.compile(r"[*_~`]")


def _t(key: str, lang: str, **kw: object) -> str:
    from alfred.conversation import _t as t

    return t(key, lang, **kw)


def _text(value: object, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(_MARKUP.sub("", value).split())[:limit].strip()


def _weekday(value: object) -> int | None | str:
    """0-6, None when the plan shows no weekday, "bad" when the value is not a weekday."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return "bad"
    if isinstance(value, int):
        return value if 0 <= value <= 6 else "bad"
    if isinstance(value, str):
        found = WEEKDAYS.get(strip_accents(value.strip().lower()))
        return "bad" if found is None else found
    return "bad"


def _item(raw: object) -> PlanItem | None:
    if not isinstance(raw, dict):
        return None
    name = _text(raw.get("exercise"), MAX_EXERCISE)
    if not name:
        return None
    sets, reps, kg = raw.get("sets"), raw.get("reps"), raw.get("load_kg")
    if isinstance(reps, int) and not isinstance(reps, bool):
        reps = str(reps)
    reps = reps.replace(" ", "") if isinstance(reps, str) else None
    ok = (
        isinstance(sets, int)
        and not isinstance(sets, bool)
        and 1 <= sets <= 99
        and reps is not None
        and _REPS.match(reps) is not None
    )
    load = None
    if isinstance(kg, int | float) and not isinstance(kg, bool) and 0 < kg <= MAX_KG:
        load = round(float(kg), 2)
    return PlanItem(name, sets if ok else None, reps if ok else None, load)


def days_from_reading(reading: object) -> tuple[list[PlanDay], bool]:
    """Clean what the model read: ``(days, assumed)``; ``assumed`` when weekdays had to be chosen."""
    if not isinstance(reading, dict) or reading.get("kind") != "workout_plan":
        return [], False
    raw_days = reading.get("days")
    if not isinstance(raw_days, list):
        return [], False
    kept: list[tuple[int | None, str, list[PlanItem]]] = []
    for raw in raw_days:
        if not isinstance(raw, dict):
            continue
        weekday = _weekday(raw.get("weekday"))
        if weekday == "bad":
            continue
        items_raw = raw.get("items")
        items = [
            i
            for i in (_item(x) for x in (items_raw if isinstance(items_raw, list) else []))
            if i is not None
        ][:MAX_ITEMS_PER_DAY]
        if not items:
            continue
        kept.append((weekday, _text(raw.get("title"), 80), items))  # type: ignore[arg-type]
    used: set[int] = set()
    days: list[PlanDay] = []
    for weekday, title, items in kept:
        if isinstance(weekday, int) and weekday not in used:
            used.add(weekday)
            days.append(PlanDay(weekday, title, items))
    assumed = False
    for weekday, title, items in kept:
        if weekday is None:
            free = next((d for d in range(7) if d not in used), None)
            if free is None:
                break
            used.add(free)
            days.append(PlanDay(free, title, items))
            assumed = True
    return sorted(days, key=lambda d: d.weekday)[:MAX_DAYS], assumed


async def plan_reply(
    reading: dict, member: Member, lang: str, session: AsyncSession
) -> Reply | None:
    """The preview of the plan in ``reading``; None when nothing usable was read."""
    days, assumed = days_from_reading(reading)
    if not days:
        return None
    note = _t("planimg_assumed", lang) if assumed else ""
    return await draft_days(days, member, lang, session, note)


def _all(pt: str, nl: str, en: str, fr: str, de: str) -> dict[str, str]:
    return {"pt": pt, "nl": nl, "en": en, "fr": fr, "de": de}


STRINGS: dict[str, dict[str, str]] = {
    "planimg_assumed": _all(
        "O arquivo não mostra os dias da semana, então coloquei os treinos a partir de segunda, na ordem. Se não for isso, cancele e escreva o plano em texto.",
        "Het bestand toont geen weekdagen, dus ik heb de trainingen vanaf maandag op volgorde gezet. Klopt dat niet, annuleer dan en typ het schema.",
        "The file doesn't show weekdays, so I placed the workouts from Monday on, in order. If that's wrong, cancel and type the plan.",
        "Le fichier n'indique pas les jours de la semaine, j'ai donc placé les séances à partir de lundi, dans l'ordre. Si ce n'est pas bon, annule et écris le plan.",
        "Die Datei zeigt keine Wochentage, daher habe ich die Einheiten ab Montag der Reihe nach gelegt. Stimmt das nicht, brich ab und tippe den Plan.",
    ),
}
