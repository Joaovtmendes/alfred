# ruff: noqa: E501
"""V2-05 — the statement reader: one sample per bank layout, plus the ways a file can be wrong."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from alfred import statement_csv as sc

TODAY = date(2026, 10, 14)

ING = (
    '"Datum","Naam / Omschrijving","Rekening","Tegenrekening","Code","Af Bij","Bedrag (EUR)","Mutatiesoort","Mededelingen"\n'
    '"20261012","Albert Heijn 1234 Utrecht","NL01INGB0001234567","","BA","Af","23,45","Betaalautomaat","Pasvolgnr: 012 Transactie: 1 Term: AB12"\n'
    '"20261010","Werkgever BV","NL01INGB0001234567","NL02ABNA0123456789","OV","Bij","2.500,00","Overschrijving","Salaris oktober"\n'
)
RABO = (
    '"IBAN/BBAN","Munt","BIC","Volgnr","Datum","Rentedatum","Bedrag","Saldo na trn","Tegenrekening IBAN/BBAN","Naam tegenpartij","Naam uiteindelijke partij","Naam initiërende partij","BIC tegenpartij","Code","Batch ID","Transactiereferentie","Machtigingskenmerk","Incassant ID","Betalingskenmerk","Omschrijving-1","Omschrijving-2","Omschrijving-3","Reden retour","Oorspr bedrag","Oorspr munt","Koers"\n'
    '"NL01RABO0123456789","EUR","RABONL2U","1","2026-10-11","2026-10-11","-12,50","+100,00","NL02ABNA0123456789","Thuisbezorgd","","","ABNANL2A","ba","","","","","","Bestelling 123","","","","","",""\n'
    '"NL01RABO0123456789","EUR","RABONL2U","2","2026-10-09","2026-10-09","+976,55","+1076,55","NL03INGB0001111111","Belastingdienst","","","INGBNL2A","ov","","","","","","Toeslag","","","","","",""\n'
)
ABN = (
    "123456789\tEUR\t20261012\t1000,00\t977,00\t20261012\t-23,00\tBEA   NR:AB12CD   12.10.26/13.45 Jumbo Utrecht,PAS123 NLD\n"
    "123456789\tEUR\t20261009\t977,00\t3477,00\t20261009\t2500,00\t/TRTP/SEPA OVERBOEKING/IBAN/NL02INGB0001234567/BIC/INGBNL2A/NAME/WERKGEVER BV/REMI/Salaris\n"
)
BUNQ = (
    "Date,Interest Date,Amount,Account,Counterparty,Name,Description\n"
    "2026-10-12,2026-10-12,-8.90,NL01BUNQ0123456789,NL02INGB0001234567,Spotify,Premium\n"
    "2026-10-11,2026-10-11,1234.56,NL01BUNQ0123456789,NL02INGB0001234567,Jan Jansen,Terugbetaling\n"
)


def _p(text: str, enc: str = "utf-8") -> sc.Parsed:
    return sc.parse_statement(text.encode(enc), TODAY)


def test_ing_layout():
    p = _p(ING)
    assert p.error == "" and p.bank == "ing" and len(p.rows) == 2
    out, inc = p.rows
    assert (out.day, out.amount, out.counterparty) == (
        date(2026, 10, 12),
        Decimal("-23.45"),
        "Albert Heijn 1234 Utrecht",
    )
    assert (inc.amount, inc.counterparty) == (Decimal("2500.00"), "Werkgever BV")


def test_rabobank_layout_signed_amounts():
    p = _p(RABO)
    assert p.bank == "rabobank" and [r.amount for r in p.rows] == [
        Decimal("-12.50"),
        Decimal("976.55"),
    ]
    assert p.rows[0].counterparty == "Thuisbezorgd" and "Bestelling" in p.rows[0].description


def test_abn_amro_headerless_tab_export():
    p = _p(ABN)
    assert p.bank == "abn" and len(p.rows) == 2
    assert p.rows[0].amount == Decimal("-23.00") and p.rows[0].day == date(2026, 10, 12)
    assert "Jumbo" in p.rows[0].counterparty
    assert p.rows[1].amount == Decimal("2500.00") and "WERKGEVER" in p.rows[1].counterparty.upper()


def test_bunq_layout_dot_decimals():
    p = _p(BUNQ)
    assert p.bank == "bunq" and [r.amount for r in p.rows] == [Decimal("-8.90"), Decimal("1234.56")]


def test_generic_layout_with_debit_credit_columns():
    p = _p("Date;Description;Debit;Credit\n12-10-2026;Coffee;3,20;\n10-10-2026;Refund;;15,00\n")
    assert [r.amount for r in p.rows] == [Decimal("-3.20"), Decimal("15.00")]


@pytest.mark.parametrize(
    ("text", "want"),
    [
        ("-23,45", "-23.45"),
        ("+976,55", "976.55"),
        ("2.500,00", "2500.00"),
        ("1,234.56", "1234.56"),
        ("23.45", "23.45"),
        ("1.234", "1234"),
        ("−23,45", "-23.45"),
        ("23,45-", "-23.45"),
        ("€ 12,00", "12.00"),
    ],
)
def test_parse_amount(text, want):
    assert sc.parse_amount(text) == Decimal(want)


@pytest.mark.parametrize("text", ["", "abc", "12,34,56x", "--5"])
def test_parse_amount_rejects_garbage(text):
    assert sc.parse_amount(text) is None


def test_parse_date_formats_and_limits():
    assert sc.parse_date("20261012", TODAY) == date(2026, 10, 12)
    assert sc.parse_date("2026-10-12", TODAY) == date(2026, 10, 12)
    assert sc.parse_date("12-10-2026", TODAY) == date(2026, 10, 12)
    assert sc.parse_date("12/10/2026", TODAY) == date(2026, 10, 12)
    assert sc.parse_date("12-10-1999", TODAY) is None  # before 2000
    assert sc.parse_date("20301012", TODAY) is None  # in the future
    assert sc.parse_date("31-02-2026", TODAY) is None  # impossible day


def test_formula_injection_and_iban_are_neutralised():
    csv = "Date,Name,Amount,Description\n2026-10-12,=HYPERLINK(\"http://x\"),-5.00,+cmd|' /C calc'!A0 NL02INGB0001234567\n"
    r = _p(csv).rows[0]
    assert not r.counterparty.startswith(("=", "+", "-", "@"))
    assert not r.description.startswith(("=", "+", "-", "@"))
    assert "NL02INGB0001234567" not in r.description and "[IBAN]" in r.description


def test_text_is_capped():
    r = _p("Date,Name,Amount\n2026-10-12," + "x" * 500 + ",-1.00\n").rows[0]
    assert len(r.counterparty) <= 120


def test_bad_lines_are_skipped_and_counted():
    p = _p("Date,Name,Amount\n2026-10-12,Ok,-1.00\nnot a date,Bad,-2.00\n2026-10-11,NoAmount,\n")
    assert len(p.rows) == 1 and p.skipped == 2


def test_cp1252_file_is_read():
    p = _p("Date;Name;Amount\n12-10-2026;Café Zürich;-4,50\n", "cp1252")
    assert p.rows[0].counterparty == "Café Zürich"


def test_binary_and_empty_and_wrong_format():
    assert sc.parse_statement(b"\x00\x01\x02\xff\xfe\x00" * 50, TODAY).error == "not_text"
    assert sc.parse_statement(b"", TODAY).error == "empty"
    assert sc.parse_statement(b"hello\nworld\n", TODAY).error == "format"
    assert sc.parse_statement(b"a" * (sc.MAX_BYTES + 1), TODAY).error == "too_big"


def test_too_many_rows():
    body = "Date,Name,Amount\n" + "".join(
        f"2026-10-12,Shop{i},-1.00\n" for i in range(sc.MAX_ROWS + 1)
    )
    assert _p(body).error == "too_many"


def test_external_id_is_stable_and_separates_identical_lines():
    twice = "Date,Name,Amount\n2026-10-12,Coffee,-3.00\n2026-10-12,Coffee,-3.00\n"
    a, b = _p(twice).rows
    assert a.external_id != b.external_id  # two real coffees the same day
    assert [r.external_id for r in _p(twice).rows] == [
        a.external_id,
        b.external_id,
    ]  # same file, same ids
    assert all(len(r.external_id) == 32 for r in (a, b))


def test_categorize():
    jumbo = Decimal("-10")
    assert sc.categorize("Albert Heijn 1234", "", jumbo) == "supermarkt"
    assert sc.categorize("Jumbo", "", jumbo) == "supermarkt"
    assert sc.categorize("Werkgever BV", "Salaris", Decimal("2500")) == "inkomen"
    assert sc.categorize("Onbekend BV", "", jumbo) == "overig"
    assert sc.categorize("Onbekend BV", "", jumbo, {"onbekend bv": "kleding"}) == "kleding"
    assert (
        sc.categorize("Jumbo", "", jumbo, {"jumbo": "entertainment"}) == "entertainment"
    )  # the member wins
