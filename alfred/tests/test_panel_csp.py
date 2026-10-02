from __future__ import annotations

import os
import re
from datetime import UTC, datetime

import pytest

from alfred import dashboard, panel_tokens
from alfred.db import AsyncSessionLocal, engine
from alfred.models import Expense, Member, Note, Task

db = pytest.mark.skipif(os.environ.get("ALFRED_TEST_DB") != "1", reason="needs test DB")

XSS = ["<script>window.__x=1</script>", '"><img src=x onerror=window.__x=1>']


async def _token(lab) -> str:
    async with AsyncSessionLocal() as s:
        m = await s.get(Member, lab.member_id)
        await panel_tokens.ensure_panel_token(s, m)
        await s.commit()
        t = str(m.dashboard_token)
    await engine.dispose()
    return t


@db
async def test_csp_uses_a_fresh_nonce_and_no_unsafe_inline(lab, client) -> None:
    token = await _token(lab)
    a = await client.get(f"/d/{token}")
    await engine.dispose()
    b = await client.get(f"/d/{token}")
    csp_a, csp_b = a.headers["content-security-policy"], b.headers["content-security-policy"]
    assert "unsafe-inline" not in csp_a and "unsafe-inline" not in csp_b
    n_a = re.search(r"'nonce-([^']+)'", csp_a).group(1)
    n_b = re.search(r"'nonce-([^']+)'", csp_b).group(1)
    assert n_a != n_b and len(n_a) >= 16
    assert f'nonce="{n_a}"' in a.text
    assert 'style="' not in a.text and "onclick=" not in a.text
    assert "__NONCE__" not in a.text
    assert "font-src 'self'" in csp_a
    await engine.dispose()


def test_template_has_no_inline_event_handlers_or_style_attributes() -> None:
    html = dashboard._HTML_TEMPLATE
    assert 'style="' not in html
    assert not re.search(r"\son[a-z]+=", html)
    assert '<script nonce="__NONCE__">' in html and '<style nonce="__NONCE__">' in html


def test_every_user_text_field_goes_through_esc_in_the_page_script() -> None:
    # field names the v1 page really renders: transactions, tasks and notes
    js = dashboard._HTML_TEMPLATE
    for field in ("t.merchant", "t.body", "n.body"):
        assert f"esc({field})" in js, field


@db
async def test_hostile_text_is_inert_in_the_json_and_stored_raw(lab, client) -> None:
    async with AsyncSessionLocal() as s:
        for i, evil in enumerate(XSS):
            s.add(
                Expense(
                    member_id=lab.member_id,
                    household_id=lab.household_id,
                    transaction_type="expense",
                    amount=1.0 + i,
                    merchant=evil,
                    category="overig",
                    expense_date=datetime.now(UTC),
                )
            )
            s.add(Note(member_id=lab.member_id, body=evil))
            s.add(Task(member_id=lab.member_id, body=evil))
        await s.commit()
    await engine.dispose()
    token = await _token(lab)
    r = await client.get(f"/api/d/{token}")
    await engine.dispose()
    assert "application/json" in r.headers["content-type"]
    data = r.json()
    merchants = {t["merchant"] for t in data["recent_transactions"]}
    assert set(XSS) <= merchants  # raw in JSON is fine; the page escapes on render
    # the HTML shell itself never embeds user text
    page = (await client.get(f"/d/{token}")).text
    assert "window.__x" not in page
    await engine.dispose()
