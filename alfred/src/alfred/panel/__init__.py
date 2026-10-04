"""Static assets and page of the v2 panel, served by ``dashboard.py`` (no CDN, no third party)."""

from __future__ import annotations

import hashlib
import html
import json
import pathlib
import re

from alfred import panel_i18n
from alfred.clock import today_local
from alfred.dashboard_i18n import normalize_lang, ui
from alfred.settings import settings

ROOT = pathlib.Path(__file__).parent
_FONTS = ROOT / "fonts"
_FILES = ("panel.css", "panel.js", *sorted(p.name for p in _FONTS.glob("*.woff2")))
_TYPES = {
    "css": "text/css; charset=utf-8",
    "js": "text/javascript; charset=utf-8",
    "woff2": "font/woff2",
}


def _path(name: str) -> pathlib.Path:
    return (_FONTS if name.endswith(".woff2") else ROOT) / name


def asset(name: str) -> tuple[bytes, str] | None:
    """(bytes, content-type) for a whitelisted asset name, else None."""
    if name not in _FILES:
        return None
    return _path(name).read_bytes(), _TYPES[name.rsplit(".", 1)[1]]


def versioned(name: str) -> str:
    """``name?v=<content hash>``: the URL changes when the file does, so it can be immutable."""
    return f"{name}?v={hashlib.sha256(_path(name).read_bytes()).hexdigest()[:10]}"


def _json_for_script(data: object) -> str:
    """JSON that can never close its <script> tag (< > & are escaped as unicode)."""
    return (
        json.dumps(data, ensure_ascii=True)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )


def render_v2(
    nonce: str,
    lang: str | None,
    token: str,
    name: str | None = None,
    has_home: bool = False,
) -> str:
    """The v2 shell. Only fixed, escaped texts, the member's display name (escaped) and the
    already-validated token go in."""
    lang = normalize_lang(lang)
    t = panel_i18n.shell(lang)
    e = html.escape
    tab_list = panel_i18n.tabs(lang, home=has_home)
    tabs_html = "".join(
        f'<button role="tab" id="tab-{e(x["id"])}" aria-controls="panel" data-tab="{e(x["id"])}" '
        f'aria-selected="{"true" if i == 0 else "false"}" '
        f'tabindex="{0 if i == 0 else -1}">{e(x["label"])}</button>'
        for i, x in enumerate(tab_list)
    )
    who = (name or "").strip()[:40]
    panel_of = f'<span class="who">{e(t["panel_of"].format(name=who))}</span>' if who else ""
    number = "".join(ch for ch in settings.whatsapp_display_number if ch.isdigit())
    if number:
        link = f"https://wa.me/{number}"
        icon = (
            '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
            'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
            '<path d="M21 11.5a8.4 8.4 0 0 1-12.4 7.4L3 21l2.1-5.4A8.4 8.4 0 1 1 21 11.5z"/></svg>'
        )
        chat_top = f'<a class="cta-top" href="{link}">{icon}{e(t["chat_button"])}</a>'
        chat_bottom = f'<div class="cta"><a href="{link}">{icon}{e(t["chat_button"])}</a></div>'
    else:  # no configured number: omit the button rather than point it at the wrong place
        chat_top = chat_bottom = ""
    config = {
        "token": token,
        "tabs": tab_list,
        "t": t,
        "lang": lang,
        "today": today_local().isoformat(),
        "categories": panel_i18n.categories(lang),
    }
    page = (ROOT / "shell.html").read_text(encoding="utf-8")
    values = {
        "__LANG__": lang,
        "__TITLE__": e(ui(lang)["title"]),
        "__CSS__": versioned("panel.css"),
        "__JS__": versioned("panel.js"),
        "__PANEL_OF__": panel_of,
        "__LINK_VALID__": e(t["link_valid"]),
        "__CHAT_TOP__": chat_top,
        "__CHAT_BOTTOM__": chat_bottom,
        "__TABS__": tabs_html,
        "__READONLY__": e(t["footer_readonly"]),
        "__PRIVACY__": e(t["privacy_footer"]),
        "__NONCE__": nonce,
        "__CONFIG__": _json_for_script(config),
    }
    # One pass: a value is never scanned again (the member's name or the JSON cannot inject a key).
    return re.sub(r"__[A-Z_]+__", lambda m: values.get(m.group(0), m.group(0)), page)
