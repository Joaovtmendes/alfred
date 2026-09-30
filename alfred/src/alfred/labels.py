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


# Workout types the LLM is asked to return (English identifiers), shown in the user's language.
_ACTIVITIES: dict[str, dict[str, str]] = {
    "running": {"pt": "corrida", "nl": "hardlopen", "en": "running", "fr": "course à pied", "de": "Laufen"},
    "strength": {"pt": "musculação", "nl": "krachttraining", "en": "strength training", "fr": "musculation", "de": "Krafttraining"},
    "cycling": {"pt": "ciclismo", "nl": "fietsen", "en": "cycling", "fr": "vélo", "de": "Radfahren"},
    "yoga": {"pt": "ioga", "nl": "yoga", "en": "yoga", "fr": "yoga", "de": "Yoga"},
    "swimming": {"pt": "natação", "nl": "zwemmen", "en": "swimming", "fr": "natation", "de": "Schwimmen"},
    "walking": {"pt": "caminhada", "nl": "wandelen", "en": "walking", "fr": "marche", "de": "Spazierengehen"},
    "football": {"pt": "futebol", "nl": "voetbal", "en": "football", "fr": "football", "de": "Fußball"},
    "basketball": {"pt": "basquete", "nl": "basketbal", "en": "basketball", "fr": "basket", "de": "Basketball"},
    "pilates": {"pt": "pilates", "nl": "pilates", "en": "pilates", "fr": "pilates", "de": "Pilates"},
    "hiit": {"pt": "HIIT", "nl": "HIIT", "en": "HIIT", "fr": "HIIT", "de": "HIIT"},
    "workout": {"pt": "treino", "nl": "training", "en": "workout", "fr": "entraînement", "de": "Training"},
    "other": {"pt": "treino", "nl": "training", "en": "workout", "fr": "entraînement", "de": "Training"},
}  # fmt: skip


def activity_label(activity: str | None, lang: str) -> str:
    """Workout type in the user's language; free-text types the LLM made up pass through."""
    key = (activity or "workout").strip().lower()
    names = _ACTIVITIES.get(key)
    if not names:
        return (activity or "").strip() or _ACTIVITIES["workout"].get(lang, "workout")
    return names.get(lang) or names["en"]
