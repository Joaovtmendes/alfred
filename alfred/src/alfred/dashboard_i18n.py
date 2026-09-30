"""UI strings of the web dashboard in the member's language (pt / nl / en / fr / de).

The page itself is a static template; the server injects this dictionary as JSON and the
script fills in the labels. Month names come from ``clock.month_name`` (single source).
"""

# ruff: noqa: E501
from __future__ import annotations

import json
from datetime import date

from alfred.clock import month_name

SUPPORTED = ("pt", "nl", "en", "fr", "de")

# Number/currency formatting locale per language (Intl.NumberFormat in the browser).
LOCALES = {"pt": "pt-BR", "nl": "nl-NL", "en": "en-GB", "fr": "fr-FR", "de": "de-DE"}

_UI: dict[str, dict[str, str]] = {
    "title": {"pt": "Painel do Alfred", "nl": "Alfred-dashboard", "en": "Alfred dashboard", "fr": "Tableau de bord Alfred", "de": "Alfred-Dashboard"},
    "loading": {"pt": "Carregando…", "nl": "Laden…", "en": "Loading…", "fr": "Chargement…", "de": "Wird geladen…"},
    "not_found": {"pt": "Painel não encontrado.", "nl": "Dashboard niet gevonden.", "en": "Dashboard not found.", "fr": "Tableau de bord introuvable.", "de": "Dashboard nicht gefunden."},
    "prev_month": {"pt": "Mês anterior", "nl": "Vorige maand", "en": "Previous month", "fr": "Mois précédent", "de": "Voriger Monat"},
    "next_month": {"pt": "Próximo mês", "nl": "Volgende maand", "en": "Next month", "fr": "Mois suivant", "de": "Nächster Monat"},
    "spent": {"pt": "Gastos", "nl": "Uitgaven", "en": "Spending", "fr": "Dépenses", "de": "Ausgaben"},
    "income": {"pt": "Receita", "nl": "Inkomsten", "en": "Income", "fr": "Revenus", "de": "Einnahmen"},
    "balance": {"pt": "Saldo", "nl": "Saldo", "en": "Balance", "fr": "Solde", "de": "Saldo"},
    "categories": {"pt": "categorias", "nl": "categorieën", "en": "categories", "fr": "catégories", "de": "Kategorien"},
    "category_one": {"pt": "categoria", "nl": "categorie", "en": "category", "fr": "catégorie", "de": "Kategorie"},
    "by_category": {"pt": "Gastos por categoria", "nl": "Uitgaven per categorie", "en": "Spending by category", "fr": "Dépenses par catégorie", "de": "Ausgaben nach Kategorie"},
    "monthly_history": {"pt": "Histórico mensal", "nl": "Maandoverzicht", "en": "Monthly history", "fr": "Historique mensuel", "de": "Monatsverlauf"},
    "recent_tx": {"pt": "Transações recentes", "nl": "Recente transacties", "en": "Recent transactions", "fr": "Transactions récentes", "de": "Letzte Transaktionen"},
    "goals": {"pt": "Metas", "nl": "Doelen", "en": "Goals", "fr": "Objectifs", "de": "Ziele"},
    "health_month": {"pt": "Saúde este mês", "nl": "Gezondheid deze maand", "en": "Health this month", "fr": "Santé ce mois-ci", "de": "Gesundheit diesen Monat"},
    "habits": {"pt": "Hábitos", "nl": "Gewoontes", "en": "Habits", "fr": "Habitudes", "de": "Gewohnheiten"},
    "pending_tasks": {"pt": "Tarefas pendentes", "nl": "Openstaande taken", "en": "Pending tasks", "fr": "Tâches en attente", "de": "Offene Aufgaben"},
    "recent_notes": {"pt": "Notas recentes", "nl": "Recente notities", "en": "Recent notes", "fr": "Notes récentes", "de": "Letzte Notizen"},
    "empty_tx": {"pt": "Ainda não há transações este mês.", "nl": "Nog geen transacties deze maand.", "en": "No transactions yet this month.", "fr": "Aucune transaction ce mois-ci.", "de": "Noch keine Transaktionen in diesem Monat."},
    "empty_goals": {"pt": "Você ainda não tem metas ativas.", "nl": "Je hebt nog geen actieve doelen.", "en": "You don't have any active goals yet.", "fr": "Tu n'as pas encore d'objectifs actifs.", "de": "Du hast noch keine aktiven Ziele."},
    "empty_habits": {"pt": "Ainda não há hábitos registrados nos últimos 30 dias.", "nl": "Geen gewoontes geregistreerd in de afgelopen 30 dagen.", "en": "No habits logged in the last 30 days.", "fr": "Aucune habitude enregistrée ces 30 derniers jours.", "de": "Keine Gewohnheiten in den letzten 30 Tagen erfasst."},
    "empty_tasks": {"pt": "Você não tem tarefas pendentes.", "nl": "Je hebt geen openstaande taken.", "en": "You have no pending tasks.", "fr": "Tu n'as aucune tâche en attente.", "de": "Du hast keine offenen Aufgaben."},
    "empty_notes": {"pt": "Você ainda não tem notas.", "nl": "Je hebt nog geen notities.", "en": "You don't have any notes yet.", "fr": "Tu n'as pas encore de notes.", "de": "Du hast noch keine Notizen."},
    "empty_health": {"pt": "Ainda não há registros de saúde este mês.", "nl": "Nog geen gezondheidsregistraties deze maand.", "en": "No health entries yet this month.", "fr": "Aucune donnée de santé ce mois-ci.", "de": "Noch keine Gesundheitseinträge in diesem Monat."},
    "day_short": {"pt": "d", "nl": "d", "en": "d", "fr": "j", "de": "T"},
    "times": {"pt": "x", "nl": "x", "en": "x", "fr": "x", "de": "x"},
    "medication": {"pt": "Medicação", "nl": "Medicatie", "en": "Medication", "fr": "Médicaments", "de": "Medikamente"},
    "mood": {"pt": "Humor", "nl": "Stemming", "en": "Mood", "fr": "Humeur", "de": "Stimmung"},
    "sleep": {"pt": "Sono", "nl": "Slaap", "en": "Sleep", "fr": "Sommeil", "de": "Schlaf"},
    "water": {"pt": "Água", "nl": "Water", "en": "Water", "fr": "Eau", "de": "Wasser"},
    "user": {"pt": "Usuário", "nl": "Gebruiker", "en": "User", "fr": "Utilisateur", "de": "Nutzer"},
}  # fmt: skip


def normalize_lang(lang: str | None) -> str:
    return lang if lang in SUPPORTED else "pt"


def ui(lang: str | None) -> dict[str, str]:
    """All UI strings for ``lang`` (Portuguese when unknown, as elsewhere in the dashboard)."""
    lang = normalize_lang(lang)
    return {key: names[lang] for key, names in _UI.items()}


def payload(lang: str | None) -> str:
    """JSON for the page: strings, month names and number locale, safe inside a <script>."""
    lang = normalize_lang(lang)
    data = {
        "lang": lang,
        "locale": LOCALES[lang],
        "t": ui(lang),
        "months": [month_name(date(2000, m, 1), lang) for m in range(1, 13)],
    }
    return json.dumps(data, ensure_ascii=True).replace("<", "\\u003c").replace(">", "\\u003e")
