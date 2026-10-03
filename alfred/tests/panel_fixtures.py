# ruff: noqa: E501
"""Card payloads in the real API format (see panel_api.py) carrying the numbers of the approved
mockups. Used by test_panel_js.py and by the screenshot comparison, never by the server."""

from __future__ import annotations

import copy
from typing import Any


def phrase(text: str, chat: str | None, severity: str = "info", key: str = "x") -> dict[str, Any]:
    return {"key": key, "text": text, "chat": chat, "severity": severity}


def _cat(cat, label, amount, pct, prev, trend, dpct):
    return {
        "category": cat,
        "label": label,
        "amount": amount,
        "pct_of_total": pct,
        "count": 3,
        "previous": prev,
        "delta": round(amount - prev, 2),
        "delta_pct": dpct,
        "trend": trend,
    }


CATEGORIES = {
    "id": "categories",
    "empty": False,
    "values": {"total": 653.0, "previous_total": 620.0, "delta": 33.0, "pct": 5.3},
    "items": [
        _cat("supermarkt", "Supermercado", 312.0, 48, 288.9, "up", 8.0),
        _cat("restaurant", "Restaurantes", 148.0, 23, 96.7, "up", 53.0),
        _cat("entertainment", "Lazer", 94.0, 14, 105.6, "down", -11.0),
        _cat("transport", "Transporte", 61.0, 9, 93.8, "down", -35.0),
        _cat("gezondheid", "Saúde", 38.0, 6, 38.0, "flat", 0.0),
    ],
    "others": None,
    "compare": {"start": "2026-09-01", "end": "2026-09-15"},
    "phrase": phrase(
        "Restaurantes é o que mais cresceu: € 51 a mais que em setembro. Transporte caiu € 33.",
        '"maiores gastos de restaurantes este mês"',
        key="categories_rise",
    ),
}

OWED = {
    "id": "owed",
    "empty": False,
    "values": {"total": 34.5, "count": 1},
    "items": [
        {"person": "Marta", "amount": 34.5, "note": "jantar", "since": "2026-10-05", "days": 9}
    ],
    "phrase": phrase("Há 9 dias em aberto.", '"Marta pagou 34,50"'),
}


def _bill(name, amount, due, days, source="recurring"):
    return {
        "name": name,
        "amount": amount,
        "due": due,
        "days": days,
        "due_text": f"vence em {days} dias",
        "overdue": False,
        "direction": "pay",
        "source": source,
        "kind": "fixed",
        "category": "wonen",
        "label": "Habitação",
    }


SUMMARY: dict[str, Any] = {
    "tab": "summary",
    "lang": "pt",
    "today": "2026-10-14",
    "filter": {
        "start": "2026-10-01",
        "end": "2026-11-01",
        "categories": [],
        "kind": None,
        "states": [],
        "trip": None,
    },
    "cards": [
        {
            "id": "balance",
            "empty": False,
            "values": {"income": 3840.0, "expense": 1997.7, "balance": 1842.3},
            "compare": {
                "start": "2026-09-01",
                "end": "2026-09-15",
                "income": {"previous": 3840.0, "delta": 0.0, "pct": 0.0},
                "expense": {"previous": 1920.87, "delta": 76.83, "pct": 4.0},
            },
            "projection": {
                "available": True,
                "sufficient": True,
                "realized": 1842.3,
                "committed": 298.0,
                "scheduled_in": 0.0,
                "variable": 424.0,
                "projected": 1120.3,
                "low": 1030.0,
                "high": 1210.0,
                "month_end": "2026-10-31",
                "days_left": 17,
                "window_days": 14,
                "entries": 23,
                "history_days": 45,
            },
            "phrase": phrase(
                "Se as contas marcadas acontecerem e o gasto seguir o seu ritmo, você fecha outubro com cerca de € 1.120. A faixa é uma estimativa, não uma garantia.",
                '"como fica meu mês?"',
                key="balance_projection",
            ),
        },
        {
            "id": "upcoming",
            "empty": False,
            "values": {
                "to_pay": 298.0,
                "to_receive": 0.0,
                "overdue": 0.0,
                "count_pay": 3,
                "count_receive": 0,
                "count_overdue": 0,
                "horizon_days": 30,
            },
            "items": [
                _bill("Energia", 118.0, "2026-10-18", 4),
                _bill("Internet", 42.0, "2026-10-20", 6),
                _bill("Seguro de saúde", 138.0, "2026-10-25", 11),
            ],
            "more": 0,
            "phrase": phrase(
                "Três contas somam € 298,00 até o fim do mês. Nada está atrasado.", '"energia paga"'
            ),
        },
        copy.deepcopy(CATEGORIES),
        {
            "id": "budgets",
            "empty": False,
            "values": {
                "spent": 521.0,
                "limit": 670.0,
                "pct": 74,
                "month": "2026-10",
                "day": 14,
                "days_in_month": 31,
            },
            "items": [
                {
                    "category": "restaurant",
                    "label": "Restaurantes",
                    "spent": 148.0,
                    "limit": 120.0,
                    "pct": 123,
                    "level": 100,
                    "crossed_on": "2026-10-03",
                    "days_to_80": None,
                },
                {
                    "category": "supermarkt",
                    "label": "Supermercado",
                    "spent": 312.0,
                    "limit": 400.0,
                    "pct": 78,
                    "level": 0,
                    "crossed_on": None,
                    "days_to_80": 2,
                },
                {
                    "category": "transport",
                    "label": "Transporte",
                    "spent": 61.0,
                    "limit": 150.0,
                    "pct": 41,
                    "level": 0,
                    "crossed_on": None,
                    "days_to_80": None,
                },
            ],
            "phrase": phrase(
                "Restaurantes passou do orçamento em 3 de outubro; Supermercado chega a 80% em cerca de 2 dias.",
                '"orçamento restaurantes 150"',
                "attention",
            ),
        },
        {
            "id": "blue_days",
            "empty": False,
            "values": {"blue": 12, "elapsed": 14, "longest": 9, "balance": 1842.3},
            "phrase": None,
        },
        copy.deepcopy(OWED),
    ],
}


def _entry(merchant, label, amount, kind="expense", status="paid"):
    return {
        "merchant": merchant,
        "category": "x",
        "label": label,
        "kind": kind,
        "amount": amount,
        "status": status,
        "settled": status in ("paid", "received"),
    }


_DAILY = [
    38,
    52,
    12,
    0,
    8,
    61,
    22,
    94,
    14,
    0,
    40,
    160,
    20,
    12,
    0,
    53,
    47,
    210,
    15,
    6,
    0,
    70,
    39,
    58,
    30,
    0,
    12,
    85,
    42,
    20,
    9,
]

MONEY: dict[str, Any] = {
    "tab": "money",
    "lang": "pt",
    "today": "2026-10-02",
    "filter": {
        "start": "2026-10-01",
        "end": "2026-11-01",
        "categories": [],
        "kind": None,
        "states": [],
        "trip": None,
    },
    "cards": [
        {
            "id": "transactions",
            "empty": False,
            "values": {"income": 3840.0, "expense": 1997.7, "balance": 1842.3, "count": 23},
            "page": {"number": 1, "size": 50, "pages": 1, "total": 23},
            "days": [
                {
                    "date": "2026-10-02",
                    "income": 3840.0,
                    "expense": 42.18,
                    "net": 3797.82,
                    "entries_total": 2,
                    "entries": [
                        _entry("Albert Heijn", "Supermercado", 42.18),
                        _entry("Salário", "Salário", 3840.0, "income", "received"),
                    ],
                },
                {
                    "date": "2026-10-01",
                    "income": 0.0,
                    "expense": 1168.4,
                    "net": -1168.4,
                    "entries_total": 2,
                    "entries": [
                        _entry("Aluguel", "Moradia", 1150.0),
                        _entry("Uber", "Transporte", 18.4),
                    ],
                },
                {
                    "date": "2026-09-30",
                    "income": 0.0,
                    "expense": 64.0,
                    "net": -64.0,
                    "entries_total": 2,
                    "entries": [
                        _entry("Restaurante Foodhallen", "Restaurantes", 64.0),
                        _entry("Energia", "Contas", 118.0, status="to_pay"),
                    ],
                },
            ],
            "phrase": phrase(
                "23 lançamentos em outubro. O maior foi o aluguel, € 1.150,00.",
                '"mostre meus gastos de outubro"',
            ),
        },
        {
            "id": "top_expenses",
            "empty": False,
            "values": {"total": 1997.7, "count": 23},
            "items": [
                {
                    "merchant": m,
                    "category": "x",
                    "label": "x",
                    "amount": a,
                    "date": "2026-10-01",
                    "pct_of_total": p,
                    "pct_of_top": t,
                }
                for m, a, p, t in [
                    ("Aluguel", 1150.0, 58, 100),
                    ("Seguro de saúde", 138.0, 7, 12),
                    ("Energia", 118.0, 6, 10),
                    ("Mercado Albert Heijn", 94.4, 5, 8),
                    ("Restaurante Foodhallen", 64.0, 3, 6),
                ]
            ],
            "phrase": phrase(
                "O aluguel é 58% de tudo que saiu.", '"qual foi minha maior despesa?"'
            ),
        },
        {
            "id": "month_vs_month",
            "empty": False,
            "values": {
                "expense": {"current": 1997.0, "previous": 1832.0, "delta": 165.0, "pct": 9.0},
                "income": {"current": 3840.0, "previous": 3840.0, "delta": 0.0, "pct": 0.0},
            },
            "compare": {"start": "2026-09-01", "end": "2026-10-01"},
            "drivers": [{"category": "restaurant", "label": "Restaurantes", "delta": 52.0}],
            "phrase": phrase(
                "Restaurantes subiram € 52 e explicam quase tudo da diferença.",
                '"compare outubro com setembro"',
            ),
        },
        {
            "id": "fixed_variable",
            "empty": False,
            "values": {"fixed": 1238.0, "variable": 760.0, "total": 1998.0, "fixed_pct": 62},
            "phrase": phrase(
                "Dá para mexer em € 760 do mês; o resto já está comprometido.",
                '"quanto é fixo e quanto é variável?"',
            ),
        },
        {
            "id": "daily",
            "empty": False,
            "unit": "day",
            "points": [
                {"date": f"2026-10-{i + 1:02d}", "amount": float(v)} for i, v in enumerate(_DAILY)
            ],
            "values": {
                "total": float(sum(_DAILY)),
                "max": 210.0,
                "max_date": "2026-10-18",
                "quiet_days": 6,
                "elapsed_days": 31,
            },
            "phrase": phrase(
                "Dia 18 concentrou o pico do mês; 8 dias sem nenhum gasto.",
                '"em que dia gasto mais?"',
            ),
        },
        copy.deepcopy(CATEGORIES)
        | {
            "items": copy.deepcopy(CATEGORIES["items"])
            + [_cat("wonen", "Habitação", 1150.0, 0, 1150.0, "flat", 0.0)],
            "others": {"amount": 80.0, "count": 4},
        },
        {
            "id": "avg_ticket",
            "empty": False,
            "values": {
                "average": 86.9,
                "count": 23,
                "total": 1997.7,
                "previous_average": 80.0,
                "delta": 6.9,
                "pct": 8.6,
            },
            "phrase": None,
        },
        {
            "id": "recurring",
            "empty": False,
            "values": {"count": 2, "monthly_total": 218.0},
            "items": [
                {
                    "name": "Internet",
                    "amount": 42.0,
                    "kind": "subscription",
                    "frequency": "monthly",
                    "next_due": "2026-10-20",
                    "due_text": "vence em 18 dias",
                    "installment": None,
                    "category": "wonen",
                    "label": "Habitação",
                },
                {
                    "name": "Notebook",
                    "amount": 100.0,
                    "kind": "installment",
                    "frequency": "monthly",
                    "next_due": "2026-10-28",
                    "due_text": "vence em 26 dias",
                    "installment": {"number": 1, "total": 13},
                    "category": "overig",
                    "label": "Outros",
                },
            ],
            "phrase": None,
        },
        copy.deepcopy(OWED),
    ],
}


def empty_card(
    card_id: str, hint_text="Registre pelo chat e aparece aqui.", chat='"gastei 25 no mercado"'
) -> dict[str, Any]:
    return {
        "id": card_id,
        "empty": True,
        "values": {},
        "items": [],
        "phrase": None,
        "hint": {"key": "h", "text": hint_text, "chat": chat, "severity": "info"},
    }
