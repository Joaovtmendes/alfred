"""V2-35 — training plan with loads, trip itinerary / packing / planned budget."""

from __future__ import annotations

import os
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from alfred.clock import today_local
from alfred.conversation import _t
from alfred.db import AsyncSessionLocal
from alfred.models import (
    Expense,
    Member,
    PendingAction,
    Trip,
    TripBudgetLine,
    TripItem,
    TripPackItem,
    WorkoutLoad,
    WorkoutPlan,
    WorkoutPlanDay,
    WorkoutPlanItem,
)
from alfred.privacy import erase_member, export_member_data
from alfred.training import fmt_kg, parse_plan
from tests.labkit import Lab

db = pytest.mark.skipif(os.environ.get("ALFRED_TEST_DB") != "1", reason="needs test DB")

WEEK = "\n".join(
    f"{d}: Supino 4x10 60kg, Remada 3x12"
    for d in ("segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo")
)
PLAN = (
    "plano de treino:\nSegunda - Peito: Supino 4x10 60kg, Crucifixo 3x12 14kg\n"
    "Quarta - Costas: Remada 4x10 50kg"
)


async def _count(lab: Lab, model) -> int:
    return await lab.scalar(
        select(func.count()).select_from(model).where(model.member_id == lab.member_id)
    )


async def _save_plan(lab: Lab, text: str = PLAN) -> None:
    await lab.say(text)
    (draft,) = await lab.rows(
        select(PendingAction.id).where(PendingAction.member_id == lab.member_id)
    )
    await lab.tap(f"plan_ok:{draft[0]}")


async def _trip(lab: Lab, **kw) -> Trip:
    trip = Trip(
        member_id=lab.member_id,
        destination="Lisboa",
        started_at=kw.pop("started_at", today_local() - timedelta(days=1)),
        active=True,
        **kw,
    )
    await lab.add(trip)
    return trip


# ── pure ──────────────────────────────────────────────────────────────────────


def test_parse_plan_reads_days_titles_sets_reps_and_loads() -> None:
    plan = parse_plan(
        "Segunda - Peito: Supino 4x10 60kg, Crucifixo 3x12 14,5kg\nQuarta: Rosca 3x8-10"
    )
    assert [d.weekday for d in plan] == [0, 2]
    assert plan[0].title == "Peito"
    assert (plan[0].items[0].exercise, plan[0].items[0].sets, plan[0].items[0].reps) == (
        "Supino",
        4,
        "10",
    )
    assert plan[0].items[1].load_kg == 14.5
    assert plan[1].items[0].reps == "8-10"


@pytest.mark.parametrize(
    "text, line",
    [
        ("blabla", 1),
        ("Segunda: Supino 4x10\nFunday: Remada 3x10", 2),
        ("Segunda: Supino 4x10\nSegunda: Remada 3x10", 2),  # the same day twice
        ("Segunda: Supino 4x10 900kg", 1),  # absurd load
        ("Segunda:  , ", 1),
    ],
)
def test_parse_plan_points_at_the_first_bad_line(text: str, line: int) -> None:
    assert parse_plan(text) == line


def test_kg_format_follows_the_language() -> None:
    assert fmt_kg(62.5, "pt") == "62,5 kg" and fmt_kg(62.5, "en") == "62.5 kg"
    assert fmt_kg(60, "pt") == "60 kg"


# ── plan: preview, confirm, cancel ────────────────────────────────────────────


@db
async def test_a_plan_is_previewed_and_nothing_is_saved_until_confirmed(lab: Lab) -> None:
    reply = await lab.say(PLAN)
    assert "Supino" in reply and "Crucifixo" in reply and "Remada" in reply
    assert await _count(lab, WorkoutPlan) == 0 and await _count(lab, PendingAction) == 1
    ids = [b[0].split(":")[0] for b in lab.buttons[-1]]
    assert ids == ["plan_ok", "plan_adjust", "plan_cancel"]


@db
async def test_confirm_saves_once_and_a_second_tap_does_nothing(lab: Lab) -> None:
    await lab.say(PLAN)
    (draft,) = await lab.rows(
        select(PendingAction.id).where(PendingAction.member_id == lab.member_id)
    )
    saved = await lab.tap(f"plan_ok:{draft[0]}")
    assert "2" in saved
    assert await _count(lab, WorkoutPlan) == 1 and await _count(lab, WorkoutPlanDay) == 2
    assert await _count(lab, WorkoutPlanItem) == 3
    again = await lab.tap(f"plan_ok:{draft[0]}")
    assert again == _t("plan_gone", "pt")
    assert await _count(lab, WorkoutPlan) == 1


@db
async def test_cancel_discards_the_draft(lab: Lab) -> None:
    await lab.say(PLAN)
    (draft,) = await lab.rows(
        select(PendingAction.id).where(PendingAction.member_id == lab.member_id)
    )
    assert "Cancelado" in await lab.tap(f"plan_cancel:{draft[0]}")
    assert await _count(lab, WorkoutPlan) == 0 and await _count(lab, PendingAction) == 0


@db
async def test_a_bad_line_saves_nothing_and_says_which_line(lab: Lab) -> None:
    reply = await lab.say("plano de treino:\nSegunda: Supino 4x10\nbla bla")
    assert "2" in reply and await _count(lab, PendingAction) == 0


@db
async def test_a_newer_plan_archives_the_older_one(lab: Lab) -> None:
    await _save_plan(lab)
    await _save_plan(lab, "plano de treino:\nSexta: Agachamento 5x5 80kg")
    plans = await lab.rows(select(WorkoutPlan.active).where(WorkoutPlan.member_id == lab.member_id))
    assert sorted(p[0] for p in plans) == [False, True]
    assert "Agachamento" in await lab.say("meu plano de treino")


@db
async def test_a_draft_of_another_member_cannot_be_confirmed(lab: Lab) -> None:
    import uuid

    reply = await lab.tap(f"plan_ok:{uuid.uuid4()}")
    assert reply == _t("plan_gone", "pt") and await _count(lab, WorkoutPlan) == 0


@db
async def test_an_expired_draft_is_refused(lab: Lab) -> None:
    await lab.say(PLAN)
    async with AsyncSessionLocal() as s:
        draft = (
            await s.execute(select(PendingAction).where(PendingAction.member_id == lab.member_id))
        ).scalar_one()
        draft.expires_at = draft.expires_at - timedelta(hours=1)
        draft_id = draft.id
        await s.commit()
    assert await lab.tap(f"plan_ok:{draft_id}") == _t("plan_expired", "pt")
    assert await _count(lab, WorkoutPlan) == 0


# ── today / loads / evolution ─────────────────────────────────────────────────


@db
async def test_workout_today_lists_todays_exercises_with_the_last_load(lab: Lab) -> None:
    await _save_plan(lab, "plano de treino:\n" + WEEK)
    await lab.say("carga supino 62 kg")
    reply = await lab.say("treino de hoje")
    assert "Supino" in reply and "62 kg" in reply and "Remada" in reply


@db
async def test_workout_today_on_a_rest_day_says_so(lab: Lab) -> None:
    other = (today_local().weekday() + 1) % 7
    names = ("segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo")
    await _save_plan(lab, f"plano de treino:\n{names[other]}: Supino 4x10")
    assert "descanse" in (await lab.say("treino de hoje")).lower()


@db
async def test_without_a_plan_today_explains_how_to_create_one(lab: Lab) -> None:
    assert await lab.say("treino de hoje") == _t("plan_none", "pt")


@db
async def test_a_load_matches_the_plan_exercise_and_reports_the_change(lab: Lab) -> None:
    await _save_plan(lab)
    await lab.add(
        WorkoutLoad(
            member_id=lab.member_id,
            exercise="Supino",
            exercise_key="supino",
            day=today_local() - timedelta(days=7),
            load_kg=60,
        )
    )
    reply = await lab.say("carga supino 62,5 kg")
    assert "Supino" in reply and "62,5 kg" in reply and "+2,5 kg" in reply and "60 kg" in reply


@db
async def test_a_load_said_twice_on_one_day_corrects_instead_of_duplicating(lab: Lab) -> None:
    await lab.say("carga rosca 20 kg")
    reply = await lab.say("carga rosca 22 kg")
    assert await _count(lab, WorkoutLoad) == 1
    assert "22 kg" in reply
    assert (
        float(
            await lab.scalar(
                select(WorkoutLoad.load_kg).where(WorkoutLoad.member_id == lab.member_id)
            )
        )
        == 22
    )


@db
async def test_two_loads_on_one_day_show_only_the_last_in_the_evolution(lab: Lab) -> None:
    # Decided by J (04/10): when the same exercise gets two loads on one day, the last one counts.
    await lab.say("carga supino 60 kg")
    await lab.say("carga supino 62,5 kg")
    reply = await lab.say("evolução do supino")
    assert "62,5 kg" in reply
    assert "60 kg" not in reply


@db
@pytest.mark.parametrize("text", ["carga supino 0 kg", "carga supino 900 kg"])
async def test_absurd_loads_are_refused(lab: Lab, text: str) -> None:
    assert await lab.say(text) == _t("load_bad", "pt")
    assert await _count(lab, WorkoutLoad) == 0


@db
async def test_evolution_shows_the_last_loads_and_the_delta(lab: Lab) -> None:
    today = today_local()
    for days, kg in ((21, 55), (14, 57.5), (7, 60)):
        await lab.add(
            WorkoutLoad(
                member_id=lab.member_id,
                exercise="Supino",
                exercise_key="supino",
                day=today - timedelta(days=days),
                load_kg=kg,
            )
        )
    reply = await lab.say("evolução do supino")
    assert "55 kg" in reply and "60 kg" in reply and "+5 kg" in reply


@db
async def test_evolution_with_one_point_shows_no_delta(lab: Lab) -> None:
    await lab.say("carga supino 60 kg")
    reply = await lab.say("evolução do supino")
    assert "60 kg" in reply and "+" not in reply and "−" not in reply


@db
async def test_evolution_without_data_explains(lab: Lab) -> None:
    assert "Ainda não" in await lab.say("evolução do supino")


@db
async def test_the_other_languages_are_understood(lab: Lab) -> None:
    assert "Bench" in await lab.say("load Bench 60 kg")
    assert "60" in await lab.say("gewicht bankdrukken 60 kg")
    assert "60" in await lab.say("charge développé 60 kg")
    assert "60" in await lab.say("i used 60 kg on squat")
    assert "Bankdrücken" in await lab.say("fortschritt bankdrücken")


# ── trip: itinerary / packing / planned budget ────────────────────────────────


@db
async def test_trip_commands_need_a_trip(lab: Lab) -> None:
    for text in (
        "roteiro 12/10 10:00 Museu",
        "bagagem: passaporte",
        "orçamento da viagem hospedagem 300",
        "roteiro",
    ):
        assert await lab.say(text) == _t("tp_no_trip", "pt"), text


@db
async def test_itinerary_is_added_listed_in_date_order_and_validated(lab: Lab) -> None:
    trip = await _trip(lab, started_at=today_local() + timedelta(days=5))
    year = trip.started_at.year
    # dates built from the trip start (a fixed "12/10" broke once the real date passed it)
    first = trip.started_at.date() if hasattr(trip.started_at, "date") else trip.started_at
    d1, d2 = f"{first:%d/%m}", f"{first + timedelta(days=1):%d/%m}"
    assert "Museu do Fado" in await lab.say(f"roteiro {d2} 10:00 Museu do Fado")
    await lab.say(f"roteiro {d1} Jantar no Alfama")
    listing = await lab.say("roteiro")
    assert listing.index(d1) < listing.index(d2) and "10:00" in listing
    assert await _count(lab, TripItem) == 2
    assert await lab.say("roteiro 31/02 algo") == _t("tp_itin_bad", "pt")
    assert await lab.say(f"roteiro {d1} 25:00 algo") == _t("tp_itin_bad", "pt")
    assert year  # entries without a year take the trip's


@db
async def test_packing_list_add_tick_and_list(lab: Lab) -> None:
    await _trip(lab)
    assert "3" in await lab.say("bagagem: passaporte, carregador, óculos de sol")
    assert "Marquei" in await lab.say("peguei o passaporte")
    listing = await lab.say("bagagem")
    assert "1 de 3" in listing and "✓ passaporte" in listing and "○ carregador" in listing
    again = await lab.say("bagagem: Passaporte, escova")  # duplicates (case/accents) are skipped
    assert "1" in again and await _count(lab, TripPackItem) == 4


@db
async def test_took_something_that_is_not_on_the_list_is_not_ours(lab: Lab) -> None:
    await _trip(lab)
    await lab.say("bagagem: passaporte")
    assert await lab.say("peguei um uber") == "[llm]"


@db
async def test_planned_budget_per_category_compares_with_spent(lab: Lab) -> None:
    trip = await _trip(lab)
    assert "300" in await lab.say("orçamento da viagem moradia 300")
    await lab.say("orçamento da viagem moradia 350")  # again: replaces, never duplicates
    assert await _count(lab, TripBudgetLine) == 1
    await lab.add(
        Expense(
            member_id=lab.member_id,
            household_id=lab.household_id,
            amount=120,
            currency="EUR",
            category="wonen",
            merchant="Hotel",
            transaction_type="expense",
            status="paid",
            trip_id=trip.id,
            expense_date=today_local(),
        )
    )
    listing = await lab.say("orçamento da viagem")
    assert "120" in listing and "350" in listing
    assert await lab.say("orçamento da viagem xyz 10") == _t("tp_budget_unknown", "pt")
    assert await lab.say("orçamento da viagem moradia 0") == _t("tp_budget_bad", "pt")


@db
async def test_trip_commands_are_understood_in_the_other_languages(lab: Lab) -> None:
    await _trip(lab)
    assert "10:00" in await lab.say("itinerary 12/10 10:00 Museum")
    assert "1" in await lab.say("packing: passport")
    assert "300" in await lab.say("reisbudget wonen 300")
    assert "Museum" in await lab.say("reiseplan")
    assert "Museum" in await lab.say("itinéraire")


# ── privacy ───────────────────────────────────────────────────────────────────


@db
async def test_export_and_erase_cover_the_new_tables(lab: Lab) -> None:
    await _save_plan(lab)
    await lab.say("carga supino 60 kg")
    await _trip(lab)
    await lab.say("roteiro 12/10 Museu")
    await lab.say("bagagem: passaporte")
    await lab.say("orçamento da viagem moradia 300")
    async with AsyncSessionLocal() as s:
        member = await s.get(Member, lab.member_id)
        tables = (await export_member_data(s, member))["tables"]
        for name in (
            "workout_plan",
            "workout_plan_day",
            "workout_plan_item",
            "workout_load",
            "trip_item",
            "trip_pack_item",
            "trip_budget_line",
        ):
            assert tables[name], name
        await erase_member(s, member)
        await s.commit()
    for model in (
        WorkoutPlan,
        WorkoutPlanDay,
        WorkoutPlanItem,
        WorkoutLoad,
        TripItem,
        TripPackItem,
        TripBudgetLine,
    ):
        assert await lab.scalar(select(func.count()).select_from(model)) is not None
        assert await _count(lab, model) == 0, model.__name__
