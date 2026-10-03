"""panel.js in a real browser (Chromium via Playwright), network faked with route handlers."""

from __future__ import annotations

import asyncio
import json
import uuid

import pytest

from alfred import panel
from alfred.web_security import _dashboard_csp

pw = pytest.importorskip("playwright.async_api")

ORIGIN = "http://panel.test"
TOKEN = str(uuid.uuid4())
BALANCE = {
    "id": "balance",
    "empty": False,
    "values": {"income": 100.0, "expense": 40.0, "balance": 60.0},
    "phrase": None,
}


async def _open(handlers: dict, lang: str = "pt"):
    """(playwright, browser, page) with /d/ + assets served locally and /api/ answered by handlers.

    ``handlers[tab]`` is an async callable returning (status, body_dict)."""
    nonce = "n0nce"
    pwm = await pw.async_playwright().start()
    try:
        browser = await pwm.chromium.launch()
    except Exception as exc:  # no Chromium on this machine (like scripts/panel_shots.py)
        await pwm.stop()
        pytest.skip(f"Chromium not available: {exc}")
    page = await browser.new_page()
    page.set_default_timeout(4000)

    async def route(r):
        url = r.request.url
        path = url.removeprefix(ORIGIN)
        if path == f"/d/{TOKEN}":
            await r.fulfill(
                status=200,
                content_type="text/html; charset=utf-8",
                headers={"Content-Security-Policy": _dashboard_csp(nonce, v2=True)},
                body=panel.render_v2(nonce, lang, TOKEN),
            )
        elif path.startswith("/panel-assets/"):
            body, ctype = panel.asset(path.split("/")[-1].split("?")[0])
            await r.fulfill(status=200, body=body, content_type=ctype)
        elif path.startswith(f"/api/d/{TOKEN}/"):
            tab = path.split("/")[-1].split("?")[0]
            status, body = await handlers[tab]()
            await r.fulfill(status=status, content_type="application/json", body=json.dumps(body))
        else:
            await r.abort()

    await page.route("**/*", route)
    await page.goto(f"{ORIGIN}/d/{TOKEN}")
    return pwm, browser, page


async def _ok(cards):
    return 200, {"tab": "x", "lang": "pt", "filter": {}, "cards": cards}


async def _close(pwm, browser) -> None:
    await browser.close()
    await pwm.stop()


async def test_a_slow_tab_never_overwrites_the_tab_the_member_moved_to() -> None:
    async def slow():
        await asyncio.sleep(0.8)
        return await _ok([BALANCE])

    async def fast():
        return await _ok([])

    pwm, browser, page = await _open({"summary": slow, "money": fast})
    try:
        await page.click('[data-tab="money"]')
        await page.wait_for_selector("#panel .empty:has-text('Em breve')")
        await asyncio.sleep(1.2)  # the slow summary answer arrives now
        assert await page.locator("#panel [data-card]").count() == 0
        assert await page.get_attribute('[data-tab="money"]', "aria-selected") == "true"
    finally:
        await _close(pwm, browser)


@pytest.mark.parametrize(
    ("status", "needle"),
    [(404, "expirou"), (429, "Muitas consultas"), (500, "Não foi possível carregar")],
)
async def test_each_failure_says_what_to_do(status, needle) -> None:
    async def fail():
        return status, {"detail": "x"}

    pwm, browser, page = await _open({"summary": fail})
    try:
        await page.wait_for_selector(f"#panel .empty:has-text('{needle}')")
    finally:
        await _close(pwm, browser)


async def test_one_failing_tab_does_not_break_the_others() -> None:
    async def fail():
        return 500, {}

    async def good():
        return await _ok([BALANCE])

    pwm, browser, page = await _open({"summary": fail, "money": good})
    try:
        await page.wait_for_selector("#panel .empty")
        await page.click('[data-tab="money"]')
        await page.wait_for_selector("#panel [data-card='balance']")
        await page.click('[data-tab="summary"]')
        await page.wait_for_selector("#panel .empty:has-text('Não foi possível')")
    finally:
        await _close(pwm, browser)


async def test_tabs_are_wired_to_a_labelled_tabpanel() -> None:
    async def good():
        return await _ok([BALANCE])

    pwm, browser, page = await _open({"summary": good, "money": good})
    try:
        await page.wait_for_selector("#panel [data-card]")
        assert await page.get_attribute("#panel", "role") == "tabpanel"
        tab = await page.get_attribute('[data-tab="summary"]', "id")
        assert tab and await page.get_attribute("#panel", "aria-labelledby") == tab
        assert await page.get_attribute('[data-tab="summary"]', "aria-controls") == "panel"
        await page.click('[data-tab="money"]')
        await page.wait_for_selector("#panel [data-card]")
        money_id = await page.get_attribute('[data-tab="money"]', "id")
        assert await page.get_attribute("#panel", "aria-labelledby") == money_id
    finally:
        await _close(pwm, browser)


async def test_balance_bars_share_one_scale_when_spending_exceeds_income() -> None:
    card = {**BALANCE, "values": {"income": 12115.0, "expense": 101174.4, "balance": -89059.4}}

    async def good():
        return await _ok([card])

    pwm, browser, page = await _open({"summary": good})
    try:
        await page.wait_for_selector("#panel [data-card]")
        widths = await page.eval_on_selector_all(
            "#panel .bar > *", "els => els.map(e => parseFloat(e.style.width))"
        )
        assert widths[1] == pytest.approx(100.0)  # the larger one fills the track
        assert widths[0] == pytest.approx(12115.0 / 101174.4 * 100, abs=0.1)  # not a full bar
    finally:
        await _close(pwm, browser)


async def test_a_tab_without_cards_yet_says_it_is_coming_not_that_data_is_missing() -> None:
    async def none():
        return await _ok([])

    pwm, browser, page = await _open({"summary": none})
    try:
        await page.wait_for_selector("#panel .empty:has-text('Em breve')")
    finally:
        await _close(pwm, browser)
