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
    # ── V1-20: ampliação (30/09) — frases naturais por língua, negativos e moedas ──
    ("pt", "paguei 35 de gasolina", {"amount": 35.0, "type": "expense"}),
    ("pt", "mercado 87,30 hoje", {"amount": 87.3, "type": "expense"}),
    ("pt", "recebi 200 do freela", {"amount": 200.0, "type": "income"}),
    ("pt", "netflix 13,99", {"amount": 13.99, "type": "expense"}),
    ("pt", "cinema 24 euros", {"amount": 24.0, "type": "expense"}),
    ("pt", "dentista 80", {"amount": 80.0, "type": "expense"}),
    ("pt", "gastei 5,50 no pão", {"amount": 5.5, "type": "expense"}),
    ("pt", "me pagaram 450 de aluguel", {"amount": 450.0, "type": "income"}),
    ("pt", "pizza 18 ontem à noite", {"amount": 18.0, "type": "expense"}),
    ("pt", "zara 55", {"amount": 55.0, "type": "expense"}),
    ("pt", "pedágio 6,20", {"amount": 6.2, "type": "expense"}),
    ("pt", "academia 39,90", {"amount": 39.9, "type": "expense"}),
    ("pt", "conta de luz 120", {"amount": 120.0, "type": "expense"}),
    ("pt", "ganhei 50 de presente", {"amount": 50.0, "type": "income"}),
    ("pt", "gastei 1.250 na reforma", {"amount": 1250.0, "type": "expense"}),
    ("pt", "obrigado!", None),
    ("pt", "o que você faz?", None),
    ("pt", "qual é a capital da França?", None),
    ("pt", "me lembra de pagar a luz amanhã", None),
    ("pt", "quanto falta para a meta?", None),
    ("nl", "Jumbo 52,10", {"amount": 52.1, "type": "expense"}),
    ("nl", "ik heb 12,50 uitgegeven aan lunch", {"amount": 12.5, "type": "expense"}),
    ("nl", "huur betaald 950", {"amount": 950.0, "type": "expense"}),
    ("nl", "kreeg 150 terug van de belasting", {"amount": 150.0, "type": "income"}),
    ("nl", "Spotify 10,99", {"amount": 10.99, "type": "expense"}),
    ("nl", "tanken 64", {"amount": 64.0, "type": "expense"}),
    ("nl", "bonus ontvangen 500", {"amount": 500.0, "type": "income"}),
    ("nl", "koffie 3,20", {"amount": 3.2, "type": "expense"}),
    ("nl", "zorgverzekering 142,50", {"amount": 142.5, "type": "expense"}),
    ("nl", "dank je wel", None),
    ("nl", "wat kun je allemaal?", None),
    ("nl", "hoeveel heb ik deze maand uitgegeven?", None),
    ("en", "Uber 14.30", {"amount": 14.3, "type": "expense"}),
    ("en", "paid 950 for rent", {"amount": 950.0, "type": "expense"}),
    ("en", "received 250 from a client", {"amount": 250.0, "type": "income"}),
    ("en", "coffee 3.80", {"amount": 3.8, "type": "expense"}),
    ("en", "bought shoes for 89", {"amount": 89.0, "type": "expense"}),
    ("en", "lunch at Wagamama 17.50", {"amount": 17.5, "type": "expense"}),
    ("en", "got a refund of 35", {"amount": 35.0, "type": "income"}),
    ("en", "gym membership 29.99", {"amount": 29.99, "type": "expense"}),
    ("en", "thanks!", None),
    ("en", "how much did I spend last week?", None),
    ("en", "good morning", None),
    ("en", "remind me to call mom tomorrow", None),
    ("fr", "Albert Heijn 41,60", {"amount": 41.6, "type": "expense"}),
    ("fr", "j'ai payé 900 de loyer", {"amount": 900.0, "type": "expense"}),
    ("fr", "reçu 2500 de salaire", {"amount": 2500.0, "type": "income"}),
    ("fr", "café 2,80", {"amount": 2.8, "type": "expense"}),
    ("fr", "essence 58", {"amount": 58.0, "type": "expense"}),
    ("fr", "merci beaucoup", None),
    ("fr", "combien ai-je dépensé ce mois-ci ?", None),
    ("fr", "abonnement Spotify 10,99", {"amount": 10.99, "type": "expense"}),
    ("de", "Lidl 29,45", {"amount": 29.45, "type": "expense"}),
    ("de", "Miete bezahlt 1000", {"amount": 1000.0, "type": "expense"}),
    ("de", "Gehalt erhalten 3200", {"amount": 3200.0, "type": "income"}),
    ("de", "Tanken 61,20", {"amount": 61.2, "type": "expense"}),
    ("de", "Kaffee 3,40", {"amount": 3.4, "type": "expense"}),
    ("de", "danke dir", None),
    ("de", "wie viel habe ich diesen Monat ausgegeben?", None),
    ("de", "Fitnessstudio 24,90", {"amount": 24.9, "type": "expense"}),
    ("pt", "gastei 15 libras em londres", {"amount": 15.0, "currency": "GBP"}),
    ("en", "paid 30 dollars for a cab", {"amount": 30.0, "currency": "USD"}),
    ("nl", "12 dollar voor een broodje", {"amount": 12.0, "currency": "USD"}),
    ("fr", "boulangerie 4,30", {"amount": 4.3, "type": "expense"}),
    ("fr", "j'ai reçu 120 de remboursement", {"amount": 120.0, "type": "income"}),
    ("fr", "cinéma 11 euros", {"amount": 11.0, "type": "expense"}),
    ("fr", "électricité 95", {"amount": 95.0, "type": "expense"}),
    ("de", "Bäckerei 5,60", {"amount": 5.6, "type": "expense"}),
    ("de", "ich habe 300 Euro zurückbekommen", {"amount": 300.0, "type": "income"}),
    ("de", "Kino 12 Euro", {"amount": 12.0, "type": "expense"}),
    ("de", "Strom bezahlt 88", {"amount": 88.0, "type": "expense"}),
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
