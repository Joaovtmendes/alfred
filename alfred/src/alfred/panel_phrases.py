# ruff: noqa: E501
"""Panel assessor phrases: rules over numbers already computed, no LLM (spec §2).

A rule is a pure function ``(numbers, lang) -> Phrase | None``. ``None`` means "nothing relevant
to say" and the card shows no phrase. At most one phrase per card. The suggestion is always a
command to type in the chat, never a product, investment or financial decision.
"""

from __future__ import annotations

from dataclasses import dataclass

LANGS = ("pt", "nl", "en", "fr", "de")

PHRASES: dict[str, dict[str, str]] = {
    "balance_projection": {
        "pt": "Se as contas marcadas acontecerem, você fecha o mês com cerca de {projected}. É uma estimativa, não uma garantia.",
        "nl": "Als de geplande rekeningen doorgaan, sluit je de maand af met ongeveer {projected}. Dit is een schatting, geen garantie.",
        "en": "If the scheduled bills happen, you end the month with about {projected}. This is an estimate, not a guarantee.",
        "fr": "Si les factures prévues tombent, tu finis le mois avec environ {projected}. C'est une estimation, pas une garantie.",
        "de": "Wenn die geplanten Rechnungen anfallen, schließt du den Monat mit etwa {projected} ab. Das ist eine Schätzung, keine Garantie.",
    },
    "budget_near": {
        "pt": "{category}: {spent} de {limit} ({pct}% do orçamento).",
        "nl": "{category}: {spent} van {limit} ({pct}% van het budget).",
        "en": "{category}: {spent} of {limit} ({pct}% of the budget).",
        "fr": "{category} : {spent} sur {limit} ({pct} % du budget).",
        "de": "{category}: {spent} von {limit} ({pct} % des Budgets).",
    },
    "budget_over": {
        "pt": "{category} passou do orçamento: {spent} de {limit} ({pct}%).",
        "nl": "{category} zit boven het budget: {spent} van {limit} ({pct}%).",
        "en": "{category} is over budget: {spent} of {limit} ({pct}%).",
        "fr": "{category} dépasse le budget : {spent} sur {limit} ({pct} %).",
        "de": "{category} liegt über dem Budget: {spent} von {limit} ({pct} %).",
    },
}

CHAT: dict[str, dict[str, str]] = {
    "balance_projection": {
        "pt": '"como fica meu mês?"',
        "nl": '"hoe ziet mijn maand eruit?"',
        "en": '"how does my month look?"',
        "fr": '"comment se présente mon mois ?"',
        "de": '"wie sieht mein Monat aus?"',
    },
    "budget": {
        "pt": '"como estão meus orçamentos?"',
        "nl": '"hoe staan mijn budgetten ervoor?"',
        "en": '"how are my budgets?"',
        "fr": '"où en sont mes budgets ?"',
        "de": '"wie stehen meine Budgets?"',
    },
}

# Words a phrase must never contain: the panel describes the member's own records, it does not
# tell anyone what to buy, sell or invest in.
FORBIDDEN_STEMS: dict[str, tuple[str, ...]] = {
    "pt": ("invista", "compre", "venda", "aplique", "resgate", "recomendo"),
    "nl": ("investeer", "koop ", "verkoop", "ik raad"),
    "en": ("invest in", "buy ", "sell ", "i recommend"),
    "fr": ("investis", "achète", "vends", "je recommande"),
    "de": ("investiere", "kaufe", "verkaufe", "ich empfehle"),
}


@dataclass(frozen=True)
class Phrase:
    key: str
    text: str
    chat: str | None
    severity: str  # "info" | "attention"


def fmt_eur(value: float, lang: str) -> str:
    """European money in every language: ``€ 1.234,56``; negatives ``− € 5,00``.

    ``lang`` is accepted for symmetry with the rules; the format is the same in all five.
    """
    rounded = round(value, 2)
    cents = f"{abs(rounded):,.2f}".replace(",", "§").replace(".", ",").replace("§", ".")
    return f"{'− ' if rounded < 0 else ''}€ {cents}"


def render(
    key: str, lang: str, chat_key: str | None = None, severity: str = "info", **values
) -> Phrase:
    lang = lang if lang in LANGS else "pt"
    chat = CHAT.get(chat_key or key, {}).get(lang)
    return Phrase(key=key, text=PHRASES[key][lang].format(**values), chat=chat, severity=severity)


def rule_balance_vs_projection(balance: float, projected: float | None, lang: str) -> Phrase | None:
    """Only with a projection; with too little history the caller passes ``None`` and says nothing."""
    if projected is None:
        return None
    return render("balance_projection", lang, projected=fmt_eur(projected, lang))


def rule_budget_over(category: str, spent: float, limit: float, lang: str) -> Phrase | None:
    if limit <= 0:
        return None
    pct = round(spent / limit * 100)
    if pct < 80:
        return None
    over = pct >= 100
    return render(
        "budget_over" if over else "budget_near",
        lang,
        chat_key="budget",
        severity="attention" if over else "info",
        category=category,
        spent=fmt_eur(spent, lang),
        limit=fmt_eur(limit, lang),
        pct=pct,
    )
