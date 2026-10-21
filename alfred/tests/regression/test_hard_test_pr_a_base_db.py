"""B-074: more than three options go out as a WhatsApp list (rows <= 24), and a row tap is a tap."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from alfred import whatsapp
from tests.test_panel_cta import _Client, _Resp

db = pytest.mark.skipif(os.environ.get("ALFRED_TEST_DB") != "1", reason="needs test DB")


@pytest.mark.bug("B-074")
async def test_up_to_three_options_stay_reply_buttons() -> None:
    sent: list = []
    with patch("alfred.whatsapp.httpx.AsyncClient", lambda **kw: _Client(_Resp(True), sent)):
        await whatsapp.send_buttons(
            "3161", "Quando?", [("a", "Hoje"), ("b", "Amanhã"), ("c", "Outra")]
        )
    assert sent[0]["interactive"]["type"] == "button"


@pytest.mark.bug("B-074")
async def test_four_options_become_a_list_with_short_rows() -> None:
    sent: list = []
    opts = [("m", "Manhã (9h)"), ("t", "Tarde (15h)"), ("d", "Dia todo"), ("x", "y" * 40)]
    with patch("alfred.whatsapp.httpx.AsyncClient", lambda **kw: _Client(_Resp(True), sent)):
        await whatsapp.send_buttons("3161", "Que horas?", opts)
    i = sent[0]["interactive"]
    assert i["type"] == "list"
    rows = i["action"]["sections"][0]["rows"]
    assert [r["id"] for r in rows] == ["m", "t", "d", "x"]
    assert all(len(r["title"]) <= 24 for r in rows)
    assert len(i["action"]["button"]) <= 20


@pytest.mark.bug("B-074")
async def test_a_failed_list_falls_back_to_plain_text() -> None:
    sent: list = []
    with patch("alfred.whatsapp.httpx.AsyncClient", lambda **kw: _Client(_Resp(False), sent)):
        with patch("alfred.whatsapp.send_text") as text:
            text.return_value = {"ok": True}
            await whatsapp.send_buttons(
                "3161", "Que horas?", [(str(n), f"op {n}") for n in range(5)]
            )
    assert "op 4" in text.call_args.args[1]


# ── follow-up questions (questions.py) ────────────────────────────────────────

from alfred import questions  # noqa: E402
from alfred.couple import Reply  # noqa: E402
from alfred.db import AsyncSessionLocal  # noqa: E402
from alfred.models import Member, PendingAction  # noqa: E402
from tests.labkit import Lab  # noqa: E402


async def _echo(session, member, lang, data, choice, today) -> Reply:
    return Reply(f"echo:{choice}:{data.get('v')}", [])


def _digit(text: str, data: dict) -> str | None:
    return text.strip() if text.strip().isdigit() else None


questions.register("t_digit", _echo, _digit)
questions.register("t_yes", _echo)


async def _ask(lab: Lab, type_: str, **data) -> None:
    async with AsyncSessionLocal() as s:
        member = await s.get(Member, lab.member_id)
        await questions.ask(s, member, type_, data, "Pergunta?", [("a", "A"), ("b", "B")])
        await s.commit()


async def _count(lab: Lab) -> int:
    rows = await lab.rows(
        __import__("sqlalchemy")
        .select(PendingAction)
        .where(PendingAction.member_id == lab.member_id, PendingAction.kind == questions.KIND)
    )
    return len(rows)


@db
@pytest.mark.bug("B-074")
async def test_a_button_answers_once_and_a_second_tap_does_nothing(lab: Lab) -> None:
    await _ask(lab, "t_digit", v=7)
    button_id = (await lab.rows(__import__("sqlalchemy").select(PendingAction.id)))[0][0]
    out = await lab.tap(f"fu:{button_id}:a")
    assert out == "echo:a:7"
    assert await _count(lab) == 0
    again = await lab.tap(f"fu:{button_id}:a")
    assert again != "echo:a:7"


@db
@pytest.mark.bug("B-074")
async def test_typed_answer_matches_the_strict_pattern_of_the_open_question(lab: Lab) -> None:
    await _ask(lab, "t_digit", v=1)
    assert await lab.say("42") == "echo:42:1"


@db
@pytest.mark.bug("B-074")
async def test_another_message_closes_the_question_and_goes_on(lab: Lab) -> None:
    await _ask(lab, "t_digit", v=1)
    out = await lab.say("saldo")
    assert "echo" not in out
    assert await _count(lab) == 0


@db
@pytest.mark.bug("B-074")
async def test_a_new_question_replaces_the_old_one(lab: Lab) -> None:
    await _ask(lab, "t_digit", v=1)
    await _ask(lab, "t_digit", v=2)
    assert await _count(lab) == 1
    assert await lab.say("5") == "echo:5:2"


@db
@pytest.mark.bug("B-075")
async def test_a_bare_yes_answers_the_open_yes_no_question(lab: Lab) -> None:
    await _ask(lab, "t_yes", v=9, yes_no=True)
    assert await lab.say("sim") == "echo:yes:9"


@db
@pytest.mark.bug("B-075")
async def test_a_bare_yes_with_no_question_asks_what_for(lab: Lab) -> None:
    out = await lab.say("sim")
    assert "echo" not in out and "?" in out or "“" in out or '"' in out
    assert "Sim para quê" in out or "pendente" in out or "pergunta aberta" in out


@db
@pytest.mark.bug("B-075")
async def test_change_language_sends_a_list_and_a_row_changes_it(lab: Lab) -> None:
    await lab.say("mudar idioma")
    rows = lab.buttons[-1]
    assert [r[1] for r in rows] == ["Português", "Nederlands", "English", "Français", "Deutsch"]
    out = await lab.pick("lang_set:en")
    assert "English" in out or "language" in out.lower()
