"""Sanitisers for structured data returned by the LLM.

The model is a *parser*, not an authority: its JSON can contain a negative or
absurd amount, a category we never defined, a ``days_ago`` of 4000, a 10 kB
"merchant". Everything is checked here, before it can reach the database or the
dashboard. Each ``sanitize_*`` returns a clean dict, or ``None`` when the
payload is unusable (the caller then treats the message as "not an X").
"""

from __future__ import annotations

import math
from typing import Any

CATEGORIES = frozenset(
    {
        "supermarkt",
        "restaurant",
        "transport",
        "gezondheid",
        "entertainment",
        "wonen",
        "kleding",
        "abonnement",
        "inkomen",
        "overig",
    }
)
CURRENCIES = frozenset({"EUR", "USD", "GBP"})
TXN_TYPES = frozenset({"expense", "income"})
HEALTH_TYPES = frozenset({"medication", "mood", "sleep", "water"})
QUERY_TYPES = frozenset({"balance", "category", "period", "comparison"})
PERIODS = frozenset({"today", "current_month", "last_month", "current_week", "last_week"})

MAX_AMOUNT = 1_000_000.0  # one message cannot move a million euros
MAX_DAYS_AGO = 366
MAX_TEXT = 255  # matches the String(255) columns
MAX_NOTES = 500


def _num(value: Any) -> float | None:
    """Finite float, or None (rejects NaN/inf/bools/garbage). "12,50" is understood."""
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        from alfred.parsing import to_amount

        cleaned = value.strip().lstrip("-").strip("€$£ ").removesuffix("EUR").strip()
        parsed = to_amount(cleaned)
        return parsed if parsed is not None else None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _text(value: Any, limit: int = MAX_TEXT) -> str | None:
    """Trimmed single-line string capped at ``limit``; None when empty."""
    if value is None:
        return None
    s = " ".join(str(value).split())  # collapses newlines/tabs
    return s[:limit] or None


def _days_ago(value: Any) -> int:
    """0..MAX_DAYS_AGO; the future and nonsense collapse to 0 (today)."""
    n = _num(value)
    if n is None:
        return 0
    return max(0, min(int(n), MAX_DAYS_AGO))


def sanitize_expense(data: dict) -> dict | None:
    amount = _num(data.get("amount"))
    if amount is None:
        return None
    amount = round(abs(amount), 2)  # the sign lives in ``type``, never in the amount
    if amount <= 0 or amount > MAX_AMOUNT:  # also rejects 0.004 (would be stored as 0.00)
        return None
    txn_type = str(data.get("type") or "expense").lower()
    if txn_type not in TXN_TYPES:
        txn_type = "expense"
    currency = str(data.get("currency") or "EUR").upper()
    if currency not in CURRENCIES:
        currency = "EUR"
    category = str(data.get("category") or "").lower().strip()
    if category not in CATEGORIES:
        category = "overig"
    if txn_type == "income":
        category = "inkomen"
    elif category == "inkomen":
        category = "overig"  # an expense cannot be "income"
    return {
        "amount": amount,
        "currency": currency,
        "merchant": _text(data.get("merchant")),
        "category": category,
        "description": _text(data.get("description")) or "",
        "type": txn_type,
        "days_ago": _days_ago(data.get("days_ago")),
    }


def sanitize_workout(data: dict) -> dict | None:
    activity = _text(data.get("activity_type"), 100) or "other"
    duration = _num(data.get("duration_minutes"))
    distance = _num(data.get("distance_km"))
    duration_i = int(duration) if duration is not None and 0 < duration <= 24 * 60 else None
    distance_f = round(distance, 2) if distance is not None and 0 < distance <= 1000 else None
    return {
        "activity_type": activity.lower(),
        "duration_minutes": duration_i,
        "distance_km": distance_f,
        "notes": _text(data.get("notes"), MAX_NOTES),
        "days_ago": _days_ago(data.get("days_ago")),
    }


def sanitize_health(data: dict) -> dict | None:
    log_type = str(data.get("log_type") or "").lower().strip()
    if log_type not in HEALTH_TYPES:
        return None
    value = _text(data.get("value"))
    if not value:
        return None
    return {
        "log_type": log_type,
        "value": value,
        "unit": _text(data.get("unit"), 20),
        "notes": _text(data.get("notes"), MAX_NOTES),
        "days_ago": _days_ago(data.get("days_ago")),
    }


def sanitize_habit(data: dict) -> dict | None:
    activity = _text(data.get("activity"))
    if not activity:
        return None
    return {
        "activity": activity,
        "notes": _text(data.get("notes"), MAX_NOTES),
        "days_ago": _days_ago(data.get("days_ago")),
    }


def sanitize_query(data: dict) -> dict:
    query_type = str(data.get("query_type") or "period").lower()
    if query_type not in QUERY_TYPES:
        query_type = "period"
    category = str(data.get("category") or "").lower().strip()
    period = str(data.get("period") or "current_month").lower()
    return {
        "query_type": query_type,
        "category": category if category in CATEGORIES else None,
        "period": period if period in PERIODS else "current_month",
    }
