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
        {"pt": "Agenda", "nl": "Agenda", "en": "Agenda", "fr": "Agenda", "de": "Termine"},
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
    "soon": {
        "pt": "Em breve: esta aba está a caminho.",
        "nl": "Binnenkort: dit tabblad komt eraan.",
        "en": "Coming soon: this tab is on its way.",
        "fr": "Bientôt : cet onglet arrive.",
        "de": "Bald: dieser Tab ist unterwegs.",
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


def _t(pt: str, nl: str, en: str, fr: str, de: str) -> dict[str, str]:
    return {"pt": pt, "nl": nl, "en": en, "fr": fr, "de": de}


# Texts of the Resumo and Dinheiro cards, filters and header. "{name}" style fields are filled by
# panel.js; straight quotes only (the chat suggestions get typographic quotes when drawn).
SHELL.update(
    {
        # header, theme, tabs
        "panel_of": _t(
            "Painel de {name}",
            "Paneel van {name}",
            "{name}'s panel",
            "Panneau de {name}",
            "Panel von {name}",
        ),
        "theme_to_light": _t(
            "Mudar para o tema claro",
            "Overschakelen naar het lichte thema",
            "Switch to the light theme",
            "Passer au thème clair",
            "Zum hellen Design wechseln",
        ),
        "theme_to_dark": _t(
            "Mudar para o tema escuro",
            "Overschakelen naar het donkere thema",
            "Switch to the dark theme",
            "Passer au thème sombre",
            "Zum dunklen Design wechseln",
        ),
        "footer_readonly": _t(
            "Este painel só mostra o que você registrou. Para mudar algo, escreva ao Alfred no WhatsApp.",
            "Dit paneel toont alleen wat je hebt vastgelegd. Wil je iets wijzigen, schrijf dan Alfred op WhatsApp.",
            "This panel only shows what you recorded. To change something, write to Alfred on WhatsApp.",
            "Ce panneau montre seulement ce que tu as enregistré. Pour changer quelque chose, écris à Alfred sur WhatsApp.",
            "Dieses Panel zeigt nur, was du erfasst hast. Um etwas zu ändern, schreibe Alfred im WhatsApp.",
        ),
        # filters
        "filters_label": _t("Filtros", "Filters", "Filters", "Filtres", "Filter"),
        "f_month": _t("Mês", "Maand", "Month", "Mois", "Monat"),
        "f_month_prev": _t(
            "Mês anterior", "Vorige maand", "Previous month", "Mois précédent", "Vorheriger Monat"
        ),
        "f_month_next": _t(
            "Próximo mês", "Volgende maand", "Next month", "Mois suivant", "Nächster Monat"
        ),
        "f_category": _t("Categoria", "Categorie", "Category", "Catégorie", "Kategorie"),
        "f_all_categories": _t(
            "Todas as categorias",
            "Alle categorieën",
            "All categories",
            "Toutes les catégories",
            "Alle Kategorien",
        ),
        "f_multi": _t(
            "{n} categorias",
            "{n} categorieën",
            "{n} categories",
            "{n} catégories",
            "{n} Kategorien",
        ),
        "f_kind": _t("Tipo", "Soort", "Type", "Type", "Art"),
        "f_kind_all": _t(
            "Despesas e receitas",
            "Uitgaven en inkomsten",
            "Spending and income",
            "Dépenses et revenus",
            "Ausgaben und Einnahmen",
        ),
        "f_kind_expense": _t(
            "Só despesas", "Alleen uitgaven", "Spending only", "Dépenses seulement", "Nur Ausgaben"
        ),
        "f_kind_income": _t(
            "Só receitas", "Alleen inkomsten", "Income only", "Revenus seulement", "Nur Einnahmen"
        ),
        "f_state": _t("Estado", "Status", "Status", "État", "Status"),
        "f_state_all": _t(
            "Todos os estados", "Alle statussen", "All statuses", "Tous les états", "Alle Status"
        ),
        "st_paid": _t("Pago", "Betaald", "Paid", "Payé", "Bezahlt"),
        "st_to_pay": _t("A pagar", "Te betalen", "To pay", "À payer", "Zu zahlen"),
        "st_received": _t("Recebido", "Ontvangen", "Received", "Reçu", "Erhalten"),
        "st_to_receive": _t("A receber", "Te ontvangen", "To receive", "À recevoir", "Zu erhalten"),
        "f_clear": _t(
            "Limpar filtros",
            "Filters wissen",
            "Clear filters",
            "Effacer les filtres",
            "Filter zurücksetzen",
        ),
        "f_note_summary": _t(
            "Os filtros só mudam o que você vê.",
            "De filters veranderen alleen wat je ziet.",
            "Filters only change what you see.",
            "Les filtres ne changent que ce que tu vois.",
            "Die Filter ändern nur, was du siehst.",
        ),
        "f_note_money": _t(
            "Só leitura. Filtre por mês, categoria, tipo e estado.",
            "Alleen lezen. Filter op maand, categorie, soort en status.",
            "Read only. Filter by month, category, type and status.",
            "Lecture seule. Filtre par mois, catégorie, type et état.",
            "Nur lesen. Filtere nach Monat, Kategorie, Art und Status.",
        ),
        # shared bits
        "vs_month": _t(
            "contra {month}",
            "tegen {month}",
            "against {month}",
            "par rapport à {month}",
            "gegenüber {month}",
        ),
        "cmp_same": _t(
            "igual a {month}",
            "gelijk aan {month}",
            "same as {month}",
            "identique à {month}",
            "wie im {month}",
        ),
        "cmp_above": _t(
            "{pct}% acima de {month}",
            "{pct}% boven {month}",
            "{pct}% above {month}",
            "{pct}% de plus qu'en {month}",
            "{pct}% über {month}",
        ),
        "cmp_below": _t(
            "{pct}% abaixo de {month}",
            "{pct}% onder {month}",
            "{pct}% below {month}",
            "{pct}% de moins qu'en {month}",
            "{pct}% unter {month}",
        ),
        "cmp_none": _t(
            "sem dados de {month}",
            "geen gegevens van {month}",
            "no data for {month}",
            "pas de données pour {month}",
            "keine Daten für {month}",
        ),
        # Resumo
        "title_balance": _t(
            "Saldo do período",
            "Saldo van de periode",
            "Balance for the period",
            "Solde de la période",
            "Saldo des Zeitraums",
        ),
        "title_balance_proj": _t(
            "Saldo do período e fim do mês projetado",
            "Saldo van de periode en verwacht maandeinde",
            "Balance for the period and projected month end",
            "Solde de la période et fin de mois estimée",
            "Saldo des Zeitraums und voraussichtliches Monatsende",
        ),
        "balance_realized": _t(
            "saldo realizado até hoje",
            "saldo tot vandaag",
            "balance up to today",
            "solde réalisé à ce jour",
            "Saldo bis heute",
        ),
        "proj_estimate": _t(
            "estimativa para {date} · faixa {low} a {high}",
            "schatting voor {date} · bandbreedte {low} tot {high}",
            "estimate for {date} · range {low} to {high}",
            "estimation pour le {date} · fourchette {low} à {high}",
            "Schätzung für den {date} · Spanne {low} bis {high}",
        ),
        "proj_estimate_plain": _t(
            "estimativa para {date}",
            "schatting voor {date}",
            "estimate for {date}",
            "estimation pour le {date}",
            "Schätzung für den {date}",
        ),
        "proj_total": _t(
            "fim do mês (estimativa)",
            "einde van de maand (schatting)",
            "month end (estimate)",
            "fin du mois (estimation)",
            "Monatsende (Schätzung)",
        ),
        "proj_range": _t(
            "faixa {low} a {high}",
            "bandbreedte {low} tot {high}",
            "range {low} to {high}",
            "fourchette {low} à {high}",
            "Spanne {low} bis {high}",
        ),
        "leg_realized": _t(
            "realizado até hoje",
            "tot vandaag gerealiseerd",
            "realised so far",
            "réalisé à ce jour",
            "bis heute realisiert",
        ),
        "leg_committed": _t(
            "contas marcadas",
            "geplande rekeningen",
            "scheduled bills",
            "factures prévues",
            "geplante Rechnungen",
        ),
        "leg_variable": _t(
            "gasto variável esperado",
            "verwachte variabele uitgaven",
            "expected variable spending",
            "dépenses variables attendues",
            "erwartete variable Ausgaben",
        ),
        "leg_incoming": _t(
            "receitas a receber",
            "nog te ontvangen inkomsten",
            "income still to receive",
            "revenus à recevoir",
            "noch zu erhaltende Einnahmen",
        ),
        "income_received": _t("recebido", "ontvangen", "received", "reçu", "erhalten"),
        "expense_paid": _t("pago", "betaald", "paid", "payé", "bezahlt"),
        "title_upcoming": _t(
            "Próximos pagamentos",
            "Komende betalingen",
            "Upcoming payments",
            "Prochains paiements",
            "Anstehende Zahlungen",
        ),
        "recurring": _t("recorrente", "terugkerend", "recurring", "récurrent", "wiederkehrend"),
        "more_items": _t(
            "+ {n} a mais", "+ {n} meer", "+ {n} more", "+ {n} de plus", "+ {n} weitere"
        ),
        "title_categories": _t(
            "Gastos por categoria",
            "Uitgaven per categorie",
            "Spending by category",
            "Dépenses par catégorie",
            "Ausgaben nach Kategorie",
        ),
        "cat_legend": _t(
            "▲ ▼ variação contra {month}",
            "▲ ▼ verschil met {month}",
            "▲ ▼ change against {month}",
            "▲ ▼ variation par rapport à {month}",
            "▲ ▼ Veränderung gegenüber {month}",
        ),
        "cat_equal": _t("= igual", "= gelijk", "= same", "= identique", "= gleich"),
        "cat_new": _t("novo", "nieuw", "new", "nouveau", "neu"),
        "cat_others": _t(
            "Outras categorias",
            "Overige categorieën",
            "Other categories",
            "Autres catégories",
            "Weitere Kategorien",
        ),
        "title_budgets": _t("Orçamentos", "Budgetten", "Budgets", "Budgets", "Budgets"),
        "budget_used": _t(
            "do orçamento do mês usado, no dia {day}",
            "van het maandbudget gebruikt, op dag {day}",
            "of the monthly budget used, on day {day}",
            "du budget du mois utilisé, au jour {day}",
            "des Monatsbudgets genutzt, an Tag {day}",
        ),
        "budget_used_short": _t(
            "{pct}% usado", "{pct}% gebruikt", "{pct}% used", "{pct}% utilisé", "{pct}% genutzt"
        ),
        "budget_of": _t(
            "{spent} de {limit}",
            "{spent} van {limit}",
            "{spent} of {limit}",
            "{spent} sur {limit}",
            "{spent} von {limit}",
        ),
        "budget_marks": _t(
            "As marcas na barra mostram 80% e 100% do limite.",
            "De markeringen op de balk tonen 80% en 100% van de limiet.",
            "The marks on the bar show 80% and 100% of the limit.",
            "Les repères sur la barre montrent 80% et 100% de la limite.",
            "Die Markierungen im Balken zeigen 80% und 100% des Limits.",
        ),
        "title_owed": _t(
            "Quem te deve",
            "Wie jou iets schuldig is",
            "Who owes you",
            "Qui te doit",
            "Wer dir Geld schuldet",
        ),
        "owing_title": _t(
            "Você deve",
            "Jij bent verschuldigd",
            "You owe",
            "Tu dois",
            "Du schuldest",
        ),
        "owed_days": _t(
            "há {n} dias", "{n} dagen geleden", "{n} days ago", "il y a {n} jours", "vor {n} Tagen"
        ),
        "owed_days_one": _t(
            "há {n} dia", "{n} dag geleden", "{n} day ago", "il y a {n} jour", "vor {n} Tag"
        ),
        "owed_today": _t("hoje", "vandaag", "today", "aujourd'hui", "heute"),
        # Dinheiro
        "title_tx": _t("Lançamentos", "Boekingen", "Entries", "Écritures", "Buchungen"),
        "today_word": _t("Hoje", "Vandaag", "Today", "Aujourd'hui", "Heute"),
        "all_days": _t("Todos", "Alle", "All", "Tous", "Alle"),
        "day_filter": _t("Dia", "Dag", "Day", "Jour", "Tag"),
        "no_merchant": _t(
            "Sem descrição",
            "Zonder omschrijving",
            "No description",
            "Sans description",
            "Ohne Beschreibung",
        ),
        "search_label": _t(
            "Buscar por nome nesta lista",
            "Zoek op naam in deze lijst",
            "Search this list by name",
            "Chercher par nom dans cette liste",
            "In dieser Liste nach Namen suchen",
        ),
        "search_note": _t(
            "A busca fica só neste navegador.",
            "Het zoeken blijft in deze browser.",
            "The search stays in this browser.",
            "La recherche reste dans ce navigateur.",
            "Die Suche bleibt in diesem Browser.",
        ),
        "search_none": _t(
            "Nenhum lançamento com esse nome.",
            "Geen boeking met die naam.",
            "No entry with that name.",
            "Aucune écriture avec ce nom.",
            "Keine Buchung mit diesem Namen.",
        ),
        "tx_total": _t(
            "Saldo de {n} lançamentos",
            "Saldo van {n} boekingen",
            "Balance of {n} entries",
            "Solde de {n} écritures",
            "Saldo von {n} Buchungen",
        ),
        "tx_total_one": _t(
            "Saldo de {n} lançamento",
            "Saldo van {n} boeking",
            "Balance of {n} entry",
            "Solde de {n} écriture",
            "Saldo von {n} Buchung",
        ),
        "page_prev": _t(
            "Página anterior",
            "Vorige pagina",
            "Previous page",
            "Page précédente",
            "Vorherige Seite",
        ),
        "page_next": _t(
            "Próxima página", "Volgende pagina", "Next page", "Page suivante", "Nächste Seite"
        ),
        "page_of": _t(
            "Página {n} de {total}",
            "Pagina {n} van {total}",
            "Page {n} of {total}",
            "Page {n} sur {total}",
            "Seite {n} von {total}",
        ),
        "copy_note": _t(
            'Quer uma cópia dos seus dados? Escreva "exportar meus dados" no chat e receba um link de download.',
            'Wil je een kopie van je gegevens? Schrijf "exporteer mijn gegevens" in de chat en je krijgt een downloadlink.',
            'Want a copy of your data? Write "export my data" in the chat and you get a download link.',
            'Tu veux une copie de tes données ? Écris "exporter mes données" dans le chat pour recevoir un lien.',
            'Du möchtest eine Kopie deiner Daten? Schreibe "meine Daten exportieren" im Chat und du erhältst einen Link.',
        ),
        "title_top": _t(
            "Maiores gastos",
            "Grootste uitgaven",
            "Biggest expenses",
            "Plus grosses dépenses",
            "Größte Ausgaben",
        ),
        "title_mom": _t(
            "Mês contra mês",
            "Maand tegen maand",
            "Month against month",
            "Mois contre mois",
            "Monat gegen Monat",
        ),
        "mom_expenses": _t("Despesas", "Uitgaven", "Spending", "Dépenses", "Ausgaben"),
        "mom_income": _t("Receitas", "Inkomsten", "Income", "Revenus", "Einnahmen"),
        "mom_drivers": _t(
            "O que mais subiu",
            "Wat het meest steeg",
            "What rose the most",
            "Ce qui a le plus augmenté",
            "Was am meisten stieg",
        ),
        "title_fixed": _t(
            "Fixos e variáveis",
            "Vast en variabel",
            "Fixed and variable",
            "Fixes et variables",
            "Fix und variabel",
        ),
        "fixed_word": _t("fixos", "vast", "fixed", "fixes", "fix"),
        "variable_word": _t("variáveis", "variabel", "variable", "variables", "variabel"),
        "title_daily": _t(
            "Gasto por dia",
            "Uitgaven per dag",
            "Spending per day",
            "Dépenses par jour",
            "Ausgaben pro Tag",
        ),
        "title_weekly": _t(
            "Gasto por semana",
            "Uitgaven per week",
            "Spending per week",
            "Dépenses par semaine",
            "Ausgaben pro Woche",
        ),
        "daily_peak": _t(
            "dia de maior gasto",
            "dag met de hoogste uitgaven",
            "day with the highest spending",
            "jour de plus forte dépense",
            "Tag mit den höchsten Ausgaben",
        ),
        "title_recurring": _t(
            "Recorrências",
            "Terugkerende posten",
            "Recurring items",
            "Récurrences",
            "Wiederkehrendes",
        ),
        "rec_total": _t(
            "{total} por mês · {n} itens",
            "{total} per maand · {n} posten",
            "{total} a month · {n} items",
            "{total} par mois · {n} éléments",
            "{total} pro Monat · {n} Posten",
        ),
        "rec_total_one": _t(
            "{total} por mês · {n} item",
            "{total} per maand · {n} post",
            "{total} a month · {n} item",
            "{total} par mois · {n} élément",
            "{total} pro Monat · {n} Posten",
        ),
        "rec_fixed": _t(
            "conta fixa", "vaste rekening", "fixed bill", "facture fixe", "feste Rechnung"
        ),
        "rec_subscription": _t("assinatura", "abonnement", "subscription", "abonnement", "Abo"),
        "rec_installment": _t(
            "parcelado", "termijnbetaling", "instalments", "paiement échelonné", "Ratenzahlung"
        ),
        "rec_part": _t(
            "parcela {n} de {total}",
            "termijn {n} van {total}",
            "instalment {n} of {total}",
            "échéance {n} sur {total}",
            "Rate {n} von {total}",
        ),
        "rec_monthly": _t("todo mês", "elke maand", "every month", "chaque mois", "jeden Monat"),
        "rec_weekly": _t("toda semana", "elke week", "every week", "chaque semaine", "jede Woche"),
        "rec_yearly": _t("todo ano", "elk jaar", "every year", "chaque année", "jedes Jahr"),
        "title_week": _t(
            "Próximos 7 dias",
            "Komende 7 dagen",
            "Next 7 days",
            "7 prochains jours",
            "Nächste 7 Tage",
        ),
        "title_tasks": _t(
            "Tarefas",
            "Taken",
            "Tasks",
            "Tâches",
            "Aufgaben",
        ),
        "title_reminders": _t(
            "Lembretes",
            "Herinneringen",
            "Reminders",
            "Rappels",
            "Erinnerungen",
        ),
        "title_map": _t(
            "Mapa das 4 semanas",
            "Kaart van 4 weken",
            "Map of 4 weeks",
            "Carte des 4 semaines",
            "Karte der 4 Wochen",
        ),
        "title_notes": _t(
            "Notas recentes",
            "Recente notities",
            "Recent notes",
            "Notes récentes",
            "Neueste Notizen",
        ),
        "tasks_open": _t(
            "abertas",
            "open",
            "open",
            "en cours",
            "offen",
        ),
        "tasks_overdue_l": _t(
            "atrasadas",
            "achterstallig",
            "overdue",
            "en retard",
            "überfällig",
        ),
        "tasks_week_l": _t(
            "com prazo em 7 dias",
            "met deadline binnen 7 dagen",
            "due in 7 days",
            "à échéance sous 7 jours",
            "fällig in 7 Tagen",
        ),
        "tasks_done_l": _t(
            "concluídas em 7 dias",
            "afgerond in 7 dagen",
            "done in 7 days",
            "terminées en 7 jours",
            "erledigt in 7 Tagen",
        ),
        "map_legend": _t(
            "quanto mais escuro, mais compromissos",
            "donkerder betekent meer afspraken",
            "darker means more appointments",
            "plus c'est foncé, plus il y a de rendez-vous",
            "je dunkler, desto mehr Termine",
        ),
        "rem_kind_medication_reminder": _t(
            "Medicação",
            "Medicatie",
            "Medication",
            "Médicament",
            "Medikamente",
        ),
        "rem_kind_reminder": _t(
            "Lembrete",
            "Herinnering",
            "Reminder",
            "Rappel",
            "Erinnerung",
        ),
        "rem_monthly": _t(
            "todo dia {d} do mês",
            "elke maand op de {d}e",
            "on the {d}th of every month",
            "le {d} de chaque mois",
            "jeden Monat am {d}.",
        ),
        "rem_kind_goal_checkin": _t(
            "Meta",
            "Doel",
            "Goal",
            "Objectif",
            "Ziel",
        ),
        "rem_kind_workout_reminder": _t(
            "Treino",
            "Training",
            "Workout",
            "Entraînement",
            "Training",
        ),
        "rem_kind_weekly_summary": _t(
            "Resumo semanal",
            "Weekoverzicht",
            "Weekly summary",
            "Résumé hebdomadaire",
            "Wochenübersicht",
        ),
        "rem_every_day": _t(
            "todo dia",
            "elke dag",
            "every day",
            "tous les jours",
            "jeden Tag",
        ),
        "day_today": _t(
            "Hoje",
            "Vandaag",
            "Today",
            "Aujourd'hui",
            "Heute",
        ),
        "day_tomorrow": _t(
            "Amanhã",
            "Morgen",
            "Tomorrow",
            "Demain",
            "Morgen",
        ),
        "title_water": _t(
            "Água",
            "Water",
            "Water",
            "Eau",
            "Wasser",
        ),
        "title_workouts": _t(
            "Treinos",
            "Trainingen",
            "Workouts",
            "Entraînements",
            "Workouts",
        ),
        "title_goals": _t(
            "Hábitos e metas",
            "Gewoonten en doelen",
            "Habits and goals",
            "Habitudes et objectifs",
            "Gewohnheiten und Ziele",
        ),
        "water_today": _t(
            "hoje",
            "vandaag",
            "today",
            "aujourd'hui",
            "heute",
        ),
        "water_avg_l": _t(
            "média por dia com registro",
            "gemiddeld per dag met registratie",
            "average per logged day",
            "moyenne par jour enregistré",
            "Schnitt pro erfasstem Tag",
        ),
        "workouts_this": _t(
            "esta semana",
            "deze week",
            "this week",
            "cette semaine",
            "diese Woche",
        ),
        "workouts_last": _t(
            "semana passada",
            "vorige week",
            "last week",
            "semaine dernière",
            "letzte Woche",
        ),
        "workouts_km": _t(
            "{km} km no total",
            "{km} km in totaal",
            "{km} km in total",
            "{km} km au total",
            "{km} km insgesamt",
        ),
        "workouts_weeks_l": _t(
            "Últimas 4 semanas",
            "Laatste 4 weken",
            "Last 4 weeks",
            "4 dernières semaines",
            "Letzte 4 Wochen",
        ),
        "goal_days": _t(
            "{n} de 7 dias",
            "{n} van 7 dagen",
            "{n} of 7 days",
            "{n} jours sur 7",
            "{n} von 7 Tagen",
        ),
        "goal_saved": _t(
            "{saved} de {target}",
            "{saved} van {target}",
            "{saved} of {target}",
            "{saved} sur {target}",
            "{saved} von {target}",
        ),
        "goal_until": _t(
            "até {date}",
            "tot {date}",
            "until {date}",
            "jusqu'au {date}",
            "bis {date}",
        ),
        "health_note": _t(
            "Só mostra o que você registrou. Não é conselho médico.",
            "Toont alleen wat je hebt vastgelegd. Dit is geen medisch advies.",
            "Shows only what you recorded. This is not medical advice.",
            "Montre seulement ce que tu as enregistré. Ce n'est pas un conseil médical.",
            "Zeigt nur, was du erfasst hast. Das ist keine medizinische Beratung.",
        ),
        "title_trip": _t(
            "Viagem",
            "Reis",
            "Trip",
            "Voyage",
            "Reise",
        ),
        "trip_budget_l": _t(
            "Orçamento",
            "Budget",
            "Budget",
            "Budget",
            "Budget",
        ),
        "trip_spent_l": _t(
            "Gasto",
            "Uitgegeven",
            "Spent",
            "Dépensé",
            "Ausgegeben",
        ),
        "trip_left_l": _t(
            "Ainda disponível",
            "Nog beschikbaar",
            "Still available",
            "Encore disponible",
            "Noch verfügbar",
        ),
        "trip_over_l": _t(
            "Acima do orçamento",
            "Boven het budget",
            "Over budget",
            "Au-dessus du budget",
            "Über dem Budget",
        ),
        "trip_state_active": _t(
            "em andamento",
            "onderweg",
            "under way",
            "en cours",
            "unterwegs",
        ),
        "trip_state_upcoming": _t(
            "começa em {n} dias",
            "begint over {n} dagen",
            "starts in {n} days",
            "commence dans {n} jours",
            "beginnt in {n} Tagen",
        ),
        "trip_state_upcoming_one": _t(
            "começa em {n} dia",
            "begint over {n} dag",
            "starts in {n} day",
            "commence dans {n} jour",
            "beginnt in {n} Tag",
        ),
        "trip_state_ended": _t(
            "encerrada",
            "afgelopen",
            "ended",
            "terminé",
            "beendet",
        ),
        "trip_no_budget": _t(
            "sem orçamento definido",
            "geen budget ingesteld",
            "no budget set",
            "pas de budget défini",
            "kein Budget festgelegt",
        ),
        "trip_days": _t(
            "{n} dias",
            "{n} dagen",
            "{n} days",
            "{n} jours",
            "{n} Tage",
        ),
        "trip_days_one": _t(
            "{n} dia",
            "{n} dag",
            "{n} day",
            "{n} jour",
            "{n} Tag",
        ),
        "trip_cats": _t(
            "Gasto por categoria",
            "Uitgaven per categorie",
            "Spending by category",
            "Dépenses par catégorie",
            "Ausgaben nach Kategorie",
        ),
        "trip_daily": _t(
            "Gasto por dia",
            "Uitgaven per dag",
            "Spending per day",
            "Dépenses par jour",
            "Ausgaben pro Tag",
        ),
        "title_past": _t(
            "Viagens anteriores",
            "Eerdere reizen",
            "Previous trips",
            "Voyages précédents",
            "Frühere Reisen",
        ),
        "past_within": _t(
            "dentro do orçamento",
            "binnen het budget",
            "within budget",
            "dans le budget",
            "im Budget",
        ),
        "past_over": _t(
            "acima do orçamento",
            "boven het budget",
            "over budget",
            "au-dessus du budget",
            "über dem Budget",
        ),
        "title_training": _t(
            "Plano de treino",
            "Trainingsschema",
            "Training plan",
            "Plan d'entraînement",
            "Trainingsplan",
        ),
        "title_itinerary": _t("Roteiro", "Reisschema", "Itinerary", "Itinéraire", "Reiseplan"),
        "title_packing": _t("Bagagem", "Paklijst", "Packing list", "Bagages", "Packliste"),
        "title_planbudget": _t(
            "Planejado e gasto",
            "Gepland en uitgegeven",
            "Planned and spent",
            "Prévu et dépensé",
            "Geplant und ausgegeben",
        ),
        "training_evolution": _t(
            "Evolução das cargas",
            "Voortgang van gewichten",
            "Load progress",
            "Évolution des charges",
            "Entwicklung der Lasten",
        ),
        "training_today": _t("Hoje", "Vandaag", "Today", "Aujourd'hui", "Heute"),
        "training_rest": _t(
            "Hoje é dia de descanso no seu plano.",
            "Vandaag is een rustdag in je schema.",
            "Today is a rest day in your plan.",
            "Aujourd'hui est un jour de repos dans ton plan.",
            "Heute ist in deinem Plan ein Ruhetag.",
        ),
        "training_plan_kg": _t("plano {kg}", "plan {kg}", "plan {kg}", "prévu {kg}", "Plan {kg}"),
        "training_since": _t(
            "desde {date}", "sinds {date}", "since {date}", "depuis le {date}", "seit {date}"
        ),
        "training_first": _t("1 registro", "1 registratie", "1 entry", "1 relevé", "1 Eintrag"),
        "pack_count": _t(
            "{done} de {total} na mala",
            "{done} van {total} ingepakt",
            "{done} of {total} packed",
            "{done} sur {total} dans la valise",
            "{done} von {total} gepackt",
        ),
        "pack_yes": _t("na mala", "ingepakt", "packed", "dans la valise", "gepackt"),
        "pack_no": _t("falta", "nog te doen", "to pack", "à mettre", "offen"),
        "planbudget_total": _t(
            "{spent} de {plan} planejados",
            "{spent} van {plan} gepland",
            "{spent} of {plan} planned",
            "{spent} sur {plan} prévus",
            "{spent} von {plan} geplant",
        ),
        "past_budget_of": _t(
            "orçamento {budget}",
            "budget {budget}",
            "budget {budget}",
            "budget {budget}",
            "Budget {budget}",
        ),
    }
)


# V2-10 — Casa tab (only members with a partner)
SHELL.update(
    {
        "title_home_balance": _t(
            "Casa de vocês", "Jullie huis", "Your home", "Foyer commun", "Euer Haushalt"
        ),
        "title_home_entries": _t(
            "Gastos da casa no mês",
            "Gedeelde uitgaven deze maand",
            "Shared expenses this month",
            "Dépenses communes ce mois-ci",
            "Gemeinsame Ausgaben diesen Monat",
        ),
        "home_month_total": _t(
            "gasto da casa no mês",
            "gedeeld deze maand",
            "shared this month",
            "commun ce mois-ci",
            "gemeinsam diesen Monat",
        ),
        "home_paid_me": _t(
            "Você pagou", "Jij betaalde", "You paid", "Tu as payé", "Du hast bezahlt"
        ),
        "home_paid_partner": _t(
            "{name} pagou",
            "{name} betaalde",
            "{name} paid",
            "{name} a payé",
            "{name} hat bezahlt",
        ),
        "home_split": _t(
            "Divisão: você {me}% e {name} {other}%",
            "Verdeling: jij {me}% en {name} {other}%",
            "Split: you {me}% and {name} {other}%",
            "Répartition : toi {me}% et {name} {other}%",
            "Aufteilung: du {me}% und {name} {other}%",
        ),
        "home_owes_me": _t(
            "{name} te deve {amount}",
            "{name} is jou {amount} schuldig",
            "{name} owes you {amount}",
            "{name} te doit {amount}",
            "{name} schuldet dir {amount}",
        ),
        "home_i_owe": _t(
            "Você deve {amount} a {name}",
            "Jij bent {name} {amount} schuldig",
            "You owe {name} {amount}",
            "Tu dois {amount} à {name}",
            "Du schuldest {name} {amount}",
        ),
        "home_even": _t(
            "Vocês estão em dia",
            "Jullie staan quitte",
            "You are even",
            "Les comptes sont équilibrés",
            "Alles ausgeglichen",
        ),
        "home_by_me": _t("você", "jij", "you", "toi", "du"),
    }
)


def categories(lang: str | None) -> list[dict[str, str]]:
    """The closed list of categories for the filter (id is what the URL carries)."""
    from alfred.labels import _LABELS, category_label

    lang = normalize_lang(lang)
    return [{"id": c, "label": category_label(c, lang)} for c in _LABELS]


# V2-10: the sixth tab exists only for a member with a partner (a phone fits five tabs)
_HOME_TAB = (
    "home",
    {"pt": "Casa", "nl": "Huis", "en": "Home", "fr": "Foyer", "de": "Haushalt"},
)


def tabs(lang: str | None, home: bool = False) -> list[dict[str, str]]:
    lang = normalize_lang(lang)
    items = (*_TABS, _HOME_TAB) if home else _TABS
    return [{"id": i, "label": names[lang], "path": f"/api/d/{{token}}/{i}"} for i, names in items]


def shell(lang: str | None) -> dict[str, str]:
    lang = normalize_lang(lang)
    return {k: v[lang] for k, v in SHELL.items()}
