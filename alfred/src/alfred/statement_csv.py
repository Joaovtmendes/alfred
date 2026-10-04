# ruff: noqa: E501
"""V2-05 — read a bank statement export (pure functions, no database, no network).

The four Dutch banks export different files, and the exports change without notice, so the
reader recognises a file by its *columns*, not by its name: ING (NL and EN headers), Rabobank,
ABN AMRO (tab-separated, with or without a header row) and bunq, plus a generic reader for any
file that has a date, an amount and a name or description. Delimiter, encoding, date and
number formats are detected. Nothing about the account is kept: the account columns are never
read, and IBANs inside a description are masked.

The formats here come from the banks' public export layouts as far as they are known; confirm
them against a real export before relying on a bank (the tests carry one sample per bank).

What a statement line becomes: ``Row(day, amount, counterparty, description)`` with a signed
amount (negative = money out). ``external_id`` is a stable hash of the line, so the same line
read twice (a repeated upload, overlapping exports) is the same entry.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from alfred.parsing import strip_accents

MAX_BYTES = 1_000_000  # about 8,000 lines: a year of a busy account
MAX_ROWS = 2000
MAX_TEXT = 120
_IBAN = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b")
_FORMULA_LEAD = "=+@-\t\r  "


@dataclass(frozen=True)
class Row:
    day: date
    amount: Decimal  # negative = money out
    counterparty: str
    description: str
    external_id: str = ""


@dataclass
class Parsed:
    bank: str = ""
    rows: list[Row] = field(default_factory=list)
    skipped: int = 0  # lines that could not be read (no date, no amount, impossible date)
    error: str = ""  # "empty" | "format" | "too_big" | "too_many" | "not_text"


# ── text, numbers, dates ──────────────────────────────────────────────────────


def decode(raw: bytes) -> str | None:
    """UTF-8 (with or without BOM), else Windows-1252; None for binary garbage."""
    if b"\x00" in raw[:4096]:
        return None
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return None


def clean_text(value: str, limit: int = MAX_TEXT) -> str:
    """One line, IBANs masked, no leading formula character (so a spreadsheet export is safe)."""
    text = _IBAN.sub("[IBAN]", " ".join(str(value or "").split()))
    return text.lstrip(_FORMULA_LEAD)[:limit].strip()


def parse_amount(text: str) -> Decimal | None:
    """ "-23,45" · "+976,55" · "2.500,00" · "1,234.56" · "23.45" · "−5,00" (unicode minus)."""
    s = str(text or "").strip().replace("−", "-").replace(" ", "").replace(" ", "")
    s = s.replace("€", "").replace("EUR", "").replace("eur", "")
    if not s:
        return None
    sign = Decimal(1)
    if s.startswith("-"):
        sign, s = Decimal(-1), s[1:]
    elif s.startswith("+"):
        s = s[1:]
    if s.endswith("-"):  # "12,50-" (some exports)
        sign, s = Decimal(-1), s[:-1]
    if not s or not re.fullmatch(r"[\d.,]+", s):
        return None
    last_dot, last_comma = s.rfind("."), s.rfind(",")
    if last_dot >= 0 and last_comma >= 0:  # both: the last one is the decimal mark
        decimal_mark = "." if last_dot > last_comma else ","
    elif last_dot >= 0 or last_comma >= 0:
        mark = "." if last_dot >= 0 else ","
        if s.count(mark) > 1:
            decimal_mark = ""  # "1.234.567": thousands only
        else:
            digits_after = len(s) - s.rfind(mark) - 1
            decimal_mark = mark if digits_after != 3 else ""  # "1.234" reads as one thousand
    else:
        decimal_mark = ""
    if decimal_mark:
        whole, _, frac = s.rpartition(decimal_mark)
        s = re.sub(r"[.,]", "", whole) + "." + frac
    else:
        s = re.sub(r"[.,]", "", s)
    try:
        return sign * Decimal(s)
    except InvalidOperation:
        return None


_DATE_PATTERNS = (
    (re.compile(r"^(\d{4})(\d{2})(\d{2})$"), (0, 1, 2)),
    (re.compile(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})"), (0, 1, 2)),
    (re.compile(r"^(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})"), (2, 1, 0)),
)


def parse_date(text: str, today: date | None = None) -> date | None:
    s = str(text or "").strip()
    for rx, (yi, mi, di) in _DATE_PATTERNS:
        if m := rx.match(s):
            g = m.groups()
            try:
                d = date(int(g[yi]), int(g[mi]), int(g[di]))
            except ValueError:
                return None
            limit = (today or date.today()) + timedelta(days=1)
            return d if date(2000, 1, 1) <= d <= limit else None
    return None


# ── columns ───────────────────────────────────────────────────────────────────


def _norm(header: str) -> str:
    return " ".join(strip_accents(str(header or "").lower()).replace("_", " ").split())


# column synonyms, normalised
_DATE_COLS = (
    "datum", "date", "transactiedatum", "boekdatum", "boekingsdatum", "transaction date", "booking date",
)  # fmt: skip
_AMOUNT_COLS = (
    "bedrag", "bedrag (eur)", "amount", "amount (eur)", "transactiebedrag", "waarde", "value",
)  # fmt: skip
_DEBIT_COLS = ("af", "debit", "debet")
_CREDIT_COLS = ("bij", "credit")
_DIRECTION_COLS = ("af bij", "debit/credit", "debit credit", "af/bij")
_NAME_COLS = (
    "naam / omschrijving", "name / description", "naam tegenpartij", "name", "naam", "counterparty name",
    "tegenpartij", "counterparty", "begunstigde", "payee",
)  # fmt: skip
_DESC_COLS = (
    "omschrijving", "omschrijving-1", "description", "mededelingen", "notifications", "details",
    "betalingskenmerk", "reference",
)  # fmt: skip


def _find(header: list[str], names: tuple[str, ...]) -> int | None:
    for n in names:
        if n in header:
            return header.index(n)
    return None


def _all_idx(header: list[str], names: tuple[str, ...]) -> list[int]:
    return [i for i, h in enumerate(header) if h in names]


def _delimiter(text: str) -> str:
    head = "\n".join(text.splitlines()[:5])
    counts = {d: head.count(d) for d in (";", "\t", ",")}
    best = max(counts, key=lambda d: counts[d])
    return best if counts[best] else ","


def detect_bank(header: list[str]) -> str:
    h = set(header)
    if "af bij" in h or "debit/credit" in h:
        return "ing"
    if "iban/bban" in h or "tegenrekening iban/bban" in h:
        return "rabobank"
    if "transactiebedrag" in h and "rentedatum" in h:
        return "abn"
    if "interest date" in h and "amount" in h:
        return "bunq"
    return "generic"


# ABN AMRO descriptions: "BEA   NR:XB1234 01.10.26/14.22 Albert Heijn 1234 AMSTERDAM,PAS123",
# "SEPA Overboeking  IBAN: ... BIC: ... Naam: Werkgever BV Omschrijving: Salaris"
_ABN_NAAM = re.compile(
    r"Naam:\s*(.+?)(?:\s+(?:Omschrijving|Kenmerk|Betalingskenmerk|IBAN|BIC|Machtiging)\s*:|$)"
)
_ABN_CARD = re.compile(
    r"^(?:BEA|GEA)\s+NR:\S+\s+\d{2}\.\d{2}\.\d{2}/\d{2}\.\d{2}\s+(.+?)(?:,PAS\d+)?$"
)
_ABN_OMSCHR = re.compile(r"Omschrijving:\s*(.+?)(?:\s+(?:Kenmerk|Betalingskenmerk|IBAN|BIC)\s*:|$)")


_ABN_SEPA = re.compile(r"/(NAME|REMI|EREF|IBAN|BIC|TRTP|CSID|MARF|ORDP|BENM|ID|ADDR)/")


def _sepa_fields(d: str) -> dict[str, str]:
    """``/TRTP/SEPA OVERBOEKING/IBAN/NL../NAME/Jan/REMI/Huur`` → {"TRTP": ..., "NAME": ..., ...}."""
    parts = _ABN_SEPA.split(d)  # ["", KEY, value, KEY, value, ...]
    return {parts[i]: parts[i + 1].strip("/ ") for i in range(1, len(parts) - 1, 2)}


def _abn_split(desc: str) -> tuple[str, str]:
    d = " ".join(desc.split())
    if m := _ABN_CARD.match(d):
        return m.group(1), d
    if d.startswith("/TRTP/"):
        f = _sepa_fields(d)
        if f.get("NAME"):
            return f["NAME"], f.get("REMI") or d
    name = _ABN_NAAM.search(d)
    omschr = _ABN_OMSCHR.search(d)
    if name:
        return name.group(1), (omschr.group(1) if omschr else d)
    return d[:40], d


# ── the reader ────────────────────────────────────────────────────────────────


def _id(row_day: date, amount: Decimal, name: str, desc: str, nth: int) -> str:
    key = f"{row_day.isoformat()}|{amount}|{name.lower()}|{desc.lower()[:80]}|{nth}"
    return hashlib.sha256(key.encode()).hexdigest()[:32]


def _rows_of(text: str, delim: str) -> list[list[str]]:
    return [
        r
        for r in csv.reader(io.StringIO(text), delimiter=delim, quotechar='"')
        if any(c.strip() for c in r)
    ]


def parse_statement(raw: bytes, today: date | None = None) -> Parsed:
    """Read a statement file. ``Parsed.error`` is set when nothing usable was found."""
    if len(raw) > MAX_BYTES:
        return Parsed(error="too_big")
    text = decode(raw)
    if text is None:
        return Parsed(error="not_text")
    if not text.strip():
        return Parsed(error="empty")
    delim = _delimiter(text)
    table = _rows_of(text, delim)
    if not table:
        return Parsed(error="empty")

    # ABN AMRO tab export: no header; account, currency, date, start, end, interest date, amount, description
    first = table[0]
    if (
        len(first) >= 8
        and len(first[1].strip()) == 3
        and re.fullmatch(r"\d{8}", first[2].strip() or "")
    ):
        return _read_abn_headerless(table, today)

    header = [_norm(c) for c in first]
    bank = detect_bank(header)
    date_i = _find(header, _DATE_COLS)
    amount_i = _find(header, _AMOUNT_COLS)
    debit_i = _find(header, _DEBIT_COLS)
    credit_i = _find(header, _CREDIT_COLS)
    dir_i = _find(header, _DIRECTION_COLS)
    name_i = _find(header, _NAME_COLS)
    desc_idx = _all_idx(header, _DESC_COLS)
    if date_i is None or (amount_i is None and debit_i is None and credit_i is None):
        return Parsed(error="format")
    if name_i is None and not desc_idx:
        return Parsed(error="format")

    out = Parsed(bank=bank)
    seen: Counter[str] = Counter()
    for cells in table[1:]:
        if len(out.rows) >= MAX_ROWS:
            return Parsed(error="too_many")

        def cell(i: int | None, _cells: list[str] = cells) -> str:
            return _cells[i].strip() if i is not None and i < len(_cells) else ""

        day = parse_date(cell(date_i), today)
        if amount_i is not None:
            amount = parse_amount(cell(amount_i))
            if amount is not None and dir_i is not None:
                flag = _norm(cell(dir_i))
                if flag in ("af", "debit", "d"):
                    amount = -abs(amount)
                elif flag in ("bij", "credit", "c"):
                    amount = abs(amount)
        else:
            debit, credit = parse_amount(cell(debit_i)), parse_amount(cell(credit_i))
            amount = (credit or Decimal(0)) - (debit or Decimal(0)) if (debit or credit) else None
        if day is None or amount is None or amount == 0:
            out.skipped += 1
            continue
        raw_desc = " ".join(cell(i) for i in desc_idx if cell(i))
        if bank == "abn" and name_i is None:  # the counterparty hides inside the description
            raw_name, raw_desc = _abn_split(raw_desc)
        else:
            raw_name = cell(name_i)
        name = clean_text(raw_name)
        desc = clean_text(raw_desc)
        if not name:
            name = desc[:40]
        base = (day.isoformat(), str(amount), name.lower(), desc.lower()[:80])
        seen["|".join(base)] += 1
        out.rows.append(
            Row(day, amount, name, desc, _id(day, amount, name, desc, seen["|".join(base)]))
        )
    if not out.rows and out.skipped == 0:
        return Parsed(error="empty")
    if not out.rows:
        return Parsed(error="format", skipped=out.skipped)
    return out


def _read_abn_headerless(table: list[list[str]], today: date | None) -> Parsed:
    out = Parsed(bank="abn")
    seen: Counter[str] = Counter()
    for cells in table:
        if len(out.rows) >= MAX_ROWS:
            return Parsed(error="too_many")
        if len(cells) < 8:
            out.skipped += 1
            continue
        day, amount = parse_date(cells[2], today), parse_amount(cells[6])
        if day is None or amount is None or amount == 0:
            out.skipped += 1
            continue
        name, desc = _abn_split(cells[7])
        name, desc = clean_text(name), clean_text(desc)
        key = f"{day.isoformat()}|{amount}|{name.lower()}|{desc.lower()[:80]}"
        seen[key] += 1
        out.rows.append(Row(day, amount, name, desc, _id(day, amount, name, desc, seen[key])))
    if not out.rows:
        return Parsed(error="format", skipped=out.skipped)
    return out


# ── categories ────────────────────────────────────────────────────────────────

_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("supermarkt", ("albert heijn", "ah to go", "jumbo", "lidl", "aldi", "dirk van", "plus ", "spar ", "coop ", "ekoplaza", "picnic", "vomar", "hoogvliet", "deka", "marqt")),
    ("restaurant", ("thuisbezorgd", "uber eats", "deliveroo", "mcdonald", "burger king", "kfc", "starbucks", "subway", "domino", "restaurant", "cafe ", "café", "bar ", "bakkerij", "lunchroom", "new york pizza")),
    ("transport", ("ns groep", "ns reizigers", "ov-chipkaart", "ov chipkaart", "gvb", "ret ", "htm", "arriva", "uber", "bolt", "shell", "bp ", "tango", "tinq", "total energies", "q-park", "parkeer", "anwb", "swapfiets", "transavia", "klm", "ryanair", "easyjet", "flixbus")),
    ("gezondheid", ("apotheek", "huisarts", "tandarts", "fysio", "ziekenhuis", "zilveren kruis", "cz groep", "vgz", "menzis", "ditzo", "dsw zorg", "kruidvat", "etos")),
    ("entertainment", ("pathe", "pathé", "bioscoop", "steam", "playstation", "nintendo", "ticketmaster", "eventbrite", "museum", "concert")),
    ("wonen", ("huur", "vve ", "hypotheek", "vattenfall", "eneco", "essent", "greenchoice", "ziggo", "kpn", "odido", "t-mobile", "vodafone", "simpel", "vitens", "waternet", "pwn", "gemeente", "waterschap", "ikea", "gamma", "praxis", "hornbach", "woningcorporatie", "brandweer", "nationale-nederlanden")),
    ("kleding", ("zalando", "h&m", "h & m", "zara", "primark", "c&a", "wehkamp", "nike", "adidas", "uniqlo", "decathlon", "bijenkorf")),
    ("abonnement", ("netflix", "spotify", "disney", "videoland", "hbo", "amazon prime", "apple.com", "google one", "youtube", "icloud", "microsoft", "adobe", "dropbox", "openai", "anthropic", "audible", "storytel", "basic-fit", "basic fit", "sportcity")),
    ("inkomen", ("salaris", "salary", "loon", "uitkering", "belastingdienst toeslag", "toeslagen", "dividend", "rente")),
)  # fmt: skip


def categorize(
    counterparty: str, description: str, amount: Decimal, overrides: dict[str, str] | None = None
) -> str:
    """Category of a line: the member's own corrections first, then known names, else "overig"."""
    if amount > 0:
        return "inkomen"
    hay = strip_accents(f"{counterparty} {description}".lower())
    name = strip_accents(counterparty.lower())
    for merchant, cat in (overrides or {}).items():
        if merchant and merchant in name:
            return cat
    for cat, words in _RULES:
        if cat == "inkomen":
            continue
        for w in words:
            if strip_accents(w) in f" {hay} ":
                return cat
    return "overig"
