"""Internal replay of the manual hard test of 18–20/10/2026 (632 typed messages and taps).

Every step of the checklist that does not need a real service (sign-up page, couple, Google,
wipe) is sent, in order, to one member through the real router and database. The model is
stubbed (it never answers), so what is checked is what the code itself says:

* every message gets exactly one reply and nothing raises;
* no reply leaks an internal word or a broken template ("overig", "{", "None", ", .", "..",
  "(Outubro 2026)", "1 lançamentos");
* a tap finds its button on an earlier reply (as on the phone) or is skipped.

``ALFRED_REPLAY_OUT=/path.json`` writes every step with its reply, for a human read.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

from alfred.conversation import _t
from tests.labkit import Lab

db = pytest.mark.skipif(os.environ.get("ALFRED_TEST_DB") != "1", reason="needs test DB")

STEPS = json.loads((Path(__file__).parent / "fixtures" / "hard_test_2010_steps.json").read_text())

_LEAKS = [
    re.compile(r"\boverig\b"),
    re.compile(r"[{}]"),
    re.compile(r"\bNone\b"),
    re.compile(r", \."),
    re.compile(r"(?<!\.)\.\.(?!\.)"),
    re.compile(
        r"\((?:Janeiro|Fevereiro|Março|Abril|Maio|Junho|Julho|Agosto|Setembro|Outubro"
        r"|Novembro|Dezembro) 20\d\d\)"
    ),
    re.compile(r"\b1 (?:lançamentos|despesas|receitas|mensagens|itens|coisas|sessões)\b"),
]


def _find_button(lab: Lab, title: str) -> str | None:
    want = title.strip().lower()
    for buttons in reversed(lab.buttons):
        for bid, btitle in buttons:
            if btitle.strip().lower() == want:
                return bid
    return None


def _stopped(log: list[dict]) -> bool:
    """True after a stop and before the next START (silence is then the right answer)."""
    for entry in reversed(log):
        reply = entry.get("reply") or ""
        if "START" in reply:
            return True
        if reply == _t("resumed_welcome", "pt"):
            return False
    return False


@db
async def test_the_hard_test_replays_cleanly(lab: Lab) -> None:
    from datetime import UTC, datetime

    from alfred.db import AsyncSessionLocal
    from alfred.models import Member

    async with AsyncSessionLocal() as s:  # a member who finished the sign-up page (as J)
        m = await s.get(Member, lab.member_id)
        m.disclosure_accepted_at = datetime.now(UTC)
        m.preferred_name = "João"
        await s.commit()
    log: list[dict] = []
    problems: list[str] = []
    for step in STEPS:
        before = len(lab.sent)
        try:
            if "send" in step:
                reply = await lab.say(step["send"])
            else:
                bid = _find_button(lab, step["tap"])
                if bid is None:
                    log.append({**step, "reply": None, "skipped": "no such button"})
                    continue
                reply = await lab.tap(bid, step["tap"])
        except AssertionError:
            if len(lab.sent) != before:
                raise  # two replies to one message
            if not _stopped(log):  # after "stop" the member is not answered (F19)
                problems.append(f"{step['id']}: no reply")
            log.append({**step, "reply": None, "silent": True})
            continue
        log.append({**step, "reply": reply, "buttons": [t for _, t in lab.buttons[-1]]})
        if reply == "[llm]":
            continue  # the stubbed model: nothing of ours to check
        for rx in _LEAKS:
            if rx.search(reply):
                problems.append(f"{step['id']}: {rx.pattern!r} in {reply[:160]!r}")
    out = os.environ.get("ALFRED_REPLAY_OUT")
    if out:
        with open(out, "w", encoding="utf-8") as fh:  # noqa: ASYNC230 (test-only dump)
            json.dump(log, fh, ensure_ascii=False, indent=1)
    assert not problems, "\n".join(problems)
