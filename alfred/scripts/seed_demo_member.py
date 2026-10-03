#!/usr/bin/env python3
"""Demo member for the v2 panel: the numbers of the approved mockups, idempotent.

Creates (or reuses) the member ``31000000000`` with ``dashboard_v2 = true`` and consent
``pending`` (no cron message is ever sent to it), then rebuilds ONLY that member's expenses,
budgets and fixed bills, so running it twice never duplicates anything. Other members are never
read or written. Prints the panel link.

October 2026: income 3.840,00, paid spending 1.997,70, balance 1.842,30; the restaurant budget
(100) is over by 23; two bills are still to pay and do not count towards the balance. September
2026 gives the month-against-month comparison; four fixed items (one in 13 instalments), three
budgets and one debt ("Marta owes 34,50") fill the other cards.

Usage: ``python scripts/seed_demo_member.py`` (needs DATABASE_URL; BASE_URL for the printed link).
"""

from __future__ import annotations

import asyncio
import pathlib
import sys
from datetime import UTC, date, datetime, time, timedelta

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from sqlalchemy import delete, select  # noqa: E402

from alfred import panel_tokens  # noqa: E402
from alfred.clock import day_start, today_local  # noqa: E402
from alfred.db import AsyncSessionLocal, engine  # noqa: E402
from alfred.models import (  # noqa: E402
    Appointment,
    Base,
    Budget,
    Expense,
    Goal,
    HabitLog,
    HealthLog,
    Household,
    Iou,
    Member,
    Message,
    Note,
    RecurringItem,
    ScheduledJob,
    Task,
    Trip,
    TripBudgetLine,
    TripItem,
    TripPackItem,
    WorkoutLoad,
    WorkoutPlan,
    WorkoutPlanDay,
    WorkoutPlanItem,
    WorkoutSession,
)
from alfred.settings import settings  # noqa: E402
from alfred.training import key_of  # noqa: E402

DEMO_PHONE = "31000000000"

# (type, amount, merchant, category, status, day of October 2026)
ROWS = [
    ("income", 3840.00, "Salário", "inkomen", "received", 1),
    ("expense", 1150.00, "Aluguel", "wonen", "paid", 1),
    ("expense", 94.40, "Albert Heijn", "supermarkt", "paid", 1),
    ("expense", 123.00, "Restaurante", "restaurant", "paid", 2),
    ("expense", 630.30, "Outros", "overig", "paid", 2),
    ("expense", 118.00, "Energia", "wonen", "to_pay", 18),
    ("expense", 138.00, "Seguro de saúde", "gezondheid", "to_pay", 25),
]
# September 2026, the month the Resumo/Dinheiro cards compare with (same names, other amounts).
SEPT_ROWS = [
    ("income", 3840.00, "Salário", "inkomen", "received", 1),
    ("expense", 1150.00, "Aluguel", "wonen", "paid", 1),
    ("expense", 88.20, "Albert Heijn", "supermarkt", "paid", 1),
    ("expense", 75.00, "Restaurante", "restaurant", "paid", 2),
    ("expense", 612.10, "Outros", "overig", "paid", 2),
    ("expense", 48.00, "Uber", "transport", "paid", 2),
    # Later in September: outside the "first three days" comparison, but they give the end-of-month
    # projection the history it needs (enough variable entries in the last 30 days).
    ("expense", 36.50, "Albert Heijn", "supermarkt", "paid", 10),
    ("expense", 24.00, "Uber", "transport", "paid", 14),
    ("expense", 41.00, "Restaurante", "restaurant", "paid", 20),
]
TRIP_ROWS = 7  # two paid entries on each of the three past trips + the Lisbon hotel deposit
EXPECTED_ROWS = len(ROWS) + len(SEPT_ROWS) + TRIP_ROWS
EXPECTED_BUDGETS = 3


async def run() -> str:
    """Seed the demo member and return its panel token (the same one on every run)."""
    async with AsyncSessionLocal() as s:
        member = (
            await s.execute(select(Member).where(Member.wa_phone == DEMO_PHONE))
        ).scalar_one_or_none()
        if member is None:
            hh = Household(name="demo")
            s.add(hh)
            await s.flush()
            member = Member(household_id=hh.id, wa_phone=DEMO_PHONE, display_name="Joao")
            s.add(member)
        member.language = "pt"
        # Not "accepted": the cron reminders and the weekly/monthly summaries only select accepted
        # members, and the demo has a fake number and a bill due on the 18th. The panel link does
        # not look at consent.
        member.consent_state = "pending"
        member.dashboard_v2 = True
        await s.flush()
        for model in (
            HabitLog,
            Goal,
            HealthLog,
            WorkoutSession,
            Appointment,
            Task,
            Note,
            ScheduledJob,
            Expense,
            Trip,
            Budget,
            RecurringItem,
            Iou,
            WorkoutLoad,
            WorkoutPlan,
        ):  # fmt: skip  (plan days/items and trip entries go with their parents)
            await s.execute(delete(model).where(model.member_id == member.id))
        for month, rows in ((10, ROWS), (9, SEPT_ROWS)):
            for kind, amount, merchant, category, status, day in rows:
                s.add(
                    Expense(
                        member_id=member.id,
                        household_id=member.household_id,
                        transaction_type=kind,
                        amount=amount,
                        merchant=merchant,
                        category=category,
                        status=status,
                        expense_date=day_start(date(2026, month, day)).replace(hour=12),
                    )
                )
        for category, limit in (
            ("restaurant", 100.00),
            ("supermarkt", 400.00),
            ("transport", 150.00),
        ):
            s.add(
                Budget(
                    member_id=member.id,
                    household_id=member.household_id,
                    category=category,
                    monthly_limit=limit,
                )
            )
        for name, amount, category, kind, due_day, due, total in (
            ("Energia", 118.00, "wonen", "fixed", 18, date(2026, 10, 18), None),
            ("Internet", 42.00, "wonen", "subscription", 20, date(2026, 10, 20), None),
            ("Seguro de saúde", 138.00, "gezondheid", "fixed", 25, date(2026, 10, 25), None),
            ("Notebook", 100.00, "overig", "installment", 28, date(2026, 10, 28), 13),
        ):
            s.add(
                RecurringItem(
                    member_id=member.id,
                    household_id=member.household_id,
                    name=name,
                    amount=amount,
                    category=category,
                    kind=kind,
                    frequency="monthly",
                    due_day=due_day,
                    next_due_date=due,
                    installments_total=total,
                    installments_paid=0,
                )
            )
        s.add(
            Iou(
                member_id=member.id,
                person="Marta",
                amount=34.50,
                direction="owed_to_me",
                note="jantar",
                created_at=datetime(2026, 10, 1, 12, tzinfo=UTC),
            )
        )
        await _seed_life(s, member)
        await panel_tokens.ensure_panel_token(s, member)
        await s.commit()
        token = str(member.dashboard_token)
    await engine.dispose()
    return token


async def _seed_life(s, member) -> None:
    """Agenda, habits and trips, dated from today (these cards look at the days around it)."""
    today = today_local()
    tz = day_start(today).tzinfo

    def at(day: date, hour: int, minute: int = 0) -> datetime:
        return datetime.combine(day, time(hour, minute), tzinfo=tz)

    mid, hid = member.id, member.household_id
    for offset, hour, minute, title, notes in (
        (0, 10, 0, "Dentista", "Rua das Flores 12"),
        (0, 15, 30, "Ligar para o contador", None),
        (1, 9, 0, "Reunião de planejamento", "online"),
        (1, 19, 0, "Jantar com Marta", "Foodhallen"),
        (2, 11, 0, "Corrida longa", None),
        (4, 9, 30, "Revisão do carro", None),
        (9, 14, 0, "Dentista (retorno)", None),
        (15, 10, 0, "Contador", None),
        (16, 18, 30, "Aniversário da Marta", None),
        (22, 9, 0, "Reunião de planejamento", None),
    ):
        s.add(
            Appointment(
                member_id=mid,
                household_id=hid,
                title=title,
                notes=notes,
                starts_at=at(today + timedelta(days=offset), hour, minute),
            )
        )
    for body, due in (
        ("Renovar o passaporte", today - timedelta(days=6)),
        ("Pagar IPTU", today + timedelta(days=2)),
        ("Enviar relatório", today + timedelta(days=3)),
        ("Marcar revisão do carro", today + timedelta(days=5)),
        ("Comprar presente", today + timedelta(days=12)),
        ("Organizar documentos", None),
    ):
        s.add(Task(member_id=mid, body=body, due_date=due))
    s.add(
        Task(
            member_id=mid,
            body="Fazer o imposto",
            due_date=today - timedelta(days=3),
            done_at=datetime.now(UTC),
        )
    )
    for kind, hhmm, mask, text in (
        ("medication_reminder", "08:00", 127, "Tomar o remédio"),
        ("workout_reminder", "11:00", 32, None),
        ("goal_checkin", "20:00", 127, "Meditar"),
    ):
        s.add(
            ScheduledJob(
                member_id=mid,
                job_type=kind,
                time_of_day=hhmm,
                days_mask=mask,
                payload={"text": text} if text else None,
            )
        )
    s.add(
        Note(
            member_id=mid,
            body="Ideia: jantar de aniversário no Foodhallen, reservar para 8 pessoas.",
        )
    )
    s.add(Note(member_id=mid, body="Perguntar ao contador sobre a declaração do ano passado."))
    for i, litres in enumerate((1.4, 2.0, 1.2, 1.8, 2.2, 0.0, 1.6)):
        if litres:
            s.add(
                HealthLog(
                    member_id=mid,
                    log_type="water",
                    value=str(litres),
                    unit="L",
                    log_date=today - timedelta(days=i),
                )
            )
    for days_ago, kind, km, minutes in (
        (0, "strength", None, 50),
        (2, "running", 5.0, 30),
        (7, "strength", None, 45),
        (9, "running", 8.0, 55),
        (14, "strength", None, 50),
    ):
        s.add(
            WorkoutSession(
                member_id=mid,
                activity_type=kind,
                distance_km=km,
                duration_minutes=minutes,
                workout_date=today - timedelta(days=days_ago),
            )
        )
    meditar = Goal(member_id=mid, title="Meditar", target_value="7", target_unit="por semana")
    walk = Goal(
        member_id=mid, title="Caminhar 8.000 passos", target_value="7", target_unit="por semana"
    )
    gym = Goal(
        member_id=mid,
        title="12 treinos no mês",
        target_value="12",
        target_unit="no mês",
        deadline=date(today.year, today.month, 28),
    )
    s.add_all([meditar, walk, gym])
    await _seed_training(s, mid, today)
    await s.flush()  # the goals need their ids for the check-ins
    for i in (0, 1, 2, 4, 5):
        s.add(
            HabitLog(
                member_id=mid,
                goal_id=meditar.id,
                activity="meditei",
                log_date=today - timedelta(days=i),
            )
        )
    for i in (0, 2, 3, 6):
        s.add(
            HabitLog(
                member_id=mid,
                goal_id=walk.id,
                activity="caminhei",
                log_date=today - timedelta(days=i),
            )
        )
    for i in (0, 2):
        s.add(
            HabitLog(
                member_id=mid, goal_id=gym.id, activity="treino", log_date=today - timedelta(days=i)
            )
        )
    spent_on = {
        "Paris": ((300, "reizen"), (180, "restaurant")),
        "Berlim": ((350, "reizen"), (260, "restaurant")),
        "Rio de Janeiro": ((1400, "reizen"), (940, "restaurant")),
    }
    for destination, start, days, budget in (
        ("Lisboa", today + timedelta(days=41), 5, 900),
        ("Paris", date(2026, 6, 12), 3, 500),
        ("Berlim", date(2026, 3, 6), 4, 550),
        ("Rio de Janeiro", date(2025, 12, 10), 14, 2500),
    ):
        trip = Trip(
            member_id=mid,
            destination=destination,
            started_at=start,
            ended_at=start + timedelta(days=days - 1),
            budget=budget,
            active=False,
        )
        s.add(trip)
        await s.flush()
        if destination == "Lisboa":
            await _seed_trip_plan(s, mid, hid, trip, at)
        for n, (amount, category) in enumerate(spent_on.get(destination, ())):
            s.add(
                Expense(
                    member_id=mid,
                    household_id=hid,
                    transaction_type="expense",
                    amount=amount,
                    merchant=destination,
                    category=category,
                    status="paid",
                    trip_id=trip.id,
                    expense_date=at(start + timedelta(days=n), 12),
                )
            )


async def _seed_training(s, mid, today: date) -> None:
    """A weekly plan (Mon/Wed/Fri/Sat) and the loads used on it over the last four weeks."""
    plan = WorkoutPlan(member_id=mid, name="", active=True)
    s.add(plan)
    await s.flush()
    for weekday, title, items in (
        (
            0,
            "Peito",
            (
                ("Supino reto", 4, "10", 60),
                ("Crucifixo", 3, "12", 14),
                ("Tríceps corda", 3, "12", 25),
            ),
        ),
        (2, "Costas", (("Remada curvada", 4, "10", 50), ("Puxada frontal", 4, "10", 55))),
        (4, "Pernas", (("Agachamento", 5, "5", 90), ("Leg press", 4, "12", 160))),
        (5, "Ombros", (("Desenvolvimento", 4, "10", 24), ("Elevação lateral", 3, "15", 8))),
    ):
        day = WorkoutPlanDay(member_id=mid, plan_id=plan.id, weekday=weekday, title=title)
        s.add(day)
        await s.flush()
        for pos, (name, sets, reps, kg) in enumerate(items):
            s.add(
                WorkoutPlanItem(
                    member_id=mid, day_id=day.id, position=pos, exercise=name,
                    sets=sets, reps=reps, load_kg=kg,
                )
            )  # fmt: skip
    for name, history in (
        ("Supino reto", ((28, 55), (21, 57.5), (14, 60), (7, 60))),
        ("Agachamento", ((28, 80), (14, 85), (3, 90))),
        ("Puxada frontal", ((7, 55),)),  # one point only: no arrow in the panel
        ("Leg press", ((21, 150), (7, 160))),
    ):
        key = key_of(name)
        for days_ago, kg in history:
            s.add(
                WorkoutLoad(
                    member_id=mid, exercise=name, exercise_key=key,
                    day=today - timedelta(days=days_ago), load_kg=kg,
                )
            )  # fmt: skip


async def _seed_trip_plan(s, mid, hid, trip: Trip, at) -> None:
    """Itinerary, packing list, planned budget by category and a paid hotel deposit (Lisbon)."""
    first = trip.started_at
    for offset, hhmm, title in (
        (0, "15:40", "Voo Amsterdã → Lisboa"),
        (0, "19:00", "Check-in no hotel (Alfama)"),
        (1, "10:00", "Torre de Belém"),
        (1, "13:00", "Almoço no Time Out Market"),
        (2, "09:30", "Passeio no bonde 28"),
        (2, "20:00", "Jantar com fado"),
        (3, "11:00", "Sintra: Palácio da Pena"),
        (4, "18:30", "Voo de volta"),
    ):
        s.add(
            TripItem(
                member_id=mid, trip_id=trip.id, day=first + timedelta(days=offset),
                at_time=hhmm, title=title,
            )
        )  # fmt: skip
    for name, packed in (
        ("Passaporte", True), ("Carregador", True), ("Adaptador de tomada", False),
        ("Óculos de sol", False), ("Casaco leve", False), ("Remédios", True), ("Câmera", False),
    ):  # fmt: skip
        s.add(TripPackItem(member_id=mid, trip_id=trip.id, name=name, packed=packed))
    for category, amount in (
        ("wonen", 450),
        ("restaurant", 220),
        ("reizen", 150),
        ("transport", 80),
    ):
        s.add(TripBudgetLine(member_id=mid, trip_id=trip.id, category=category, amount=amount))
    s.add(
        Expense(
            member_id=mid, household_id=hid, transaction_type="expense", amount=180,
            merchant="Hotel Alfama", category="wonen", status="paid", trip_id=trip.id,
            expense_date=at(today_local() - timedelta(days=12), 11),
        )
    )  # fmt: skip


async def purge() -> None:
    """Remove the demo member and everything that hangs off it (used by the tests' teardown)."""
    async with AsyncSessionLocal() as s:
        member = (
            await s.execute(select(Member).where(Member.wa_phone == DEMO_PHONE))
        ).scalar_one_or_none()
        if member is not None:
            for table in reversed(Base.metadata.sorted_tables):
                if "member_id" in table.c and table.name != "member":
                    await s.execute(delete(table).where(table.c.member_id == member.id))
            await s.execute(delete(Message).where(Message.household_id == member.household_id))
            await s.execute(delete(Member).where(Member.id == member.id))
            await s.execute(delete(Household).where(Household.id == member.household_id))
            await s.commit()
    await engine.dispose()


def main() -> None:
    token = asyncio.run(run())
    base = (settings.base_url or "http://localhost:8000").rstrip("/")
    print(f"{base}/d/{token}")


if __name__ == "__main__":
    main()
