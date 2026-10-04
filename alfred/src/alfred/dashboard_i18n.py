"""Server-side strings of the token pages (title, expired link, data export) in the member's
language (pt / nl / en / fr / de). The panel's own texts live in ``panel_i18n``."""

# ruff: noqa: E501
from __future__ import annotations

SUPPORTED = ("pt", "nl", "en", "fr", "de")

# Number/currency formatting locale per language (Intl.NumberFormat in the browser).
LOCALES = {"pt": "pt-BR", "nl": "nl-NL", "en": "en-GB", "fr": "fr-FR", "de": "de-DE"}

_UI: dict[str, dict[str, str]] = {
    "title": {"pt": "Painel do Alfred", "nl": "Alfred-dashboard", "en": "Alfred dashboard", "fr": "Tableau de bord Alfred", "de": "Alfred-Dashboard"},
    "export_title": {"pt": "Exportar meus dados", "nl": "Mijn gegevens exporteren", "en": "Export my data", "fr": "Exporter mes données", "de": "Meine Daten exportieren"},
    "export_text": {"pt": "O arquivo (JSON) tem tudo o que o Alfred guarda sobre você. Este link vale uma vez só.", "nl": "Het bestand (JSON) bevat alles wat Alfred over je bewaart. Deze link werkt maar één keer.", "en": "The file (JSON) holds everything Alfred stores about you. This link works only once.", "fr": "Le fichier (JSON) contient tout ce qu'Alfred conserve sur toi. Ce lien ne fonctionne qu'une fois.", "de": "Die Datei (JSON) enthält alles, was Alfred über dich speichert. Dieser Link funktioniert nur einmal."},
    "export_button": {"pt": "Baixar meus dados", "nl": "Mijn gegevens downloaden", "en": "Download my data", "fr": "Télécharger mes données", "de": "Meine Daten herunterladen"},
    "books_export_title": {"pt": "Exportar a contabilidade", "nl": "Boekhouding exporteren", "en": "Export the accounting", "fr": "Exporter la comptabilité", "de": "Buchhaltung exportieren"},
    "books_export_text": {"pt": "O arquivo (CSV) tem os lançamentos do ano, com data, valor, BTW e categoria, para você abrir no Excel ou enviar ao seu contador. Este link vale uma vez só.", "nl": "Het bestand (CSV) bevat de boekingen van het jaar, met datum, bedrag, btw en categorie, om in Excel te openen of naar je boekhouder te sturen. Deze link werkt maar één keer.", "en": "The file (CSV) has the year's entries with date, amount, BTW and category, to open in Excel or send to your accountant. This link works only once.", "fr": "Le fichier (CSV) contient les écritures de l'année avec date, montant, TVA et catégorie, à ouvrir dans Excel ou à envoyer à ton comptable. Ce lien ne fonctionne qu'une fois.", "de": "Die Datei (CSV) enthält die Buchungen des Jahres mit Datum, Betrag, MwSt und Kategorie, zum Öffnen in Excel oder für deinen Steuerberater. Dieser Link gilt nur einmal."},
    "books_export_button": {"pt": "Baixar o CSV", "nl": "CSV downloaden", "en": "Download the CSV", "fr": "Télécharger le CSV", "de": "CSV herunterladen"},
    "link_expired_title": {"pt": "Este link expirou", "nl": "Deze link is verlopen", "en": "This link has expired", "fr": "Ce lien a expiré", "de": "Dieser Link ist abgelaufen"},
    "link_expired_text": {"pt": "Por segurança, o link do painel vale por poucos dias. Escreva \"meu dashboard\" no WhatsApp e o Alfred envia um novo.", "nl": "Om veiligheidsredenen is de dashboardlink maar een paar dagen geldig. Schrijf \"mijn dashboard\" in WhatsApp en Alfred stuurt een nieuwe.", "en": "For security, the dashboard link is valid for a few days only. Write \"my dashboard\" in WhatsApp and Alfred sends a new one.", "fr": "Par sécurité, le lien du tableau de bord n'est valable que quelques jours. Écris \"mon tableau de bord\" sur WhatsApp et Alfred t'en envoie un nouveau.", "de": "Aus Sicherheitsgründen gilt der Dashboard-Link nur wenige Tage. Schreibe \"mein Dashboard\" in WhatsApp und Alfred schickt dir einen neuen."},
}  # fmt: skip


def normalize_lang(lang: str | None) -> str:
    return lang if lang in SUPPORTED else "pt"


def ui(lang: str | None) -> dict[str, str]:
    """All UI strings for ``lang`` (Portuguese when unknown, as elsewhere in the dashboard)."""
    lang = normalize_lang(lang)
    return {key: names[lang] for key, names in _UI.items()}
