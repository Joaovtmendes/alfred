"""B-103/B-104: the sign-up form uses the site's own messages; analysis answers can be explained."""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from alfred.db import AsyncSessionLocal, engine
from alfred.models import Household, Member
from tests.labkit import Lab

db = pytest.mark.skipif(os.environ.get("ALFRED_TEST_DB") != "1", reason="needs test DB")


async def _pending(lang: str):
    async with AsyncSessionLocal() as s:
        hh = Household(name="signup")
        s.add(hh)
        await s.flush()
        token = uuid.uuid4()
        s.add(
            Member(
                household_id=hh.id,
                wa_phone="3161" + uuid.uuid4().hex[:7],
                consent_state="pending_signup",
                language=lang,
                signup_token=token,
                signup_token_expires_at=datetime.now(UTC) + timedelta(minutes=30),
            )
        )
        await s.commit()
    return token


@db
@pytest.mark.bug("B-103")
async def test_signup_form_is_novalidate_and_errors_come_in_the_chosen_language(client) -> None:
    token = await _pending("pt")
    page = await client.get(f"/cadastro/{token}")
    assert "novalidate" in page.text
    # an empty e-mail posted in Dutch gets the site's own Dutch message, not the browser's
    bad = await client.post(
        f"/cadastro/{token}",
        content="lang=nl&name=Maria&email=&privacy=1",
        headers={"content-type": "application/x-www-form-urlencoded"},
    )
    assert bad.status_code == 400 and "Vul een geldig e-mailadres in." in bad.text
    await engine.dispose()
    no_box = await client.post(
        f"/cadastro/{token}",
        content="lang=de&name=Maria&email=maria@example.com",
        headers={"content-type": "application/x-www-form-urlencoded"},
    )
    assert no_box.status_code == 400 and "Datenschutzerklärung" in no_box.text
    await engine.dispose()


SPEC = {
    "supported": True,
    "spec": {"metric": "spent", "group_by": "category", "period": "last_3_months"},
}


@db
@pytest.mark.bug("B-104")
async def test_analysis_answer_has_an_explain_button(lab: Lab) -> None:
    with patch("alfred.analysis.extract_analysis_spec", AsyncMock(return_value=SPEC)):
        await lab.say("quanto gastei por categoria nos últimos 3 meses?")
    (btn,) = [b for b in lab.buttons[-1] if b[0].startswith("an_exp:")]
    out = await lab.tap(btn[0])
    assert "Somei as despesas" in out and "Período" in out and "aba Dinheiro" in out
    assert "pagos ou recebidos" in out
