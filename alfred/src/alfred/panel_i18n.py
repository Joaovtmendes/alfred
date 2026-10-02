# ruff: noqa: E501
"""Fixed texts of the v2 shell in pt / nl / en / fr / de."""

from __future__ import annotations

from alfred.dashboard_i18n import normalize_lang

_TABS = (
    (
        "summary",
        {"pt": "Resumo", "nl": "Overzicht", "en": "Summary", "fr": "Résumé", "de": "Übersicht"},
    ),
    ("money", {"pt": "Dinheiro", "nl": "Geld", "en": "Money", "fr": "Argent", "de": "Geld"}),
    (
        "agenda",
        {
            "pt": "Agenda e tarefas",
            "nl": "Agenda en taken",
            "en": "Agenda and tasks",
            "fr": "Agenda et tâches",
            "de": "Termine und Aufgaben",
        },
    ),
    (
        "health",
        {
            "pt": "Hábitos",
            "nl": "Gewoonten",
            "en": "Habits",
            "fr": "Habitudes",
            "de": "Gewohnheiten",
        },
    ),
    ("trips", {"pt": "Viagens", "nl": "Reizen", "en": "Trips", "fr": "Voyages", "de": "Reisen"}),
)

SHELL = {
    "chat_button": {
        "pt": "Conversar no WhatsApp",
        "nl": "Chatten op WhatsApp",
        "en": "Chat on WhatsApp",
        "fr": "Discuter sur WhatsApp",
        "de": "Im WhatsApp chatten",
    },
    "link_valid": {
        "pt": "link válido por até 7 dias",
        "nl": "link maximaal 7 dagen geldig",
        "en": "link valid for up to 7 days",
        "fr": "lien valable jusqu'à 7 jours",
        "de": "Link bis zu 7 Tage gültig",
    },
    "ask_in_chat": {
        "pt": "Peça no chat",
        "nl": "Vraag in de chat",
        "en": "Ask in the chat",
        "fr": "Demande dans le chat",
        "de": "Frag im Chat",
    },
    "table_view": {
        "pt": "Ver como tabela",
        "nl": "Bekijk als tabel",
        "en": "View as table",
        "fr": "Voir en tableau",
        "de": "Als Tabelle ansehen",
    },
    "loading": {
        "pt": "Carregando…",
        "nl": "Laden…",
        "en": "Loading…",
        "fr": "Chargement…",
        "de": "Wird geladen…",
    },
    "empty": {
        "pt": "Ainda não há dados aqui. Registre pelo chat e eles aparecem.",
        "nl": "Hier zijn nog geen gegevens. Leg ze vast via de chat.",
        "en": "No data here yet. Record it in the chat and it shows up.",
        "fr": "Pas encore de données ici. Enregistre-les dans le chat.",
        "de": "Hier gibt es noch keine Daten. Erfasse sie im Chat.",
    },
    "error": {
        "pt": "Não foi possível carregar esta aba. Tente de novo em instantes.",
        "nl": "Dit tabblad kon niet worden geladen. Probeer het zo opnieuw.",
        "en": "This tab could not be loaded. Try again in a moment.",
        "fr": "Impossible de charger cet onglet. Réessaie dans un instant.",
        "de": "Dieser Tab konnte nicht geladen werden. Versuche es gleich noch einmal.",
    },
    "expired": {
        "pt": 'Este link expirou. Escreva "meu dashboard" no WhatsApp para receber um novo.',
        "nl": 'Deze link is verlopen. Schrijf "mijn dashboard" in WhatsApp voor een nieuwe.',
        "en": 'This link has expired. Write "my dashboard" in WhatsApp to get a new one.',
        "fr": 'Ce lien a expiré. Écris "mon tableau de bord" sur WhatsApp pour en recevoir un nouveau.',
        "de": 'Dieser Link ist abgelaufen. Schreibe "mein Dashboard" in WhatsApp, um einen neuen zu bekommen.',
    },
    "rate_limited": {
        "pt": "Muitas consultas em pouco tempo. Aguarde um minuto e tente de novo.",
        "nl": "Te veel verzoeken in korte tijd. Wacht een minuut en probeer het opnieuw.",
        "en": "Too many requests in a short time. Wait a minute and try again.",
        "fr": "Trop de demandes en peu de temps. Attends une minute et réessaie.",
        "de": "Zu viele Anfragen in kurzer Zeit. Warte eine Minute und versuche es erneut.",
    },
    "privacy_footer": {
        "pt": 'Privacidade: escreva "exportar meus dados" ou "apagar meus dados" no chat.',
        "nl": "Privacy: schrijf “exporteer mijn gegevens” of “verwijder mijn gegevens” in de chat.",
        "en": "Privacy: write “export my data” or “delete my data” in the chat.",
        "fr": "Confidentialité : écris « exporter mes données » ou « supprimer mes données » dans le chat.",
        "de": "Datenschutz: schreibe „meine Daten exportieren“ oder „meine Daten löschen“ im Chat.",
    },
    "tab_balance": {
        "pt": "Saldo do período",
        "nl": "Saldo van de periode",
        "en": "Balance for the period",
        "fr": "Solde de la période",
        "de": "Saldo des Zeitraums",
    },
    "income": {
        "pt": "Entradas",
        "nl": "Inkomsten",
        "en": "Income",
        "fr": "Entrées",
        "de": "Einnahmen",
    },
    "expense": {
        "pt": "Saídas",
        "nl": "Uitgaven",
        "en": "Spending",
        "fr": "Sorties",
        "de": "Ausgaben",
    },
}


def tabs(lang: str | None) -> list[dict[str, str]]:
    lang = normalize_lang(lang)
    return [{"id": i, "label": names[lang], "path": f"/api/d/{{token}}/{i}"} for i, names in _TABS]


def shell(lang: str | None) -> dict[str, str]:
    lang = normalize_lang(lang)
    return {k: v[lang] for k, v in SHELL.items()}
