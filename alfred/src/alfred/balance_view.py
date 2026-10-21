# ruff: noqa: E501
"""The short "saldo" answer and the month-end estimate (F05-07, F04-43)."""

from __future__ import annotations

import calendar
from datetime import date

MIN_DAYS = 7  # before this the month is too young for a pace to mean anything


def projection(total_out: float, today: date) -> float | None:
    """Expenses at the end of the month if the pace so far continues; None when too early."""
    if total_out <= 0 or today.day < MIN_DAYS:
        return None
    days = calendar.monthrange(today.year, today.month)[1]
    return round(total_out / today.day * days, 2)


def _all(pt: str, nl: str, en: str, fr: str, de: str) -> dict[str, str]:
    return {"pt": pt, "nl": nl, "en": en, "fr": fr, "de": de}


STRINGS: dict[str, dict[str, str]] = {
    "btn_balance_detail": _all(
        "Ver detalhes", "Details", "See details", "Voir le détail", "Details ansehen"
    ),
    "saldo_projection": _all(
        "_Estimativa: no ritmo atual, as despesas do mês fecham em cerca de {amount}._",
        "_Schatting: in dit tempo komen de uitgaven van de maand op ongeveer {amount}._",
        "_Estimate: at the current pace, this month's expenses end around {amount}._",
        "_Estimation : à ce rythme, les dépenses du mois s'élèvent à environ {amount}._",
        "_Schätzung: im aktuellen Tempo enden die Ausgaben des Monats bei etwa {amount}._",
    ),
}
