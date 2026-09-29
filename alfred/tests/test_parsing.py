"""Pure parsing rules — trips (M14) and category corrections (M12).

Every negative case here is a real message that an earlier, unanchored pattern
swallowed (found in the Sprint 0 audit).
"""

from __future__ import annotations

import pytest

from alfred.parsing import (
    TRIP_END_RE,
    TRIP_LIST_RE,
    TRIP_QUERY_RE,
    clean_destination,
    has_money,
    match_trip_start,
    parse_category_correction,
)

# ── money ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("text", ["€ 12", "45€", "20 euro", "$5", "3 eur", "€3"])
def test_has_money_true(text: str) -> None:
    assert has_money(text)


@pytest.mark.parametrize("text", ["olá", "voltei", "trip summary", "reis", "ns reis 12,50"])
def test_has_money_false(text: str) -> None:
    """A bare number is not money on its own (only with a currency marker)."""
    assert not has_money(text)


# ── trip start ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("text", "dest"),
    [
        ("viagem a lisboa", "Lisboa"),
        ("em viagem para roma", "Roma"),
        ("estou de viagem para paris", "Paris"),
        ("criar viagem portugal €500 de 1 a 7 de outubro", "Portugal"),
        ("nova viagem são paulo", "São Paulo"),
        ("trip: amsterdam", "Amsterdam"),
        ("trip to berlin", "Berlin"),
        ("op reis naar berlijn €300", "Berlijn"),
        ("voyage à nice", "Nice"),
        ("reise nach wien", "Wien"),
    ],
)
def test_trip_start_matches(text: str, dest: str) -> None:
    assert match_trip_start(text) == dest


@pytest.mark.parametrize(
    "text",
    [
        "trip summary",  # was: started a trip called "Summary"
        "trip ended",  # was: started a trip called "Ended"
        "trips",
        "quanto gastei na viagem a paris",  # was: started a trip to Paris
        "resumo da viagem",
        "viagem terminada",
        "gastei 20 numa viagem a lisboa",
        "recebi o dinheiro da viagem",
        "op reis",
    ],
)
def test_trip_start_does_not_steal_other_messages(text: str) -> None:
    assert match_trip_start(text) is None


def test_clean_destination_rejects_sentences() -> None:
    assert clean_destination("a very long sentence that is not a place at all") is None
    assert clean_destination("   ") is None
    assert clean_destination("€500") is None


# ── trip end ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        "voltei",
        "voltei a casa",
        "back home",
        "trip ended",
        "terug",
        "ik ben terug thuis",
        "rentré",
        "zuhause",
        "fim da viagem",
        "voltei!",
    ],
)
def test_trip_end_matches(text: str) -> None:
    assert TRIP_END_RE.match(text)


@pytest.mark.parametrize(
    "text",
    [
        "20 euro terug gekregen",  # was: "no active trip", expense lost
        "voltei a gastar 20 no jumbo",
        "terug naar de winkel 15 euro",
        "zurück von der tankstelle 40 euro",
        "back to the gym",
        "cheguei ao jumbo",
    ],
)
def test_trip_end_does_not_swallow_expenses(text: str) -> None:
    assert not TRIP_END_RE.match(text)


# ── trip query / list ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        "quanto gastei na viagem?",
        "resumo da viagem",
        "trip summary",
        "trip expenses",
        "reiskosten",
        "quero ver o resumo da viagem",
        "quanto gastei na viagem a paris",
    ],
)
def test_trip_query_matches(text: str) -> None:
    assert TRIP_QUERY_RE.match(text)


@pytest.mark.parametrize("text", ["ns reis 12,50", "reis", "viagem", "trip", "gastei 5 na viagem"])
def test_trip_query_ignores_expenses_and_bare_words(text: str) -> None:
    assert not TRIP_QUERY_RE.match(text)


@pytest.mark.parametrize(
    "text",
    ["as minhas viagens", "viagens", "my trips", "trips", "mijn reizen", "mes voyages"],
)
def test_trip_list_matches(text: str) -> None:
    assert TRIP_LIST_RE.match(text)


@pytest.mark.parametrize("text", ["quero viagens baratas", "viagens de 2020 foram caras", "trip"])
def test_trip_list_is_whole_message_only(text: str) -> None:
    assert not TRIP_LIST_RE.match(text)


# ── category corrections ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("jumbo é supermarkt", ("jumbo", "supermarkt")),
        ("Albert Heijn is not restaurant, is supermarkt", ("Albert Heijn", "supermarkt")),
        ("isso não é wonen, é transport", ("isso", "transport")),
        ("shell is transport.", ("shell", "transport")),
        ("zara é uma roupas", ("zara", "roupas")),
    ],
)
def test_category_correction_parses(text: str, expected: tuple[str, str]) -> None:
    assert parse_category_correction(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "recebi 2800 de salário e renda extra",  # was: income never recorded
        "jumbo e 20 euro",
        "tarefa: comprar comida e outros",
        "hoje é sexta e vou ao ginásio com a maria e depois jantar fora",
        "quem é o presidente",
        "what is the weather",
    ],
)
def test_category_correction_rejects_ordinary_sentences(text: str) -> None:
    assert parse_category_correction(text) is None


@pytest.mark.parametrize(
    ("text", "parsed"),
    [
        ("comprar comida e outros", ("comprar comida", "outros")),
        ("comprei pão e leite", ("comprei pão", "leite")),
        ("o meu cão é preto", ("o meu cão", "preto")),
    ],
)
def test_category_correction_lookalikes_parse_but_the_handler_rejects_them(
    text: str, parsed: tuple[str, str]
) -> None:
    """Short 'X e Y' sentences parse. The handler then requires a KNOWN merchant
    (see test_conversation_regressions), which is what keeps them out."""
    assert parse_category_correction(text) == parsed


# ── trip budget ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("text", "amount"),
    [
        ("criar viagem portugal €500 de 1 a 7 de outubro", 500.0),
        ("trip to rome 1.500 euro", 1500.0),
        ("viagem a paris 1.500,50€", 1500.5),
        ("berlin € 12,5", 12.5),
        ("op reis naar berlijn 300 eur", 300.0),
    ],
)
def test_parse_budget(text: str, amount: float) -> None:
    from alfred.parsing import parse_budget

    assert parse_budget(text) == amount


@pytest.mark.parametrize(
    "text", ["trip to oslo", "nova viagem 3 dias", "€0", "viagem 7 de outubro"]
)
def test_parse_budget_needs_a_currency_marker(text: str) -> None:
    from alfred.parsing import parse_budget

    assert parse_budget(text) is None


def test_strip_accents_is_length_preserving() -> None:
    from alfred.parsing import like_escape, strip_accents

    text = "como está o meu humor? bebi água, medicação — coração ñ"
    assert len(strip_accents(text)) == len(text)
    assert strip_accents("está água") == "esta agua"
    assert like_escape("100%_x\\") == "100\\%\\_x\\\\"


# ── review findings ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    ["viagem a lisboa 30€ gasolina", "viagem em uber 15,00", "trip to the store 5,00"],
)
def test_trip_start_does_not_swallow_expenses_with_a_tail(text: str) -> None:
    assert match_trip_start(text) is None


@pytest.mark.parametrize(
    ("text", "dest"),
    [
        ("viagem a lisboa com a maria", "Lisboa"),
        ("trip to rome 1.500 euro 3 to 9 march", "Rome"),
        ("viagem a paris 10/10 a 15/10", "Paris"),
        ("trip to berlin from 3 oct", "Berlin"),
    ],
)
def test_trip_start_still_accepts_budget_dates_and_companions(text: str, dest: str) -> None:
    assert match_trip_start(text) == dest


@pytest.mark.parametrize(
    "text", ["❤️ cancela lembrete de água", "✔️ meta correr", "한국어 é", "ç ñ ü ß"]
)
def test_strip_accents_is_length_preserving_for_any_input(text: str) -> None:
    """U+FE0F (❤️, ✔️, ☀️) is a combining mark: dropping it shifted every later slice."""
    from alfred.parsing import strip_accents

    assert len(strip_accents(text)) == len(text)


def test_to_amount_leading_zero_is_a_decimal() -> None:
    from alfred.parsing import to_amount

    assert to_amount("0.500") == 0.5
    assert to_amount("1.500") == 1500


# ── Sprint 2: expense-query windows and commands ──────────────────────────────

from datetime import date  # noqa: E402

from alfred.parsing import (  # noqa: E402
    DELETE_LAST_EXPENSE_RE,
    TOP_CATEGORIES_RE,
    parse_last_n,
    parse_period,
)

_TODAY = date(2026, 9, 29)  # a Tuesday


@pytest.mark.parametrize(
    ("text", "kind", "start", "end"),
    [
        ("quanto gastei ontem?", "yesterday", date(2026, 9, 28), date(2026, 9, 29)),
        ("gastos de hoje", "today", date(2026, 9, 29), date(2026, 9, 30)),
        ("gastos da semana passada", "last_week", date(2026, 9, 21), date(2026, 9, 28)),
        ("quanto gastei esta semana", "current_week", date(2026, 9, 28), None),
        ("gastos do mes passado", "last_month", date(2026, 8, 1), date(2026, 9, 1)),
        ("resumo de setembro", "month", date(2026, 9, 1), None),
        ("resumo de agosto", "month", date(2026, 8, 1), date(2026, 9, 1)),
        ("gastos de agosto 2025", "month", date(2025, 8, 1), date(2025, 9, 1)),
        ("quanto gastei este ano?", "year", date(2026, 1, 1), None),
        ("hoeveel vorig jaar", "year", date(2025, 1, 1), date(2026, 1, 1)),
        ("wat gaf ik gisteren uit", "yesterday", date(2026, 9, 28), date(2026, 9, 29)),
    ],
)
def test_parse_period(text, kind, start, end):
    w = parse_period(text, _TODAY)
    assert (w.kind, w.start, w.end) == (kind, start, end)


def test_parse_period_none_and_december_rollover():
    assert parse_period("qual e o meu saldo", _TODAY) is None
    # a named month later in the year means last year's occurrence
    w = parse_period("resumo de dezembro", _TODAY)
    assert (w.start, w.end) == (date(2025, 12, 1), date(2026, 1, 1))


@pytest.mark.parametrize(
    ("text", "n"),
    [
        ("ultimas 5 despesas", 5),
        ("ultimas despesas", 5),
        ("last 3 expenses", 3),
        ("ultimas 99 despesas", 20),
        ("laatste 4 uitgaven", 4),
    ],
)
def test_parse_last_n(text, n):
    assert parse_last_n(text) == n


@pytest.mark.parametrize("text", ["gastei 5 no supermercado", "ultimas notas", "treinos"])
def test_parse_last_n_ignores_other_text(text):
    assert parse_last_n(text) is None


@pytest.mark.parametrize(
    "text", ["apaga", "apagar ultima", "apaga a ultima despesa", "delete last expense", "verwijder"]
)
def test_delete_last_expense_matches(text):
    assert DELETE_LAST_EXPENSE_RE.match(text)


@pytest.mark.parametrize(
    "text", ["apaga treino", "apaga a tarefa 3", "delete my workout", "apagar lembrete cafe"]
)
def test_delete_last_expense_does_not_steal_other_deletes(text):
    assert not DELETE_LAST_EXPENSE_RE.match(text)


def test_top_categories_regex():
    assert TOP_CATEGORIES_RE.match("top categorias este mes")
    assert not TOP_CATEGORIES_RE.match("top gastos")
