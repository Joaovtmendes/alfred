"""Static assets and page of the v2 panel, served by ``dashboard.py`` (no CDN, no third party)."""

from __future__ import annotations

import hashlib
import html
import json
import pathlib

from alfred import panel_i18n
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


def render_v2(nonce: str, lang: str | None, token: str) -> str:
    """The v2 shell. Only fixed, escaped texts and the already-validated token go in."""
    lang = normalize_lang(lang)
    t = panel_i18n.shell(lang)
    e = html.escape
    tab_list = panel_i18n.tabs(lang)
    tabs_html = "".join(
        f'<button role="tab" data-tab="{e(x["id"])}" '
        f'aria-selected="{"true" if i == 0 else "false"}" '
        f'tabindex="{0 if i == 0 else -1}">{e(x["label"])}</button>'
        for i, x in enumerate(tab_list)
    )
    number = "".join(ch for ch in settings.whatsapp_display_number if ch.isdigit())
    if number:
        link = f"https://wa.me/{number}"
        chat_top = f'<a class="cta-top" href="{link}">{e(t["chat_button"])}</a>'
        chat_bottom = f'<div class="cta"><a href="{link}">{e(t["chat_button"])}</a></div>'
    else:  # no configured number: omit the button rather than point it at the wrong place
        chat_top = chat_bottom = ""
    config = {"token": token, "tabs": tab_list, "t": t, "lang": lang}
    page = (ROOT / "shell.html").read_text(encoding="utf-8")
    values = {
        "__LANG__": lang,
        "__TITLE__": e(ui(lang)["title"]),
        "__CSS__": versioned("panel.css"),
        "__JS__": versioned("panel.js"),
        "__LINK_VALID__": e(t["link_valid"]),
        "__CHAT_TOP__": chat_top,
        "__CHAT_BOTTOM__": chat_bottom,
        "__TABS__": tabs_html,
        "__PRIVACY__": e(t["privacy_footer"]),
        "__NONCE__": nonce,
        "__CONFIG__": _json_for_script(config),
    }
    config_json = values.pop("__CONFIG__")
    for key, val in values.items():
        page = page.replace(key, val)
    return page.replace("__CONFIG__", config_json)  # last: JSON text is never re-scanned
