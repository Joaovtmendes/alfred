#!/usr/bin/env python3
"""WCAG contrast of the panel colour tokens in both themes (AA: 4.5 text, 3.0 graphics).

Reads ``panel.css`` itself, so a token edited there is checked on the next run. The light theme
is declared twice in the CSS (``[data-theme="light"]`` and the ``prefers-color-scheme`` copy);
both are checked so the copies cannot drift apart.

Usage: ``python scripts/contrast_check.py`` (exit 1 when any pair is below its minimum).
"""

from __future__ import annotations

import pathlib
import re
import sys

CSS = pathlib.Path(__file__).resolve().parent.parent / "src" / "alfred" / "panel" / "panel.css"

TEXT, GRAPHIC = 4.5, 3.0
PAIRS = [  # (foreground, background, minimum ratio)
    ("ink", "bg", TEXT),
    ("ink", "surface", TEXT),
    ("ink", "surface2", TEXT),
    ("muted", "bg", TEXT),
    ("muted", "surface", TEXT),
    ("muted", "surface2", TEXT),
    ("accent-text", "accent-soft", TEXT),
    ("accent-text", "surface", TEXT),
    ("on-accent", "accent", TEXT),
    ("warm", "surface", TEXT),  # delta text such as "up 8%"
    ("good", "surface", TEXT),
    ("accent", "bg", GRAPHIC),  # bars, focus ring, active-tab underline
    ("accent", "surface", GRAPHIC),
    ("accent", "surface2", GRAPHIC),  # bar fill over its track
    ("warm", "surface2", GRAPHIC),
    ("good", "surface2", GRAPHIC),
    ("line", "surface", 1.0),  # decorative divider, no minimum
]


def _lum(hex_color: str) -> float:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i : i + 2], 16) / 255 for i in (0, 2, 4))

    def f(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def ratio(a: str, b: str) -> float:
    la, lb = sorted((_lum(a), _lum(b)), reverse=True)
    return round((la + 0.05) / (lb + 0.05), 2)


def _tokens(block: str) -> dict[str, str]:
    return dict(re.findall(r"--([a-z0-9-]+):\s*(#[0-9A-Fa-f]{6})", block))


def _themes() -> dict[str, dict[str, str]]:
    css = CSS.read_text()
    dark = re.search(r':root,\[data-theme="dark"\]\s*\{([^}]*)\}', css).group(1)
    light = re.search(r'\[data-theme="light"\]\s*\{([^}]*)\}', css).group(1)
    media = re.search(
        r"@media \(prefers-color-scheme:light\)\{\s*:root:not\(\[data-theme-locked\]\)\{([^}]*)\}",
        css,
    ).group(1)
    return {"dark": _tokens(dark), "light": _tokens(light), "dark-media-light": _tokens(media)}


def check() -> list[str]:
    failures = []
    for theme, tokens in _themes().items():
        for fg, bg, minimum in PAIRS:
            r = ratio(tokens[fg], tokens[bg])
            if r < minimum:
                failures.append(f"{theme}: {fg} on {bg} = {r} (< {minimum})")
    return failures


def report() -> None:
    for theme, tokens in _themes().items():
        for fg, bg, minimum in PAIRS:
            r = ratio(tokens[fg], tokens[bg])
            verdict = "ok" if r >= minimum else "FAIL"
            print(f"{theme:17} {fg:12} on {bg:12} {r:6.2f}  min {minimum:>3}  {verdict}")


if __name__ == "__main__":
    if "-v" in sys.argv:
        report()
    problems = check()
    print("\n".join(problems) or "all pairs pass AA")
    sys.exit(1 if problems else 0)
