# ruff: noqa: E501
"""Habit follow-ups (hard test F10-11/12/17, F10-21/22, F10-30).

* a habit logged with no goal offers to create one (every day / 3x a week / other);
* a mood note gets a short answer and an emoji by range; a score above 10 is checked;
* ``count_word`` writes "1 registro" in the singular.
"""

from __future__ import annotations

import re
import uuid
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from alfred import questions
from alfred.audit import audit
from alfred.couple import Reply
from alfred.models import AuditLog, Goal, HabitLog, Member


def _t(key: str, lang: str, **kw: object) -> str:
    from alfred.conversation import _t as t

    return t(key, lang, **kw)


def _all(pt: str, nl: str, en: str, fr: str, de: str) -> dict[str, str]:
    return {"pt": pt, "nl": nl, "en": en, "fr": fr, "de": de}


STRINGS: dict[str, dict[str, str]] = {
    "habit_goal_offer": _all(
        "Quer criar uma meta de *{activity}*? Com que frequência?",
        "Wil je een doel maken voor *{activity}*? Hoe vaak?",
        "Want to set a goal for *{activity}*? How often?",
        "Tu veux créer un objectif pour *{activity}* ? À quelle fréquence ?",
        "Möchtest du ein Ziel für *{activity}* anlegen? Wie oft?",
    ),
    "btn_goal_daily": _all("Todo dia", "Elke dag", "Every day", "Chaque jour", "Jeden Tag"),
    "btn_goal_3x": _all(
        "3x por semana", "3x per week", "3x a week", "3x par semaine", "3x pro Woche"
    ),
    "btn_goal_other": _all("Outra", "Anders", "Other", "Autre", "Andere"),
    "habit_goal_times_ask": _all(
        "Quantas vezes por semana? Responda com um número de 1 a 7.",
        "Hoe vaak per week? Antwoord met een getal van 1 tot 7.",
        "How many times a week? Reply with a number from 1 to 7.",
        "Combien de fois par semaine ? Réponds avec un nombre de 1 à 7.",
        "Wie oft pro Woche? Antworte mit einer Zahl von 1 bis 7.",
    ),
    "habit_goal_created": _all(
        "Meta criada: *{title}*. Cada registro conta para ela.",
        "Doel gemaakt: *{title}*. Elke registratie telt mee.",
        "Goal created: *{title}*. Every log counts toward it.",
        "Objectif créé : *{title}*. Chaque enregistrement compte.",
        "Ziel angelegt: *{title}*. Jeder Eintrag zählt dafür.",
    ),
    "habit_freq_daily": _all("todo dia", "elke dag", "every day", "chaque jour", "jeden Tag"),
    "habit_freq_n": _all(
        "{n}x por semana", "{n}x per week", "{n}x a week", "{n}x par semaine", "{n}x pro Woche"
    ),
    "mood_band_low": _all(
        "Anotado: {value} de 10 hoje. 💙 Dias assim passam; se quiser falar, estou aqui.",
        "Genoteerd: {value} van 10 vandaag. 💙 Zulke dagen gaan voorbij; wil je praten, dan ben ik er.",
        "Noted: {value} out of 10 today. 💙 Days like this pass; I'm here if you want to talk.",
        "Noté : {value} sur 10 aujourd'hui. 💙 Ces jours passent ; je suis là si tu veux parler.",
        "Notiert: heute {value} von 10. 💙 Solche Tage gehen vorbei; ich bin da, wenn du reden möchtest.",
    ),
    "mood_band_mid": _all(
        "Anotado: {value} de 10 hoje. 🙂",
        "Genoteerd: {value} van 10 vandaag. 🙂",
        "Noted: {value} out of 10 today. 🙂",
        "Noté : {value} sur 10 aujourd'hui. 🙂",
        "Notiert: heute {value} von 10. 🙂",
    ),
    "mood_band_good": _all(
        "Anotado: {value} de 10 hoje. 😊 Bom dia!",
        "Genoteerd: {value} van 10 vandaag. 😊 Fijne dag!",
        "Noted: {value} out of 10 today. 😊 Good day!",
        "Noté : {value} sur 10 aujourd'hui. 😊 Belle journée !",
        "Notiert: heute {value} von 10. 😊 Schöner Tag!",
    ),
    "mood_band_top": _all(
        "Anotado: {value} de 10 hoje. 🌟 Que dia!",
        "Genoteerd: {value} van 10 vandaag. 🌟 Wat een dag!",
        "Noted: {value} out of 10 today. 🌟 What a day!",
        "Noté : {value} sur 10 aujourd'hui. 🌟 Quelle journée !",
        "Notiert: heute {value} von 10. 🌟 Was für ein Tag!",
    ),
    "mood_over_ask": _all(
        "A escala vai de 0 a 10 e você escreveu {value}. Registro 10?",
        "De schaal loopt van 0 tot 10 en je schreef {value}. Zal ik 10 noteren?",
        "The scale goes from 0 to 10 and you wrote {value}. Log it as 10?",
        "L'échelle va de 0 à 10 et tu as écrit {value}. J'enregistre 10 ?",
        "Die Skala reicht von 0 bis 10 und du hast {value} geschrieben. Soll ich 10 eintragen?",
    ),
    "btn_mood_ten": _all("Registrar 10", "10 noteren", "Log 10", "Noter 10", "10 eintragen"),
    "btn_mood_cancel": _all("Cancelar", "Annuleren", "Cancel", "Annuler", "Abbrechen"),
    "mood_over_cancelled": _all(
        "Certo, não registrei. Diga o humor de 0 a 10 quando quiser.",
        "Prima, niets genoteerd. Zeg je stemming van 0 tot 10 wanneer je wilt.",
        "Okay, nothing logged. Tell me your mood from 0 to 10 whenever you like.",
        "D'accord, rien noté. Dis-moi ton humeur de 0 à 10 quand tu veux.",
        "Okay, nichts eingetragen. Sag mir deine Stimmung von 0 bis 10, wann du magst.",
    ),
}

_ENTRIES = {
    "pt": ("registro", "registros"),
    "nl": ("registratie", "registraties"),
    "en": ("entry", "entries"),
    "fr": ("entrée", "entrées"),
    "de": ("Eintrag", "Einträge"),
}


def count_word(n: int, lang: str) -> str:
    """ "1 registro" / "3 registros" (F10-30)."""
    one, many = _ENTRIES.get(lang, _ENTRIES["en"])
    return f"{n} {one if n == 1 else many}"


def mood_reply(value: int, lang: str) -> str:
    key = (
        "mood_band_low"
        if value <= 3
        else "mood_band_mid"
        if value <= 6
        else "mood_band_good"
        if value <= 8
        else "mood_band_top"
    )
    return _t(key, lang, value=value)


# ── mood above 10 ─────────────────────────────────────────────────────────────


def mood_value(raw: object) -> int | None:
    m = re.match(r"\s*(\d{1,3})", str(raw))
    return int(m.group(1)) if m else None


async def ask_mood_over(
    session: AsyncSession, member: Member, lang: str, health_data: dict, log_date
):
    return await questions.ask(
        session,
        member,
        "mood_over",
        {"date": log_date.isoformat(), "value": mood_value(health_data["value"]), "yes_no": True},
        _t("mood_over_ask", lang, value=mood_value(health_data["value"])),
        [("yes", _t("btn_mood_ten", lang)), ("no", _t("btn_mood_cancel", lang))],
    )


async def _on_mood_over(session, member, lang, data, choice, today) -> Reply:
    from datetime import date as _date

    from alfred.models import HealthLog

    if choice != "yes":
        return Reply(_t("mood_over_cancelled", lang), [])
    session.add(
        HealthLog(
            id=uuid.uuid4(),
            member_id=member.id,
            log_type="mood",
            value="10",
            log_date=_date.fromisoformat(data["date"]),
        )
    )
    return Reply(mood_reply(10, lang), [])


# ── a habit with no goal ──────────────────────────────────────────────────────


async def _already_offered(session: AsyncSession, member_id: uuid.UUID, activity: str) -> bool:
    found = await session.scalar(
        select(AuditLog.id)
        .where(
            AuditLog.member_id == member_id,
            AuditLog.event == "habit_goal_offered",
            AuditLog.detail["activity"].astext == activity.strip().lower(),
        )
        .limit(1)
    )
    return found is not None


async def offer_goal(
    session: AsyncSession, member: Member, lang: str, activity: str, logged_text: str
) -> Reply | str:
    """The "Anotei" text plus the goal question, once per habit."""
    from alfred.conversation import _habit_label

    if await _already_offered(session, member.id, activity):
        return logged_text
    audit(session, "habit_goal_offered", member.id, activity=activity.strip().lower())
    label = _habit_label(activity, lang)
    reply = await questions.ask(
        session,
        member,
        "habit_goal",
        {"activity": activity, "label": label},
        f"{logged_text}\n\n{_t('habit_goal_offer', lang, activity=label)}",
        [
            ("daily", _t("btn_goal_daily", lang)),
            ("3", _t("btn_goal_3x", lang)),
            ("other", _t("btn_goal_other", lang)),
        ],
    )
    return reply


def _parse_times(text: str, data: dict) -> str | None:
    m = re.fullmatch(r"\s*([1-7])\s*(?:x|vezes|keer|times|fois|mal)?\s*", text.lower())
    return m.group(1) if m else None


async def _on_habit_goal(session, member, lang, data, choice, today) -> Reply:
    if choice == "other":
        return await questions.ask(
            session, member, "habit_goal_times", data, _t("habit_goal_times_ask", lang), []
        )
    return await _create_goal(session, member, lang, data, 7 if choice == "daily" else int(choice))


async def _on_times(session, member, lang, data, choice, today) -> Reply:
    return await _create_goal(session, member, lang, data, int(choice))


async def _create_goal(session, member, lang, data, per_week: int) -> Reply:
    freq = _t("habit_freq_daily", lang) if per_week == 7 else _t("habit_freq_n", lang, n=per_week)
    title = f"{str(data['label']).capitalize()} {freq}"
    session.add(
        Goal(
            id=uuid.uuid4(),
            member_id=member.id,
            title=title,
            target_value=f"{per_week}x",
            target_unit="per week",
            active=True,
        )
    )
    audit(session, "goal_created", member.id)
    return Reply(_t("habit_goal_created", lang, title=title), [])


questions.register("mood_over", _on_mood_over)
questions.register("habit_goal", _on_habit_goal)
questions.register("habit_goal_times", _on_times, _parse_times)


# ── F10-10: "li 30 páginas" keeps the 30 and the week adds up ────────────────

_UNITS = {
    "paginas": "páginas", "pagina": "páginas", "pages": "pages", "page": "pages",
    "bladzijden": "bladzijden", "bladzijde": "bladzijden", "seiten": "Seiten", "seite": "Seiten",
    "minutos": "min", "minuto": "min", "minutes": "min", "minuten": "min", "min": "min",
    "minute": "min", "km": "km", "copos": "copos", "copo": "copos", "glasses": "glasses",
    "glazen": "glazen", "verres": "verres", "gläser": "Gläser", "glaeser": "Gläser",
    "litros": "L", "litro": "L", "liters": "L", "l": "L", "horas": "h", "hora": "h",
}  # fmt: skip
_QTY_RE = re.compile(
    r"\b(\d{1,4}(?:[.,]\d{1,2})?)\s*(" + "|".join(sorted(_UNITS, key=len, reverse=True)) + r")\b"
)
STRINGS.update(
    {
        "habit_logged_qty": _all(
            "Anotei: {activity}, {qty}. Esta semana: {week}.",
            "Genoteerd: {activity}, {qty}. Deze week: {week}.",
            "Noted: {activity}, {qty}. This week: {week}.",
            "Noté : {activity}, {qty}. Cette semaine : {week}.",
            "Notiert: {activity}, {qty}. Diese Woche: {week}.",
        ),
        "habit_added_qty": _all(
            "Somei ao de hoje: {activity}, {qty}. Esta semana: {week}.",
            "Erbij voor vandaag: {activity}, {qty}. Deze week: {week}.",
            "Added to today: {activity}, {qty}. This week: {week}.",
            "Ajouté à aujourd'hui : {activity}, {qty}. Cette semaine : {week}.",
            "Zu heute addiert: {activity}, {qty}. Diese Woche: {week}.",
        ),
    }
)


def parse_quantity(body_plain: str) -> tuple[float, str] | None:
    m = _QTY_RE.search(body_plain)
    if not m:
        return None
    return float(m.group(1).replace(",", ".")), _UNITS[m.group(2)]


def fmt_qty(qty: float, unit: str) -> str:
    n = f"{qty:.2f}".rstrip("0").rstrip(".").replace(".", ",")
    return f"{n} {unit}"


async def week_total(
    session: AsyncSession, member_id: uuid.UUID, activity: str, unit: str, today: date
) -> float:
    start = today - timedelta(days=today.weekday())
    total = await session.scalar(
        select(func.coalesce(func.sum(HabitLog.quantity), 0)).where(
            HabitLog.member_id == member_id,
            func.lower(HabitLog.activity) == activity.strip().lower(),
            HabitLog.unit == unit,
            HabitLog.log_date >= start,
            HabitLog.log_date <= today,
        )
    )
    return float(total or 0)


async def log_with_quantity(
    session: AsyncSession,
    member: Member,
    lang: str,
    activity: str,
    goal: Goal | None,
    qty: float,
    unit: str,
    today: date,
) -> str:
    """Save (or add to today's) log with its quantity; the answer shows the week so far."""
    existing = await session.scalar(
        select(HabitLog).where(
            HabitLog.member_id == member.id,
            func.lower(HabitLog.activity) == activity.strip().lower(),
            HabitLog.log_date == today,
        )
    )
    if existing is not None:
        existing.quantity = float(existing.quantity or 0) + qty if existing.unit == unit else qty
        existing.unit = unit
        session.add(existing)
        key = "habit_added_qty"
    else:
        session.add(
            HabitLog(
                id=uuid.uuid4(),
                member_id=member.id,
                goal_id=goal.id if goal else None,
                activity=activity,
                log_date=today,
                quantity=qty,
                unit=unit,
            )
        )
        key = "habit_logged_qty"
    await session.flush()
    week = await week_total(session, member.id, activity, unit, today)
    return _t(key, lang, activity=activity, qty=fmt_qty(qty, unit), week=fmt_qty(week, unit))
