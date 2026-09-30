#!/usr/bin/env python3
"""LLM regression set for expense extraction (the parser behind the router).

Usage (needs a real key; costs a few cents; NOT run in CI):

    LLM_API_KEY=... python scripts/eval_llm.py            # all cases
    LLM_API_KEY=... python scripts/eval_llm.py --lang nl  # one language

Each case pins only what must stay stable: amount, type, and (when listed) currency /
"is not an expense". Categories are checked loosely on purpose. Grow this file whenever
the hard test finds a phrasing the parser got wrong — it is the safety net for any
future change of prompt or model (roadmap 1.6).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from alfred.llm import extract_expense  # noqa: E402

# (lang, text, expected) — expected None = must NOT be recorded as a transaction
CASES: list[tuple[str, str, dict | None]] = [
    ("pt", "Jumbo 23,40", {"amount": 23.40, "type": "expense"}),
    ("pt", "gastei 12 no café", {"amount": 12.0, "type": "expense"}),
    ("pt", "almoço 15 euros ontem", {"amount": 15.0, "type": "expense"}),
    ("pt", "recebi 1500 de salário", {"amount": 1500.0, "type": "income"}),
    ("pt", "uber 8,5", {"amount": 8.5, "type": "expense"}),
    ("pt", "farmácia 10€", {"amount": 10.0, "type": "expense"}),
    ("pt", "aluguel 1200", {"amount": 1200.0, "type": "expense"}),
    ("pt", "bom dia, tudo bem?", None),
    ("pt", "quanto gastei este mês?", None),
    ("pt", "gastei 20 dólares no uber", {"amount": 20.0, "currency": "USD"}),
    ("nl", "Albert Heijn 34,20", {"amount": 34.20, "type": "expense"}),
    ("nl", "ik heb 9 euro betaald voor de bioscoop", {"amount": 9.0, "type": "expense"}),
    ("nl", "salaris 2800 ontvangen", {"amount": 2800.0, "type": "income"}),
    ("nl", "hoe laat is het?", None),
    ("nl", "OV-chipkaart 20", {"amount": 20.0, "type": "expense"}),
    ("en", "Starbucks 5.80", {"amount": 5.80, "type": "expense"}),
    ("en", "spent 42 on groceries", {"amount": 42.0, "type": "expense"}),
    ("en", "got paid 3000 today", {"amount": 3000.0, "type": "income"}),
    ("en", "what can you do?", None),
    ("en", "taxi 18.50 yesterday", {"amount": 18.50, "type": "expense"}),
    ("fr", "courses 27,90", {"amount": 27.90, "type": "expense"}),
    ("fr", "j'ai dépensé 14 euros au restaurant", {"amount": 14.0, "type": "expense"}),
    ("de", "Supermarkt 31,10", {"amount": 31.10, "type": "expense"}),
    ("de", "ich habe 7 Euro für Kaffee bezahlt", {"amount": 7.0, "type": "expense"}),
    ("de", "wie geht es dir?", None),
]


def check(got: dict | None, expected: dict | None) -> str | None:
    """Return None when ``got`` satisfies ``expected``, else a short reason."""
    if expected is None:
        return None if got is None else f"expected no transaction, got {got.get('amount')}"
    if got is None:
        return "expected a transaction, got none"
    if "amount" in expected and abs(float(got["amount"]) - expected["amount"]) > 0.005:
        return f"amount {got['amount']} != {expected['amount']}"
    if "type" in expected and got.get("type", "expense") != expected["type"]:
        return f"type {got.get('type')} != {expected['type']}"
    if "currency" in expected and got.get("currency") != expected["currency"]:
        return f"currency {got.get('currency')} != {expected['currency']}"
    return None


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lang", help="only this language (pt|nl|en|fr|de)")
    args = ap.parse_args()
    if not os.environ.get("LLM_API_KEY"):
        print("LLM_API_KEY is not set — nothing to evaluate.", file=sys.stderr)
        return 2
    cases = [c for c in CASES if not args.lang or c[0] == args.lang]
    failures = 0
    for lang, text, expected in cases:
        got = await extract_expense(text, lang=lang)
        why = check(got, expected)
        if why:
            failures += 1
        print(f"{'FAIL' if why else 'ok  '} [{lang}] {text!r}" + (f"  → {why}" if why else ""))
    print(f"\n{len(cases) - failures}/{len(cases)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
