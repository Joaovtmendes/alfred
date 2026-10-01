"""V2-09 — Health Score: a 0–100 note for the last weeks, with a plain explanation.

Computed when asked and never stored (health data, art. 9 AVG). It only reads what the member
already logs (workouts, habits, goals, health logs) and gives no medical judgement: a nudge,
not advice. With fewer than ``MIN_HISTORY_DAYS`` days of history it says it is still getting to
know the member instead of inventing a number.

The weights are fixed and shown to the member:  workouts 35 · habits 30 · goals 20 · health logs 15.
"""

# ruff: noqa: E501
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from alfred.clock import today_local
from alfred.models import Goal, HabitLog, HealthLog, Member, WorkoutSession

MIN_HISTORY_DAYS = 7
WEIGHTS: dict[str, int] = {"workouts": 35, "habits": 30, "goals": 20, "health": 15}
assert sum(WEIGHTS.values()) == 100

# What "full marks" means (per component), deliberately modest and the same for everyone.
WORKOUT_DAYS_7_FULL = 3  # 3 days with a workout in the last 7
WORKOUT_DAYS_30_FULL = 12  # 12 days in the last 30
HABIT_DAYS_7_FULL = 5
HABIT_DAYS_30_FULL = 20
HEALTH_DAYS_14_FULL = 10


@dataclass(frozen=True)
class Counts:
    workout_days_7: int = 0
    workout_days_30: int = 0
    habit_days_7: int = 0
    habit_days_30: int = 0
    health_days_14: int = 0
    active_goals: int = 0
    goals_with_activity_14: int = 0  # active goals with at least one habit log in 14 days


@dataclass(frozen=True)
class Score:
    total: int  # 0..100
    parts: dict[str, float]  # points earned per component (out of its weight)
    available: dict[str, bool]  # goals only count when the member has active goals


def _ratio(value: int, full: int) -> float:
    return max(0.0, min(1.0, value / full))


def compute(c: Counts) -> Score:
    """Pure function of the counts: deterministic, 0–100, bounded."""
    ratios = {
        "workouts": 0.5 * _ratio(c.workout_days_7, WORKOUT_DAYS_7_FULL)
        + 0.5 * _ratio(c.workout_days_30, WORKOUT_DAYS_30_FULL),
        "habits": 0.5 * _ratio(c.habit_days_7, HABIT_DAYS_7_FULL)
        + 0.5 * _ratio(c.habit_days_30, HABIT_DAYS_30_FULL),
        "goals": _ratio(c.goals_with_activity_14, c.active_goals) if c.active_goals else 0.0,
        "health": _ratio(c.health_days_14, HEALTH_DAYS_14_FULL),
    }
    available = {k: (k != "goals" or c.active_goals > 0) for k in WEIGHTS}
    parts = {k: ratios[k] * WEIGHTS[k] for k in WEIGHTS}
    max_available = sum(WEIGHTS[k] for k, ok in available.items() if ok)
    earned = sum(parts[k] for k, ok in available.items() if ok)
    total = round(earned * 100 / max_available) if max_available else 0
    return Score(max(0, min(100, total)), parts, available)


def best_next_step(c: Counts) -> tuple[str, int] | None:
    """The single small action that raises the note the most: ("workout"|"habit"|"health", points)."""
    base = compute(c).total
    options = {
        "workout": Counts(
            **{
                **c.__dict__,
                "workout_days_7": c.workout_days_7 + 1,
                "workout_days_30": c.workout_days_30 + 1,
            }
        ),
        "habit": Counts(
            **{
                **c.__dict__,
                "habit_days_7": c.habit_days_7 + 1,
                "habit_days_30": c.habit_days_30 + 1,
            }
        ),
        "health": Counts(**{**c.__dict__, "health_days_14": c.health_days_14 + 1}),
    }
    gains = {k: compute(v).total - base for k, v in options.items()}
    kind, gain = max(gains.items(), key=lambda kv: kv[1])
    return (kind, gain) if gain > 0 else None


async def _distinct_days(
    session: AsyncSession, col, member_col, member_id: uuid.UUID, since: date
) -> int:
    return int(
        await session.scalar(
            select(func.count(func.distinct(col))).where(member_col == member_id, col >= since)
        )
        or 0
    )


async def gather(session: AsyncSession, member_id: uuid.UUID, today: date) -> tuple[Counts, int]:
    """(counts, days of history). History = days since the member's first workout/habit/health log."""
    d7, d14, d30 = today - timedelta(days=6), today - timedelta(days=13), today - timedelta(days=29)
    w, h, hl = WorkoutSession, HabitLog, HealthLog
    goals = (
        (
            await session.execute(
                select(Goal.id).where(Goal.member_id == member_id, Goal.active.is_(True))
            )
        )
        .scalars()
        .all()
    )
    with_activity = 0
    if goals:
        with_activity = int(
            await session.scalar(
                select(func.count(func.distinct(h.goal_id))).where(
                    h.member_id == member_id, h.goal_id.in_(goals), h.log_date >= d14
                )
            )
            or 0
        )
    firsts = [
        await session.scalar(select(func.min(w.workout_date)).where(w.member_id == member_id)),
        await session.scalar(select(func.min(h.log_date)).where(h.member_id == member_id)),
        await session.scalar(select(func.min(hl.log_date)).where(hl.member_id == member_id)),
    ]
    firsts = [f for f in firsts if f is not None]
    history = (today - min(firsts)).days + 1 if firsts else 0
    counts = Counts(
        workout_days_7=await _distinct_days(session, w.workout_date, w.member_id, member_id, d7),
        workout_days_30=await _distinct_days(session, w.workout_date, w.member_id, member_id, d30),
        habit_days_7=await _distinct_days(session, h.log_date, h.member_id, member_id, d7),
        habit_days_30=await _distinct_days(session, h.log_date, h.member_id, member_id, d30),
        health_days_14=await _distinct_days(session, hl.log_date, hl.member_id, member_id, d14),
        active_goals=len(goals),
        goals_with_activity_14=with_activity,
    )
    return counts, history


_WORDS = {
    "nota de saude", "minha nota de saude", "minha nota", "nota da saude", "meu health score",
    "health score", "my health score", "meu score de saude", "score de saude", "meu score",
    "my score", "gezondheidsscore", "mijn gezondheidsscore", "gezondheidscore", "mon score sante",
    "score sante", "mon score de sante", "gesundheitsscore", "mein gesundheitsscore",
}  # fmt: skip
_CLEAN = re.compile(r"\s+")


async def handle_score_command(
    body_plain: str, member: Member, lang: str, session: AsyncSession
) -> str | None:
    from alfred.conversation import _t

    plain = _CLEAN.sub(" ", body_plain).strip(" .!?")
    for poss in (
        "o ",
        "a ",
        "my ",
        "mijn ",
        "mon ",
        "ma ",
        "mein ",
        "meine ",
        "the ",
        "de ",
        "het ",
    ):
        if plain.startswith(poss) and plain[len(poss) :] in _WORDS:
            plain = plain[len(poss) :]
            break
    if plain not in _WORDS:
        return None
    counts, history = await gather(session, member.id, today_local())
    if history < MIN_HISTORY_DAYS:
        return _t("score_learning", lang, days=MIN_HISTORY_DAYS)
    score = compute(counts)
    names = {k: _t(f"score_part_{k}", lang) for k in WEIGHTS}
    shown = [
        f"{names[k]} {round(score.parts[k])}/{WEIGHTS[k]}" for k in WEIGHTS if score.available[k]
    ]
    lines = [_t("score_header", lang, total=score.total), " · ".join(shown)]
    if not score.available["goals"]:
        lines.append(_t("score_no_goals", lang))
    best = max(
        (k for k in WEIGHTS if score.available[k]), key=lambda k: score.parts[k] / WEIGHTS[k]
    )
    lines.append(_t("score_best", lang, part=names[best].lower()))
    step = best_next_step(counts)
    if step:
        lines.append(_t(f"score_tip_{step[0]}", lang, points=step[1]))
    lines.append(_t("score_disclaimer", lang))
    return "\n".join(lines)


STRINGS: dict[str, dict[str, str | tuple[str, ...]]] = {  # fmt: skip
    "score_header": {
        "pt": "Sua nota de saúde: *{total}/100*",
        "nl": "Je gezondheidsscore: *{total}/100*",
        "en": "Your health score: *{total}/100*",
        "fr": "Ton score santé : *{total}/100*",
        "de": "Dein Gesundheitsscore: *{total}/100*",
    },
    "score_learning": {
        "pt": "Ainda estou te conhecendo. Preciso de pelo menos {days} dias de registros (treinos, hábitos ou saúde) para calcular sua nota. Continue registrando!",
        "nl": "Ik leer je nog kennen. Ik heb minstens {days} dagen aan registraties (training, gewoontes of gezondheid) nodig om je score te berekenen. Blijf loggen!",
        "en": "I'm still getting to know you. I need at least {days} days of entries (workouts, habits or health) to work out your score. Keep logging!",
        "fr": "Je fais encore ta connaissance. Il me faut au moins {days} jours d'enregistrements (sport, habitudes ou santé) pour calculer ton score. Continue !",
        "de": "Ich lerne dich noch kennen. Für deinen Score brauche ich mindestens {days} Tage an Einträgen (Training, Gewohnheiten oder Gesundheit). Bleib dran!",
    },
    "score_part_workouts": {
        "pt": "Treinos",
        "nl": "Training",
        "en": "Workouts",
        "fr": "Sport",
        "de": "Training",
    },
    "score_part_habits": {
        "pt": "Hábitos",
        "nl": "Gewoontes",
        "en": "Habits",
        "fr": "Habitudes",
        "de": "Gewohnheiten",
    },
    "score_part_goals": {
        "pt": "Metas",
        "nl": "Doelen",
        "en": "Goals",
        "fr": "Objectifs",
        "de": "Ziele",
    },
    "score_part_health": {
        "pt": "Registros de saúde",
        "nl": "Gezondheidslogs",
        "en": "Health logs",
        "fr": "Suivi santé",
        "de": "Gesundheitslogs",
    },
    "score_no_goals": {
        "pt": "Sem metas ativas, essa parte não entra na conta.",
        "nl": "Zonder actieve doelen telt dit onderdeel niet mee.",
        "en": "With no active goals, that part is left out of the total.",
        "fr": "Sans objectif actif, cette partie ne compte pas.",
        "de": "Ohne aktive Ziele zählt dieser Teil nicht mit.",
    },
    "score_best": {
        "pt": "O que mais ajudou: {part}.",
        "nl": "Wat het meest hielp: {part}.",
        "en": "What helped most: {part}.",
        "fr": "Ce qui a le plus aidé : {part}.",
        "de": "Am meisten geholfen hat: {part}.",
    },
    "score_tip_workout": {
        "pt": "Dica: mais 1 dia de treino sobe cerca de {points} pontos.",
        "nl": "Tip: nog 1 trainingsdag levert ongeveer {points} punten op.",
        "en": "Tip: one more workout day adds about {points} points.",
        "fr": "Astuce : un jour de sport de plus ajoute environ {points} points.",
        "de": "Tipp: ein weiterer Trainingstag bringt etwa {points} Punkte.",
    },
    "score_tip_habit": {
        "pt": "Dica: registrar mais 1 dia de hábito sobe cerca de {points} pontos.",
        "nl": "Tip: nog 1 dag een gewoonte loggen levert ongeveer {points} punten op.",
        "en": "Tip: logging one more habit day adds about {points} points.",
        "fr": "Astuce : enregistrer un jour d'habitude de plus ajoute environ {points} points.",
        "de": "Tipp: einen weiteren Gewohnheitstag zu erfassen bringt etwa {points} Punkte.",
    },
    "score_tip_health": {
        "pt": "Dica: registrar mais 1 dia de saúde (sono, água, humor) sobe cerca de {points} pontos.",
        "nl": "Tip: nog 1 dag gezondheid loggen (slaap, water, stemming) levert ongeveer {points} punten op.",
        "en": "Tip: logging one more health day (sleep, water, mood) adds about {points} points.",
        "fr": "Astuce : un jour de suivi santé de plus (sommeil, eau, humeur) ajoute environ {points} points.",
        "de": "Tipp: einen weiteren Gesundheitstag zu erfassen (Schlaf, Wasser, Stimmung) bringt etwa {points} Punkte.",
    },
    "score_disclaimer": {
        "pt": "É só um incentivo com base no que você registra, não orientação médica.",
        "nl": "Dit is alleen een stimulans op basis van wat je logt, geen medisch advies.",
        "en": "This is just a nudge based on what you log, not medical advice.",
        "fr": "C'est juste un encouragement basé sur ce que tu enregistres, pas un avis médical.",
        "de": "Das ist nur ein Anstoß auf Basis deiner Einträge, keine medizinische Beratung.",
    },
}
