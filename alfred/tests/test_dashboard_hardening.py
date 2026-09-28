"""Dashboard: malformed ?month= must not 500, and no unescaped model/user text in innerHTML."""

from __future__ import annotations

from datetime import date

import pytest

from alfred import dashboard

TODAY = date(2026, 9, 28)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026-03", (2026, 3)),
        (None, (2026, 9)),
        ("", (2026, 9)),
        ("2026-13", (2026, 9)),  # was: ValueError → HTTP 500 in monthrange
        ("2026-00", (2026, 9)),
        ("abcd-ef", (2026, 9)),
        ("2026", (2026, 9)),
        ("1-2-3", (2026, 9)),
        ("0001-01", (2026, 9)),
        ("9999-01", (2026, 9)),
    ],
)
def test_parse_month(raw, expected) -> None:
    assert dashboard._parse_month(raw, TODAY) == expected


def test_template_escapes_every_interpolated_field() -> None:
    html = dashboard._HTML_TEMPLATE
    # attribute-position value is forced to a known class, never raw model text
    assert '${t.type==="income"?"income":"expense"}' in html
    assert "${t.type}" not in html
    # health labels fall back to the raw log_type — must be escaped
    assert "${esc(labels[h.type]||h.type)}" in html
    assert "${labels[h.type]||h.type}" not in html
    # esc() covers quotes and non-strings
    assert "String(s)" in html and "&#39;" in html
