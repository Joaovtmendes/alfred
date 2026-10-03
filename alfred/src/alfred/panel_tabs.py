# ruff: noqa: E501
"""Cards of the Agenda, Hábitos and Viagens tabs of the v2 panel (read-only, one query per card).

Cards take the request context of ``panel_api`` (session, member id, language, today, ``hint``)
and return the same shape as every other card: ``id``, ``empty``, ``values``, ``phrase`` and, when
empty, ``hint``. Everything here describes what the member recorded; nothing is diagnosed, scored
or advised. The tabs have no filters: each card has its own fixed window, named in its title.

Agenda cards
------------
``week``       values {count, days}; days [{date, weekday (0 = Monday), items [{time "HH:MM", title,
               notes|null}]}] for today and the next 6 days; ``more`` = items not shown (20 shown).
``tasks``      values {open, overdue, due_week, no_due, done_week}; items [{body, due|null, days|null,
               kind: "overdue"|"due"}] (overdue first, oldest first; then due in 7 days; 6 at most).
``reminders``  values {count}; items [{kind, text|null, time "HH:MM", days: [0..6], every_day}] (8 at most).
``month_map``  values {count, peak|null}; weeks [[{date, count, today, past}] x 7] x 4 starting on the
               Monday of this week.
``notes``      values {count}; items [{date, body (200 characters at most)}] newest first (5).

Hábitos cards
-------------
``water``      values {today, average, days}; points [{date, litres}] for the last 7 days (litres 0 = nothing logged).
``workouts``   values {this_week, last_week, km_week, minutes_week}; weeks [{start, count}] (4, oldest
               first); activities [{activity, count}] this week.
``goals``      values {count}; items [{title, target|null, deadline|null, logs_7d, logs_30d}] (6).

Viagens cards
-------------
``trip``       the active trip, else the next one that has not started, else the latest. values {budget|null,
               spent, remaining|null, pct|null, days, planned_days|null}; trip {destination, start, end|null,
               state: "active"|"upcoming"|"ended", days_to_start|null}; categories [{category, label, amount}]
               (5); points [{date, amount}] (at most 62 days).
``trips_past`` values {count, with_budget, within}; items [{destination, start, end|null, days, spent, budget|null,
               within|null}] newest first (6).
"""

from __future__ import annotations

import uuid
from collections import Counter, defaultdict
from datetime import date, timedelta
from typing import Any

from sqlalchemy import func, select

from alfred import panel_phrases as pp
from alfred.clock import day_start, to_local, week_start
from alfred.models import (
    SETTLED,
    Appointment,
    Expense,
    Goal,
    HabitLog,
    HealthLog,
    Note,
    ScheduledJob,
    Task,
    Trip,
    WorkoutSession,
)
from alfred.panel_calc import _local_day, dec, money

_WEEK_ITEMS = 20
_TASK_ITEMS = 6
_REMINDER_ITEMS = 8
_NOTE_ITEMS = 5
_NOTE_CHARS = 200
_TRIP_DAYS_SHOWN = 62
_PAST_TRIPS = 6
_ALL_DAYS = 127
_REMINDER_KINDS = ("medication_reminder", "goal_checkin", "workout_reminder", "weekly_summary")


def _phrase(p: pp.Phrase | None) -> dict[str, Any] | None:
    if p is None:
        return None
    return {"key": p.key, "text": p.text, "chat": p.chat, "severity": p.severity}


def _card(
    card_id: str, empty: bool, values: dict[str, Any], ctx, hint: str | None = None, **extra
) -> dict[str, Any]:
    card: dict[str, Any] = {
        "id": card_id,
        "empty": empty,
        "values": values,
        **extra,
        "phrase": None,
    }
    if empty and hint:
        card["hint"] = ctx.hint(hint)
    return card


def _num(raw: str | None) -> float | None:
    """A logged number ("7", "6,5", " 0.5 "); anything else is ignored."""
    try:
        value = float((raw or "").strip().replace(",", "."))
    except ValueError:
        return None
    return value if value == value and abs(value) < 1e6 else None  # not NaN, not absurd


# ── Agenda ────────────────────────────────────────────────────────────────────


async def _appointments(ctx, first: date, days: int) -> list[tuple[date, Appointment]]:
    stmt = (
        select(Appointment)
        .where(
            Appointment.member_id == ctx.mid,
            Appointment.status == "active",
            Appointment.starts_at >= day_start(first),
            Appointment.starts_at < day_start(first + timedelta(days=days)),
        )
        .order_by(Appointment.starts_at)
        .limit(500)
    )
    rows = (await ctx.session.execute(stmt)).scalars().all()
    return [(to_local(a.starts_at).date(), a) for a in rows]


async def week_card(ctx) -> dict[str, Any]:
    rows = await _appointments(ctx, ctx.today, 7)
    by_day: dict[date, list[Appointment]] = defaultdict(list)
    for day, appt in rows:
        by_day[day].append(appt)
    days = []
    shown = 0
    for day in sorted(by_day):
        items = []
        for appt in by_day[day]:
            if shown >= _WEEK_ITEMS:
                break
            shown += 1
            items.append(
                {
                    "time": to_local(appt.starts_at).strftime("%H:%M"),
                    "title": appt.title,
                    "notes": (appt.notes or "").strip()[:120] or None,
                }
            )
        if items:
            days.append({"date": day.isoformat(), "weekday": day.weekday(), "items": items})
    card = _card(
        "week",
        not rows,
        {"count": len(rows), "days": len(by_day)},
        ctx,
        "agenda",
        days=days,
        more=max(len(rows) - shown, 0),
    )
    if rows:
        card["phrase"] = _phrase(
            pp.rule_agenda_week({d.weekday(): len(v) for d, v in by_day.items()}, ctx.lang)
        )
    return card


async def tasks_card(ctx) -> dict[str, Any]:
    today = ctx.today
    stmt = (
        select(Task.body, Task.due_date)
        .where(Task.member_id == ctx.mid, Task.done_at.is_(None))
        .limit(1000)
    )
    open_rows = (await ctx.session.execute(stmt)).all()
    done_since = day_start(today - timedelta(days=6))
    done = (
        await ctx.session.execute(
            select(func.count())
            .select_from(Task)
            .where(Task.member_id == ctx.mid, Task.done_at >= done_since)
        )
    ).scalar_one()
    overdue = sorted(
        (r for r in open_rows if r.due_date and r.due_date < today), key=lambda r: r.due_date
    )
    soon = sorted(
        (r for r in open_rows if r.due_date and today <= r.due_date < today + timedelta(days=7)),
        key=lambda r: r.due_date,
    )
    no_due = sum(1 for r in open_rows if not r.due_date)
    items = [
        {
            "body": r.body,
            "due": r.due_date.isoformat(),
            "days": (today - r.due_date).days,
            "kind": "overdue",
        }
        for r in overdue
    ] + [
        {
            "body": r.body,
            "due": r.due_date.isoformat(),
            "days": (r.due_date - today).days,
            "kind": "due",
        }
        for r in soon
    ]
    values = {
        "open": len(open_rows),
        "overdue": len(overdue),
        "due_week": len(soon),
        "no_due": no_due,
        "done_week": int(done),
    }
    card = _card(
        "tasks", not open_rows and not done, values, ctx, "tasks", items=items[:_TASK_ITEMS]
    )
    if open_rows:
        card["phrase"] = _phrase(
            pp.rule_tasks(
                len(overdue),
                overdue[0].body if overdue else None,
                (today - overdue[0].due_date).days if overdue else 0,
                len(soon),
                soon[0].body if soon else None,
                (soon[0].due_date - today).days if soon else 0,
                ctx.lang,
            )
        )
    return card


def _days_of(mask: int) -> list[int]:
    return [i for i in range(7) if mask & (1 << i)]


async def reminders_card(ctx) -> dict[str, Any]:
    stmt = (
        select(ScheduledJob)
        .where(
            ScheduledJob.member_id == ctx.mid,
            ScheduledJob.active.is_(True),
            ScheduledJob.job_type.in_(_REMINDER_KINDS),
        )
        .order_by(ScheduledJob.time_of_day, ScheduledJob.created_at)
        .limit(50)
    )
    rows = (await ctx.session.execute(stmt)).scalars().all()
    items = []
    for job in rows[:_REMINDER_ITEMS]:
        text = (job.payload or {}).get("text") if isinstance(job.payload, dict) else None
        items.append(
            {
                "kind": job.job_type,
                "text": str(text).strip()[:120] if text else None,
                "time": job.time_of_day,
                "days": _days_of(job.days_mask),
                "every_day": job.days_mask == _ALL_DAYS,
            }
        )
    return _card("reminders", not rows, {"count": len(rows)}, ctx, "reminders", items=items)


async def month_map_card(ctx) -> dict[str, Any]:
    first = week_start(ctx.today)
    rows = await _appointments(ctx, first, 28)
    per_day = Counter(day for day, _ in rows)
    weeks = [
        [
            {
                "date": (d := first + timedelta(days=w * 7 + i)).isoformat(),
                "count": per_day.get(d, 0),
                "today": d == ctx.today,
                "past": d < ctx.today,
            }
            for i in range(7)
        ]
        for w in range(4)
    ]
    ahead = [(d, n) for d, n in per_day.items() if d >= ctx.today]
    by_weekday = Counter()
    for d, n in ahead:
        by_weekday[d.weekday()] += n
    peak = max(by_weekday, key=lambda k: (by_weekday[k], -k)) if by_weekday else None
    card = _card(
        "month_map",
        not rows,
        {"count": sum(by_weekday.values()), "peak": peak},
        ctx,
        "agenda",
        weeks=weeks,
    )
    if ahead:
        card["phrase"] = _phrase(pp.rule_agenda_map(dict(by_weekday), ctx.lang))
    return card


async def notes_card(ctx) -> dict[str, Any]:
    total = (
        await ctx.session.execute(
            select(func.count()).select_from(Note).where(Note.member_id == ctx.mid)
        )
    ).scalar_one()
    rows = (
        await ctx.session.execute(
            select(Note.body, Note.created_at)
            .where(Note.member_id == ctx.mid)
            .order_by(Note.created_at.desc())
            .limit(_NOTE_ITEMS)
        )
    ).all()
    items = [
        {"date": to_local(r.created_at).date().isoformat(), "body": r.body.strip()[:_NOTE_CHARS]}
        for r in rows
    ]
    return _card("notes", not rows, {"count": int(total)}, ctx, "notes", items=items)


# ── Hábitos ───────────────────────────────────────────────────────────────────


async def _health(ctx, log_type: str, first: date) -> list[tuple[date, str, str | None]]:
    stmt = (
        select(HealthLog.log_date, HealthLog.value, HealthLog.unit)
        .where(
            HealthLog.member_id == ctx.mid,
            HealthLog.log_type == log_type,
            HealthLog.log_date >= first,
            HealthLog.log_date <= ctx.today,
        )
        .order_by(HealthLog.log_date, HealthLog.created_at)
        .limit(2000)
    )
    return [(r.log_date, r.value, r.unit) for r in (await ctx.session.execute(stmt)).all()]


def _span(today: date, days: int) -> list[date]:
    return [today - timedelta(days=days - 1 - i) for i in range(days)]


async def water_card(ctx) -> dict[str, Any]:
    span = _span(ctx.today, 7)
    litres: dict[date, float] = defaultdict(float)
    for day, raw, unit in await _health(ctx, "water", span[0]):
        n = _num(raw)
        if n is None or n <= 0:
            continue
        litres[day] += n / 1000 if (unit or "").lower() == "ml" else n
    logged = [v for v in litres.values() if v > 0]
    avg = sum(logged) / len(logged) if logged else 0.0
    values = {
        "today": round(litres.get(ctx.today, 0.0), 2),
        "average": round(avg, 2),
        "days": len(logged),
    }
    card = _card(
        "water",
        not logged,
        values,
        ctx,
        "water",
        points=[{"date": d.isoformat(), "litres": round(litres.get(d, 0.0), 2)} for d in span],
    )
    if logged:
        card["phrase"] = _phrase(pp.rule_water(avg, len(logged), ctx.lang))
    return card


async def workouts_card(ctx) -> dict[str, Any]:
    this_monday = week_start(ctx.today)
    first = this_monday - timedelta(weeks=3)
    stmt = (
        select(
            WorkoutSession.workout_date,
            WorkoutSession.activity_type,
            WorkoutSession.duration_minutes,
            WorkoutSession.distance_km,
        )
        .where(
            WorkoutSession.member_id == ctx.mid,
            WorkoutSession.workout_date >= first,
            WorkoutSession.workout_date <= ctx.today,
        )
        .limit(2000)
    )
    rows = (await ctx.session.execute(stmt)).all()
    per_week: Counter[date] = Counter(week_start(r.workout_date) for r in rows)
    mine = [r for r in rows if week_start(r.workout_date) == this_monday]
    activities = Counter(
        (r.activity_type or "").strip().lower() for r in mine if (r.activity_type or "").strip()
    )
    values = {
        "this_week": len(mine),
        "last_week": per_week.get(this_monday - timedelta(weeks=1), 0),
        "km_week": round(sum(r.distance_km or 0 for r in mine), 1),
        "minutes_week": int(sum(r.duration_minutes or 0 for r in mine)),
    }
    card = _card(
        "workouts", not rows, values, ctx, "workouts",
        weeks=[{"start": (first + timedelta(weeks=i)).isoformat(), "count": per_week.get(first + timedelta(weeks=i), 0)} for i in range(4)],
        activities=[{"activity": a, "count": n} for a, n in activities.most_common(5)],
    )  # fmt: skip
    if rows:
        card["phrase"] = _phrase(
            pp.rule_workouts(values["this_week"], values["last_week"], ctx.lang)
        )
    return card


async def goals_card(ctx) -> dict[str, Any]:
    goals = (
        (
            await ctx.session.execute(
                select(Goal)
                .where(Goal.member_id == ctx.mid, Goal.active.is_(True))
                .order_by(Goal.created_at)
                .limit(50)
            )
        )
        .scalars()
        .all()
    )
    since30 = ctx.today - timedelta(days=29)
    since7 = ctx.today - timedelta(days=6)
    stmt = (
        select(HabitLog.goal_id, HabitLog.log_date)
        .where(
            HabitLog.member_id == ctx.mid,
            HabitLog.goal_id.is_not(None),
            HabitLog.log_date >= since30,
            HabitLog.log_date <= ctx.today,
        )
        .limit(5000)
    )
    logs: dict[uuid.UUID, list[date]] = defaultdict(list)
    for gid, day in (await ctx.session.execute(stmt)).all():
        logs[gid].append(day)
    items = []
    for g in goals:
        days = set(logs.get(g.id, []))  # one check-in per day counts once
        target = " ".join(x for x in (g.target_value, g.target_unit) if x) or None
        items.append(
            {
                "title": g.title,
                "target": target,
                "deadline": g.deadline.isoformat() if g.deadline else None,
                "logs_7d": sum(1 for d in days if d >= since7),
                "logs_30d": len(days),
            }
        )
    items.sort(key=lambda i: (-i["logs_7d"], -i["logs_30d"], i["title"]))
    card = _card("goals", not goals, {"count": len(goals)}, ctx, "goals", items=items[:6])
    if items and items[0]["logs_7d"] > 0:
        card["phrase"] = _phrase(pp.rule_goal(items[0]["title"], items[0]["logs_7d"], ctx.lang))
    return card


# ── Viagens ───────────────────────────────────────────────────────────────────


def _trip_days(trip: Trip, today: date) -> int:
    end = trip.ended_at or today
    return max((min(end, today) - trip.started_at).days + 1, 1) if trip.started_at <= today else 0


async def _trip_spend(ctx, trip_ids: list[uuid.UUID]) -> dict[uuid.UUID, Any]:
    if not trip_ids:
        return {}
    stmt = (
        select(Expense.trip_id, func.sum(Expense.amount))
        .where(
            Expense.member_id == ctx.mid,
            Expense.trip_id.in_(trip_ids),
            Expense.transaction_type == "expense",
            Expense.status.in_(SETTLED),
        )
        .group_by(Expense.trip_id)
    )
    return {tid: dec(total) for tid, total in (await ctx.session.execute(stmt)).all()}


async def _trips(ctx) -> list[Trip]:
    stmt = (
        select(Trip)
        .where(Trip.member_id == ctx.mid)
        .order_by(Trip.started_at.desc(), Trip.created_at.desc())
        .limit(40)
    )
    return list((await ctx.session.execute(stmt)).scalars().all())


def _main_trip(trips: list[Trip], today: date) -> Trip | None:
    active = [t for t in trips if t.active and t.started_at <= today]
    if active:
        return active[0]
    upcoming = sorted((t for t in trips if t.started_at > today), key=lambda t: t.started_at)
    if upcoming:
        return upcoming[0]
    return trips[0] if trips else None


def _state(trip: Trip, today: date) -> str:
    if trip.started_at > today:
        return "upcoming"
    if trip.active and (trip.ended_at is None or trip.ended_at >= today):
        return "active"
    return "ended"


async def trip_card(ctx) -> dict[str, Any]:
    trips = await _trips(ctx)
    trip = _main_trip(trips, ctx.today)
    if trip is None:
        return _card(
            "trip",
            True,
            {
                "budget": None,
                "spent": 0.0,
                "remaining": None,
                "pct": None,
                "days": 0,
                "planned_days": None,
            },
            ctx,
            "trips",
            trip=None,
            categories=[],
            points=[],
        )
    spent = (await _trip_spend(ctx, [trip.id])).get(trip.id, dec(0))
    budget = float(trip.budget) if trip.budget else None
    cat = func.coalesce(Expense.category, "overig")
    cat_rows = (
        await ctx.session.execute(
            select(cat, func.sum(Expense.amount))
            .where(
                Expense.member_id == ctx.mid,
                Expense.trip_id == trip.id,
                Expense.transaction_type == "expense",
                Expense.status.in_(SETTLED),
            )
            .group_by(cat)
            .order_by(func.sum(Expense.amount).desc(), cat)
            .limit(5)
        )
    ).all()
    local_day = _local_day()
    day_rows = (
        await ctx.session.execute(
            select(local_day, func.sum(Expense.amount))
            .where(
                Expense.member_id == ctx.mid,
                Expense.trip_id == trip.id,
                Expense.transaction_type == "expense",
                Expense.status.in_(SETTLED),
            )
            .group_by(local_day)
            .order_by(local_day)
            .limit(_TRIP_DAYS_SHOWN)
        )
    ).all()
    state = _state(trip, ctx.today)
    days = _trip_days(trip, ctx.today)
    values = {
        "budget": money(budget) if budget else None,
        "spent": money(spent),
        "remaining": money(budget - float(spent)) if budget else None,
        "pct": pp.budget_pct(spent, budget) if budget else None,
        "days": days,
        "planned_days": ((trip.ended_at - trip.started_at).days + 1) if trip.ended_at else None,
    }
    card = _card(
        "trip", False, values, ctx,
        trip={
            "destination": trip.destination,
            "start": trip.started_at.isoformat(),
            "end": trip.ended_at.isoformat() if trip.ended_at else None,
            "state": state,
            "days_to_start": (trip.started_at - ctx.today).days if state == "upcoming" else None,
        },
        categories=[{"category": c, "label": ctx.label(c), "amount": money(a)} for c, a in cat_rows],
        points=[{"date": d, "amount": money(a)} for d, a in day_rows],
    )  # fmt: skip
    card["phrase"] = _phrase(pp.rule_trip(float(spent), budget, days, ctx.lang))
    return card


async def trips_past_card(ctx) -> dict[str, Any]:
    trips = await _trips(ctx)
    main = _main_trip(trips, ctx.today)
    others = [t for t in trips if main is None or t.id != main.id][:_PAST_TRIPS]
    spend = await _trip_spend(ctx, [t.id for t in others])
    items = []
    for t in others:
        spent = float(spend.get(t.id, 0))
        budget = float(t.budget) if t.budget else None
        items.append(
            {
                "destination": t.destination,
                "start": t.started_at.isoformat(),
                "end": t.ended_at.isoformat() if t.ended_at else None,
                "days": _trip_days(t, ctx.today),
                "spent": money(spent),
                "budget": money(budget) if budget else None,
                "within": (spent <= budget) if budget else None,
            }
        )
    with_budget = [i for i in items if i["budget"] is not None and i["spent"] > 0]
    within = sum(1 for i in with_budget if i["within"])
    card = _card(
        "trips_past",
        not items,
        {"count": len(items), "with_budget": len(with_budget), "within": within},
        ctx,
        None,
        items=items,
    )
    if items:
        card["phrase"] = _phrase(pp.rule_trips_history(len(with_budget), within, ctx.lang))
    return card
