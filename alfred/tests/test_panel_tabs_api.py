# ruff: noqa: E501
"""Agenda, Hábitos and Viagens: the cards of the three tabs, with what the member recorded."""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, time, timedelta

import pytest
from sqlalchemy import delete

from alfred import panel_tokens
from alfred.clock import day_start, today_local, week_start
from alfred.db import AsyncSessionLocal
from alfred.models import (
    Appointment,
    Expense,
    Goal,
    HabitLog,
    HealthLog,
    Member,
    Note,
    ScheduledJob,
    Task,
    Trip,
    WorkoutSession,
)
from alfred.web_security import _dashboard_limiter

db = pytest.mark.skipif(os.environ.get("ALFRED_TEST_DB") != "1", reason="needs test DB")
TODAY = today_local()


@pytest.fixture(autouse=True)
def _fresh_rate_limiter():
    _dashboard_limiter._hits.clear()
    yield
    _dashboard_limiter._hits.clear()


async def _token(member_id) -> str:
    async with AsyncSessionLocal() as s:
        m = await s.get(Member, member_id)
        await panel_tokens.ensure_panel_token(s, m)
        await s.commit()
        return str(m.dashboard_token)


async def _cards(client, lab, tab: str) -> dict[str, dict]:
    token = await _token(lab.member_id)
    r = await client.get(f"/api/d/{token}/{tab}")
    assert r.status_code == 200, r.text
    return {c["id"]: c for c in r.json()["cards"]}


def _at(day, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=day_start(day).tzinfo)


def _appt(lab, title, day, hour, minute=0, **kw) -> Appointment:
    return Appointment(
        member_id=kw.pop("member_id", lab.member_id),
        household_id=lab.household_id,
        title=title,
        starts_at=_at(day, hour, minute),
        **kw,
    )


# ── empty member: every card teaches the chat sentence ────────────────────────


@db
@pytest.mark.parametrize(
    ("tab", "ids"),
    [
        ("agenda", ["week", "tasks", "month_map", "reminders", "notes"]),
        ("health", ["water", "workouts", "goals", "sleep", "mood", "medication"]),
        ("trips", ["trip"]),
    ],
)
async def test_a_new_member_sees_empty_cards_with_a_chat_hint(lab, client, tab, ids) -> None:
    cards = await _cards(client, lab, tab)
    assert list(cards) == ids
    for card in cards.values():
        assert card["empty"] is True and card["phrase"] is None
        assert card["hint"]["text"] and card["hint"]["chat"].startswith('"')


# ── Agenda ────────────────────────────────────────────────────────────────────


@db
async def test_week_lists_the_next_seven_days_and_skips_cancelled_and_far_ones(lab, client) -> None:
    tomorrow = TODAY + timedelta(days=1)
    await lab.add(
        _appt(lab, "Dentista", TODAY, 10),
        _appt(lab, "Contador", TODAY, 15, 30, notes="ligar antes"),
        _appt(lab, "Jantar", tomorrow, 19),
        _appt(lab, "Reunião", tomorrow, 9),
        _appt(lab, "Cancelada", tomorrow, 12, status="cancelled"),
        _appt(lab, "Longe", TODAY + timedelta(days=10), 9),
    )
    week = (await _cards(client, lab, "agenda"))["week"]
    assert week["empty"] is False and week["values"] == {"count": 4, "days": 2}
    first, second = week["days"]
    assert first["date"] == TODAY.isoformat() and first["weekday"] == TODAY.weekday()
    assert [i["title"] for i in first["items"]] == ["Dentista", "Contador"]
    assert first["items"][0]["time"] == "10:00" and first["items"][1]["notes"] == "ligar antes"
    assert [i["title"] for i in second["items"]] == [
        "Reunião",
        "Jantar",
    ]  # by time, not by creation
    assert week["more"] == 0
    assert "Cancelada" not in str(week) and "Longe" not in str(week)


@db
async def test_week_phrase_needs_three_appointments_and_one_busiest_day(lab, client) -> None:
    tomorrow = TODAY + timedelta(days=1)
    await lab.add(
        _appt(lab, "A", TODAY, 9), _appt(lab, "B", tomorrow, 9), _appt(lab, "C", tomorrow, 10)
    )
    week = (await _cards(client, lab, "agenda"))["week"]
    assert week["phrase"]["key"] == "agenda_week" and week["phrase"]["chat"] == '"minha agenda"'
    assert week["phrase"]["text"].startswith("3 compromissos")


@db
async def test_week_never_shows_another_members_appointments(lab, client) -> None:
    async with AsyncSessionLocal() as s:
        other = Member(
            household_id=lab.household_id,
            wa_phone="3161" + uuid.uuid4().hex[:7],
            consent_state="accepted",
            language="pt",
        )
        s.add(other)
        await s.commit()
        other_id = other.id
    try:
        await lab.add(_appt(lab, "Segredo", TODAY, 9, member_id=other_id))
        week = (await _cards(client, lab, "agenda"))["week"]
        assert week["empty"] is True and "Segredo" not in str(week)
    finally:
        async with AsyncSessionLocal() as s:
            await s.execute(delete(Appointment).where(Appointment.member_id == other_id))
            await s.execute(delete(Member).where(Member.id == other_id))
            await s.commit()


@db
async def test_tasks_count_overdue_deadlines_and_done_and_phrase_names_the_oldest(
    lab, client
) -> None:
    await lab.add(
        Task(
            member_id=lab.member_id, body="Renovar passaporte", due_date=TODAY - timedelta(days=9)
        ),
        Task(member_id=lab.member_id, body="Pagar IPTU", due_date=TODAY + timedelta(days=2)),
        Task(member_id=lab.member_id, body="Sem prazo"),
        Task(
            member_id=lab.member_id,
            body="Feita",
            due_date=TODAY - timedelta(days=30),
            done_at=datetime.now(UTC),
        ),
        Task(member_id=lab.member_id, body="Futura", due_date=TODAY + timedelta(days=20)),
    )
    tasks = (await _cards(client, lab, "agenda"))["tasks"]
    assert tasks["values"] == {"open": 4, "overdue": 1, "due_week": 1, "no_due": 1, "done_week": 1}
    assert [(i["kind"], i["body"], i["days"]) for i in tasks["items"]] == [
        ("overdue", "Renovar passaporte", 9),
        ("due", "Pagar IPTU", 2),
    ]
    assert (
        tasks["phrase"]["severity"] == "attention"
        and "Renovar passaporte" in tasks["phrase"]["text"]
        and "9 dias" in tasks["phrase"]["text"]
    )


@db
async def test_tasks_phrase_falls_back_to_the_deadlines_when_nothing_is_overdue(
    lab, client
) -> None:
    await lab.add(
        Task(member_id=lab.member_id, body="Enviar relatório", due_date=TODAY + timedelta(days=1))
    )
    tasks = (await _cards(client, lab, "agenda"))["tasks"]
    assert tasks["phrase"]["key"] == "tasks_deadlines" and "vence amanhã" in tasks["phrase"]["text"]


@db
async def test_reminders_notes_and_the_four_week_map(lab, client) -> None:
    monday = week_start(TODAY)
    await lab.add(
        ScheduledJob(
            member_id=lab.member_id,
            job_type="medication_reminder",
            time_of_day="08:00",
            days_mask=127,
            payload={"text": "Tomar o remédio"},
        ),
        ScheduledJob(
            member_id=lab.member_id, job_type="workout_reminder", time_of_day="11:00", days_mask=32
        ),
        ScheduledJob(
            member_id=lab.member_id,
            job_type="goal_checkin",
            time_of_day="20:00",
            days_mask=127,
            active=False,
        ),
        Note(member_id=lab.member_id, body="x" * 300),
        _appt(lab, "A", monday + timedelta(days=7 + 1), 9),
        _appt(lab, "B", monday + timedelta(days=7 + 1), 10),
        _appt(lab, "C", monday + timedelta(days=14 + 1), 9),
        _appt(lab, "D", monday + timedelta(days=21 + 1), 9),
        _appt(lab, "E", monday + timedelta(days=21 + 3), 9),
        _appt(lab, "F", monday + timedelta(days=14 + 1), 11),
    )
    cards = await _cards(client, lab, "agenda")
    rem = cards["reminders"]
    assert rem["values"]["count"] == 2  # the inactive one is not listed
    assert [(i["kind"], i["time"], i["every_day"], i["days"]) for i in rem["items"]] == [
        ("medication_reminder", "08:00", True, [0, 1, 2, 3, 4, 5, 6]),
        ("workout_reminder", "11:00", False, [5]),
    ]
    assert rem["items"][0]["text"] == "Tomar o remédio" and rem["items"][1]["text"] is None
    notes = cards["notes"]
    assert notes["values"]["count"] == 1 and len(notes["items"][0]["body"]) == 200
    grid = cards["month_map"]
    assert len(grid["weeks"]) == 4 and all(len(w) == 7 for w in grid["weeks"])
    assert grid["weeks"][0][0]["date"] == monday.isoformat()
    assert sum(d["count"] for w in grid["weeks"] for d in w) == 6
    assert grid["weeks"][1][1]["count"] == 2 and grid["weeks"][2][1]["count"] == 2
    assert (
        grid["values"]["peak"] == 1 and grid["phrase"]["key"] == "agenda_map"
    )  # Tuesday holds most


# ── Hábitos ───────────────────────────────────────────────────────────────────


def _log(lab, kind, value, day, unit=None) -> HealthLog:
    return HealthLog(member_id=lab.member_id, log_type=kind, value=value, unit=unit, log_date=day)


@db
async def test_water_sleep_mood_and_medication_use_their_own_windows(lab, client) -> None:
    d = lambda n: TODAY - timedelta(days=n)  # noqa: E731
    await lab.add(
        _log(lab, "water", "1.4", d(0), "L"), _log(lab, "water", "0,6", d(0), "L"),
        _log(lab, "water", "500", d(1), "ml"), _log(lab, "water", "2", d(2), "L"),
        _log(lab, "water", "abc", d(3), "L"), _log(lab, "water", "9", d(8), "L"),
        *[_log(lab, "sleep", str(h), d(i)) for i, h in enumerate([7, 6.5, 8, 7, 6, 99], start=1)],
        _log(lab, "sleep", "7", d(20)),
        *[_log(lab, "mood", str(v), d(i)) for i, v in enumerate([8, 6, 7], start=0)],
        _log(lab, "mood", "11", d(3)),
        _log(lab, "medication", "x", d(0)), _log(lab, "medication", "x", d(0)), _log(lab, "medication", "x", d(2)),
    )  # fmt: skip
    cards = await _cards(client, lab, "health")
    water = cards["water"]
    assert water["values"] == {
        "today": 2.0,
        "average": 1.5,
        "days": 3,
    }  # 2.0, 0.5, 2.0 (9 L is outside the window)
    assert len(water["points"]) == 7 and water["points"][-1] == {
        "date": TODAY.isoformat(),
        "litres": 2.0,
    }
    assert water["phrase"]["key"] == "water_avg" and "1,5 L" in water["phrase"]["text"]
    sleep = cards["sleep"]
    assert (
        sleep["values"]["nights"] == 5 and sleep["values"]["average"] == 6.9
    )  # 99 h and day -20 are ignored
    assert len(sleep["points"]) == 14 and sleep["phrase"]["key"] == "sleep_avg"
    mood = cards["mood"]
    assert (
        mood["values"]["count"] == 3
        and mood["values"]["average"] == 7.0
        and mood["values"]["last"] == 8.0
    )
    assert mood["phrase"]["key"] == "mood_avg"
    med = cards["medication"]
    assert (
        med["values"] == {"days": 2, "today": 2}
        and med["phrase"]["text"] == "Medicação registrada em 2 dos últimos 7 dias."
    )


@db
async def test_health_phrases_wait_for_enough_data(lab, client) -> None:
    await lab.add(
        _log(lab, "sleep", "7", TODAY),
        _log(lab, "water", "1", TODAY, "L"),
        _log(lab, "mood", "7", TODAY),
    )
    cards = await _cards(client, lab, "health")
    for card_id in ("sleep", "water", "mood"):
        assert cards[card_id]["empty"] is False and cards[card_id]["phrase"] is None


@db
async def test_workouts_count_this_week_against_last_and_sum_distance(lab, client) -> None:
    monday = week_start(TODAY)
    last = monday - timedelta(days=3)
    rows = [
        WorkoutSession(
            member_id=lab.member_id,
            activity_type="running",
            distance_km=5.0,
            duration_minutes=30,
            workout_date=monday,
        )
    ]
    if TODAY > monday:
        rows.append(
            WorkoutSession(
                member_id=lab.member_id,
                activity_type="Strength",
                duration_minutes=45,
                workout_date=TODAY,
            )
        )
    rows += [WorkoutSession(member_id=lab.member_id, activity_type="running", distance_km=3.2, workout_date=last),
             WorkoutSession(member_id=lab.member_id, activity_type="cycling", workout_date=monday - timedelta(days=60))]  # fmt: skip
    await lab.add(*rows)
    w = (await _cards(client, lab, "health"))["workouts"]
    n = 2 if TODAY > monday else 1
    assert w["values"]["this_week"] == n and w["values"]["last_week"] == 1
    assert w["values"]["km_week"] == 5.0 and w["values"]["minutes_week"] == (75 if n == 2 else 30)
    assert [x["count"] for x in w["weeks"]] == [0, 0, 1, n]
    assert w["weeks"][-1]["start"] == monday.isoformat() and "cycling" not in str(w)
    assert w["phrase"]["key"] == "workouts_week"


@db
async def test_goals_count_check_in_days_once_and_order_by_recent_activity(lab, client) -> None:
    async with AsyncSessionLocal() as s:
        a = Goal(
            member_id=lab.member_id, title="Meditar", target_value="7", target_unit="por semana"
        )
        b = Goal(member_id=lab.member_id, title="Ler", deadline=TODAY + timedelta(days=30))
        c = Goal(member_id=lab.member_id, title="Antiga", active=False)
        s.add_all([a, b, c])
        await s.commit()
        ids = (a.id, b.id, c.id)
    d = lambda n: TODAY - timedelta(days=n)  # noqa: E731
    await lab.add(
        HabitLog(member_id=lab.member_id, goal_id=ids[0], activity="meditei", log_date=d(0)),
        HabitLog(member_id=lab.member_id, goal_id=ids[0], activity="meditei", log_date=d(0)),
        HabitLog(member_id=lab.member_id, goal_id=ids[0], activity="meditei", log_date=d(2)),
        HabitLog(member_id=lab.member_id, goal_id=ids[0], activity="meditei", log_date=d(20)),
        HabitLog(member_id=lab.member_id, goal_id=ids[1], activity="li", log_date=d(10)),
        HabitLog(member_id=lab.member_id, goal_id=ids[2], activity="x", log_date=d(0)),
        HabitLog(member_id=lab.member_id, goal_id=None, activity="solto", log_date=d(0)),
    )
    goals = (await _cards(client, lab, "health"))["goals"]
    assert goals["values"]["count"] == 2
    assert [(i["title"], i["logs_7d"], i["logs_30d"]) for i in goals["items"]] == [
        ("Meditar", 2, 3),
        ("Ler", 0, 1),
    ]
    assert (
        goals["items"][0]["target"] == "7 por semana"
        and goals["items"][1]["deadline"] == (TODAY + timedelta(days=30)).isoformat()
    )
    assert goals["phrase"]["key"] == "goals_pace" and "Meditar" in goals["phrase"]["text"]
    await lab.add(
        HabitLog(member_id=lab.member_id, goal_id=ids[1], activity="li", log_date=TODAY)
    )  # nothing breaks with more rows


# ── Viagens ───────────────────────────────────────────────────────────────────


async def _trip(lab, destination, start, end=None, budget=None, active=False) -> uuid.UUID:
    async with AsyncSessionLocal() as s:
        t = Trip(
            member_id=lab.member_id,
            destination=destination,
            started_at=start,
            ended_at=end,
            budget=budget,
            active=active,
        )
        s.add(t)
        await s.commit()
        return t.id


def _spend(lab, trip_id, amount, day, category="overig", status="paid", kind="expense") -> Expense:
    return Expense(
        member_id=lab.member_id, household_id=lab.household_id, transaction_type=kind, amount=amount, merchant="x",
        category=category, status=status, trip_id=trip_id, expense_date=_at(day, 12),
    )  # fmt: skip


@db
async def test_active_trip_shows_budget_spent_remaining_categories_and_days(lab, client) -> None:
    start = TODAY - timedelta(days=2)
    tid = await _trip(lab, "Lisboa", start, budget=900, active=True)
    await lab.add(
        _spend(lab, tid, 310, start, "reizen"), _spend(lab, tid, 230, start, "reizen"),
        _spend(lab, tid, 60, TODAY, "restaurant"), _spend(lab, tid, 99, TODAY, status="to_pay"),
        _spend(lab, tid, 500, TODAY, kind="income", status="received"),
    )  # fmt: skip
    cards = await _cards(client, lab, "trips")
    trip = cards["trip"]
    assert trip["trip"]["destination"] == "Lisboa" and trip["trip"]["state"] == "active"
    assert trip["values"] == {
        "budget": 900.0,
        "spent": 600.0,
        "remaining": 300.0,
        "pct": 67,
        "days": 3,
        "planned_days": None,
    }
    assert [(c["category"], c["amount"]) for c in trip["categories"]] == [
        ("reizen", 540.0),
        ("restaurant", 60.0),
    ]
    assert trip["points"] == [
        {"date": start.isoformat(), "amount": 540.0},
        {"date": TODAY.isoformat(), "amount": 60.0},
    ]
    assert (
        trip["phrase"]["key"] == "trip_budget"
        and "67%" in trip["phrase"]["text"]
        and trip["phrase"]["severity"] == "info"
    )
    assert "trips_past" not in cards  # a single trip has no "other trips"


@db
async def test_trip_over_budget_is_attention_and_without_budget_it_only_reports_the_spend(
    lab, client
) -> None:
    start = TODAY - timedelta(days=1)
    tid = await _trip(lab, "Paris", start, budget=100, active=True)
    await lab.add(_spend(lab, tid, 130, TODAY))
    trip = (await _cards(client, lab, "trips"))["trip"]
    assert (
        trip["values"]["remaining"] == -30.0
        and trip["values"]["pct"] == 130
        and trip["phrase"]["severity"] == "attention"
    )
    async with AsyncSessionLocal() as s:
        t = await s.get(Trip, tid)
        t.budget = None
        await s.commit()
    trip = (await _cards(client, lab, "trips"))["trip"]
    assert trip["values"]["budget"] is None and trip["values"]["pct"] is None
    assert trip["phrase"]["key"] == "trip_spent"


@db
async def test_upcoming_trip_comes_before_past_ones_and_past_trips_compare_with_budget(
    lab, client
) -> None:
    await _trip(lab, "Rio", TODAY - timedelta(days=300), TODAY - timedelta(days=286), budget=2500)
    await _trip(lab, "Berlim", TODAY - timedelta(days=200), TODAY - timedelta(days=197), budget=550)
    await _trip(lab, "Roma", TODAY - timedelta(days=100), TODAY - timedelta(days=98))
    soon = await _trip(
        lab, "Lisboa", TODAY + timedelta(days=41), TODAY + timedelta(days=45), budget=900
    )
    async with AsyncSessionLocal() as s:
        from sqlalchemy import select

        ids = {
            t.destination: t.id
            for t in (
                await s.execute(select(Trip).where(Trip.member_id == lab.member_id))
            ).scalars()
        }
    await lab.add(
        _spend(lab, ids["Rio"], 2340, TODAY - timedelta(days=299)),
        _spend(lab, ids["Berlim"], 610, TODAY - timedelta(days=199)),
        _spend(lab, ids["Roma"], 90, TODAY - timedelta(days=99)),
    )
    cards = await _cards(client, lab, "trips")
    main = cards["trip"]
    assert (
        main["trip"]["destination"] == "Lisboa"
        and main["trip"]["state"] == "upcoming"
        and main["trip"]["days_to_start"] == 41
    )
    assert (
        main["values"]["planned_days"] == 5
        and main["values"]["spent"] == 0.0
        and main["phrase"] is None
    )
    past = cards["trips_past"]
    assert [i["destination"] for i in past["items"]] == ["Roma", "Berlim", "Rio"]  # newest first
    by = {i["destination"]: i for i in past["items"]}
    assert (
        by["Rio"]["within"] is True
        and by["Berlim"]["within"] is False
        and by["Roma"]["within"] is None
    )
    assert by["Berlim"]["days"] == 4 and by["Rio"]["spent"] == 2340.0
    assert past["values"] == {"count": 3, "with_budget": 2, "within": 1}
    assert past["phrase"]["key"] == "trips_history"
    assert soon  # created


@db
async def test_a_trip_never_counts_another_members_expenses(lab, client) -> None:
    tid = await _trip(lab, "Lisboa", TODAY, budget=100, active=True)
    async with AsyncSessionLocal() as s:
        other = Member(
            household_id=lab.household_id,
            wa_phone="3162" + uuid.uuid4().hex[:7],
            consent_state="accepted",
            language="pt",
        )
        s.add(other)
        await s.commit()
        other_id = other.id
        s.add(
            Expense(
                member_id=other_id,
                household_id=lab.household_id,
                transaction_type="expense",
                amount=77,
                merchant="x",
                status="paid",
                trip_id=tid,
                expense_date=_at(TODAY, 12),
            )
        )
        await s.commit()
    try:
        trip = (await _cards(client, lab, "trips"))["trip"]
        assert trip["values"]["spent"] == 0.0 and trip["categories"] == []
    finally:
        async with AsyncSessionLocal() as s:
            await s.execute(delete(Expense).where(Expense.member_id == other_id))
            await s.execute(delete(Member).where(Member.id == other_id))
            await s.commit()
