"""Defects found by the production hard test of 01/10 (WhatsApp Web, test number)."""

from __future__ import annotations

import os
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select, update

from alfred.conversation import _fmt_eur, _t
from alfred.db import AsyncSessionLocal
from alfred.lang_cmd import parse_language_command
from alfred.models import Appointment, Budget, Expense, Member, ScheduledJob, Task
from tests.labkit import Lab, lab  # noqa: F401

db = pytest.mark.skipif(os.environ.get("ALFRED_TEST_DB") != "1", reason="needs test DB")


# ── language switch ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("text", "lang"),
    [
        ("Change to English", "en"),
        ("idioma inglês", "en"),
        ("mudar idioma para holandês", "nl"),
        ("muda para português", "pt"),
        ("spreek Nederlands", "nl"),
        ("taal Engels", "en"),
        ("language Dutch", "nl"),
        ("langue anglais", "en"),
        ("parle français", "fr"),
        ("Sprache Englisch", "en"),
        ("sprich Deutsch", "de"),
        ("em inglês", "en"),
        ("in het Nederlands", "nl"),
        ("english please", "en"),
        ("english", "en"),
    ],
)
def test_language_requests_are_understood(text: str, lang: str) -> None:
    assert parse_language_command(text) == lang


@pytest.mark.parametrize(
    "text",
    [
        "aula de inglês 40",
        "gastei 20 no uber",
        "o inglês",
        "mudar en",
        "pt",
        "inglês é difícil",
        "",
    ],
)
def test_language_lookalikes_are_not_requests(text: str) -> None:
    assert parse_language_command(text) is None


@db
async def test_changing_language_replies_in_the_new_language(lab: Lab) -> None:  # noqa: F811
    assert await lab.say("Change to English") == _t("lang_changed", "en")
    assert await lab.scalar(select(Member.language).where(Member.id == lab.member_id)) == "en"
    assert await lab.say("taal Nederlands") == _t("lang_changed", "nl")
    assert await lab.say("idioma português") == _t("lang_changed", "pt")
    assert lab.habit.await_count == 0  # it was a habit ("meditação") before the fix


# ── natural reminders and tasks ───────────────────────────────────────────────


@db
@pytest.mark.parametrize(
    ("text", "hhmm"),
    [
        ("me lembra de tomar remédio às 8h", "08:00"),
        ("me lembra de tomar o remédio às 8:30", "08:30"),
        ("lembre-me de treinar às 18h45", "18:45"),
        ("remind me to take medication at 8", "08:00"),
        ("herinner me eraan om medicijnen te nemen om 9 uur", "09:00"),
        ("lembrete: tomar o remédio às 08:00", "08:00"),
    ],
)
async def test_reminders_in_natural_words(lab: Lab, text: str, hhmm: str) -> None:  # noqa: F811
    await lab.say(text)
    rows = await lab.rows(select(ScheduledJob.time_of_day))
    assert [r[0] for r in rows] == [hhmm]


@db
async def test_task_without_colon(lab: Lab) -> None:  # noqa: F811
    await lab.say("tarefa comprar pão")
    assert await lab.scalar(select(func.count()).select_from(Task)) == 1
    await lab.say("tarefa: ligar para o banco")
    assert await lab.scalar(select(func.count()).select_from(Task)) == 2
    assert await lab.scalar(select(func.count()).select_from(Task)) == 2
    await lab.say("minhas tarefas")  # the list command is not a new task
    assert await lab.scalar(select(func.count()).select_from(Task)) == 2


# ── amounts ───────────────────────────────────────────────────────────────────


def test_negative_money_has_the_sign_first() -> None:
    assert _fmt_eur(-133.6) == "-€133,60"
    assert _fmt_eur(1234.5) == "€1.234,50"
    assert _fmt_eur(0) == "€0,00"


@db
async def test_zero_amount_is_not_taken_for_a_habit(lab: Lab) -> None:  # noqa: F811
    lab.habit = AsyncMock(return_value={"activity": "café", "days_ago": 0})
    assert await lab.say("café 0") == _t("invalid_amount_check", "pt")
    assert lab.habit.await_count == 0


# ── the help text only advertises commands that work ─────────────────────────

EXAMPLES = {
    "pt": [
        "a pagar luz 120 dia 20",
        "orçamento mercado 300",
        "dentista amanhã às 14h",
        "lembrete: tomar o remédio às 08:00",
        "tarefa: comprar pão",
    ],
    "nl": [
        "te betalen huur 900 dag 1",
        "budget supermarkt 300",
        "tandarts morgen om 14u",
        "herinnering: medicijnen om 08:00",
        "taak: brood kopen",
    ],
    "en": [
        "to pay rent 900 day 1",
        "budget groceries 300",
        "dentist tomorrow at 2pm",
        "reminder: take medication at 08:00",
        "task: buy bread",
    ],
    "fr": [
        "à payer loyer 900 jour 1",
        "budget courses 300",
        "dentiste demain à 14h",
        "rappel : médicament à 08:00",
        "tâche : acheter du pain",
    ],
    "de": [
        "zu zahlen Miete 900 Tag 1",
        "Budget Supermarkt 300",
        "Zahnarzt morgen um 14 Uhr",
        "Erinnerung: Medikament um 08:00",
        "Aufgabe: Brot kaufen",
    ],
}


@db
@pytest.mark.parametrize("lang", sorted(EXAMPLES))
async def test_every_example_in_the_help_text_works(lab: Lab, lang: str) -> None:  # noqa: F811
    async with AsyncSessionLocal() as s:
        await s.execute(update(Member).where(Member.id == lab.member_id).values(language=lang))
        await s.commit()
    for text in EXAMPLES[lang]:
        await lab.say(text)
    counts = [
        await lab.scalar(select(func.count()).select_from(model))
        for model in (Expense, Budget, Appointment, ScheduledJob, Task)
    ]
    assert counts == [1, 1, 1, 1, 1], (lang, counts)
    assert lab.llm_reply.await_count == 0


# ── tone decisions of 01/10: formal pt-BR, straight quotes, "Supermercado", Undo on drafts ──


def test_one_quote_style_outside_french() -> None:
    from alfred.conversation import _STRINGS

    for key, langs in _STRINGS.items():
        for lg in ("pt", "nl", "en", "de"):
            variants = langs[lg] if isinstance(langs[lg], tuple) else (langs[lg],)
            for tmpl in variants:
                try:
                    out = tmpl.format_map(_Any())
                except (KeyError, ValueError, IndexError):
                    continue
                assert "\u201c" not in _t_render(key, lg), key
                assert "\u201d" not in _t_render(key, lg), key
                del out


class _Any(dict):
    def __missing__(self, key: str) -> str:
        return "x"


def _t_render(key: str, lang: str) -> str:
    from alfred import conversation

    tmpl = conversation._STRINGS[key][lang]
    tmpl = tmpl[0] if isinstance(tmpl, tuple) else tmpl
    # same path as _t, with every placeholder filled
    import string

    fields = {f for _, f, _, _ in string.Formatter().parse(tmpl) if f}
    return conversation._t(key, lang, **dict.fromkeys(fields, "x"))


def test_portuguese_llm_prompts_are_brazilian_and_formal() -> None:
    from alfred import llm

    assert "Brasil" in llm._LANG_INSTRUCTION["pt"]
    assert (
        "Responda" in llm._LANG_INSTRUCTION["pt"] and "Responde " not in llm._LANG_INSTRUCTION["pt"]
    )
    assert "tua" not in llm._LLM_ERROR["pt"] and "Tente" in llm._LLM_ERROR["pt"]


def test_draft_buttons_say_undo_not_cancel() -> None:
    from alfred.batch import STRINGS

    assert STRINGS["batch_btn_cancel"]["pt"] == "Desfazer"
    assert all(len(v) <= 20 for v in STRINGS["batch_btn_cancel"].values())  # Meta button limit


def _exp(amount: float, name: str) -> dict:
    return {
        "amount": amount,
        "currency": "EUR",
        "merchant": name,
        "category": "supermarkt",
        "description": name,
        "type": "expense",
        "days_ago": 0,
    }


@db
async def test_category_context_uses_the_category_name(lab: Lab) -> None:  # noqa: F811
    lab.expense.return_value = _exp(45, "mercado")
    await lab.say("gastei 45 no mercado")
    lab.expense.return_value = _exp(6.4, "Padaria")
    reply = await lab.say("Padaria 6,40")
    assert "Supermercado" in reply and "supermercado" not in reply
