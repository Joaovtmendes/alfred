# ruff: noqa: E501
"""panel.js in a real browser (Chromium via Playwright), network faked with route handlers."""

from __future__ import annotations

import asyncio
import json
import uuid

import pytest

from alfred import panel
from alfred.web_security import _dashboard_csp
from tests import panel_fixtures as fx

pw = pytest.importorskip("playwright.async_api")

ORIGIN = "http://panel.test"
TOKEN = str(uuid.uuid4())
BALANCE = {
    "id": "balance",
    "empty": False,
    "values": {"income": 100.0, "expense": 40.0, "balance": 60.0},
    "phrase": None,
}


CSP_PROBE = """
window.__csp = [];
document.addEventListener('securitypolicyviolation', e => window.__csp.push(e.violatedDirective));
"""


async def _open(handlers: dict, lang: str = "pt", query: str = "", size: dict | None = None):
    """(playwright, browser, page) with /d/ + assets served locally and /api/ answered by handlers.

    ``handlers[tab]`` is an async callable returning (status, body_dict)."""
    nonce = "n0nce"
    pwm = await pw.async_playwright().start()
    try:
        browser = await pwm.chromium.launch()
    except Exception as exc:  # no Chromium on this machine (like scripts/panel_shots.py)
        await pwm.stop()
        pytest.skip(f"Chromium not available: {exc}")
    page = await browser.new_page(viewport=size or {"width": 1280, "height": 900})
    page.set_default_timeout(4000)
    page.api_calls = []  # (method, url) of every API request the page makes
    await page.add_init_script(CSP_PROBE)

    async def route(r):
        url = r.request.url
        path = url.removeprefix(ORIGIN)
        if path.split("?")[0] == f"/d/{TOKEN}":
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
            page.api_calls.append((r.request.method, url))
            status, body = await handlers[tab]()
            await r.fulfill(status=status, content_type="application/json", body=json.dumps(body))
        else:
            await r.abort()

    await page.route("**/*", route)
    await page.goto(f"{ORIGIN}/d/{TOKEN}{query}")
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


async def test_a_tab_without_cards_yet_says_it_is_coming_not_that_data_is_missing() -> None:
    async def none():
        return await _ok([])

    pwm, browser, page = await _open({"summary": none})
    try:
        await page.wait_for_selector("#panel .empty:has-text('Em breve')")
    finally:
        await _close(pwm, browser)


# ── the cards of Resumo and Dinheiro ─────────────────────────────────────────────────────────


def _handlers(summary=None, money=None):
    async def s():
        return 200, summary or fx.SUMMARY

    async def m():
        return 200, money or fx.MONEY

    async def other():
        return 200, {"tab": "x", "lang": "pt", "filter": {}, "cards": []}

    return {"summary": s, "money": m, "agenda": other, "health": other, "trips": other}


async def _poll(page, expression: str) -> None:
    """wait_for_function evaluates strings, which this page's CSP (rightly) forbids."""
    for _ in range(60):
        if await page.evaluate(expression):
            return
        await asyncio.sleep(0.05)
    raise AssertionError(expression)


async def _last_call_ends_with(page, tail: str) -> None:
    """The API call follows the address change a moment later: poll for it."""
    for _ in range(40):
        if page.api_calls and page.api_calls[-1][1].endswith(tail):
            return
        await asyncio.sleep(0.05)
    raise AssertionError((tail, page.api_calls[-1:]))


async def _text(page, selector: str) -> str:
    return await page.inner_text(selector)


async def test_balance_card_shows_hero_estimate_tiles_bar_and_the_alfred_phrase() -> None:
    pwm, browser, page = await _open(_handlers())
    try:
        await page.wait_for_selector("[data-card=balance]")
        card = "[data-card=balance]"
        assert "€ 1.842,30" in await _text(page, f"{card} .hero")
        assert "~ € 1.120" in await _text(page, f"{card} .proj-side")
        assert "faixa € 1.030 a € 1.210" in await _text(page, f"{card} .proj-side")
        tiles = await page.locator(f"{card} .tile").all_inner_texts()
        assert "€ 3.840,00" in tiles[0] and "igual a setembro" in tiles[0]
        assert "€ 1.997,70" in tiles[1] and "4% acima de setembro" in tiles[1]
        assert await page.locator(f"{card} .legend li").count() == 3  # realised, bills, variable
        flex = await page.eval_on_selector_all(
            f"{card} .bar.stacked > span", "e => e.map(x => parseFloat(x.style.flexGrow))"
        )
        assert flex[0] > flex[1] and flex[2] > flex[1]  # proportional to 1842 / 298 / 424
        assert await page.locator(f"{card} .bar > span.hatch").count() == 1
        voice = await _text(page, f"{card} .voice")
        assert "você fecha outubro" in voice and "Peça no chat: “como fica meu mês?”" in voice
    finally:
        await _close(pwm, browser)


async def test_on_a_phone_the_end_of_month_total_sits_below_the_legend() -> None:
    pwm, browser, page = await _open(_handlers(), size={"width": 390, "height": 844})
    try:
        await page.wait_for_selector("[data-card=balance]")
        legend = await page.locator("[data-card=balance] .legend").bounding_box()
        total = await page.locator("[data-card=balance] .total").bounding_box()
        assert total["y"] >= legend["y"] + legend["height"]  # "fim do mês" BELOW the legend
        assert "~ € 1.120" in await _text(page, "[data-card=balance] .total")
        assert not await page.locator("[data-card=balance] .proj-side").is_visible()
        # the legend is a readable column: every label and amount fully inside the card
        for li in await page.locator("[data-card=balance] .legend li").all():
            box, card = (
                await li.bounding_box(),
                await page.locator("[data-card=balance]").bounding_box(),
            )
            assert box["x"] >= card["x"] and box["x"] + box["width"] <= card["x"] + card["width"]
    finally:
        await _close(pwm, browser)


async def test_projection_without_enough_history_shows_only_what_is_known() -> None:
    card = {**fx.SUMMARY["cards"][0]}
    card["projection"] = {
        **card["projection"],
        "sufficient": False,
        "variable": None,
        "projected": None,
        "low": None,
        "high": None,
    }
    body = {**fx.SUMMARY, "cards": [card]}
    pwm, browser, page = await _open(_handlers(summary=body))
    try:
        await page.wait_for_selector("[data-card=balance]")
        assert await page.locator("[data-card=balance] .proj-side").count() == 0
        assert await page.locator("[data-card=balance] .total").count() == 0
        assert (
            await page.locator("[data-card=balance] .legend li").count() == 2
        )  # no invented estimate
        assert await page.locator("[data-card=balance] .bar > span.hatch").count() == 0
    finally:
        await _close(pwm, browser)


async def test_upcoming_categories_budgets_blue_days_and_owed() -> None:
    pwm, browser, page = await _open(_handlers())
    try:
        await page.wait_for_selector("[data-card=owed]")
        bills = page.locator("[data-card=upcoming] .bill")
        assert await bills.count() == 3
        assert "hot" in await bills.nth(0).locator(".date-tile").get_attribute("class")
        assert "hot" not in await bills.nth(1).locator(".date-tile").get_attribute("class")
        assert "vence em 4 dias" in await bills.nth(0).inner_text()
        rows = page.locator("[data-card=categories] .cat")
        assert await rows.count() == 5
        widths = await page.eval_on_selector_all(
            "[data-card=categories] .cat .track > i", "e => e.map(x => parseFloat(x.style.width))"
        )
        assert widths[0] == pytest.approx(100.0) and widths[1] == pytest.approx(
            148 / 312 * 100, abs=0.1
        )
        texts = await rows.all_inner_texts()
        assert "▲ 8%" in texts[0] and "▼ 11%" in texts[2] and "= igual" in texts[4]
        budgets = page.locator("[data-card=budgets] .brow")
        assert await budgets.count() == 3
        assert "€ 148 de € 120 · 123%" in await budgets.nth(0).inner_text()
        assert "74" in await _text(page, "[data-card=budgets] .hero")
        assert (
            await page.locator("[data-card=budgets] .track > u").count() == 6
        )  # 80% and 100% marks
        assert await page.locator("[data-card=budgets] .track > i.warm").count() == 1
        assert "12" in await _text(page, "[data-card=blue_days] .hero")
        assert "€ 34,50" in await _text(page, "[data-card=owed] .hero")
        assert "Marta" in await _text(page, "[data-card=owed]") and "há 9 dias" in await _text(
            page, "[data-card=owed]"
        )
    finally:
        await _close(pwm, browser)


async def test_summary_card_order_follows_the_mockups_desktop_and_phone() -> None:
    async def order(page):
        return await page.eval_on_selector_all(
            "#panel [data-card]",
            "els => els.sort((a, b) => a.getBoundingClientRect().top - b.getBoundingClientRect().top || a.getBoundingClientRect().left - b.getBoundingClientRect().left).map(e => e.dataset.card)",
        )

    pwm, browser, page = await _open(_handlers())
    try:
        await page.wait_for_selector("[data-card=owed]")
        assert await order(page) == [
            "balance",
            "upcoming",
            "categories",
            "budgets",
            "blue_days",
            "owed",
        ]
        grid = await page.eval_on_selector_all(
            "#panel [data-card]", "els => els.map(e => Math.round(e.getBoundingClientRect().width))"
        )
        assert grid[0] > grid[1] > 0 and abs(grid[0] / grid[1] - 7 / 5) < 0.1  # 7 + 5 of 12 columns
        await page.set_viewport_size({"width": 390, "height": 844})
        assert await order(page) == [
            "balance",
            "categories",
            "budgets",
            "blue_days",
            "upcoming",
            "owed",
        ]
        lefts = await page.eval_on_selector_all(
            "#panel [data-card]", "els => els.map(e => Math.round(e.getBoundingClientRect().left))"
        )
        assert len(set(lefts)) == 1  # one column
    finally:
        await _close(pwm, browser)


async def test_money_cards() -> None:
    pwm, browser, page = await _open(_handlers(), query="")
    try:
        await page.click('[data-tab="money"]')
        await page.wait_for_selector("[data-card=recurring]")
        tx = "[data-card=transactions]"
        days = await page.locator(f"{tx} .day").all_inner_texts()
        assert "Hoje, 2 out" in days[0] and "+ € 3.797,82" in days[0]  # the day's total
        assert "1 out" in days[1] and "30 set" in days[2]
        rows = await page.locator(f"{tx} .entry").all_inner_texts()
        assert (
            "Albert Heijn" in rows[0]
            and "supermercado · pago" in rows[0].lower()
            and "− € 42,18" in rows[0]
        )
        assert "+ € 3.840,00" in rows[1] and "recebido" in rows[1]
        assert "a pagar" in rows[-1]
        assert "mostre meus gastos de outubro" in await _text(page, tx)
        assert "exportar meus dados" in await _text(page, tx)  # the copy comes through the chat
        assert await page.locator(f"{tx} button").count() == 0  # single page: no pager
        assert await page.locator("[data-card=top_expenses] .brow").count() == 5
        mom = await _text(page, "[data-card=month_vs_month]")
        assert "€ 1.997" in mom and "▲ 9% contra setembro" in mom and "igual a setembro" in mom
        assert "62" in await _text(page, "[data-card=fixed_variable] .hero")
        legend = await page.locator("[data-card=fixed_variable] .legend li").all_inner_texts()
        assert len(legend) == 2 and "1.238" in legend[0] and "760" in legend[1]
        assert await page.locator("[data-card=daily] .daily > i").count() == 31
        assert await page.locator("[data-card=daily] .daily > i.peak").count() == 1
        cats = page.locator("[data-card=categories] .cat")
        assert await cats.count() == 7  # six items + "outras categorias"
        assert "€ 86,90" in await _text(page, "[data-card=avg_ticket] .hero")
        rec = await _text(page, "[data-card=recurring]")
        assert "parcela 1 de 13" in rec and "€ 218,00 por mês · 2 itens" in rec
        assert "Marta" in await _text(page, "[data-card=owed]")
    finally:
        await _close(pwm, browser)


async def test_every_chart_has_a_view_as_table() -> None:
    pwm, browser, page = await _open(_handlers())
    try:
        await page.wait_for_selector("[data-card=owed]")
        for card in ("balance", "upcoming", "categories", "budgets"):
            assert (
                await page.locator(f"[data-card={card}] details.table summary").inner_text()
                == "Ver como tabela"
            ), card
        await page.click('[data-tab="money"]')
        await page.wait_for_selector("[data-card=recurring]")
        for card in ("top_expenses", "month_vs_month", "fixed_variable", "daily", "categories"):
            assert await page.locator(f"[data-card={card}] details.table").count() == 1, card
        # the table is real, with column headers, and opens from the keyboard
        await page.focus("[data-card=daily] details.table summary")
        await page.keyboard.press("Enter")
        assert (
            await page.locator("[data-card=daily] details.table[open] th[scope=col]").count() == 2
        )
        assert await page.locator("[data-card=daily] details.table tbody tr").count() == 31
    finally:
        await _close(pwm, browser)


XSS = '<img src=x onerror="window.__pwn=1">'


async def test_texts_from_the_member_are_never_html() -> None:
    summary = {**fx.SUMMARY, "cards": [dict(c) for c in fx.SUMMARY["cards"]]}
    for c in summary["cards"]:
        if c["id"] == "upcoming":
            c["items"] = [{**c["items"][0], "name": XSS}]
        if c["id"] == "owed":
            c["items"] = [{**c["items"][0], "person": XSS, "note": XSS}]
        if c["id"] == "categories":
            c["items"] = [{**c["items"][0], "label": XSS}]
        if c["id"] == "budgets":
            c["items"] = [{**c["items"][0], "label": XSS}]
        if c["id"] == "balance":
            c["phrase"] = fx.phrase(XSS, XSS)
    money = {**fx.MONEY, "cards": [dict(c) for c in fx.MONEY["cards"]]}
    for c in money["cards"]:
        if c["id"] == "transactions":
            c["days"] = [{**c["days"][0], "entries": [fx._entry(XSS, XSS, 1.0)]}]
        if c["id"] == "top_expenses":
            c["items"] = [{**c["items"][0], "merchant": XSS}]
        if c["id"] == "recurring":
            c["items"] = [{**c["items"][0], "name": XSS}]
    pwm, browser, page = await _open(_handlers(summary, money))
    try:
        await page.wait_for_selector("[data-card=owed]")
        await page.click('[data-tab="money"]')
        await page.wait_for_selector("[data-card=recurring]")
        assert await page.locator("#panel img").count() == 0
        assert await page.evaluate("window.__pwn") is None
        assert XSS in await page.inner_text("[data-card=transactions]")  # shown as plain text
        assert await page.evaluate("window.__csp.length") == 0
    finally:
        await _close(pwm, browser)


async def test_empty_cards_say_what_to_type_in_the_chat() -> None:
    summary = {**fx.SUMMARY, "cards": [fx.empty_card(c["id"]) for c in fx.SUMMARY["cards"]]}
    money = {**fx.MONEY, "cards": [fx.empty_card(c["id"]) for c in fx.MONEY["cards"]]}
    pwm, browser, page = await _open(_handlers(summary, money))
    try:
        await page.wait_for_selector("[data-card=owed]")
        assert await page.locator("#panel [data-card]").count() == 6
        for text in await page.locator("#panel [data-card]").all_inner_texts():
            assert "Registre pelo chat" in text and "Peça no chat: “gastei 25 no mercado”" in text
        assert await page.locator("#panel .hero").count() == 0  # no zeros dressed up as data
        await page.click('[data-tab="money"]')
        await page.wait_for_selector("[data-card=recurring]")
        assert await page.locator("#panel [data-card]").count() == 9
    finally:
        await _close(pwm, browser)


async def test_filters_only_change_the_address_and_use_closed_lists() -> None:
    pwm, browser, page = await _open(_handlers())
    try:
        await page.wait_for_selector("[data-card=owed]")
        await page.select_option('select[aria-label="Categoria"]', "restaurant")
        await _poll(page, "location.search.includes('categories=restaurant')")
        await page.select_option('select[aria-label="Tipo"]', "expense")
        await page.select_option('select[aria-label="Estado"]', "to_pay")
        await _poll(page, "location.search.includes('state=to_pay')")
        assert (
            await page.evaluate("location.search")
            == "?categories=restaurant&kind=expense&state=to_pay"
        )
        await _last_call_ends_with(page, "/summary?categories=restaurant&kind=expense&state=to_pay")
        await page.click('button[aria-label="Mês anterior"]')
        await _poll(page, "location.search.includes('month=2026-09')")
        await _last_call_ends_with(
            page, "/summary?month=2026-09&categories=restaurant&kind=expense&state=to_pay"
        )
        await page.click("button.clear")
        await _poll(page, "location.search === ''")
        assert await page.locator("[data-card=balance]").count() == 1  # redrawn from the cache
        assert all(method == "GET" for method, _ in page.api_calls)  # read only
    finally:
        await _close(pwm, browser)


async def test_a_tampered_address_is_dropped_before_it_reaches_the_server() -> None:
    q = "?month=<script>&categories=x%27%20OR%201=1,restaurant&kind=bogus&state=paid,zzz&merchant=Albert&page=9"
    pwm, browser, page = await _open(_handlers(), query=q)
    try:
        await page.wait_for_selector("[data-card=owed]")
        url = page.api_calls[0][1]
        assert url.endswith("/summary?categories=restaurant&state=paid")  # only closed-list values
        assert "merchant" not in url and "script" not in url and "page" not in url
    finally:
        await _close(pwm, browser)


async def test_month_arrows_stop_at_the_current_month() -> None:
    pwm, browser, page = await _open(_handlers())
    try:
        await page.wait_for_selector("[data-card=owed]")
        assert await page.locator('button[aria-label="Próximo mês"]').is_disabled()
        await page.click('button[aria-label="Mês anterior"]')
        await page.wait_for_selector(".pill.month .cur:has-text('Setembro de 2026')")
        assert not await page.locator('button[aria-label="Próximo mês"]').is_disabled()
    finally:
        await _close(pwm, browser)


async def test_merchant_search_filters_in_the_browser_and_never_leaves_it() -> None:
    pwm, browser, page = await _open(_handlers())
    try:
        await page.click('[data-tab="money"]')
        await page.wait_for_selector("[data-card=transactions] input[type=search]")
        calls = len(page.api_calls)
        await page.fill("[data-card=transactions] input[type=search]", "alb")
        assert await page.locator("[data-card=transactions] .entry:visible").count() == 1
        assert (
            await page.locator("[data-card=transactions] .daygrp:visible").count() == 1
        )  # empty days vanish
        await page.fill("[data-card=transactions] input[type=search]", "zzz")
        assert await page.locator("[data-card=transactions] .empty:visible").count() == 1
        await page.fill("[data-card=transactions] input[type=search]", "")
        assert await page.locator("[data-card=transactions] .entry:visible").count() == 6
        assert await page.evaluate("location.search") == ""  # nothing in the address
        assert len(page.api_calls) == calls  # and nothing sent
        assert await page.locator("form").count() == 0
    finally:
        await _close(pwm, browser)


async def test_money_pagination_changes_only_the_page_number() -> None:
    card = {**fx.MONEY["cards"][0], "page": {"number": 1, "size": 50, "pages": 3, "total": 120}}
    money = {**fx.MONEY, "cards": [card]}
    pwm, browser, page = await _open(_handlers(money=money))
    try:
        await page.click('[data-tab="money"]')
        await page.wait_for_selector("[data-card=transactions] .pager")
        assert await page.locator(".pager button").first.is_disabled()
        assert "Página 1 de 3" in await _text(page, ".pager")
        await page.click(".pager button:has-text('Próxima')")
        await _poll(page, "location.search === '?page=2'")
        await _last_call_ends_with(page, "/money?page=2")
    finally:
        await _close(pwm, browser)


async def test_theme_switch_toggles_and_remembers_the_choice() -> None:
    pwm, browser, page = await _open(_handlers())
    try:
        await page.wait_for_selector("[data-card=owed]")
        before = await page.get_attribute("#theme-toggle", "aria-label")
        await page.click("#theme-toggle")
        after = await page.get_attribute("#theme-toggle", "aria-label")
        assert before != after and {"Mudar para o tema claro", "Mudar para o tema escuro"} == {
            before,
            after,
        }
        theme = await page.evaluate("document.documentElement.dataset.theme")
        bg = await page.evaluate("getComputedStyle(document.body).backgroundColor")
        assert bg == {"light": "rgb(243, 245, 248)", "dark": "rgb(13, 17, 23)"}[theme]
        await page.reload()
        await page.wait_for_selector("[data-card=owed]")
        assert await page.evaluate("document.documentElement.dataset.theme") == theme  # remembered
        assert await page.evaluate("getComputedStyle(document.body).backgroundColor") == bg
    finally:
        await _close(pwm, browser)


async def test_tab_bar_on_a_phone_scrolls_with_a_fade_and_keeps_the_selected_tab_visible() -> None:
    pwm, browser, page = await _open(_handlers(), size={"width": 390, "height": 844})
    try:
        await page.wait_for_selector("[data-card=owed]")
        assert await page.eval_on_selector(".tabs-wrap", "e => e.classList.contains('fade-r')")
        assert not await page.eval_on_selector(".tabs-wrap", "e => e.classList.contains('fade-l')")
        await page.click('[data-tab="trips"]')
        await _poll(page, "document.querySelector('.tabs-wrap').classList.contains('fade-l')")
        box = await page.locator('[data-tab="trips"]').bounding_box()
        assert (
            box["x"] >= 0 and box["x"] + box["width"] <= 390 + 1
        )  # selected tab scrolled into view
        assert await page.evaluate(
            "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
        )
    finally:
        await _close(pwm, browser)


async def test_tabs_work_from_the_keyboard_and_focus_is_visible() -> None:
    pwm, browser, page = await _open(_handlers())
    try:
        await page.wait_for_selector("[data-card=owed]")
        await page.focus('[data-tab="summary"]')
        await page.keyboard.press("ArrowRight")
        assert await page.get_attribute('[data-tab="money"]', "aria-selected") == "true"
        await page.keyboard.press("End")
        assert await page.get_attribute('[data-tab="trips"]', "aria-selected") == "true"
        await page.keyboard.press("Home")
        await page.keyboard.press("Tab")  # leaves the tab list for the filters
        outline = await page.evaluate("getComputedStyle(document.activeElement).outlineStyle")
        assert outline != "none" or await page.evaluate("!!document.activeElement.closest('.pill')")
    finally:
        await _close(pwm, browser)


@pytest.mark.parametrize("size", [{"width": 1280, "height": 900}, {"width": 390, "height": 844}])
async def test_no_horizontal_overflow_and_no_csp_violation_with_real_looking_data(size) -> None:
    pwm, browser, page = await _open(_handlers(), size=size)
    try:
        await page.wait_for_selector("[data-card=owed]")
        await page.click('[data-tab="money"]')
        await page.wait_for_selector("[data-card=recurring]")
        for tab in ("summary", "money"):
            await page.click(f'[data-tab="{tab}"]')
            await page.wait_for_selector("#panel [data-card]")
            assert (
                await page.evaluate(
                    "document.documentElement.scrollWidth - document.documentElement.clientWidth"
                )
                <= 0
            )
        assert await page.evaluate("window.__csp.length") == 0
    finally:
        await _close(pwm, browser)


async def test_the_panel_is_read_only() -> None:
    pwm, browser, page = await _open(_handlers())
    try:
        await page.wait_for_selector("[data-card=owed]")
        await page.click('[data-tab="money"]')
        await page.wait_for_selector("[data-card=recurring]")
        assert await page.locator("form, input[type=submit], button[type=submit]").count() == 0
        assert all(method == "GET" for method, _ in page.api_calls)
    finally:
        await _close(pwm, browser)


async def test_days_without_settled_entries_show_no_zero_total_and_few_days_keep_bars_slim() -> None:
    money = {**fx.MONEY, "cards": [dict(c) for c in fx.MONEY["cards"]]}
    for c in money["cards"]:
        if c["id"] == "transactions":
            c["days"] = [{"date": "2026-10-18", "income": 0.0, "expense": 0.0, "net": 0.0, "entries_total": 1, "entries": [fx._entry("Energia", "Habitação", 118.0, status="to_pay")]}]
        if c["id"] == "daily":
            c["points"] = c["points"][:3]
    pwm, browser, page = await _open(_handlers(money=money))
    try:
        await page.click('[data-tab="money"]')
        await page.wait_for_selector("[data-card=daily]")
        assert await page.locator("[data-card=transactions] .day .num").count() == 0
        box = await page.locator("[data-card=daily] .daily").bounding_box()
        assert box["width"] <= 3 * 56 + 1
    finally:
        await _close(pwm, browser)
