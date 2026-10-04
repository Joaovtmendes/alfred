# ruff: noqa: E501
"""panel.js in a real browser (Chromium via Playwright), network faked with route handlers."""

from __future__ import annotations

import asyncio
import copy
import json
import pathlib
import re
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
                headers={"Content-Security-Policy": _dashboard_csp(nonce)},
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


async def _all_texts(locator) -> list[str]:
    return [t.replace("\u00a0", " ") for t in await locator.all_inner_texts()]


async def _text(page, selector: str) -> str:
    # The page keeps "€ 5,00" unbreakable (no-break spaces); the tests read it with plain spaces.
    return (await page.inner_text(selector)).replace("\u00a0", " ")


async def test_balance_card_shows_hero_estimate_tiles_bar_and_the_alfred_phrase() -> None:
    pwm, browser, page = await _open(_handlers())
    try:
        await page.wait_for_selector("[data-card=balance]")
        card = "[data-card=balance]"
        assert "€ 1.842,30" in await _text(page, f"{card} .hero")
        assert "~ € 1.120" in await _text(page, f"{card} .proj-side")
        assert "faixa € 1.030 a € 1.210" in await _text(page, f"{card} .proj-side")
        tiles = await _all_texts(page.locator(f"{card} .tile"))
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


async def test_upcoming_categories_goals_and_week_in_resumo() -> None:
    pwm, browser, page = await _open(_handlers())
    try:
        await page.wait_for_selector("[data-card=upcoming]")
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
        texts = await _all_texts(rows)
        assert "▲ 8%" in texts[0] and "▼ 11%" in texts[2] and "= igual" in texts[4]
        assert await page.locator("[data-card=goals] .item, [data-card=goals] .brow").count() >= 1
        assert await page.locator("[data-card=week]").count() == 1
        for gone in ("blue_days", "budgets", "owed"):
            assert await page.locator(f"[data-card={gone}]").count() == 0, gone
    finally:
        await _close(pwm, browser)


async def test_card_order_is_the_same_on_desktop_and_phone() -> None:
    """One order for every screen: the one the API sends (no CSS reordering on the phone)."""

    async def order(page):
        return await page.eval_on_selector_all(
            "#panel [data-card]",
            "els => els.sort((a, b) => a.getBoundingClientRect().top - b.getBoundingClientRect().top || a.getBoundingClientRect().left - b.getBoundingClientRect().left).map(e => e.dataset.card)",
        )

    want = ["balance", "categories", "goals", "week", "upcoming"]
    pwm, browser, page = await _open(_handlers())
    try:
        await page.wait_for_selector("[data-card=upcoming]")
        assert await order(page) == want
        grid = await page.eval_on_selector_all(
            "#panel [data-card]", "els => els.map(e => Math.round(e.getBoundingClientRect().width))"
        )
        assert grid[0] > grid[1] > 0 and abs(grid[0] / grid[1] - 7 / 5) < 0.1  # 7 + 5 of 12 columns
        await page.set_viewport_size({"width": 390, "height": 844})
        assert await order(page) == want
        lefts = await page.eval_on_selector_all(
            "#panel [data-card]", "els => els.map(e => Math.round(e.getBoundingClientRect().left))"
        )
        assert len(set(lefts)) == 1  # one column
        dom = await page.eval_on_selector_all(
            "#panel [data-card]", "els => els.map(e => e.dataset.card)"
        )
        assert dom == want  # the DOM order is the visual order too
    finally:
        await _close(pwm, browser)


async def test_money_and_habits_card_order_is_the_approved_one() -> None:
    pwm, browser, page = await _open(_handlers(), query="")
    try:
        await page.click('[data-tab="money"]')
        await page.wait_for_selector("[data-card=recurring]")
        for width in (1280, 390):
            await page.set_viewport_size({"width": width, "height": 900})
            dom = await page.eval_on_selector_all(
                "#panel [data-card]", "els => els.map(e => e.dataset.card)"
            )
            assert dom == [
                "transactions", "month_vs_month", "top_expenses", "categories", "budgets",
                "fixed_variable", "owed", "daily", "recurring",
            ]  # fmt: skip
    finally:
        await _close(pwm, browser)


async def test_money_cards() -> None:
    pwm, browser, page = await _open(_handlers(), query="")
    try:
        await page.click('[data-tab="money"]')
        await page.wait_for_selector("[data-card=recurring]")
        tx = "[data-card=transactions]"
        await page.click(f"{tx} .daychips button:has-text('Todos')")
        days = await _all_texts(page.locator(f"{tx} .day"))
        assert "Hoje, 2 out" in days[0] and "+ € 3.797,82" in days[0]  # the day's total
        assert "1 out" in days[1] and "30 set" in days[2]
        rows = await _all_texts(page.locator(f"{tx} .entry"))
        assert (
            "Albert Heijn" in rows[0]
            and "supermercado · pago" in rows[0].lower()
            and "− € 42,18" in rows[0]
        )
        assert "+ € 3.840,00" in rows[1] and "recebido" in rows[1]
        assert "a pagar" in rows[-1]
        assert "mostre meus gastos de outubro" in await _text(page, tx)
        assert "exportar meus dados" in await _text(page, tx)  # the copy comes through the chat
        assert await page.locator(f"{tx} .pager button").count() == 0  # single page: no pager
        assert await page.locator("[data-card=top_expenses] .brow").count() == 5
        mom = await _text(page, "[data-card=month_vs_month]")
        assert "€ 1.997" in mom and "▲ 9% contra setembro" in mom and "igual a setembro" in mom
        assert "62" in await _text(page, "[data-card=fixed_variable] .hero")
        legend = await _all_texts(page.locator("[data-card=fixed_variable] .legend li"))
        assert len(legend) == 2 and "1.238" in legend[0] and "760" in legend[1]
        assert await page.locator("[data-card=daily] .daily > i").count() == 31
        assert await page.locator("[data-card=daily] .daily > i.peak").count() == 1
        cats = page.locator("[data-card=categories] .cat")
        assert await cats.count() == 7  # six items + "outras categorias"
        assert await page.locator("[data-card=avg_ticket]").count() == 0
        budgets = page.locator("[data-card=budgets] .brow")
        assert await budgets.count() == 3
        assert "€ 148 de € 120 · 123%" in (await budgets.nth(0).inner_text()).replace("\u00a0", " ")
        assert "74" in await _text(page, "[data-card=budgets] .hero")
        assert (
            await page.locator("[data-card=budgets] .track > u").count() == 6
        )  # 80% and 100% marks
        assert await page.locator("[data-card=budgets] .track > i.warm").count() == 1
        assert "€ 34,50" in await _text(page, "[data-card=owed] .hero")
        assert "há 9 dias" in await _text(page, "[data-card=owed]")
        rec = await _text(page, "[data-card=recurring]")
        assert "parcela 1 de 13" in rec and "€ 218,00 por mês · 2 itens" in rec
        assert "Marta" in await _text(page, "[data-card=owed]")
    finally:
        await _close(pwm, browser)


async def test_no_card_offers_a_view_as_table() -> None:
    """The "view as table" twin was removed from every card of every tab."""
    pwm, browser, page = await _open(_handlers(), query="")
    try:
        await page.wait_for_selector("[data-card=upcoming]")
        assert await page.locator("details.table").count() == 0
        assert "Ver como tabela" not in await _text(page, "#panel")
        await page.click('[data-tab="money"]')
        await page.wait_for_selector("[data-card=recurring]")
        assert await page.locator("details.table").count() == 0
        assert "Ver como tabela" not in await _text(page, "#panel")
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
        await page.wait_for_selector("[data-card=upcoming]")
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
        await page.wait_for_selector("[data-card=upcoming]")
        assert await page.locator("#panel [data-card]").count() == 5
        for text in await _all_texts(page.locator("#panel [data-card]")):
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
        await page.wait_for_selector("[data-card=upcoming]")
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
        await page.wait_for_selector("[data-card=upcoming]")
        url = page.api_calls[0][1]
        assert url.endswith("/summary?categories=restaurant&state=paid")  # only closed-list values
        assert "merchant" not in url and "script" not in url and "page" not in url
    finally:
        await _close(pwm, browser)


@pytest.mark.parametrize("month", ["2099-01", "1999-12", "0001-01"])
async def test_a_month_the_server_would_ignore_is_not_shown_as_selected(month) -> None:
    """The server only answers 2000 .. next year; anything else would show the current month's
    numbers under another month's title, so the address is dropped before it is read."""
    pwm, browser, page = await _open(_handlers(), query=f"?month={month}")
    try:
        await page.wait_for_selector("[data-card=upcoming]")
        assert page.api_calls[0][1].endswith("/summary")
        await page.wait_for_selector(".pill.month .cur:has-text('Outubro de 2026')")
    finally:
        await _close(pwm, browser)


_PLURAL_WRONG = {
    "pt": r"\b1 (dias|lançamentos|gastos|itens)\b",
    "nl": r"\b1 (dagen|boekingen|uitgaven)\b",
    "en": r"\b1 (days|entries|expenses|items)\b",
    "fr": r"\b1 (jours|écritures|dépenses|éléments)\b",
    "de": r"\b1 (Tagen|Tage|Buchungen|Ausgaben)\b",
}
_ONE = {
    "pt": ["há 1 dia", "Saldo de 1 lançamento", "1 item"],
    "nl": ["1 dag geleden", "Saldo van 1 boeking", "1 post"],
    "en": ["1 day ago", "Balance of 1 entry", "1 item"],
    "fr": ["il y a 1 jour", "Solde de 1 écriture", "1 élément"],
    "de": ["vor 1 Tag", "Saldo von 1 Buchung", "1 Posten"],
}  # fmt: skip


@pytest.mark.parametrize("lang", ["pt", "nl", "en", "fr", "de"])
async def test_a_count_of_one_reads_in_the_singular_in_every_language(lang) -> None:
    summary, money = copy.deepcopy(fx.SUMMARY), copy.deepcopy(fx.MONEY)
    for body in (summary, money):
        for c in body["cards"]:
            if c["id"] == "owed":
                c["items"][0]["days"] = 1
            if c["id"] in ("transactions", "recurring"):
                c["values"]["count"] = 1
                if c["id"] == "recurring":
                    c["items"] = c["items"][:1]
    pwm, browser, page = await _open(_handlers(summary, money), lang=lang)
    try:
        await page.wait_for_selector("[data-card=upcoming]")
        text = await _text(page, "#panel")
        await page.click('[data-tab="money"]')
        await page.wait_for_selector("[data-card=recurring]")
        text += " " + await _text(page, "#panel")
        assert not re.search(_PLURAL_WRONG[lang], text), re.findall(_PLURAL_WRONG[lang], text)
        for fragment in _ONE[lang]:
            assert fragment in text, (lang, fragment)
    finally:
        await _close(pwm, browser)


async def test_month_arrows_stop_at_the_current_month() -> None:
    pwm, browser, page = await _open(_handlers())
    try:
        await page.wait_for_selector("[data-card=upcoming]")
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
        assert await page.locator("[data-card=transactions] .entry:visible").count() == 2  # today
        await page.click("[data-card=transactions] .daychips button:has-text('Todos')")
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
        await page.wait_for_selector("[data-card=upcoming]")
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
        await page.wait_for_selector("[data-card=upcoming]")
        assert await page.evaluate("document.documentElement.dataset.theme") == theme  # remembered
        assert await page.evaluate("getComputedStyle(document.body).backgroundColor") == bg
    finally:
        await _close(pwm, browser)


async def test_tab_bar_on_a_phone_scrolls_with_a_fade_and_keeps_the_selected_tab_visible() -> None:
    pwm, browser, page = await _open(_handlers(), size={"width": 390, "height": 844})
    try:
        await page.wait_for_selector("[data-card=upcoming]")
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
        await page.wait_for_selector("[data-card=upcoming]")
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
        await page.wait_for_selector("[data-card=upcoming]")
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
        await page.wait_for_selector("[data-card=upcoming]")
        await page.click('[data-tab="money"]')
        await page.wait_for_selector("[data-card=recurring]")
        assert await page.locator("form, input[type=submit], button[type=submit]").count() == 0
        assert all(method == "GET" for method, _ in page.api_calls)
    finally:
        await _close(pwm, browser)


async def test_days_without_settled_entries_show_no_zero_total_and_a_running_month_keeps_its_full_width() -> (
    None
):
    money = {**fx.MONEY, "cards": [dict(c) for c in fx.MONEY["cards"]]}
    for c in money["cards"]:
        if c["id"] == "transactions":
            c["days"] = [
                {
                    "date": "2026-10-18",
                    "income": 0.0,
                    "expense": 0.0,
                    "net": 0.0,
                    "entries_total": 1,
                    "entries": [fx._entry("Energia", "Habitação", 118.0, status="to_pay")],
                }
            ]
        if c["id"] == "daily":
            c["points"] = c["points"][:3]
    pwm, browser, page = await _open(_handlers(money=money))
    try:
        await page.click('[data-tab="money"]')
        await page.wait_for_selector("[data-card=daily]")
        assert await page.locator("[data-card=transactions] .day .num").count() == 0
        assert await page.locator("[data-card=daily] .daily > i").count() == 31
        box = await page.locator("[data-card=daily] .daily > i").first.bounding_box()
        assert box["width"] < 40
    finally:
        await _close(pwm, browser)


async def test_the_euro_sign_never_separates_from_its_number_or_its_sign() -> None:
    pwm, browser, page = await _open(_handlers())
    try:
        await page.wait_for_selector("[data-card=balance] .hero")
        raw = await page.inner_text("[data-card=balance] .hero")
        assert "€ " in raw and "€ " not in raw
        for t in await page.locator("#panel [data-card]").all_inner_texts():
            assert "− € " not in t and "+ € " not in t
    finally:
        await _close(pwm, browser)


# ── Agenda, Hábitos and Viagens (payloads captured from the real API on the demo member) ──────

_TABS_FX = json.loads((pathlib.Path(__file__).parent / "panel_tabs_fixture.json").read_text())


def _tab_handlers():
    async def mk(tab):
        return 200, _TABS_FX[tab]

    async def a():
        return await mk("agenda")

    async def h():
        return await mk("health")

    async def t():
        return await mk("trips")

    return {**_handlers(), "agenda": a, "health": h, "trips": t}


@pytest.mark.parametrize(
    ("tab", "cards"),
    [
        ("agenda", ["week", "tasks", "month_map", "reminders", "notes"]),
        ("health", ["goals", "workouts", "training", "water"]),
        ("trips", ["trip", "packing", "itinerary", "plan_budget", "trips_past"]),
    ],
)
async def test_the_three_new_tabs_draw_every_card_without_breaking_the_page(tab, cards) -> None:
    pwm, browser, page = await _open(_tab_handlers())
    try:
        await page.click(f'[data-tab="{tab}"]')
        await page.wait_for_selector("#panel [data-card]")
        ids = await page.eval_on_selector_all(
            "#panel [data-card]", "els => els.map(e => e.dataset.card)"
        )
        assert ids == cards
        for c in cards:
            assert await page.locator(f"[data-card={c}]").count() == 1
        assert await page.evaluate(
            "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
        )
    finally:
        await _close(pwm, browser)


async def test_agenda_names_today_and_tomorrow_and_flags_overdue_tasks() -> None:
    pwm, browser, page = await _open(_tab_handlers())
    try:
        await page.click('[data-tab="agenda"]')
        await page.wait_for_selector("[data-card=week]")
        heads = await page.locator("[data-card=week] .day-head").all_inner_texts()
        assert heads[0].lower().startswith("hoje") and heads[1].lower().startswith("amanhã")
        assert await page.locator("[data-card=week] .appt").count() >= 4
        assert await page.locator("[data-card=tasks] .stat.warm").count() == 1
        assert await page.locator("[data-card=tasks] .amt.late").count() == 1
        assert await page.locator("[data-card=month_map] .cell").count() == 28
        assert await page.locator("[data-card=month_map] .cell.today").count() == 1
    finally:
        await _close(pwm, browser)


async def test_trips_show_budget_tiles_and_the_health_tab_says_it_is_not_medical_advice() -> None:
    pwm, browser, page = await _open(_tab_handlers())
    try:
        await page.click('[data-tab="trips"]')
        await page.wait_for_selector("[data-card=trip]")
        assert await page.locator("[data-card=trip] .tile").count() == 3
        assert "Lisboa" in await _text(page, "[data-card=trip] .hero")
        assert await page.locator("[data-card=trips_past] .item").count() == 3
        await page.click('[data-tab="health"]')
        await page.wait_for_selector("[data-card=water]")
        assert "conselho médico" in await _text(page, "#panel .grid-note")
    finally:
        await _close(pwm, browser)


async def test_training_plan_shows_today_first_and_no_arrow_before_two_points() -> None:
    pwm, browser, page = await _open(_tab_handlers())
    try:
        await page.click('[data-tab="health"]')
        await page.wait_for_selector("[data-card=training]")
        card = "[data-card=training]"
        assert (
            await page.locator(f"{card} details.plan-day").count() == 3
        )  # the other days fold away
        await page.evaluate(
            "document.querySelectorAll('details.plan-day').forEach(d => d.open = true)"
        )
        text = await _text(page, card)
        assert "↑ +" in text  # a load that went up shows its change
        assert (
            await page.locator(
                f"{card} .item:has-text('Puxada frontal') .s:has-text('1 registro')"
            ).count()
            == 1
        )
        assert "↑" not in await _text(page, f"{card} .item:has-text('Puxada frontal')")
        assert await page.locator(f"{card} details.table").count() == 0
    finally:
        await _close(pwm, browser)


async def test_trip_plan_cards_show_itinerary_packing_and_planned_against_spent() -> None:
    pwm, browser, page = await _open(_tab_handlers())
    try:
        await page.click('[data-tab="trips"]')
        await page.wait_for_selector("[data-card=packing]")
        assert "3 de 7" in await _text(page, "[data-card=packing] .sub")
        assert await page.locator("[data-card=packing] .item").count() == 7
        assert await page.locator("[data-card=itinerary] .appt").count() == 8
        assert await page.locator("[data-card=itinerary] .day-head").count() == 5
        assert await page.locator("[data-card=plan_budget] .cat").count() == 4
        assert "€ 180 / € 450" in (await _text(page, "[data-card=plan_budget]")).replace(
            "\u00a0", " "
        )
    finally:
        await _close(pwm, browser)


def test_the_assets_carry_no_css_reordering_and_no_table_twin() -> None:
    """Contract: the card order is the API order. No `order:` rule per card, no table helper."""
    root = pathlib.Path(__file__).parent.parent / "src" / "alfred" / "panel"
    css, js = (root / "panel.css").read_text(), (root / "panel.js").read_text()
    assert not re.search(r"\[data-card=[^\]]*\]\s*\{[^}]*\border\s*:", css)
    assert "tableView" not in js and "table_view" not in js and "details.table" not in css


async def test_transactions_show_one_day_at_a_time_with_the_day_filter_on_top() -> None:
    pwm, browser, page = await _open(_handlers(), query="")
    try:
        await page.click('[data-tab="money"]')
        await page.wait_for_selector("[data-card=recurring]")
        tx = "[data-card=transactions]"
        chips = await _all_texts(page.locator(f"{tx} .daychips button"))
        assert chips == ["Todos", "Hoje", "1 out", "30 set"]
        # the filter sits above the search box and the list
        order = await page.eval_on_selector(
            tx,
            "c => [...c.children].map(e => e.className).filter(x => /daychips|search|list-days/.test(x))",
        )
        assert order[0].startswith("daychips") and order.index("list-days") > 0
        assert (
            await page.locator(f"{tx} .daychips button[aria-pressed=true]").inner_text() == "Hoje"
        )
        # daily view: only today's entries are on screen
        assert await page.locator(f"{tx} .daygrp:visible").count() == 1
        assert "Hoje, 2 out" in await _text(page, f"{tx} .daygrp:visible .day")
        await page.click(f"{tx} .daychips button:has-text('1 out')")
        assert await page.locator(f"{tx} .daygrp:visible").count() == 1
        assert "1 out" in await _text(page, f"{tx} .daygrp:visible .day")
        assert (
            await page.locator(f"{tx} .daychips button[aria-pressed=true]").inner_text() == "1 out"
        )
        await page.click(f"{tx} .daychips button:has-text('Todos')")
        assert await page.locator(f"{tx} .daygrp:visible").count() == 3
        # a search looks through every day, whatever day is selected
        await page.click(f"{tx} .daychips button:has-text('1 out')")
        await page.fill(f"{tx} input[type=search]", "albert")
        assert await page.locator(f"{tx} .entry:visible").count() >= 1
    finally:
        await _close(pwm, browser)


async def test_transactions_amounts_are_red_when_negative_and_green_when_positive() -> None:
    pwm, browser, page = await _open(_handlers(), query="")
    try:
        await page.click('[data-tab="money"]')
        await page.wait_for_selector("[data-card=recurring]")
        tx = "[data-card=transactions]"
        await page.click(f"{tx} .daychips button:has-text('Todos')")

        async def color(selector):
            return await page.eval_on_selector(selector, "e => getComputedStyle(e).color")

        async def token(name):
            return await page.evaluate(
                "n => { const p = document.createElement('i'); p.style.color = `var(${n})`; document.body.append(p); const c = getComputedStyle(p).color; p.remove(); return c; }",
                name,
            )

        red, green = await token("--warm-text"), await token("--good")
        assert red != green
        expense = f"{tx} .entry:has(.amt.neg) .amt"
        income = f"{tx} .entry:has(.amt.pos) .amt"
        assert await color(expense) == red and await color(income) == green
        assert await page.locator(f"{tx} .entry .amt.neg").count() >= 1
        # no expense is green and no income is red
        assert await page.locator(f"{tx} .entry .amt.neg:has-text('+')").count() == 0
        assert await page.locator(f"{tx} .entry .amt.pos:has-text('−')").count() == 0
        # the day total follows the same rule
        assert await color(f"{tx} .day .num.pos") == green
    finally:
        await _close(pwm, browser)


async def test_the_day_filter_fits_a_phone_without_scrolling_the_page_sideways() -> None:
    pwm, browser, page = await _open(_handlers(), query="", size={"width": 375, "height": 812})
    try:
        await page.click('[data-tab="money"]')
        await page.wait_for_selector("[data-card=transactions] .daychips")
        assert await page.evaluate(
            "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
        )
        box = await page.locator("[data-card=transactions] .daychips button").first.bounding_box()
        assert box["height"] >= 40
    finally:
        await _close(pwm, browser)


async def test_a_trip_in_progress_with_no_end_date_says_it_is_still_going() -> None:
    """Defect 16: an open trip showed only its start date."""
    import copy

    fx = copy.deepcopy(_TABS_FX["trips"])
    card = next(c for c in fx["cards"] if c["id"] == "trip")
    card["trip"].update({"start": "2026-10-01", "end": None, "state": "active"})

    async def trips():
        return 200, fx

    pwm, browser, page = await _open({**_tab_handlers(), "trips": trips})
    try:
        await page.click('[data-tab="trips"]')
        await page.wait_for_selector("[data-card=trip]")
        sub = await _text(page, "[data-card=trip] .sub")
        assert "–" in sub and "hoje" in sub
    finally:
        await _close(pwm, browser)
