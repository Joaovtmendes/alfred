# ruff: noqa: E501
"""V2-12 — accounting maths: year balance, categories, fixed/variable, business profit, BTW, reserve."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from alfred import accounting as ac

D = Decimal


def L(day, kind, amount, cat="overig", scope="personal", btw=None, deductible=True, merchant=None):
    return ac.Line(day, kind, D(str(amount)), cat, scope, btw, deductible, merchant)


def test_btw_part_is_the_vat_inside_a_gross_amount() -> None:
    assert ac.btw_part(D("121"), 21) == D("21.00")
    assert ac.btw_part(D("109"), 9) == D("9.00")
    assert ac.btw_part(D("50"), 0) == D("0.00")
    assert ac.btw_part(D("10.00"), 21) == D("1.74")  # 10 * 21 / 121 = 1.7355 → rounds half up


@pytest.mark.parametrize(
    ("month", "q"), [(1, 1), (3, 1), (4, 2), (6, 2), (7, 3), (9, 3), (10, 4), (12, 4)]
)
def test_quarter(month, q) -> None:
    assert ac.quarter(date(2026, month, 15)) == q


def test_year_summary_balance_rate_and_months() -> None:
    lines = [
        L(date(2026, 1, 5), "income", 3000),
        L(date(2026, 1, 9), "expense", 1000),
        L(date(2026, 2, 5), "income", 3000),
        L(date(2026, 2, 7), "expense", 2000),
        L(date(2026, 3, 1), "expense", 100),
    ]
    s = ac.year_summary(lines, last_month=3)
    assert (s["income"], s["expense"], s["balance"]) == (D("6000"), D("3100"), D("2900"))
    assert s["saving_rate"] == D("48.3")  # 2900 / 6000
    assert [(m["m"], m["income"], m["expense"]) for m in s["months"]] == [
        (1, D("3000"), D("1000")),
        (2, D("3000"), D("2000")),
        (3, D("0"), D("100")),
    ]


def test_year_summary_without_income_has_no_rate_and_empty_year_is_zero() -> None:
    assert ac.year_summary([L(date(2026, 1, 5), "expense", 10)], 1)["saving_rate"] is None
    s = ac.year_summary([], 12)
    assert (s["income"], s["expense"], s["balance"], len(s["months"])) == (D(0), D(0), D(0), 12)


def test_by_category_sorts_and_only_counts_spending() -> None:
    lines = [
        L(date(2026, 1, 1), "expense", 10, "supermarkt"),
        L(date(2026, 1, 2), "expense", 30, "wonen"),
        L(date(2026, 1, 3), "expense", 5, "supermarkt"),
        L(date(2026, 1, 4), "income", 999, "inkomen"),
    ]
    assert ac.by_category(lines) == [("wonen", D("30")), ("supermarkt", D("15"))]


def test_fixed_split_by_name_or_category() -> None:
    lines = [
        L(date(2026, 1, 1), "expense", 800, "wonen"),
        L(date(2026, 1, 2), "expense", 12, "overig", merchant="Gym"),
        L(date(2026, 1, 3), "expense", 60, "supermarkt"),
        L(date(2026, 1, 4), "income", 500, "inkomen"),
    ]
    assert ac.fixed_split(lines, {"gym"}) == (D("812"), D("60"))


def _business_lines():
    d = date(2026, 5, 10)
    return [
        L(d, "income", 1210, "inkomen", "business", 21),
        L(d, "expense", 121, "overig", "business", 21),
        L(d, "expense", 109, "overig", "business", 9),
        L(d, "expense", 50, "overig", "business", None),  # no VAT rate given
        L(d, "expense", 80, "overig", "business", 21, deductible=False),
        L(d, "expense", 999, "wonen", "personal", 21),  # never in the business numbers
        L(d, "income", 999, "inkomen", "personal", 21),
    ]


def test_business_profit_is_net_of_btw_and_ignores_personal_and_non_deductible() -> None:
    b = ac.business_summary(_business_lines(), last_month=5)
    assert b["income"] == D("1000.00")  # 1210 gross, 210 VAT
    assert b["expense"] == D("250.00")  # 100 + 100 + 50 (no rate: taken as it is)
    assert b["profit"] == D("750.00")
    assert b["non_deductible"] == D("80")
    assert b["unrated"] == 1
    assert [m["m"] for m in b["months"]] == [1, 2, 3, 4, 5]
    assert b["months"][4]["profit"] == D("750.00")


def test_btw_quarters_owed_input_and_net() -> None:
    lines = _business_lines() + [L(date(2026, 8, 1), "income", 121, "inkomen", "business", 21)]
    q = ac.btw_quarters(lines)
    assert [x["q"] for x in q] == [1, 2, 3, 4]
    assert (q[1]["owed"], q[1]["input"], q[1]["net"], q[1]["unrated"]) == (
        D("210.00"),
        D("30.00"),
        D("180.00"),
        1,
    )
    assert (q[2]["owed"], q[2]["input"], q[2]["net"]) == (D("21.00"), D("0.00"), D("21.00"))
    assert q[0]["net"] == D("0.00") and q[3]["net"] == D("0.00")


def test_btw_refund_is_negative() -> None:
    q = ac.btw_quarters([L(date(2026, 2, 1), "expense", 121, "overig", "business", 21)])
    assert q[0]["net"] == D("-21.00")


def test_deductible_by_category() -> None:
    d = date(2026, 5, 10)
    lines = [
        L(d, "expense", 100, "overig", "business"),
        L(d, "expense", 40, "transport", "business"),
        L(d, "expense", 25, "transport", "business"),
        L(d, "expense", 7, "transport", "business", deductible=False),
        L(d, "expense", 500, "wonen", "personal"),
    ]
    assert ac.deductible_by_category(lines) == [("overig", D("100")), ("transport", D("65"))]


def test_reserve() -> None:
    assert ac.reserve(D("750.00"), 30) == D("225.00")
    assert ac.reserve(D("-10"), 30) == D("0.00")
    assert ac.reserve(D("750"), None) is None


def test_months_covered() -> None:
    assert ac.months_covered(D("6000"), D("2000")) == D("3.0")
    assert ac.months_covered(D("5000"), D("1800")) == D("2.8")
    assert ac.months_covered(None, D("2000")) is None
    assert ac.months_covered(D("5000"), None) is None
    assert ac.months_covered(D("5000"), D("0")) is None


def test_csv_cell_neutralises_formulas() -> None:
    assert ac.csv_cell("=1+1") == "'=1+1"
    assert ac.csv_cell("+cmd") == "'+cmd"
    assert ac.csv_cell("-5") == "'-5"
    assert ac.csv_cell("@x") == "'@x"
    assert ac.csv_cell("Jumbo") == "Jumbo"
    assert ac.csv_cell(None) == ""
