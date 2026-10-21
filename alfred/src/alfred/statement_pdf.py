# ruff: noqa: E501
"""F15-01 — a bank statement sent as a PDF: text is read locally, the model only sees masked text.

The PDF is never sent to the model. We pull the text out ourselves (``pypdf``), mask every IBAN,
and only then ask the model to list the transactions. The model's answer is untrusted: every row
is rebuilt here (date, amount, short texts) and shown as a draft; nothing is written before the
member confirms, and the file is dropped as soon as it is read.

Limits: 5 MB, 10 pages, 300 lines of text sent to the model. Scanned PDFs (no text) are refused.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from alfred import statement_csv as csvp

MAX_PDF_BYTES = 5_000_000
MAX_PAGES = 10
MAX_LINES = 300
MAX_CHARS = 30_000

# NL91 ABNA 0417 1643 00 — with or without the spaces a bank prints
_IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]{4}){2,7}(?:[ ]?[A-Z0-9]{1,4})?\b")
_DATE_LINE = re.compile(r"\b\d{1,2}[-/.]\d{1,2}[-/.](?:\d{2}|\d{4})\b|\b\d{4}-\d{2}-\d{2}\b")
_AMOUNT_LINE = re.compile(r"[-+]?\d{1,3}(?:[.,]\d{3})*[.,]\d{2}\b|[-+]?\d+[.,]\d{2}\b")
_STATEMENT_WORDS = re.compile(
    r"afschrift|rekening|saldo|iban|bijschrijving|afschrijving|statement|balance|account|"
    r"extrato|conta|releve|kontoauszug|kontostand",
    re.I,
)


def mask_ibans(text: str) -> str:
    """Every IBAN becomes ``[IBAN]`` before anything leaves this process."""
    return _IBAN.sub("[IBAN]", text)


@dataclass
class PdfInfo:
    pages: int = 0
    text: str = ""
    encrypted: bool = False
    broken: bool = False


def read_pdf(data: bytes) -> PdfInfo:
    """Page count and the text of the first ``MAX_PAGES`` pages; never raises."""
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            return PdfInfo(encrypted=True)
        pages = len(reader.pages)
        text = "\n".join((p.extract_text() or "") for p in reader.pages[:MAX_PAGES])
        return PdfInfo(pages=pages, text=text.strip())
    except Exception:  # damaged or exotic file
        return PdfInfo(broken=True)


def looks_like_statement(text: str) -> bool:
    """Several lines carry both a date and an amount, and the page talks about an account."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    both = sum(1 for ln in lines if _DATE_LINE.search(ln) and _AMOUNT_LINE.search(ln))
    return both >= 3 and bool(_STATEMENT_WORDS.search(text))


def prepare(text: str) -> str:
    """The text the model gets: masked, trimmed to the lines that matter, size-capped."""
    masked = mask_ibans(text)
    lines = [" ".join(ln.split()) for ln in masked.splitlines() if ln.strip()]
    return "\n".join(lines[:MAX_LINES])[:MAX_CHARS]


def rows_from_reading(reading: object, today: date) -> csvp.Parsed:
    """Rebuild transactions from the model's JSON; anything odd is skipped, never trusted."""
    parsed = csvp.Parsed(bank="generic")
    rows = reading.get("rows") if isinstance(reading, dict) else None
    if not isinstance(rows, list):
        return csvp.Parsed(error="format")
    seen: dict[str, int] = {}
    for item in rows:
        if len(parsed.rows) >= csvp.MAX_ROWS:
            return csvp.Parsed(error="too_many")
        if not isinstance(item, dict):
            parsed.skipped += 1
            continue
        raw_day = item.get("date")
        day = csvp.parse_date(str(raw_day), today) if isinstance(raw_day, str) else None
        amount = item.get("amount")
        if isinstance(amount, bool) or not isinstance(amount, int | float | str):
            parsed.skipped += 1
            continue
        value = csvp.parse_amount(str(amount))
        if day is None or value is None or value == 0 or abs(value) >= Decimal(10_000_000):
            parsed.skipped += 1
            continue
        name = csvp.clean_text(str(item.get("counterparty") or ""))
        desc = csvp.clean_text(str(item.get("description") or ""))
        if not name:
            name = desc[:40]
        if not name:
            parsed.skipped += 1
            continue
        key = "|".join((day.isoformat(), str(value), name.lower(), desc.lower()[:80]))
        seen[key] = seen.get(key, 0) + 1
        parsed.rows.append(
            csvp.Row(day, value, name, desc, csvp._id(day, value, name, desc, seen[key]))
        )
    if not parsed.rows:
        return csvp.Parsed(
            error="empty" if not parsed.skipped else "format", skipped=parsed.skipped
        )
    return parsed
