"""Display names for the ten canonical (Dutch) expense categories.

The database and the LLM contract keep the Dutch identifiers; users read their own
language ("Supermercado", not "Supermarkt").
"""

# ruff: noqa: E501
from __future__ import annotations

_LABELS: dict[str, dict[str, str]] = {
    "supermarkt": {"pt": "Supermercado", "nl": "Supermarkt", "en": "Groceries", "fr": "Supermarché", "de": "Supermarkt"},
    "restaurant": {"pt": "Restaurante", "nl": "Restaurant", "en": "Restaurant", "fr": "Restaurant", "de": "Restaurant"},
    "transport": {"pt": "Transporte", "nl": "Vervoer", "en": "Transport", "fr": "Transport", "de": "Verkehr"},
    "gezondheid": {"pt": "Saúde", "nl": "Gezondheid", "en": "Health", "fr": "Santé", "de": "Gesundheit"},
    "entertainment": {"pt": "Lazer", "nl": "Entertainment", "en": "Entertainment", "fr": "Loisirs", "de": "Freizeit"},
    "wonen": {"pt": "Habitação", "nl": "Wonen", "en": "Housing", "fr": "Logement", "de": "Wohnen"},
    "kleding": {"pt": "Vestuário", "nl": "Kleding", "en": "Clothing", "fr": "Vêtements", "de": "Kleidung"},
    "abonnement": {"pt": "Subscrições", "nl": "Abonnement", "en": "Subscriptions", "fr": "Abonnements", "de": "Abos"},
    "inkomen": {"pt": "Rendimento", "nl": "Inkomen", "en": "Income", "fr": "Revenu", "de": "Einkommen"},
    "overig": {"pt": "Outros", "nl": "Overig", "en": "Other", "fr": "Autres", "de": "Sonstiges"},
}  # fmt: skip


def category_label(category: str | None, lang: str) -> str:
    """Localised name; unknown categories are just capitalised."""
    cat = (category or "overig").lower()
    names = _LABELS.get(cat)
    if not names:
        return cat.capitalize()
    return names.get(lang) or names["en"]
