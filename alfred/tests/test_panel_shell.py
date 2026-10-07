"""Dashboard v2 shell: tabs, visual tokens, own assets, flag-based page choice."""

from __future__ import annotations

import os
import pathlib
import re

import pytest
from sqlalchemy import update

from alfred import panel_i18n, panel_tokens
from alfred.db import AsyncSessionLocal, engine
from alfred.models import Member

db = pytest.mark.skipif(os.environ.get("ALFRED_TEST_DB") != "1", reason="needs test DB")
PANEL = pathlib.Path(__file__).parent.parent / "src" / "alfred" / "panel"


def test_tabs_order_and_labels_in_every_language() -> None:
    for lang in ("pt", "nl", "en", "fr", "de"):
        ids = [t["id"] for t in panel_i18n.tabs(lang)]
        assert ids == ["summary", "money", "agenda", "health", "trips", "books", "home"]
        assert all(t["label"].strip() for t in panel_i18n.tabs(lang))
    assert [t["label"] for t in panel_i18n.tabs("pt")] == [
        "Resumo",
        "Dinheiro",
        "Agenda",
        "Hábitos",
        "Viagens",
        "Contabilidade",
        "Casa",
    ]


def test_shell_texts_cover_all_languages() -> None:
    for key, names in panel_i18n.SHELL.items():
        assert set(names) == {"pt", "nl", "en", "fr", "de"}, key
        assert all(v.strip() for v in names.values()), key


def test_js_never_writes_html() -> None:
    js = (PANEL / "panel.js").read_text()
    for banned in ("innerHTML", "insertAdjacentHTML", "document.write", "eval(", "outerHTML"):
        assert banned not in js, banned
    assert "textContent" in js and "de-DE" in js


def test_js_loads_each_tab_on_demand() -> None:
    js = (PANEL / "panel.js").read_text()
    assert "fetch(" in js and "cfg.tabs" in js and "loaded" in js
    for fn in ("barRow", "sparkline", "stackedBar", "cardShell"):
        assert f"function {fn}" in js, fn


def test_css_declares_both_themes_with_the_approved_tokens() -> None:
    css = (PANEL / "panel.css").read_text().lower()
    flat = css.replace(" ", "")
    for token in (
        "--bg:#0d1117",
        "--surface:#161b22",
        "--accent:#7fb0ff",
        "--warm:#ff6b7a",
        "--good:#4ade9a",
    ):
        assert token in flat, token
    assert "#f3f5f8" in css and "#1d4ed8" in css  # light theme
    assert "@font-face" in css and "https://" not in css
    assert ":focus-visible" in css


def test_font_files_and_licences_ship_with_the_panel() -> None:
    fonts = PANEL / "fonts"
    assert len(list(fonts.glob("*.woff2"))) == 5
    for lic in fonts.glob("LICENSE-OFL-*.txt"):
        assert "SIL OPEN FONT LICENSE" in lic.read_text().upper()
    assert len(list(fonts.glob("LICENSE-OFL-*.txt"))) == 2


def test_every_font_face_points_at_a_shipped_file() -> None:
    from alfred import panel

    css = (PANEL / "panel.css").read_text()
    for name in re.findall(r"/panel-assets/([\w.-]+\.woff2)", css):
        assert panel.asset(name) is not None, name


def test_asset_whitelist_rejects_everything_else() -> None:
    from alfred import panel

    assert panel.asset("panel.css") and panel.asset("panel.js")
    for bad in ("../models.py", "secret.txt", "fonts/x.woff2", "shell.html", ""):
        assert panel.asset(bad) is None, bad


async def _page(lab, client, v2: bool, **extra) -> tuple[str, dict]:
    async with AsyncSessionLocal() as s:
        await s.execute(update(Member).where(Member.id == lab.member_id).values(dashboard_v2=v2))
        m = await s.get(Member, lab.member_id)
        await panel_tokens.ensure_panel_token(s, m)
        await s.commit()
        token = str(m.dashboard_token)
    await engine.dispose()
    r = await client.get(f"/d/{token}")
    await engine.dispose()
    return r.text, dict(r.headers)


@db
async def test_every_member_gets_the_v2_panel_whatever_the_old_flag_says(lab, client) -> None:
    for old_flag in (False, True):
        v2, headers = await _page(lab, client, old_flag)
        assert 'role="tablist"' in v2 and 'data-theme="dark"' in v2, old_flag
        assert "Chart" not in v2
        labels = re.findall(r'role="tab"[^>]*>([^<]+)<', v2)
        assert labels == [
            "Resumo",
            "Dinheiro",
            "Agenda",
            "Hábitos",
            "Viagens",
            "Contabilidade",
            "Casa",
        ]
        csp = headers["content-security-policy"]
        assert "unsafe-inline" not in csp and "cdnjs" not in v2 and "cdnjs" not in csp
        assert not re.search(r'<(script|link)[^>]+(src|href)="https?://', v2)
        nonce = re.search(r"'nonce-([^']+)'", csp).group(1)
        assert f'nonce="{nonce}"' in v2
        assert not re.search(r"__[A-Z_]+__", v2)
        assert 'style="' not in v2


@db
async def test_the_v1_page_and_its_data_api_are_gone(lab, client) -> None:
    from alfred import dashboard

    assert not hasattr(dashboard, "_HTML_TEMPLATE") and not hasattr(dashboard, "dashboard_api")
    _, _ = await _page(lab, client, False)
    async with AsyncSessionLocal() as s:
        token = str((await s.get(Member, lab.member_id)).dashboard_token)
    await engine.dispose()
    r = await client.get(f"/api/d/{token}")
    await engine.dispose()
    assert r.status_code in (404, 405)
    assert "recent_transactions" not in r.text


@db
async def test_the_language_chosen_in_the_chat_is_the_language_of_the_panel(lab, client) -> None:
    await lab.say("idioma inglês")
    page, _ = await _page(lab, client, True)
    assert '<html lang="en"' in page
    assert re.findall(r'role="tab"[^>]*>([^<]+)<', page) == [
        "Summary", "Money", "Agenda", "Habits", "Trips", "Accounting", "Home",
    ]  # fmt: skip
    await lab.say("language Dutch")
    page, _ = await _page(lab, client, True)
    assert '<html lang="nl"' in page


@db
async def test_v2_config_cannot_close_its_script_tag(lab, client) -> None:
    v2, _ = await _page(lab, client, True)
    cfg = re.search(r'id="panel-config"[^>]*>(.*?)</script>', v2, re.S).group(1)
    assert "<" not in cfg


@db
async def test_assets_are_public_whitelisted_and_cacheable(client) -> None:
    ok = await client.get("/panel-assets/panel.css")
    assert ok.status_code == 200 and "immutable" in ok.headers["cache-control"]
    assert ok.headers["content-type"].startswith("text/css")
    await engine.dispose()
    font = await client.get("/panel-assets/hanken-grotesk-latin-400-normal.woff2")
    assert font.status_code == 200 and font.headers["content-type"] == "font/woff2"
    await engine.dispose()
    assert (await client.get("/panel-assets/..%2Fmodels.py")).status_code in (404, 400)
    assert (await client.get("/panel-assets/secret.txt")).status_code == 404
    await engine.dispose()


def test_shell_texts_pass_the_message_audit() -> None:
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location(
        "message_audit_shell", PANEL.parent.parent.parent / "scripts" / "message_audit.py"
    )
    audit = importlib.util.module_from_spec(spec)
    sys.modules["message_audit_shell"] = audit
    spec.loader.exec_module(audit)
    findings, _ = audit.run()
    assert [str(f) for f in findings if "panel.shell" in f.where] == []


def test_every_placeholder_of_a_text_exists_in_all_five_languages() -> None:
    import re

    for key, names in panel_i18n.SHELL.items():
        fields = {lang: set(re.findall(r"\{(\w+)\}", text)) for lang, text in names.items()}
        assert len({frozenset(f) for f in fields.values()}) == 1, (key, fields)
        assert not any('"' in t and "“" in t for t in names.values()), key  # straight quotes only


def test_category_filter_is_the_closed_list_of_labels() -> None:
    cats = panel_i18n.categories("pt")
    assert [c["id"] for c in cats][:2] == ["supermarkt", "restaurant"] and len(cats) == 10
    assert {c["label"] for c in panel_i18n.categories("en")} >= {"Groceries", "Housing"}


def test_shell_html_carries_the_name_escaped_and_never_rescans_user_text() -> None:
    from alfred import panel

    page = panel.render_v2("n0nce", "pt", "tok", '<b>__NONCE__</b>"')
    assert "&lt;b&gt;__NONCE__&lt;/b&gt;&quot;" in page  # escaped, and not replaced by the nonce
    assert 'id="theme-toggle"' in page and 'id="filter-bar"' in page
    assert "Painel de" in page
    assert 'class="who"' not in panel.render_v2("n0nce", "pt", "tok", None)
