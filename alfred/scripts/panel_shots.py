#!/usr/bin/env python3
"""Screenshots of the v2 panel (dark/light x desktop 1280 / mobile 390) plus browser checks.

For each view it opens the demo member's panel with the Chromium that Playwright already has,
and fails when the real browser reports a Content-Security-Policy violation, a page error, a
failed font, or horizontal overflow. A second set of images ("-showcase") adds the drawing
primitives (stacked bar with legend and the "end of month" total, bars, sparkline) to the
Summary tab, so the layouts the later PRs will reuse are checked now. ``--check-v1`` also loads
the v1 page (Chart.js) once and reports its CSP violations.

Usage:
    python scripts/panel_shots.py [--base URL] [--out DIR] [--check-v1] [--chartjs chart.umd.js]

The server must be running with the demo member seeded (``python scripts/seed_demo_member.py``).
Exits 1 on any finding.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from playwright.async_api import async_playwright  # noqa: E402

VIEWS = [
    ("dark", "desktop", {"width": 1280, "height": 900}),
    ("dark", "mobile", {"width": 390, "height": 844}),
    ("light", "desktop", {"width": 1280, "height": 900}),
    ("light", "mobile", {"width": 390, "height": 844}),
]

# Records CSP violations the browser itself raises (more reliable than console text).
INIT = """
window.__csp = [];
document.addEventListener('securitypolicyviolation', e => window.__csp.push(
  e.violatedDirective + ' blocked ' + (e.blockedURI || 'inline')));
"""

SHOWCASE = """
() => {
  const P = window.AlfredPanel;
  const grid = document.querySelector('#panel .grid');
  const c = P.cardShell({id: 'projection'}, 'Saldo do período e fim do mês projetado', 's5');
  P.stackedBar(c, [
    {label: 'realizado até hoje', value: 1842.3, text: P.money(1842.3)},
    {label: 'contas marcadas', value: 298, text: P.money(-298), tone: 'warm'},
    {label: 'gasto variável esperado', value: 424, text: P.money(-424), tone: 'hatch'},
  ], {total: {label: 'fim do mês (estimativa) · faixa € 1.030 a € 1.210', text: '~ € 1.120'}});
  P.sparkline(c, [3, 5, 4, 8, 6, 9, 7], 'ritmo de gasto');
  P.barRow(c, {label: 'Restaurante', value: P.money(123), pct: 100, tone: 'warm'});
  grid.append(c);
}
"""


async def _view(browser, base, token, theme, kind, size, out, showcase, problems):
    ctx = await browser.new_context(
        viewport=size, color_scheme=theme, device_scale_factor=1, is_mobile=(kind == "mobile")
    )
    page = await ctx.new_page()
    await page.add_init_script(INIT)
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    page.on(
        "console",
        lambda m: (
            errors.append(f"console.{m.type}: {m.text}") if m.type in ("error", "warning") else None
        ),
    )
    page.on("requestfailed", lambda r: errors.append(f"requestfailed: {r.url.split('?')[0]}"))
    await page.goto(f"{base}/d/{token}", wait_until="networkidle")
    await page.wait_for_selector("#panel .card")
    if showcase:
        await page.evaluate(SHOWCASE)
    await page.evaluate("document.fonts.ready")
    tag = f"{theme}-{kind}" + ("-showcase" if showcase else "")
    await page.screenshot(path=str(out / f"{tag}.png"), full_page=True)

    if kind == "mobile" and not showcase:  # the sticky button must not hide the last content
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await page.screenshot(path=str(out / f"{tag}-bottom.png"))
    if not showcase and theme == "dark" and kind == "desktop":
        await _keyboard(page, problems)
    csp = await page.evaluate("window.__csp")
    overflow = await page.evaluate(
        "document.documentElement.scrollWidth - document.documentElement.clientWidth"
    )
    fonts = await page.evaluate(
        "[...document.fonts].filter(f => f.status !== 'loaded')"
        ".map(f => f.family + ' ' + f.weight + ' ' + f.status)"
    )
    used = await page.evaluate("document.fonts.check('16px \"Hanken Grotesk\"')")
    tabs = await page.evaluate(
        "[...document.querySelectorAll('[role=tab]')].map(t => t.textContent)"
    )
    for what, bad in (
        ("csp", csp),
        ("errors", errors),
        ("overflow-px", overflow if overflow > 0 else None),
    ):
        if bad:
            problems.append(f"{tag}: {what}: {bad}")
    # declared-but-unused faces stay 'unloaded'; only 'error' (or an unusable family) is a problem
    if any("error" in f for f in fonts) or not used:
        problems.append(f"{tag}: fonts: {fonts} used={used}")
    print(f"{tag:22} tabs={tabs} csp={len(csp)} errors={len(errors)} overflow={overflow}px")
    await ctx.close()


async def _keyboard(page, problems) -> None:
    """Arrows/Home/End move between tabs, and each API is called only when its tab opens."""
    calls: list[str] = []
    page.on(
        "request",
        lambda r: (
            calls.append(r.url.split("/api/d/")[-1].split("/", 1)[-1].split("?")[0])
            if "/api/d/" in r.url
            else None
        ),
    )
    await page.focus("[role=tab][data-tab=summary]")
    seen = []
    for key in ("ArrowRight", "End", "ArrowRight", "Home", "ArrowLeft"):
        await page.keyboard.press(key)
        await page.wait_for_timeout(250)
        seen.append(
            await page.evaluate(
                "document.querySelector('[role=tab][aria-selected=true]').dataset.tab"
            )
        )
    expected = ["money", "trips", "summary", "summary", "trips"]
    if seen != expected:
        problems.append(f"keyboard: {seen} != {expected}")
    print(f"keyboard order={seen} api calls after load={calls}")
    # summary was cached; money and trips were fetched once each, nothing else was touched
    if sorted(calls) != ["money", "trips"]:
        problems.append(f"on-demand: unexpected API calls {calls}")


async def _serve_chartjs_locally(page, lib: pathlib.Path) -> None:
    """Sandbox without access to cdnjs: serve a local Chart.js for that one URL.

    The page's integrity hash belongs to the cdnjs file, so the HTML copy handed to the browser
    gets the hash of the local file; the CSP header and every other byte stay untouched.
    """
    import base64
    import hashlib

    body = await asyncio.to_thread(lib.read_bytes)
    sri = "sha384-" + base64.b64encode(hashlib.sha384(body).digest()).decode()

    async def cdn(route):
        await route.fulfill(
            status=200,
            body=body,
            headers={"content-type": "text/javascript", "access-control-allow-origin": "*"},
        )

    async def doc(route):
        resp = await route.fetch()
        html = (await resp.text()).replace(
            "sha384-bs/nf9FbdNouRbMiFcrcZfLXYPKiPaGVGplVbv7dLGECccEXDW+S3zjqSKR5ZEaD", sri
        )
        await route.fulfill(response=resp, body=html)

    await page.route("https://cdnjs.cloudflare.com/**", cdn)
    await page.route(re.compile(r".*/d/[0-9a-f-]{36}$"), doc)


async def _check_v1(browser, base, out, chartjs, problems) -> None:
    """Load the v1 page (flag off) once and report CSP violations; restores the flag."""
    from sqlalchemy import update

    from alfred.db import AsyncSessionLocal, engine
    from alfred.models import Member

    async def flag(value: bool) -> None:
        async with AsyncSessionLocal() as s:
            await s.execute(
                update(Member).where(Member.wa_phone == "31000000000").values(dashboard_v2=value)
            )
            await s.commit()
        await engine.dispose()

    import seed_demo_member as seed

    token = await seed.run()
    await flag(False)
    try:
        ctx = await browser.new_context(viewport={"width": 1280, "height": 900})
        page = await ctx.new_page()
        await page.add_init_script(INIT)
        net: list[str] = []
        errors: list[str] = []
        page.on("requestfailed", lambda r: net.append(r.url.split("?")[0]))
        page.on("pageerror", lambda e: errors.append(str(e)))
        if chartjs:
            await _serve_chartjs_locally(page, pathlib.Path(chartjs))
        await page.goto(f"{base}/d/{token}", wait_until="networkidle")
        await page.wait_for_timeout(1500)
        csp = await page.evaluate("window.__csp")
        chart = await page.evaluate("typeof window.Chart")
        drawn = await page.evaluate(
            "[...document.querySelectorAll('canvas')].filter(c => c.width > 0).length"
        )
        await page.screenshot(path=str(out / "v1-page.png"), full_page=True)
        print(
            f"v1 page: csp violations={csp} Chart={chart} canvases={drawn} "
            f"page errors={errors} failed requests={net}"
        )
        if chartjs and (chart != "function" or errors):
            problems.append(f"v1: Chart.js did not run ({chart}, {errors})")
        if csp:
            problems.append(f"v1: csp: {csp}")
        await ctx.close()
    finally:
        await flag(True)


async def main_async(args) -> int:
    import seed_demo_member as seed

    token = args.token or await seed.run()
    out = pathlib.Path(args.out)
    await asyncio.to_thread(out.mkdir, parents=True, exist_ok=True)
    problems: list[str] = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        for theme, kind, size in VIEWS:
            for showcase in (False, True):
                await _view(browser, args.base, token, theme, kind, size, out, showcase, problems)
        if args.check_v1:
            await _check_v1(browser, args.base, out, args.chartjs, problems)
        await browser.close()
    print(json.dumps({"problems": problems}, indent=2, ensure_ascii=False))
    return 1 if problems else 0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--out", default="/tmp/panel-shots")
    ap.add_argument("--token", default="")
    ap.add_argument("--check-v1", action="store_true")
    ap.add_argument(
        "--chartjs", default="", help="local chart.umd.js to serve in place of cdnjs (offline runs)"
    )
    sys.exit(asyncio.run(main_async(ap.parse_args())))


if __name__ == "__main__":
    main()
