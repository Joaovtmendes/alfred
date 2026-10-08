"""LLM output is untrusted: every extractor result goes through validation."""

from __future__ import annotations

import pytest

from alfred.validation import (
    sanitize_expense,
    sanitize_habit,
    sanitize_health,
    sanitize_query,
    sanitize_workout,
)


def _exp(**kw):
    base = {
        "amount": 12.5,
        "currency": "EUR",
        "merchant": "Jumbo",
        "category": "supermarkt",
        "description": "x",
        "type": "expense",
        "days_ago": 0,
    }
    return {**base, **kw}


def test_valid_expense_passes_through() -> None:
    out = sanitize_expense(_exp())
    assert out == {**_exp(), "days_ago": 0}


@pytest.mark.parametrize("amount", [0, -5, "abc", None, float("nan"), float("inf"), 5e9, True])
def test_bad_amounts_are_rejected(amount) -> None:
    if amount == -5:  # sign is dropped, magnitude kept
        assert sanitize_expense(_exp(amount=amount))["amount"] == 5.0
    else:
        assert sanitize_expense(_exp(amount=amount)) is None


def test_unknown_category_falls_back_to_overig() -> None:
    assert sanitize_expense(_exp(category="<script>"))["category"] == "overig"
    assert sanitize_expense(_exp(category=None))["category"] == "overig"


def test_income_and_category_stay_consistent() -> None:
    assert sanitize_expense(_exp(type="income", category="wonen"))["category"] == "inkomen"
    assert sanitize_expense(_exp(type="expense", category="inkomen"))["category"] == "overig"
    assert sanitize_expense(_exp(type="refund"))["type"] == "expense"


def test_currency_is_allow_listed() -> None:
    # a real 3-letter code is kept so the chat refuses it; garbage falls back to euro
    assert sanitize_expense(_exp(currency="btc"))["currency"] == "BTC"
    assert sanitize_expense(_exp(currency="euro?"))["currency"] == "EUR"
    assert sanitize_expense(_exp(currency="usd"))["currency"] == "USD"


@pytest.mark.parametrize(("raw", "expected"), [(-3, 0), (4000, 366), ("x", 0), (2.9, 2), (None, 0)])
def test_days_ago_is_clamped(raw, expected) -> None:
    assert sanitize_expense(_exp(days_ago=raw))["days_ago"] == expected


def test_text_is_trimmed_and_single_line() -> None:
    out = sanitize_expense(_exp(merchant="A" * 1000, description="line1\nline2\t  x"))
    assert len(out["merchant"]) == 255
    assert out["description"] == "line1 line2 x"
    assert sanitize_expense(_exp(merchant="   "))["merchant"] is None


def test_health_requires_known_type_and_value() -> None:
    assert sanitize_health({"log_type": "mood", "value": 7, "unit": "/10"})["value"] == "7"
    assert sanitize_health({"log_type": "<img>", "value": "x"}) is None
    assert sanitize_health({"log_type": "water", "value": ""}) is None


def test_workout_limits() -> None:
    out = sanitize_workout(
        {"activity_type": "Running", "duration_minutes": 99999, "distance_km": -3, "days_ago": 1}
    )
    assert out["activity_type"] == "running"
    assert out["duration_minutes"] is None and out["distance_km"] is None
    assert sanitize_workout({"duration_minutes": 30.0})["duration_minutes"] == 30


def test_habit_needs_activity() -> None:
    assert sanitize_habit({"activity": "  "}) is None
    assert sanitize_habit({"activity": "meditar"})["activity"] == "meditar"


def test_query_is_normalised() -> None:
    assert sanitize_query({"query_type": "hack", "category": "x", "period": "forever"}) == {
        "query_type": "period",
        "category": None,
        "period": "current_month",
    }
    assert (
        sanitize_query({"query_type": "category", "category": "Wonen", "period": "last_week"})[
            "category"
        ]
        == "wonen"
    )


def test_string_amounts_and_tiny_amounts() -> None:
    assert sanitize_expense(_exp(amount="12,50"))["amount"] == 12.5
    assert sanitize_expense(_exp(amount="€ 12"))["amount"] == 12.0
    assert sanitize_expense(_exp(amount=0.004)) is None  # would be stored as 0.00


# ── parse_llm_json: what models actually return ───────────────────────────────

from alfred.validation import parse_llm_json  # noqa: E402


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('{"is_habit": true, "activity": "gym"}', {"is_habit": True, "activity": "gym"}),
        ('```json\n{"a": 1}\n```', {"a": 1}),
        ('{"a": 1}\n\nNota: isto é um hábito.', {"a": 1}),  # "Extra data" in production
        ('Claro! {"a": 1}', {"a": 1}),
        ('{"a": {"b": [1, 2]}} trailing {"c": 2}', {"a": {"b": [1, 2]}}),
    ],
)
def test_parse_llm_json_accepts_wrapped_objects(raw, expected):
    assert parse_llm_json(raw) == expected


@pytest.mark.parametrize(
    "raw", ["", "sem json", "[1, 2]", '[{"amount": 5}, {"amount": 6}]', '{"unterminated": ']
)
def test_parse_llm_json_rejects_everything_else(raw):
    assert parse_llm_json(raw) is None
