"""Conversation handler — routes inbound messages to the right response.

State machine:
  pending          → sends disclosure, advances to pending_response
  pending_response + sim/yes/…   → accepted, sends confirmation
  pending_response + nao/no/…    → rejected, sends rejection message
  pending_response + ?           → stays pending_response, sends reminder
  rejected         → silently ignored
  accepted         → commands or LLM reply with conversation history
"""

from __future__ import annotations

import random
import re
import unicodedata
import uuid
from datetime import UTC, date, datetime, time, timedelta

import structlog
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import set_committed_value

from alfred import balance_view, bills, budgets, habits_ext, questions
from alfred import goals as goals_view
from alfred import google_calendar as _gcal
from alfred.accounting import STRINGS as _ACCT_STRINGS
from alfred.accounting import default_scope, handle_accounting_command
from alfred.agenda import STRINGS as _AGENDA_STRINGS
from alfred.agenda import handle_agenda_command
from alfred.agenda import handle_button as handle_agenda_button
from alfred.analysis import STRINGS as _ANALYSIS_STRINGS
from alfred.analysis import handle_analysis_question, handle_view_command, looks_like_analysis
from alfred.audit import audit
from alfred.batch import STRINGS as _BATCH_STRINGS
from alfred.batch import clean_items, create_draft, handle_batch_button, handle_batch_text
from alfred.budgets import STRINGS as _BUDGET_STRINGS
from alfred.budgets import alert_after_expense, handle_budget_command
from alfred.clock import (
    day_start,
    local_tz,
    month_name,
    month_start,
    now_local,
    prev_month_start,
    to_local,
    today_local,
    use_member_timezone,
    week_start,
    weekday_abbr,
)
from alfred.couple import STRINGS as _COUPLE_STRINGS
from alfred.couple import (
    Reply,
    active_link,
    handle_couple_button,
    handle_couple_command,
    is_home_by_default,
)
from alfred.followup import (
    STRINGS as _FOLLOWUP_STRINGS,
)
from alfred.followup import (
    deeper_buttons,
    handle_deeper_button,
    handle_feedback_button,
)
from alfred.goals import STRINGS as _GOALS_STRINGS
from alfred.goals import handle_goal_command
from alfred.google_calendar import STRINGS as _GCAL_STRINGS
from alfred.home import STRINGS as _HOME_STRINGS
from alfred.home import handle_home_button, handle_home_command
from alfred.insights import STRINGS as _INSIGHT_STRINGS
from alfred.insights import handle_insight_command
from alfred.invoices import STRINGS as _INVOICE_STRINGS
from alfred.invoices import handle_invoice_button, handle_invoice_command
from alfred.iou import STRINGS as _IOU_STRINGS
from alfred.iou import handle_iou_command
from alfred.labels import activity_label as activity_name
from alfred.labels import category_label
from alfred.lang_cmd import (
    LANG_ROWS,
    is_bare_language_name,
    is_language_menu_request,
    parse_language_choice,
    parse_language_command,
)
from alfred.lang_cmd import STRINGS as _LANG_STRINGS
from alfred.ledger_status import STRINGS as _LEDGER_STRINGS
from alfred.ledger_status import handle_ledger_command
from alfred.llm import (
    classify_query,
    extract_expense,
    extract_expenses_multi,
    extract_habit,
    extract_health_log,
    extract_workout,
    generate_reply,
)
from alfred.media_input import STRINGS as _MEDIA_STRINGS
from alfred.media_input import handle_media_file, is_media_file
from alfred.models import (
    SETTLED,
    AuditLog,
    Expense,
    Goal,
    HabitLog,
    HealthLog,
    Member,
    MerchantCategoryOverride,
    Message,
    Note,
    PendingAction,
    Task,
    Trip,
    TripBudgetLine,
    WorkoutSession,
)
from alfred.monthly_summary import STRINGS as _MSUM_STRINGS
from alfred.monthly_summary import handle_monthly_summary_command
from alfred.nl_calendar import STRINGS as _CAL_STRINGS
from alfred.nl_calendar import handle_calendar_button, handle_calendar_command
from alfred.observability import alert
from alfred.outbox import STRINGS as _OUTBOX_STRINGS
from alfred.outbox import handle_outbox_command
from alfred.parsing import (
    CORRECTION_PRONOUNS,
    DELETE_LAST_EXPENSE_RE,
    INCOME_QUERY_RE,
    MONTH_WORDS,
    TOP_CATEGORIES_RE,
    TRIP_END_RE,
    TRIP_LIST_RE,
    TRIP_QUERY_RE,
    like_escape,
    match_trip_start,
    parse_budget,
    parse_category_correction,
    parse_last_n,
    parse_period,
    parse_trip_start_date,
    strip_accents,
    to_amount,
    trip_bare_budget,
)
from alfred.receipt import STRINGS as _RECEIPT_STRINGS
from alfred.recurrence import cadence_label, parse_recurrence
from alfred.recurring import STRINGS as _RECURRING_STRINGS
from alfred.recurring import handle_recurring_button, handle_recurring_command
from alfred.score import STRINGS as _SCORE_STRINGS
from alfred.score import handle_score_command
from alfred.services import STRINGS as _SERVICE_STRINGS
from alfred.services import handle_service_button, handle_service_command
from alfred.settings import settings
from alfred.signup import STRINGS as _SIGNUP_STRINGS
from alfred.signup import WAITING_STATES as SIGNUP_WAITING_STATES
from alfred.signup import send_invite as send_signup_invite
from alfred.statement import STRINGS as _STATEMENT_STRINGS
from alfred.statement import (
    handle_document,
    handle_statement_button,
    handle_statement_command,
)
from alfred.training import STRINGS as _TRAINING_STRINGS
from alfred.training import handle_training_button, handle_training_command
from alfred.training_media import STRINGS as _TRAINING_MEDIA_STRINGS
from alfred.tripplan import STRINGS as _TRIPPLAN_STRINGS
from alfred.tripplan import handle_tripplan_command
from alfred.validation import MAX_AMOUNT
from alfred.whatsapp import send_buttons, send_cta_url, send_text

logger = structlog.get_logger()

# ── i18n ─────────────────────────────────────────────────────────────────────

_SUPPORTED_LANGS = ("pt", "nl", "en", "fr", "de")

_STRINGS: dict[str, dict[str, str | tuple[str, ...]]] = {
    "consent_accepted": {
        "pt": (
            'Tudo pronto! Me diga o que você gastou e eu anoto, por exemplo:\n\n• "gastei €45 no Jumbo" — anotar uma despesa\n• "recebi €2.800 de salário" — anotar uma receita\n• "resumo" — ver os gastos do mês\n• "ajuda" — ver tudo o que sei fazer'
        ),
        "nl": (
            "Alles klaar. Je kunt nu beginnen.\n\n"
            '• "€45 uitgegeven bij Jumbo" — uitgave registreren\n'
            '• "€2.800 salaris ontvangen" — inkomsten registreren\n'
            '• "overzicht" — uitgaven van de maand bekijken\n'
            '• "hulp" — alle commando\'s bekijken'
        ),
        "en": (
            "All set. You can start now.\n\n"
            '• "spent €45 at Jumbo" — record expense\n'
            '• "received €2,800 salary" — record income\n'
            '• "summary" — view monthly expenses\n'
            '• "help" — see all commands'
        ),
        "fr": (
            "Tout est prêt. Tu peux commencer maintenant.\n\n"
            '• "dépensé €45 au Jumbo" — enregistrer une dépense\n'
            '• "reçu €2.800 de salaire" — enregistrer un revenu\n'
            '• "résumé" — voir les dépenses du mois\n'
            '• "aide" — voir toutes les commandes'
        ),
        "de": (
            "Alles bereit. Du kannst jetzt beginnen.\n\n"
            '• "€45 bei Jumbo ausgegeben" — Ausgabe erfassen\n'
            '• "€2.800 Gehalt erhalten" — Einnahme erfassen\n'
            '• "übersicht" — Monatsausgaben anzeigen\n'
            '• "hilfe" — alle Befehle anzeigen'
        ),
    },
    "greet_word": {"pt": "Oi", "nl": "Hoi", "en": "Hi", "fr": "Bonjour", "de": "Hallo"},
    "greet_reply": {
        "pt": "{hello}! Em que posso ajudar? Pode me mandar um gasto, como “mercado 20”, perguntar o *saldo* ou dizer *ajuda*.",
        "nl": "{hello}! Waarmee kan ik helpen? Stuur een uitgave, zoals “boodschappen 20”, vraag je *saldo* of zeg *help*.",
        "en": "{hello}! How can I help? Send me an expense, like “groceries 20”, ask for your *balance* or say *help*.",
        "fr": "{hello} ! Je peux t'aider ? Envoie une dépense, comme « courses 20 », demande ton *solde* ou dis *aide*.",
        "de": "{hello}! Wobei kann ich helfen? Schick mir eine Ausgabe wie „Einkauf 20“, frag nach dem *Saldo* oder sag *hilfe*.",
    },
    "resumed_welcome": {
        "pt": "Que bom ter você de volta! Está tudo como você deixou. Me diga o que precisa.",
        "nl": "Fijn dat je terug bent! Alles staat nog zoals je het liet. Waarmee kan ik helpen?",
        "en": "Good to have you back! Everything is as you left it. What do you need?",
        "fr": "Content de te revoir ! Tout est comme tu l'as laissé. De quoi as-tu besoin ?",
        "de": "Schön, dass du wieder da bist! Alles ist so, wie du es verlassen hast. Was brauchst du?",
    },
    "leave_explain": {
        "pt": "Se quiser que eu pare de mandar mensagens, envie *STOP* (para voltar, *START*). Se quiser apagar tudo o que guardei, diga *apagar meus dados*.",
        "nl": "Wil je dat ik geen berichten meer stuur? Stuur *STOP* (terug met *START*). Wil je alles wissen wat ik heb bewaard? Zeg *verwijder mijn gegevens*.",
        "en": "If you want me to stop messaging, send *STOP* (to come back, *START*). To erase everything I keep, say *delete my data*.",
        "fr": "Pour que j'arrête d'envoyer des messages, envoie *STOP* (pour revenir, *START*). Pour tout effacer, dis *supprimer mes données*.",
        "de": "Wenn ich keine Nachrichten mehr schicken soll, sende *STOP* (zurück mit *START*). Um alles zu löschen, sag *meine Daten löschen*.",
    },
    "consent_rejected": {
        "pt": "Entendido, não vou processar mais mensagens. Para voltar, envie *START*.",
        "nl": "Begrepen. Ik verwerk geen berichten meer. Stuur *START* om te hervatten.",
        "en": "Understood. I won't process any more messages. Send *START* to resume.",
        "fr": "Compris. Je ne traiterai plus de messages. Envoie *START* pour reprendre.",
        "de": "Verstanden. Ich verarbeite keine Nachrichten mehr. Sende *START*, um fortzufahren.",
    },
    "help": {
        "pt": (
            '*Jofie* — o que eu posso fazer por você:\n\n*Despesas*\n• "gastei €45 no Jumbo"\n• "Uber 12,50"\n• "paguei €180 de aluguel"\n\n*Receitas*\n• "recebi €2.800 de salário"\n\n*Consultas*\n• "resumo" — gastos do mês\n• "saldo" — receitas e despesas\n• "gastos desta semana" — por período\n• "compara este mês com o mês passado"\n• "ajuda" — esta mensagem\n\n_Para sair: "stop"_'
        ),
        "nl": (
            "*Jofie* — wat ik voor je kan doen:\n\n"
            "*Uitgaven registreren*\n"
            '• "€45 uitgegeven bij Jumbo"\n'
            '• "Uber 12,50"\n'
            '• "€180 huur betaald"\n\n'
            "*Inkomsten registreren*\n"
            '• "€2.800 salaris ontvangen"\n\n'
            "*Opvragen*\n"
            '• "overzicht" — uitgaven van de maand\n'
            '• "saldo" — inkomsten/uitgaven balans\n'
            '• "uitgaven deze week" — per periode\n'
            '• "vergelijk deze maand met vorige maand"\n'
            '• "hulp" — dit bericht\n\n'
            '_Om te stoppen: "stoppen"_'
        ),
        "en": (
            "*Jofie* — what I can do for you:\n\n"
            "*Record expenses*\n"
            '• "spent €45 at Jumbo"\n'
            '• "Uber 12.50"\n'
            '• "paid €180 rent"\n\n'
            "*Record income*\n"
            '• "received €2,800 salary"\n\n'
            "*Queries*\n"
            '• "summary" — monthly expenses\n'
            '• "balance" — income/expense balance\n'
            '• "expenses this week" — by period\n'
            '• "compare this month with last month"\n'
            '• "help" — this message\n\n'
            '_To stop: "stop"_'
        ),
        "fr": (
            "*Jofie* — ce que je peux faire pour toi :\n\n"
            "*Enregistrer des dépenses*\n"
            '• "dépensé €45 au Jumbo"\n'
            '• "Uber 12,50"\n'
            '• "payé €180 de loyer"\n\n'
            "*Enregistrer des revenus*\n"
            '• "reçu €2.800 de salaire"\n\n'
            "*Consulter*\n"
            '• "résumé" — dépenses du mois\n'
            '• "solde" — balance revenus/dépenses\n'
            '• "dépenses cette semaine" — par période\n'
            '• "compare ce mois avec le mois dernier"\n'
            '• "aide" — ce message\n\n'
            '_Pour arrêter : "stop"_'
        ),
        "de": (
            "*Jofie* — was ich für dich tun kann:\n\n"
            "*Ausgaben erfassen*\n"
            '• "€45 bei Jumbo ausgegeben"\n'
            '• "Uber 12,50"\n'
            '• "€180 Miete bezahlt"\n\n'
            "*Einnahmen erfassen*\n"
            '• "€2.800 Gehalt erhalten"\n\n'
            "*Abfragen*\n"
            '• "Übersicht" — Monatsausgaben\n'
            '• "Bilanz" — Einnahmen/Ausgaben-Balance\n'
            '• "Ausgaben diese Woche" — nach Zeitraum\n'
            '• "vergleiche diesen Monat mit letztem Monat"\n'
            '• "hilfe" — diese Nachricht\n\n'
            '_Zum Beenden: "stop"_'
        ),
    },
    "no_records_scope": {
        "pt": "Não encontrei nada em *{category}* {phrase}.",
        "nl": "Ik vond niets in *{category}* {phrase}.",
        "en": "I found nothing in *{category}* {phrase}.",
        "fr": "Je n'ai rien trouvé dans *{category}* {phrase}.",
        "de": "Ich habe {phrase} nichts in *{category}* gefunden.",
    },
    "no_records_period": {
        "pt": "Não encontrei nenhum lançamento {phrase}.",
        "nl": "Ik vond geen registraties {phrase}.",
        "en": "I found no entries {phrase}.",
        "fr": "Je n'ai trouvé aucune opération {phrase}.",
        "de": "Ich habe {phrase} keine Einträge gefunden.",
    },
    "no_records_month": {
        "pt": "Ainda não tenho nada este mês. Envie a primeira despesa quando quiser.",
        "nl": "Geen registraties deze maand.",
        "en": "No records this month.",
        "fr": "Aucun enregistrement ce mois-ci.",
        "de": "Keine Einträge diesen Monat.",
    },
    "summary_title": {
        "pt": "*Gastos — {period_label}*",
        "nl": "*Uitgaven — {period_label}*",
        "en": "*Expenses — {period_label}*",
        "fr": "*Dépenses — {period_label}*",
        "de": "*Ausgaben — {period_label}*",
    },
    "category_title": {
        "pt": "*{category} — {period_label}*",
        "nl": "*{category} — {period_label}*",
        "en": "*{category} — {period_label}*",
        "fr": "*{category} — {period_label}*",
        "de": "*{category} — {period_label}*",
    },
    "total_expenses": {
        "pt": "*Total despesas: {amount}*",
        "nl": "*Totaal uitgaven: {amount}*",
        "en": "*Total expenses: {amount}*",
        "fr": "*Total dépenses : {amount}*",
        "de": "*Gesamtausgaben: {amount}*",
    },
    "category_total": {
        "pt": "*Total: {amount}*",
        "nl": "*Totaal: {amount}*",
        "en": "*Total: {amount}*",
        "fr": "*Total : {amount}*",
        "de": "*Gesamt: {amount}*",
    },
    "income_line": {
        "pt": "Receitas: {amount}",
        "nl": "Inkomsten: {amount}",
        "en": "Income: {amount}",
        "fr": "Revenus : {amount}",
        "de": "Einnahmen: {amount}",
    },
    "balance_line": {
        "pt": "_Saldo: {sign}{amount}_",
        "nl": "_Saldo: {sign}{amount}_",
        "en": "_Balance: {sign}{amount}_",
        "fr": "_Solde : {sign}{amount}_",
        "de": "_Saldo: {sign}{amount}_",
    },
    "transactions_count": {
        "pt": "_{n} transações_",
        "nl": "_{n} transacties_",
        "en": "_{n} transactions_",
        "fr": "_{n} transactions_",
        "de": "_{n} Buchungen_",
    },
    "saldo_title": {
        "pt": "*Saldo — {month}*",
        "nl": "*Saldo — {month}*",
        "en": "*Balance — {month}*",
        "fr": "*Solde — {month}*",
        "de": "*Saldo — {month}*",
    },
    "saldo_income": {
        "pt": "• Receitas: {amount}",
        "nl": "• Inkomsten: {amount}",
        "en": "• Income: {amount}",
        "fr": "• Revenus : {amount}",
        "de": "• Einnahmen: {amount}",
    },
    "saldo_expenses": {
        "pt": "• Despesas: {amount}",
        "nl": "• Uitgaven: {amount}",
        "en": "• Expenses: {amount}",
        "fr": "• Dépenses : {amount}",
        "de": "• Ausgaben: {amount}",
    },
    "saldo_note": {
        "pt": "_Receitas menos despesas do período, não o saldo do banco._",
        "nl": "_Inkomsten min uitgaven van de periode, niet je banksaldo._",
        "en": "_Income minus expenses for the period, not your bank balance._",
        "fr": "_Revenus moins dépenses de la période, pas le solde bancaire._",
        "de": "_Einnahmen minus Ausgaben im Zeitraum, nicht dein Kontostand._",
    },
    "saldo_balance": {
        "pt": "\n*Saldo: {sign}{amount}*",
        "nl": "\n*Saldo: {sign}{amount}*",
        "en": "\n*Balance: {sign}{amount}*",
        "fr": "\n*Solde : {sign}{amount}*",
        "de": "\n*Saldo: {sign}{amount}*",
    },
    "comparison_title": {
        "pt": "*Comparação de despesas*",
        "nl": "*Vergelijking uitgaven*",
        "en": "*Expense comparison*",
        "fr": "*Comparaison des dépenses*",
        "de": "*Ausgabenvergleich*",
    },
    "comparison_no_prev": {
        "pt": "sem dados no mês anterior",
        "nl": "geen gegevens vorige maand",
        "en": "no data for previous month",
        "fr": "pas de données le mois précédent",
        "de": "keine Daten für den Vormonat",
    },
    "comparison_equal": {
        "pt": "_igual ao mês anterior_",
        "nl": "_gelijk aan vorige maand_",
        "en": "_same as previous month_",
        "fr": "_identique au mois précédent_",
        "de": "_gleich wie letzter Monat_",
    },
    "expense_recorded": {
        "pt": (
            "Anotei: {amount} em *{name}*.",
            "Feito, {amount} em *{name}*.",
            "Registrado: *{name}*, {amount}.",
        ),
        "nl": (
            "Genoteerd: {amount} bij *{name}*.",
            "Gedaan, {amount} bij *{name}*.",
            "Vastgelegd: *{name}*, {amount}.",
        ),
        "en": (
            "Noted: {amount} at *{name}*.",
            "Done, {amount} at *{name}*.",
            "Logged: *{name}*, {amount}.",
        ),
        "fr": (
            "C’est noté : {amount} chez *{name}*.",
            "Fait, {amount} chez *{name}*.",
            "Enregistré : *{name}*, {amount}.",
        ),
        "de": (
            "Notiert: {amount} bei *{name}*.",
            "Erledigt, {amount} bei *{name}*.",
            "Eingetragen: *{name}*, {amount}.",
        ),
    },
    "income_recorded": {
        "pt": (
            "Ótimo, entrou {amount} de *{name}*.",
            "Anotei a receita: {amount} de *{name}*.",
            "Registrado: {amount} de *{name}*.",
        ),
        "nl": (
            "Mooi, er is {amount} binnengekomen van *{name}*.",
            "Inkomen genoteerd: {amount} van *{name}*.",
            "Vastgelegd: {amount} van *{name}*.",
        ),
        "en": (
            "Nice, {amount} came in from *{name}*.",
            "Income noted: {amount} from *{name}*.",
            "Logged: {amount} from *{name}*.",
        ),
        "fr": (
            "Super, {amount} reçus de *{name}*.",
            "Revenu noté : {amount} de *{name}*.",
            "Enregistré : {amount} de *{name}*.",
        ),
        "de": (
            "Schön, {amount} von *{name}* sind eingegangen.",
            "Einnahme notiert: {amount} von *{name}*.",
            "Eingetragen: {amount} von *{name}*.",
        ),
    },
    "category_income_refused": {
        "pt": '*{merchant}* foi registrado como despesa, então não fica em *Renda*. Se foi dinheiro que entrou, apague e mande de novo como entrada, por exemplo "recebi 20 de {merchant}".',
        "nl": '*{merchant}* staat als uitgave, dus het gaat niet naar *Inkomen*. Was het geld dat binnenkwam? Verwijder het en stuur het opnieuw als inkomsten, bijvoorbeeld "ontvangen 20 van {merchant}".',
        "en": '*{merchant}* was saved as an expense, so it can\'t go under *Income*. If it was money coming in, delete it and send it again as income, for example "received 20 from {merchant}".',
        "fr": "*{merchant}* est enregistré comme dépense, donc il ne va pas dans *Revenus*. Si c'était de l'argent reçu, supprime-le et renvoie-le comme revenu, par exemple \"reçu 20 de {merchant}\".",
        "de": '*{merchant}* ist als Ausgabe gespeichert und kommt deshalb nicht unter *Einkommen*. War es Geld, das reinkam? Lösche es und schick es neu als Einnahme, zum Beispiel "20 von {merchant} erhalten".',
    },
    "category_corrected": {
        "pt": "Combinado, a partir de agora *{merchant}* fica em *{category}*.",
        "nl": "✓ Begrepen! {merchant} → *{category}*. Ik onthoud dit voor de volgende keer.",
        "en": "✓ Got it! {merchant} → *{category}*. I'll remember that.",
        "fr": "✓ Compris ! {merchant} → *{category}*. Je m'en souviendrai.",
        "de": "✓ Verstanden! {merchant} → *{category}*. Das merke ich mir.",
    },
    "category_corrected_no_merchant": {
        "pt": "Não achei nenhuma despesa recente desse lugar para corrigir. Pode registrar de novo?",
        "nl": "Ik vond geen recente uitgave van die winkel om te corrigeren. Kun je het opnieuw invoeren?",
        "en": "I couldn't find a recent expense from that merchant to correct. Can you re-enter it?",
        "fr": "Je n'ai pas trouvé de dépense récente de ce marchand à corriger. Peux-tu la re-saisir ?",
        "de": "Ich habe keine aktuelle Ausgabe von diesem Händler gefunden, die ich korrigieren könnte. Kannst du sie erneut eingeben?",
    },
    "correction_no_expense": {
        "pt": "Não achei nenhuma despesa recente para corrigir. Pode registrar de novo?",
        "nl": "Ik vond geen recente uitgave om te corrigeren. Probeer het opnieuw in te voeren.",
        "en": "I couldn't find a recent expense to correct. Please re-enter it.",
        "fr": "Je n'ai pas trouvé de dépense récente à corriger. Peux-tu la re-saisir ?",
        "de": "Ich konnte keine aktuelle Ausgabe zum Korrigieren finden. Bitte gib sie erneut ein.",
    },
    "lembrete_set": {
        "pt": "⏰ Combinado! Vou te lembrar de *{text}* {when}, às {time}.",
        "nl": "⏰ Herinnering ingesteld: *{text}* om {time}, {when}.",
        "en": "⏰ Reminder set: *{text}* at {time}, {when}.",
        "fr": "⏰ Rappel configuré : *{text}* à {time}, {when}.",
        "de": "⏰ Erinnerung gesetzt: *{text}* um {time} Uhr, {when}.",
    },
    "lembrete_invalid": {
        "pt": "Não consegui pegar o horário. Tente assim: *lembrete: tomar o remédio às 08:00*",
        "nl": "Ongeldig formaat. Probeer: *herinnering: medicatie innemen om 08:00*",
        "en": "Invalid format. Try: *reminder: take medication at 08:00*",
        "fr": "Format invalide. Essaie : *rappel : prendre médicament à 08:00*",
        "de": "Ungültiges Format. Versuche: *Erinnerung: Medikament nehmen um 08:00*",
    },
    "days_ago_suffix": {
        "pt": " _(referente a {n}d atrás)_",
        "nl": " _({n}d geleden)_",
        "en": " _({n}d ago)_",
        "fr": " _(il y a {n}j)_",
        "de": " _(vor {n}T)_",
    },
    "period_yesterday": {
        "pt": "ontem",
        "nl": "gisteren",
        "en": "yesterday",
        "fr": "hier",
        "de": "gestern",
    },
    "currency_unsupported": {
        "pt": 'Por enquanto só trabalho em euros, então não guardei esse valor em {cur}. Se quiser, mande de novo convertido em €, por exemplo "Jumbo 23,50".',
        "nl": 'Ik registreer voorlopig alleen euro\'s — {cur} is niet opgeslagen. Reken om naar € en stuur opnieuw (bijv. "Jumbo 23,50").',
        "en": 'I only record euros for now — {cur} was not saved. Convert to € and send it again (e.g. "Jumbo 23.50").',
        "fr": "Je n'enregistre que des euros pour l'instant — {cur} n'a pas été enregistré. Convertis en € et renvoie (ex. « Jumbo 23,50 »).",
        "de": "Ich erfasse vorerst nur Euro — {cur} wurde nicht gespeichert. Rechne in € um und sende es erneut (z. B. „Jumbo 23,50“).",
    },
    "month_total_context": {
        "pt": " No mês, você já gastou {total}.",
        "nl": " Deze maand heb je al {total} uitgegeven.",
        "en": " That's {total} spent so far this month.",
        "fr": " Cela fait {total} dépensés ce mois-ci.",
        "de": " Das sind {total} Ausgaben in diesem Monat.",
    },
    "day_total_context": {
        "pt": " Hoje você já gastou {total}.",
        "nl": " Vandaag heb je al {total} uitgegeven.",
        "en": " That's {total} spent today.",
        "fr": " Cela fait {total} dépensés aujourd'hui.",
        "de": " Heute sind das {total} Ausgaben.",
    },
    "day_total_context_other": {
        "pt": " Nesse dia você gastou {total}.",
        "nl": " Die dag gaf je {total} uit.",
        "en": " That day you spent {total}.",
        "fr": " Ce jour-là, tu as dépensé {total}.",
        "de": " An dem Tag hast du {total} ausgegeben.",
    },
    "month_category_context": {
        "pt": (
            " Já são {total} em {category} este mês.",
            " Com essa, {category} chega a {total} no mês.",
        ),
        "nl": (
            " Dat is al {total} aan {category} deze maand.",
            " Met deze erbij is {category} deze maand op {total}.",
        ),
        "en": (
            " That's {total} on {category} so far this month.",
            " With this one, {category} is at {total} for the month.",
        ),
        "fr": (
            " Cela fait déjà {total} en {category} ce mois-ci.",
            " Avec celle-ci, {category} monte à {total} ce mois-ci.",
        ),
        "de": (
            " Das sind schon {total} für {category} in diesem Monat.",
            " Damit liegt {category} diesen Monat bei {total}.",
        ),
    },
    "health_unsupported": {
        "pt": "Ainda não acompanho peso nem pressão, só medicação, humor, sono e água.",
        "nl": "Gewicht en bloeddruk volg ik nog niet, alleen medicatie, stemming, slaap en water.",
        "en": "I don't track weight or blood pressure yet, only medication, mood, sleep and water.",
        "fr": "Je ne suis pas encore le poids ni la tension, seulement médicaments, humeur, sommeil et eau.",
        "de": "Gewicht und Blutdruck verfolge ich noch nicht, nur Medikamente, Stimmung, Schlaf und Wasser.",
    },
    "health_consent_ask": {
        "pt": "Antes de guardar dados de saúde (sono, humor, remédios, água), preciso da sua autorização. Eles ficam só para você ver aqui e no painel, e você pode retirar quando quiser com *retirar consentimento de saúde*.",
        "nl": "Voordat ik gezondheidsgegevens bewaar (slaap, stemming, medicijnen, water), heb ik je toestemming nodig. Alleen jij ziet ze, hier en in het dashboard, en je trekt ze in met *toestemming gezondheid intrekken*.",
        "en": "Before I keep health data (sleep, mood, medication, water), I need your permission. Only you see it, here and in the dashboard, and you can withdraw it any time with *withdraw health consent*.",
        "fr": "Avant de garder des données de santé (sommeil, humeur, médicaments, eau), j'ai besoin de ton accord. Toi seul les vois, ici et dans le tableau de bord, et tu peux le retirer avec *retirer consentement santé*.",
        "de": "Bevor ich Gesundheitsdaten speichere (Schlaf, Stimmung, Medikamente, Wasser), brauche ich deine Zustimmung. Nur du siehst sie, hier und im Dashboard, und du kannst sie mit *Gesundheitszustimmung widerrufen* zurückziehen.",
    },
    "btn_hc_yes": {
        "pt": "Autorizo",
        "nl": "Ik geef toestemming",
        "en": "I agree",
        "fr": "J'accepte",
        "de": "Ich stimme zu",
    },
    "btn_hc_no": {"pt": "Não autorizo", "nl": "Nee", "en": "No", "fr": "Non", "de": "Nein"},
    "health_consent_given": {
        "pt": "Obrigado, autorização registrada. Agora mande de novo o que quer registrar.",
        "nl": "Dank je, toestemming genoteerd. Stuur nu opnieuw wat je wilt bijhouden.",
        "en": "Thanks, permission recorded. Now send again what you want to log.",
        "fr": "Merci, accord enregistré. Renvoie maintenant ce que tu veux noter.",
        "de": "Danke, Zustimmung gespeichert. Schick jetzt noch einmal, was du festhalten willst.",
    },
    "health_consent_refused": {
        "pt": "Combinado, não guardo dados de saúde. Se mudar de ideia, é só registrar de novo.",
        "nl": "Prima, ik bewaar geen gezondheidsgegevens. Verander je van gedachten, stuur het dan opnieuw.",
        "en": "Fine, I won't keep health data. If you change your mind, just log it again.",
        "fr": "D'accord, je ne garde pas de données de santé. Si tu changes d'avis, renvoie-le.",
        "de": "In Ordnung, ich speichere keine Gesundheitsdaten. Wenn du es dir anders überlegst, schick es einfach noch einmal.",
    },
    "health_consent_withdrawn": {
        "pt": "Pronto: retirei a autorização e apaguei os seus dados de saúde ({n}). Não guardo mais esse tipo de informação.",
        "nl": "Klaar: toestemming ingetrokken en je gezondheidsgegevens verwijderd ({n}). Ik bewaar dit soort informatie niet meer.",
        "en": "Done: permission withdrawn and your health data deleted ({n}). I won't keep this kind of information any more.",
        "fr": "C'est fait : accord retiré et données de santé supprimées ({n}). Je ne garde plus ce type d'information.",
        "de": "Erledigt: Zustimmung widerrufen und deine Gesundheitsdaten gelöscht ({n}). Ich speichere diese Art von Informationen nicht mehr.",
    },
    "expense_delete_ask": {
        "pt": "Apagar {amount} em *{name}* ({date})?",
        "nl": "{amount} bij *{name}* ({date}) verwijderen?",
        "en": "Delete {amount} at *{name}* ({date})?",
        "fr": "Supprimer {amount} chez *{name}* ({date}) ?",
        "de": "{amount} bei *{name}* ({date}) löschen?",
    },
    "btn_delete": {
        "pt": "Apagar",
        "nl": "Verwijderen",
        "en": "Delete",
        "fr": "Supprimer",
        "de": "Löschen",
    },
    "btn_keep": {"pt": "Manter", "nl": "Behouden", "en": "Keep", "fr": "Garder", "de": "Behalten"},
    "multi_truncated": {
        "pt": "Li só os {n} primeiros. Mande o resto em outra mensagem.",
        "nl": "Ik heb alleen de eerste {n} gelezen. Stuur de rest in een ander bericht.",
        "en": "I only read the first {n}. Send the rest in another message.",
        "fr": "J'ai lu seulement les {n} premiers. Envoie le reste dans un autre message.",
        "de": "Ich habe nur die ersten {n} gelesen. Schick den Rest in einer weiteren Nachricht.",
    },
    "high_value_hint": {
        "pt": "\nÉ um valor alto, confere se está certo?",
        "nl": "\nHoog bedrag — weet je zeker dat het bedrag klopt?",
        "en": "\nHigh amount — are you sure about the value?",
        "fr": "\nMontant élevé — es-tu sûr du montant ?",
        "de": "\nHoher Betrag — bist du dir beim Betrag sicher?",
    },
    "invalid_amount_check": {
        "pt": "Esse valor não dá para registrar (zero ou negativo). Pode enviar novamente com o valor correto?",
        "nl": "Ongeldig bedrag (nul of negatief) — niets geregistreerd. Controleer en stuur opnieuw.",
        "en": "Invalid amount (zero or negative) — nothing recorded. Check it and send again.",
        "fr": "Montant invalide (zéro ou négatif) — rien enregistré. Vérifie et renvoie.",
        "de": "Ungültiger Betrag (null oder negativ) — nichts erfasst. Prüfe ihn und sende erneut.",
    },
    "multi_recorded_title": {
        "pt": "Anotei estes lançamentos ({n}):",
        "nl": "{n} transacties geregistreerd:",
        "en": "Recorded {n} transactions:",
        "fr": "{n} transactions enregistrées :",
        "de": "{n} Buchungen erfasst:",
    },
    "multi_skipped_currency": {
        "pt": "Não guardei {name}: estava em {cur} e por enquanto só trabalho em euros.",
        "nl": "{name} heb ik niet opgeslagen: dat was in {cur} en ik werk voorlopig alleen in euro's.",
        "en": "I didn't save {name}: it was in {cur} and for now I only work in euros.",
        "fr": "Je n'ai pas enregistré {name} : c'était en {cur} et pour l'instant je travaille seulement en euros.",
        "de": "{name} habe ich nicht gespeichert: Das war in {cur}, und ich arbeite vorerst nur in Euro.",
    },
    "fallback_no_record": {
        "pt": "Não consegui anotar nada. Envie uma despesa por linha, com valor e descrição, por exemplo:\nMercado 20\nFarmácia 10",
        "nl": "Ik heb niets geregistreerd. Stuur één uitgave per regel met bedrag en omschrijving, bijvoorbeeld:\nBoodschappen 20\nApotheek 10",
        "en": "I didn't record anything. Send one expense per line with amount and description, e.g.:\nGroceries 20\nPharmacy 10",
        "fr": "Je n'ai rien enregistré. Envoie une dépense par ligne avec montant et description, par ex. :\nCourses 20\nPharmacie 10",
        "de": "Ich habe nichts erfasst. Sende eine Ausgabe pro Zeile mit Betrag und Beschreibung, z. B.:\nEinkauf 20\nApotheke 10",
    },
    "fallback_unknown": {
        "pt": (
            "Essa escapou de mim. Pode dizer de outro jeito? Por exemplo: “Mercado 20”, “corri 5km” ou “dormi 7h”.",
            "Não entendi essa. Tente algo como “Mercado 20”, “corri 5km” ou “dormi 7h”.",
        ),
        "nl": (
            "Die is me ontglipt. Kun je het anders zeggen? Bijvoorbeeld: “Jumbo 20”, “ik heb 5 km gerend” of “ik sliep 7 uur”.",
            "Dat snap ik niet. Probeer iets als “Jumbo 20”, “ik heb 5 km gerend” of “ik sliep 7 uur”.",
        ),
        "en": (
            "That one slipped past me. Could you put it another way? For example: “Groceries 20”, “ran 5km” or “slept 7h”.",
            "I didn't get that. Try something like “Groceries 20”, “ran 5km” or “slept 7h”.",
        ),
        "fr": (
            "Celle-ci m’a échappé. Tu peux la formuler autrement ? Par exemple : « Courses 20 », « couru 5 km » ou « dormi 7 h ».",
            "Je n’ai pas compris. Essaie par exemple : « Courses 20 », « couru 5 km » ou « dormi 7 h ».",
        ),
        "de": (
            "Das ist mir entgangen. Kannst du es anders sagen? Zum Beispiel: „Einkauf 20“, „5 km gelaufen“ oder „7 Std. geschlafen“.",
            "Das habe ich nicht verstanden. Versuch es mit „Einkauf 20“, „5 km gelaufen“ oder „7 Std. geschlafen“.",
        ),
    },
    "bare_yes": {
        "pt": (
            "Sim para quê? Não tenho nada esperando confirmação. Me diga o que quer registrar, por exemplo: “Mercado 20”.",
            "Fico feliz com o sim, mas não há nada pendente. Para registrar algo, é só escrever, por exemplo: “Café 3,50”.",
            "Não tenho nenhuma pergunta aberta. Quer anotar um gasto (“Mercado 20”), um treino (“corri 5 km”) ou ver o saldo?",
        ),
        "nl": (
            "Ja, waarvoor? Er staat niets open om te bevestigen. Zeg wat je wilt vastleggen, bijvoorbeeld: “Jumbo 20”.",
            "Fijn, maar er is niets in behandeling. Wil je iets noteren, schrijf bijvoorbeeld: “Koffie 3,50”.",
            "Ik heb geen open vraag. Wil je een uitgave noteren (“Jumbo 20”), een training (“5 km gelopen”) of je saldo zien?",
        ),
        "en": (
            "Yes to what? Nothing is waiting for confirmation. Tell me what to log, e.g. “Groceries 20”.",
            "Glad to hear it, but nothing is pending. To log something, just write it, e.g. “Coffee 3.50”.",
            "I have no open question. Want to log an expense (“Groceries 20”), a workout (“ran 5 km”) or see your balance?",
        ),
        "fr": (
            "Oui à quoi ? Rien n'attend de confirmation. Dis-moi ce que tu veux noter, par ex. « Courses 20 ».",
            "Bien noté, mais rien n'est en attente. Pour enregistrer quelque chose, écris-le, par ex. « Café 3,50 ».",
            "Je n'ai aucune question ouverte. Tu veux noter une dépense (« Courses 20 »), un entraînement (« couru 5 km ») ou voir ton solde ?",
        ),
        "de": (
            "Ja, wozu? Es wartet nichts auf Bestätigung. Sag mir, was ich erfassen soll, z. B. „Einkauf 20“.",
            "Schön, aber es ist nichts offen. Zum Erfassen schreib es einfach, z. B. „Kaffee 3,50“.",
            "Ich habe keine offene Frage. Möchtest du eine Ausgabe erfassen („Einkauf 20“), ein Training („5 km gelaufen“) oder dein Saldo sehen?",
        ),
    },
    "expense_corrected": {
        "pt": "Corrigido: {name}, {old} → {amount}.",
        "nl": "Gecorrigeerd — {name}: {old} → {amount}",
        "en": "Corrected — {name}: {old} → {amount}",
        "fr": "Corrigé — {name} : {old} → {amount}",
        "de": "Korrigiert — {name}: {old} → {amount}",
    },
    "wipe_ask": {
        "pt": "Isso apaga *tudo* o que guardei sobre você (despesas, notas, metas, mensagens) e não dá para desfazer. Quer mesmo?",
        "nl": "Ik verwijder *al* je gegevens (uitgaven, notities, doelen, berichten…). Dit kan niet ongedaan worden gemaakt. Bevestig je?",
        "en": "I'll delete *all* your data (expenses, notes, goals, messages…). This can't be undone. Confirm?",
        "fr": "Je vais supprimer *toutes* tes données (dépenses, notes, objectifs, messages…). C'est irréversible. Tu confirmes ?",
        "de": "Ich lösche *alle* deine Daten (Ausgaben, Notizen, Ziele, Nachrichten…). Das lässt sich nicht rückgängig machen. Bestätigst du?",
    },
    "btn_wipe": {
        "pt": "Apagar tudo",
        "nl": "Alles verwijderen",
        "en": "Delete everything",
        "fr": "Tout supprimer",
        "de": "Alles löschen",
    },
    "btn_cancel": {
        "pt": "Cancelar",
        "nl": "Annuleren",
        "en": "Cancel",
        "fr": "Annuler",
        "de": "Abbrechen",
    },
    "wipe_done": {
        "pt": "Pronto, apaguei tudo. Se quiser voltar, é só mandar uma mensagem.",
        "nl": "Klaar. Ik heb al je gegevens verwijderd. Wil je terugkomen, stuur dan gewoon een bericht.",
        "en": "Done. I deleted all your data. If you want to come back, just send a message.",
        "fr": "C'est fait. J'ai supprimé toutes tes données. Pour revenir, envoie simplement un message.",
        "de": "Erledigt. Ich habe alle deine Daten gelöscht. Wenn du zurückkommen willst, schreib einfach eine Nachricht.",
    },
    "wipe_cancelled": {
        "pt": "Ok, não apaguei nada.",
        "nl": "Oké, ik heb niets verwijderd.",
        "en": "OK, I deleted nothing.",
        "fr": "D'accord, je n'ai rien supprimé.",
        "de": "OK, ich habe nichts gelöscht.",
    },
    "wipe_expired": {
        "pt": "Esse pedido expirou. Se ainda quiser apagar seus dados, escreva *apagar meus dados* de novo.",
        "nl": "Dit verzoek is verlopen. Wil je je gegevens nog verwijderen, schrijf dan opnieuw *verwijder mijn gegevens*.",
        "en": "This request expired. If you still want your data deleted, write *delete my data* again.",
        "fr": "Cette demande a expiré. Pour supprimer tes données, écris à nouveau *supprimer mes données*.",
        "de": "Diese Anfrage ist abgelaufen. Willst du deine Daten noch löschen, schreib erneut *meine Daten löschen*.",
    },
    "export_link": {
        "pt": "Aqui está o link para baixar a cópia dos seus dados (arquivo JSON). Ele vale por {minutes} minutos e funciona uma única vez:\n{url}",
        "nl": "Hier is de link om je gegevens als JSON te downloaden. Hij is {minutes} minuten geldig en werkt maar één keer:\n{url}",
        "en": "Here is the link to download your data as JSON. It is valid for {minutes} minutes and works only once:\n{url}",
        "fr": "Voici le lien pour télécharger tes données en JSON. Il est valable {minutes} minutes et ne fonctionne qu'une fois :\n{url}",
        "de": "Hier ist der Link zum Herunterladen deiner Daten als JSON. Er gilt {minutes} Minuten und funktioniert nur einmal:\n{url}",
    },
    "btn_undo": {
        "pt": "Desfazer",
        "nl": "Ongedaan maken",
        "en": "Undo",
        "fr": "Défaire",
        "de": "Rückgängig",
    },
    "btn_edit": {
        "pt": "Editar",
        "nl": "Aanpassen",
        "en": "Edit",
        "fr": "Modifier",
        "de": "Ändern",
    },
    "btn_ok": {
        "pt": "Está certo",
        "nl": "Klopt",
        "en": "It's right",
        "fr": "C'est bon",
        "de": "Stimmt",
    },
    "button_ok_reply": {
        "pt": (
            "Combinado, fica assim.",
            "Certo, fica assim.",
            "Perfeito, deixo assim.",
        ),
        "nl": (
            "Top, het blijft zo.",
            "Prima, zo laat ik het.",
            "Helder, het staat erin.",
        ),
        "en": (
            "Great, it stays as is.",
            "Perfect, I'll leave it.",
            "Got it, it stays recorded.",
        ),
        "fr": (
            "Parfait, on laisse comme ça.",
            "Super, je garde ça.",
            "Compris, c’est enregistré.",
        ),
        "de": (
            "Super, es bleibt so.",
            "Alles klar, ich lasse es so.",
            "Verstanden, es bleibt eingetragen.",
        ),
    },
    "button_edit_hint": {
        "pt": "Qual é o valor certo? Por exemplo: *na verdade foi 25*",
        "nl": "Geef me het juiste bedrag, bijv.: *actually 25*",
        "en": "Tell me the right amount, e.g.: *actually 25*",
        "fr": "Donne-moi le bon montant, par ex. : *actually 25*",
        "de": "Nenne mir den richtigen Betrag, z. B.: *actually 25*",
    },
    "button_used": {
        "pt": "Esse botão não vale mais: já foi usado ou expirou.",
        "nl": "Die knop werkt niet meer: hij is al gebruikt of verlopen.",
        "en": "That button no longer works: it was already used or it expired.",
        "fr": "Ce bouton ne fonctionne plus : il a déjà servi ou il a expiré.",
        "de": "Dieser Button gilt nicht mehr: schon benutzt oder abgelaufen.",
    },
    "button_gone": {
        "pt": "Não encontrei esse registro: ele já foi apagado ou desfeito.",
        "nl": "Ik vind die registratie niet meer: ze is al verwijderd of ongedaan gemaakt.",
        "en": "I can't find that entry: it was already deleted or undone.",
        "fr": "Je ne retrouve pas cet enregistrement : il a déjà été supprimé ou défait.",
        "de": "Diesen Eintrag finde ich nicht mehr: Er wurde schon gelöscht oder rückgängig gemacht.",
    },
    "expense_deleted": {
        "pt": (
            "Apaguei: {amount} em *{name}* ({date}).",
            "Pronto, apaguei {amount} em *{name}* ({date}).",
        ),
        "nl": (
            "Verwijderd: {amount} bij *{name}* ({date}).",
            "Klaar, {amount} bij *{name}* ({date}) is weg.",
        ),
        "en": (
            "Deleted: {amount} at *{name}* ({date}).",
            "Done, removed {amount} at *{name}* ({date}).",
        ),
        "fr": (
            "Supprimé : {amount} chez *{name}* ({date}).",
            "C’est fait, {amount} chez *{name}* ({date}) est supprimé.",
        ),
        "de": (
            "Gelöscht: {amount} bei *{name}* ({date}).",
            "Erledigt, {amount} bei *{name}* ({date}) ist weg.",
        ),
    },
    "expense_delete_none": {
        "pt": "Não tenho nenhuma despesa para apagar.",
        "nl": "Ik heb geen uitgave om te verwijderen.",
        "en": "I have no expense to delete.",
        "fr": "Je n'ai aucune dépense à supprimer.",
        "de": "Ich habe keine Ausgabe zum Löschen.",
    },
    "last_expenses_title": {
        "pt": "Suas últimas {n} despesas",
        "nl": "Laatste {n} uitgaven",
        "en": "Last {n} expenses",
        "fr": "Les {n} dernières dépenses",
        "de": "Letzte {n} Ausgaben",
    },
    "last_expenses_title_one": {
        "pt": "Sua última despesa",
        "nl": "Laatste uitgave",
        "en": "Your last expense",
        "fr": "Ta dernière dépense",
        "de": "Deine letzte Ausgabe",
    },
    "last_incomes_title": {
        "pt": "Suas últimas {n} receitas",
        "nl": "Laatste {n} inkomsten",
        "en": "Last {n} incomes",
        "fr": "Tes {n} derniers revenus",
        "de": "Letzte {n} Einnahmen",
    },
    "last_incomes_title_one": {
        "pt": "Sua última receita",
        "nl": "Laatste inkomsten",
        "en": "Your last income",
        "fr": "Ton dernier revenu",
        "de": "Deine letzte Einnahme",
    },
    "income_none": {
        "pt": "Você ainda não registrou nenhuma receita.",
        "nl": "Je hebt nog geen inkomsten geregistreerd.",
        "en": "You have not recorded any income yet.",
        "fr": "Tu n'as encore enregistré aucun revenu.",
        "de": "Du hast noch keine Einnahmen erfasst.",
    },
    "top_categories_title": {
        "pt": "Top categorias — {period_label}",
        "nl": "Top categorieën — {period_label}",
        "en": "Top categories — {period_label}",
        "fr": "Top catégories — {period_label}",
        "de": "Top-Kategorien — {period_label}",
    },
    "period_today_yesterday": {
        "pt": "hoje e ontem",
        "nl": "vandaag en gisteren",
        "en": "today and yesterday",
        "fr": "aujourd'hui et hier",
        "de": "heute und gestern",
    },
    "period_last_days": {
        "pt": "últimos {n} dias",
        "nl": "afgelopen {n} dagen",
        "en": "last {n} days",
        "fr": "{n} derniers jours",
        "de": "letzte {n} Tage",
    },
    "list_more": {
        "pt": "… e mais {n}.",
        "nl": "… en nog {n}.",
        "en": "… and {n} more.",
        "fr": "… et {n} de plus.",
        "de": "… und {n} weitere.",
    },
    "negative_as_income_ask": {
        "pt": "Valor negativo não entra como despesa. Foi dinheiro que entrou? Posso anotar {amount} de *{name}* como receita.",
        "nl": "Een negatief bedrag boek ik niet als uitgave. Kwam er geld binnen? Dan noteer ik {amount} van *{name}* als inkomsten.",
        "en": "A negative amount doesn't go in as spending. Was it money coming in? I can log {amount} from *{name}* as income.",
        "fr": "Un montant négatif n'entre pas comme dépense. C'était une entrée d'argent ? Je peux noter {amount} de *{name}* comme revenu.",
        "de": "Ein negativer Betrag zählt nicht als Ausgabe. War es Geld, das reinkam? Ich kann {amount} von *{name}* als Einnahme notieren.",
    },
    "btn_neg_income": {
        "pt": "Anotar receita",
        "nl": "Als inkomsten",
        "en": "Log as income",
        "fr": "Noter en revenu",
        "de": "Als Einnahme",
    },
    "btn_neg_no": {"pt": "Não", "nl": "Nee", "en": "No", "fr": "Non", "de": "Nein"},
    "negative_dropped": {
        "pt": "Certo, não anotei nada.",
        "nl": "Prima, ik heb niets genoteerd.",
        "en": "Fine, I logged nothing.",
        "fr": "D'accord, je n'ai rien noté.",
        "de": "Gut, ich habe nichts notiert.",
    },
    "ask_amount_item": {
        "pt": "Quanto foi {item}? Pode mandar só o valor, por exemplo: 75.",
        "nl": "Hoeveel was {item}? Je mag alleen het bedrag sturen, bijvoorbeeld: 75.",
        "en": "How much was {item}? You can send just the amount, for example: 75.",
        "fr": "Combien était {item} ? Tu peux envoyer seulement le montant, par ex. : 75.",
        "de": "Wie viel war {item}? Du kannst nur den Betrag schicken, z. B.: 75.",
    },
    "category_which": {
        "pt": "Não sei a qual lançamento você se refere. Diga o nome, por exemplo: “Padaria é Transporte”.",
        "nl": "Ik weet niet welke uitgave je bedoelt. Noem de naam, bijvoorbeeld: “Bakker is Vervoer”.",
        "en": "I'm not sure which entry you mean. Say its name, for example: “Bakery is Transport”.",
        "fr": "Je ne sais pas de quelle dépense il s'agit. Dis son nom, par ex. : « Boulangerie est Transport ».",
        "de": "Ich weiß nicht, welchen Eintrag du meinst. Nenne den Namen, z. B.: „Bäckerei ist Verkehr“.",
    },
    "bare_word_ask": {
        "pt": "“{word}” sozinha eu não registro. Quer que eu anote “{phrase}”?",
        "nl": "“{word}” alleen noteer ik niet. Zal ik “{phrase}” noteren?",
        "en": "I don't log “{word}” on its own. Should I log “{phrase}”?",
        "fr": "Je n'enregistre pas « {word} » seul. Je note « {phrase} » ?",
        "de": "„{word}“ allein trage ich nicht ein. Soll ich „{phrase}“ eintragen?",
    },
    "btn_bare_yes": {
        "pt": "Sim, anotar",
        "nl": "Ja, noteer",
        "en": "Yes, log it",
        "fr": "Oui, note",
        "de": "Ja, eintragen",
    },
    "btn_bare_no": {"pt": "Não", "nl": "Nee", "en": "No", "fr": "Non", "de": "Nein"},
    "bare_word_no": {
        "pt": "Certo, não anotei nada.",
        "nl": "Prima, ik heb niets genoteerd.",
        "en": "Fine, I logged nothing.",
        "fr": "D'accord, je n'ai rien noté.",
        "de": "Gut, ich habe nichts notiert.",
    },
    "tz_set": {
        "pt": "Fuso horário: {zone}. Agora, aí, são {time}.",
        "nl": "Tijdzone: {zone}. Bij jou is het nu {time}.",
        "en": "Timezone: {zone}. It is {time} where you are.",
        "fr": "Fuseau horaire : {zone}. Chez toi, il est {time}.",
        "de": "Zeitzone: {zone}. Bei dir ist es jetzt {time}.",
    },
    "tz_unknown": {
        "pt": "Não reconheci esse fuso. Diga a cidade ou o nome completo, por exemplo: “fuso horário Lisboa” ou “fuso horário Europe/Amsterdam”.",
        "nl": "Die tijdzone ken ik niet. Noem de stad of de volledige naam, bijvoorbeeld: “tijdzone Lissabon” of “tijdzone Europe/Amsterdam”.",
        "en": "I don't know that timezone. Say the city or the full name, for example: “timezone Lisbon” or “timezone Europe/Amsterdam”.",
        "fr": "Je ne connais pas ce fuseau. Dis la ville ou le nom complet, par ex. : « fuseau horaire Lisbonne » ou « fuseau horaire Europe/Amsterdam ».",
        "de": "Diese Zeitzone kenne ich nicht. Nenne die Stadt oder den vollen Namen, z. B.: „Zeitzone Lissabon“ oder „Zeitzone Europe/Amsterdam“.",
    },
    "purchase_ask_price": {
        "pt": "Quanto custou {item}? Mande com o valor, por exemplo: “{item} 4,50”.",
        "nl": "Wat kostte {item}? Stuur het met het bedrag, bijvoorbeeld: “{item} 4,50”.",
        "en": "How much was the {item}? Send it with the amount, for example: “{item} 4.50”.",
        "fr": "Combien a coûté {item} ? Envoie-le avec le montant, par ex. : « {item} 4,50 ».",
        "de": "Was hat {item} gekostet? Schick es mit dem Betrag, z. B.: „{item} 4,50“.",
    },
    "bank_balance_not_tracked": {
        "pt": "Ainda não acompanho o saldo da sua conta no banco. Aqui, *saldo* é o que entrou menos o que saiu no mês. Se esse valor foi um gasto ou uma entrada, me diga assim: “mercado 300” ou “recebi 300”.",
        "nl": "Je banksaldo houd ik nog niet bij. Hier is *saldo* wat er deze maand binnenkwam min wat eruit ging. Was dit bedrag een uitgave of inkomsten? Zeg dan bijvoorbeeld “boodschappen 300” of “300 ontvangen”.",
        "en": "I don't track your bank account balance yet. Here, *balance* is what came in minus what went out this month. If that amount was spending or income, tell me like this: “groceries 300” or “received 300”.",
        "fr": "Je ne suis pas encore le solde de ton compte bancaire. Ici, le *solde* est ce qui est entré moins ce qui est sorti dans le mois. Si ce montant était une dépense ou une entrée, dis-le ainsi : « courses 300 » ou « reçu 300 ».",
        "de": "Deinen Kontostand verfolge ich noch nicht. Hier ist der *Saldo* das, was im Monat reinkam, minus das, was rausging. War der Betrag eine Ausgabe oder eine Einnahme? Dann schreib z. B. „Einkauf 300“ oder „300 erhalten“.",
    },
    "phrase_month": {
        "pt": "em {month}",
        "nl": "in {month}",
        "en": "in {month}",
        "fr": "en {month}",
        "de": "im {month}",
    },
    "month_of_year": {
        "pt": "{month} de {year}",
        "nl": "{month} {year}",
        "en": "{month} {year}",
        "fr": "{month} {year}",
        "de": "{month} {year}",
    },
    "phrase_year": {
        "pt": "em {year}",
        "nl": "in {year}",
        "en": "in {year}",
        "fr": "en {year}",
        "de": "im Jahr {year}",
    },
    "phrase_last_days": {
        "pt": "nos últimos {n} dias",
        "nl": "de afgelopen {n} dagen",
        "en": "in the last {n} days",
        "fr": "ces {n} derniers jours",
        "de": "in den letzten {n} Tagen",
    },
    "phrase_this_week": {
        "pt": "esta semana",
        "nl": "deze week",
        "en": "this week",
        "fr": "cette semaine",
        "de": "diese Woche",
    },
    "phrase_last_week": {
        "pt": "na semana passada",
        "nl": "vorige week",
        "en": "last week",
        "fr": "la semaine dernière",
        "de": "letzte Woche",
    },
    "period_today": {
        "pt": "hoje",
        "nl": "vandaag",
        "en": "today",
        "fr": "aujourd'hui",
        "de": "heute",
    },
    "period_current_week": {
        "pt": "esta semana",
        "nl": "deze week",
        "en": "this week",
        "fr": "cette semaine",
        "de": "diese Woche",
    },
    "period_last_week": {
        "pt": "semana passada",
        "nl": "vorige week",
        "en": "last week",
        "fr": "semaine dernière",
        "de": "letzte Woche",
    },
    # ── M7 — Treino ─────────────────────────────────────────────────────────
    "workout_saved": {
        "pt": (
            "Muito bem! Treino anotado: {activity}{duration}. 💪",
            "Treino anotado: {activity}{duration}.",
            "Registrado: {activity}{duration}. Bom trabalho!",
        ),
        "nl": (
            "Mooi! Training genoteerd: {activity}{duration}. 💪",
            "Training genoteerd: {activity}{duration}.",
            "Vastgelegd: {activity}{duration}. Goed bezig!",
        ),
        "en": (
            "Nice! Workout noted: {activity}{duration}. 💪",
            "Workout noted: {activity}{duration}.",
            "Logged: {activity}{duration}. Nice work!",
        ),
        "fr": (
            "Bravo ! Séance notée : {activity}{duration}. 💪",
            "Séance notée : {activity}{duration}.",
            "Enregistré : {activity}{duration}. Beau travail !",
        ),
        "de": (
            "Stark! Training notiert: {activity}{duration}. 💪",
            "Training notiert: {activity}{duration}.",
            "Eingetragen: {activity}{duration}. Gut gemacht!",
        ),
    },
    "workout_summary_header": {
        "pt": "Treinos desta semana ({n} sessões):",
        "nl": "Trainingen deze week ({n} sessies):",
        "en": "Workouts this week ({n} sessions):",
        "fr": "Entraînements cette semaine ({n} séances) :",
        "de": "Trainings diese Woche ({n} Einheiten):",
    },
    "workout_summary_row": {
        "pt": "• {date}: {activity} {duration}",
        "nl": "• {date}: {activity} {duration}",
        "en": "• {date}: {activity} {duration}",
        "fr": "• {date} : {activity} {duration}",
        "de": "• {date}: {activity} {duration}",
    },
    "workout_summary_empty": {
        "pt": "Nenhum treino registrado esta semana.",
        "nl": "Geen trainingen geregistreerd deze week.",
        "en": "No workouts logged this week.",
        "fr": "Aucun entraînement enregistré cette semaine.",
        "de": "Kein Training diese Woche eingetragen.",
    },
    # ── M8 — Saúde ───────────────────────────────────────────────────────────
    "health_saved_medication": {
        "pt": (
            "Anotei: você tomou {value}.",
            "Registrado: {value} tomado.",
        ),
        "nl": (
            "Genoteerd: je hebt {value} genomen.",
            "Vastgelegd: {value} ingenomen.",
        ),
        "en": (
            "Noted: you took {value}.",
            "Logged: {value} taken.",
        ),
        "fr": (
            "C’est noté : tu as pris {value}.",
            "Enregistré : {value} pris.",
        ),
        "de": (
            "Notiert: Du hast {value} genommen.",
            "Eingetragen: {value} eingenommen.",
        ),
    },
    "health_saved_mood": {
        "pt": (
            "Anotado: {value} de 10 hoje.",
            "Humor de hoje: {value}/10, anotado.",
        ),
        "nl": (
            "Genoteerd: {value} van 10 vandaag.",
            "Stemming van vandaag: {value}/10, genoteerd.",
        ),
        "en": (
            "Noted: {value} out of 10 today.",
            "Today's mood: {value}/10, noted.",
        ),
        "fr": (
            "Noté : {value} sur 10 aujourd’hui.",
            "Humeur du jour : {value}/10, notée.",
        ),
        "de": (
            "Notiert: heute {value} von 10.",
            "Stimmung heute: {value}/10, notiert.",
        ),
    },
    "health_saved_sleep": {
        "pt": (
            "Anotei: {value}h de sono.",
            "Sono registrado: {value}h.",
        ),
        "nl": (
            "Genoteerd: {value} uur slaap.",
            "Slaap vastgelegd: {value} uur.",
        ),
        "en": (
            "Noted: {value}h of sleep.",
            "Sleep logged: {value}h.",
        ),
        "fr": (
            "Noté : {value} h de sommeil.",
            "Sommeil enregistré : {value} h.",
        ),
        "de": (
            "Notiert: {value} Std. Schlaf.",
            "Schlaf eingetragen: {value} Std.",
        ),
    },
    "health_saved_water": {
        "pt": (
            "Anotei {value} L de água.",
            "Água registrada: {value} L.",
        ),
        "nl": (
            "Genoteerd: {value} L water.",
            "Water vastgelegd: {value} L.",
        ),
        "en": (
            "Noted: {value} L of water.",
            "Water logged: {value} L.",
        ),
        "fr": (
            "Noté : {value} L d’eau.",
            "Eau enregistrée : {value} L.",
        ),
        "de": (
            "Notiert: {value} L Wasser.",
            "Wasser eingetragen: {value} L.",
        ),
    },
    # ── M9 — Metas & Hábitos ─────────────────────────────────────────────────
    "goal_created": {
        "pt": "Meta criada: *{title}*. Eu ajudo você a acompanhar.",
        "nl": "Doel aangemaakt: *{title}*",
        "en": "Goal created: *{title}*",
        "fr": "Objectif créé : *{title}*",
        "de": "Ziel erstellt: *{title}*",
    },
    "habit_logged": {
        "pt": (
            "Anotei: {activity}.",
            "{activity}: anotado. Mais um dia!",
        ),
        "nl": (
            "Genoteerd: {activity}.",
            "{activity}: genoteerd. Weer een dag erbij!",
        ),
        "en": (
            "Noted: {activity}.",
            "{activity}: logged. One more day!",
        ),
        "fr": (
            "Noté : {activity}.",
            "{activity} : noté. Un jour de plus !",
        ),
        "de": (
            "Notiert: {activity}.",
            "{activity}: eingetragen. Wieder ein Tag mehr!",
        ),
    },
    "goals_list_header": {
        "pt": "Suas metas ativas ({n}):",
        "nl": "Jouw actieve doelen ({n}):",
        "en": "Your active goals ({n}):",
        "fr": "Tes objectifs actifs ({n}) :",
        "de": "Deine aktiven Ziele ({n}):",
    },
    "goals_list_empty": {
        "pt": "Você ainda não tem metas. Crie uma assim: *meta: quero X*",
        "nl": "Nog geen doelen. Maak er een met: *doel: ik wil X*",
        "en": "No goals yet. Create one with: *goal: I want to X*",
        "fr": "Pas encore d'objectifs. Crées-en un avec : *objectif : je veux X*",
        "de": "Noch keine Ziele. Erstelle eines mit: *Ziel: Ich will X*",
    },
    "goals_list_row": {
        "pt": "• {title}",
        "nl": "• {title}",
        "en": "• {title}",
        "fr": "• {title}",
        "de": "• {title}",
    },
    # ── M10 — Produtividade ───────────────────────────────────────────────────
    "note_saved": {
        "pt": (
            "Anotado.",
            "Guardei a nota.",
            "Nota salva.",
        ),
        "nl": (
            "Genoteerd.",
            "Notitie bewaard.",
            "Staat erin.",
        ),
        "en": (
            "Noted.",
            "Note saved.",
            "Got it, saved.",
        ),
        "fr": (
            "Noté.",
            "Note gardée.",
            "C’est dans ton carnet.",
        ),
        "de": (
            "Notiert.",
            "Notiz gespeichert.",
            "Alles klar, gespeichert.",
        ),
    },
    "task_saved": {
        "pt": (
            "Tarefa adicionada: *{body}*",
            "Anotei a tarefa: *{body}*",
        ),
        "nl": (
            "Taak toegevoegd: *{body}*",
            "Taak genoteerd: *{body}*",
        ),
        "en": (
            "Task added: *{body}*",
            "Noted the task: *{body}*",
        ),
        "fr": (
            "Tâche ajoutée : *{body}*",
            "J’ai noté la tâche : *{body}*",
        ),
        "de": (
            "Aufgabe hinzugefügt: *{body}*",
            "Aufgabe notiert: *{body}*",
        ),
    },
    "task_due_suffix": {
        "pt": " (prazo: {when})",
        "nl": " (deadline: {when})",
        "en": " (due {when})",
        "fr": " (échéance : {when})",
        "de": " (fällig: {when})",
    },
    "task_done": {
        "pt": (
            "Tarefa concluída.",
            "Feito, tarefa concluída.",
        ),
        "nl": (
            "Taak afgerond.",
            "Klaar, taak afgerond.",
        ),
        "en": (
            "Task done.",
            "Done, task completed.",
        ),
        "fr": (
            "Tâche terminée.",
            "C’est fait, tâche terminée.",
        ),
        "de": (
            "Aufgabe erledigt.",
            "Erledigt, Aufgabe abgeschlossen.",
        ),
    },
    "task_not_found": {
        "pt": "Não achei essa tarefa em aberto. Diga “minhas tarefas” para ver a lista.",
        "nl": "Ik kon die openstaande taak niet vinden.",
        "en": "I couldn't find that open task.",
        "fr": "Je n'ai pas trouvé cette tâche ouverte.",
        "de": "Ich konnte diese offene Aufgabe nicht finden.",
    },
    "tasks_list_header": {
        "pt": "Suas tarefas em aberto ({n}):",
        "nl": "Jouw openstaande taken ({n}):",
        "en": "Your open tasks ({n}):",
        "fr": "Tes tâches ouvertes ({n}) :",
        "de": "Deine offenen Aufgaben ({n}):",
    },
    "tasks_list_empty": {
        "pt": "Você não tem tarefas em aberto.",
        "nl": "Geen openstaande taken.",
        "en": "No open tasks.",
        "fr": "Pas de tâches ouvertes.",
        "de": "Keine offenen Aufgaben.",
    },
    "tasks_list_row": {
        "pt": "• {n}. {body}{due}",
        "nl": "• {n}. {body}{due}",
        "en": "• {n}. {body}{due}",
        "fr": "• {n}. {body}{due}",
        "de": "• {n}. {body}{due}",
    },
    "tasks_list_due": {
        "pt": " (prazo: {date})",
        "nl": " (deadline: {date})",
        "en": " (due: {date})",
        "fr": " (échéance : {date})",
        "de": " (fällig: {date})",
    },
    # ── M5 — Lembretes list/cancel ────────────────────────────────────────────
    "lembretes_list_header": {
        "pt": "Seus lembretes ativos ({n}):",
        "nl": "Jouw actieve herinneringen ({n}):",
        "en": "Your active reminders ({n}):",
        "fr": "Tes rappels actifs ({n}) :",
        "de": "Deine aktiven Erinnerungen ({n}):",
    },
    "lembretes_list_empty": {
        "pt": "Você não tem lembretes ativos.",
        "nl": "Geen actieve herinneringen.",
        "en": "No active reminders.",
        "fr": "Pas de rappels actifs.",
        "de": "Keine aktiven Erinnerungen.",
    },
    "lembretes_list_row": {
        "pt": "• {time} — {text} [{days}]",
        "nl": "• {time} — {text} [{days}]",
        "en": "• {time} — {text} [{days}]",
        "fr": "• {time} — {text} [{days}]",
        "de": "• {time} — {text} [{days}]",
    },
    "lembrete_cancelled": {
        "pt": "Lembrete cancelado: *{text}*",
        "nl": "Herinnering geannuleerd: *{text}*",
        "en": "Reminder cancelled: *{text}*",
        "fr": "Rappel annulé : *{text}*",
        "de": "Erinnerung gelöscht: *{text}*",
    },
    "lembrete_cancel_not_found": {
        "pt": "Não achei esse lembrete ativo.",
        "nl": "Ik kon die actieve herinnering niet vinden.",
        "en": "I couldn't find that active reminder.",
        "fr": "Je n'ai pas trouvé ce rappel actif.",
        "de": "Ich konnte diese aktive Erinnerung nicht finden.",
    },
    # ── M7 — Treino extras ────────────────────────────────────────────────────
    "workout_deleted": {
        "pt": "Treino apagado.",
        "nl": "Training verwijderd.",
        "en": "Workout deleted.",
        "fr": "Entraînement supprimé.",
        "de": "Training gelöscht.",
    },
    "workout_delete_which": {
        "pt": 'No dia {day} há mais de um treino: {names}. Diga qual, por exemplo "apaga a corrida de segunda".',
        "nl": 'Op {day} staan meerdere trainingen: {names}. Zeg welke, bijvoorbeeld "verwijder de hardlopen van maandag".',
        "en": 'There is more than one workout on {day}: {names}. Say which, for example "delete the running on monday".',
        "fr": 'Il y a plusieurs entraînements le {day} : {names}. Dis lequel, par exemple "supprime la course de lundi".',
        "de": 'Am {day} gibt es mehrere Trainings: {names}. Sag welches, zum Beispiel "lösche Laufen vom Montag".',
    },
    "workout_delete_not_found": {
        "pt": "Não achei nenhum treino recente para apagar.",
        "nl": "Geen recente training gevonden om te verwijderen.",
        "en": "No recent workout found to delete.",
        "fr": "Aucun entraînement récent trouvé à supprimer.",
        "de": "Kein aktuelles Training zum Löschen gefunden.",
    },
    "workout_activity_summary": {
        "pt": "Você fez *{activity}* {n}x nos últimos 7 dias.",
        "nl": "Je deed *{activity}* {n}x in de afgelopen 7 dagen.",
        "en": "You did *{activity}* {n}x in the last 7 days.",
        "fr": "Tu as fait *{activity}* {n}x ces 7 derniers jours.",
        "de": "Du hast *{activity}* {n}x in den letzten 7 Tagen gemacht.",
    },
    "workout_month_header": {
        "pt": "*Treinos de {month}*\n{n} sessões · {min} min · {km} km\n",
        "nl": "*Trainingen {month}*\n{n} sessies · {min} min · {km} km\n",
        "en": "*Workouts {month}*\n{n} sessions · {min} min · {km} km\n",
        "fr": "*Entraînements {month}*\n{n} séances · {min} min · {km} km\n",
        "de": "*Trainings {month}*\n{n} Einheiten · {min} Min. · {km} km\n",
    },
    # ── M8 — Saude queries ────────────────────────────────────────────────────
    "health_mood_history": {
        "pt": "Humor nesta semana: {entries}",
        "nl": "Stemming deze week: {entries}",
        "en": "Mood this week: {entries}",
        "fr": "Humeur cette semaine : {entries}",
        "de": "Stimmung diese Woche: {entries}",
    },
    "health_mood_empty": {
        "pt": "Ainda não há registros de humor esta semana.",
        "nl": "Geen stemmingsregistraties deze week.",
        "en": "No mood entries this week.",
        "fr": "Pas d'entrées d'humeur cette semaine.",
        "de": "Keine Stimmungseinträge diese Woche.",
    },
    "health_sleep_avg": {
        "pt": "Você dorme em média *{avg}h* (últimos 7 dias, {n}).",
        "nl": "Je slaapt gemiddeld *{avg} uur* (laatste 7 dagen, {n}).",
        "en": "You sleep an average of *{avg}h* (last 7 days, {n}).",
        "fr": "Tu dors en moyenne *{avg}h* (7 derniers jours, {n}).",
        "de": "Du schläfst im Schnitt *{avg}h* (letzte 7 Tage, {n}).",
    },
    "health_sleep_empty": {
        "pt": "Ainda não há registros de sono esta semana.",
        "nl": "Geen slaapregistraties deze week.",
        "en": "No sleep entries this week.",
        "fr": "Pas d'entrées de sommeil cette semaine.",
        "de": "Keine Schlafeinträge diese Woche.",
    },
    "health_medication_adherence": {
        "pt": "Medicação: você tomou em {n} de {total} dias esta semana.",
        "nl": "Medicatie: je nam het {n}/{total} dagen deze week.",
        "en": "Medication: you took it {n}/{total} days this week.",
        "fr": "Médicament : tu l'as pris {n}/{total} jours cette semaine.",
        "de": "Medikament: Du hast es {n}/{total} Tage diese Woche genommen.",
    },
    "health_medication_empty": {
        "pt": "Ainda não há registros de medicação esta semana.",
        "nl": "Geen medicatieregistraties deze week.",
        "en": "No medication entries this week.",
        "fr": "Pas d'entrées de médicament cette semaine.",
        "de": "Keine Medikamenteneinträge diese Woche.",
    },
    "health_water_today": {
        "pt": "Água hoje: *{total} L* ({n} registros).",
        "nl": "Water vandaag: *{total}L* ({n} registraties).",
        "en": "Water today: *{total}L* ({n} entries).",
        "fr": "Eau aujourd'hui : *{total}L* ({n} entrées).",
        "de": "Wasser heute: *{total}L* ({n} Einträge).",
    },
    "health_water_empty": {
        "pt": "Ainda não há registros de água hoje.",
        "nl": "Geen waterregistraties vandaag.",
        "en": "No water entries today.",
        "fr": "Pas d'entrées d'eau aujourd'hui.",
        "de": "Keine Wassereinträge heute.",
    },
    # ── M9 — extras ───────────────────────────────────────────────────────────
    "habit_already_today": {
        "pt": "{activity} já está anotado hoje, não precisa repetir.",
        "nl": "{activity} staat vandaag al genoteerd. Eén keer per dag telt.",
        "en": "{activity} is already logged for today, no need to repeat it.",
        "fr": "{activity} est déjà noté aujourd'hui. Une fois par jour compte.",
        "de": "{activity} ist für heute schon eingetragen. Einmal pro Tag zählt.",
    },
    "habit_logged_with_goal": {
        "pt": "Anotei: {activity}. Meta: {goal}.",
        "nl": "Gewoonte gelogd: *{activity}* (doel: {goal})",
        "en": "Habit logged: *{activity}* (goal: {goal})",
        "fr": "Habitude enregistrée : *{activity}* (objectif : {goal})",
        "de": "Gewohnheit protokolliert: *{activity}* (Ziel: {goal})",
    },
    "goal_completed": {
        "pt": "Meta cumprida: *{title}*! 🎉 Ela sai da sua lista de metas ativas.",
        "nl": "Doel bereikt: *{title}* \U0001f389 Het gaat van je lijst met actieve doelen.",
        "en": "Goal completed: *{title}* \U0001f389 It leaves your list of active goals.",
        "fr": "Objectif atteint : *{title}* \U0001f389 Il quitte ta liste d'objectifs actifs.",
        "de": "Ziel erreicht: *{title}* \U0001f389 Es verschwindet aus deiner Liste aktiver Ziele.",
    },
    "lembrete_cancel_ambiguous": {
        "pt": "Mais de um lembrete combina:\n{items}\nQual deles? Mande, por exemplo, *cancela lembrete tomar vitamina*.",
        "nl": "Meer dan één herinnering past:\n{items}\nWelke? Stuur bijvoorbeeld *annuleer herinnering vitamine*.",
        "en": "More than one reminder fits:\n{items}\nWhich one? Send, for example, *cancel reminder take vitamin*.",
        "fr": "Plusieurs rappels correspondent :\n{items}\nLequel ? Envoie par ex. *annule rappel vitamine*.",
        "de": "Mehrere Erinnerungen passen:\n{items}\nWelche? Schreib z. B. *Erinnerung Vitamin löschen*.",
    },
    "habit_q_yes": {
        "pt": "Sim, {day} já está registrado: *{activity}* ✓",
        "nl": "Ja, *{activity}* staat {day} al genoteerd. ✓",
        "en": "Yes, *{activity}* is already logged {day}. ✓",
        "fr": "Oui, *{activity}* est déjà noté {day}. ✓",
        "de": "Ja, *{activity}* ist {day} schon eingetragen. ✓",
    },
    "habit_q_no": {
        "pt": "Ainda não tenho *{activity}* registrado {day}. Se já fez, me diga sem a interrogação que eu anoto.",
        "nl": "Ik heb *{activity}* {day} nog niet. Al gedaan? Stuur het zonder vraagteken.",
        "en": "I don't have *{activity}* {day} yet. If you did it, just tell me without the question mark.",
        "fr": "Je n'ai pas encore *{activity}* {day}. Si c'est fait, dis-le-moi sans le point d'interrogation.",
        "de": "Ich habe {day} noch kein *{activity}*. Schon erledigt? Schreib es ohne Fragezeichen.",
    },
    "goal_deleted": {
        "pt": "Pronto, tirei a meta *{title}* da sua lista. Os registros já feitos continuam guardados.",
        "nl": "Klaar, het doel *{title}* staat niet meer in je lijst.",
        "en": "Done, I removed the goal *{title}* from your list. Logs you already made are kept.",
        "fr": "C'est fait, j'ai retiré l'objectif *{title}* de ta liste.",
        "de": "Erledigt, das Ziel *{title}* ist nicht mehr in deiner Liste.",
    },
    "goal_delete_ambiguous": {
        "pt": "Achei mais de uma meta assim:\n{items}\nDiga o nome completo.",
        "nl": "Ik vond meer dan één doel:\n{items}\nNoem de volledige naam.",
        "en": "I found more than one goal like that:\n{items}\nSay the full name.",
        "fr": "J'ai trouvé plusieurs objectifs :\n{items}\nDonne le nom complet.",
        "de": "Ich habe mehrere Ziele gefunden:\n{items}\nNenne den vollen Namen.",
    },
    "note_deleted": {
        "pt": "Pronto, apaguei a nota: _{body}_",
        "nl": "Klaar, notitie verwijderd: _{body}_",
        "en": "Done, note deleted: _{body}_",
        "fr": "C'est fait, note supprimée : _{body}_",
        "de": "Erledigt, Notiz gelöscht: _{body}_",
    },
    "note_delete_not_found": {
        "pt": "Não achei essa nota. Diga “minhas notas” para ver a lista.",
        "nl": "Die notitie vond ik niet. Zeg “mijn notities” voor de lijst.",
        "en": "I couldn't find that note. Say “my notes” to see the list.",
        "fr": "Je n'ai pas trouvé cette note. Dis « mes notes » pour voir la liste.",
        "de": "Diese Notiz habe ich nicht gefunden. Sag „meine Notizen“ für die Liste.",
    },
    "note_delete_ambiguous": {
        "pt": "Achei mais de uma nota assim:\n{items}\nDiga uma palavra que só uma delas tem.",
        "nl": "Ik vond meer dan één notitie:\n{items}\nNoem een woord dat er maar in één staat.",
        "en": "I found more than one note like that:\n{items}\nSay a word only one of them has.",
        "fr": "J'ai trouvé plusieurs notes :\n{items}\nDonne un mot qu'une seule contient.",
        "de": "Ich habe mehrere Notizen gefunden:\n{items}\nNenne ein Wort, das nur eine enthält.",
    },
    "note_secret_hint": {
        "pt": "Dica: para senhas e PINs, um gerenciador de senhas é mais seguro do que uma nota.",
        "nl": "Tip: voor wachtwoorden en pincodes is een wachtwoordmanager veiliger dan een notitie.",
        "en": "Tip: for passwords and PINs, a password manager is safer than a note.",
        "fr": "Astuce : pour les mots de passe et codes PIN, un gestionnaire de mots de passe est plus sûr qu'une note.",
        "de": "Tipp: Für Passwörter und PINs ist ein Passwortmanager sicherer als eine Notiz.",
    },
    "goal_complete_not_found": {
        "pt": "Não achei essa meta ativa.",
        "nl": "Ik kon dat actieve doel niet vinden.",
        "en": "I couldn't find that active goal.",
        "fr": "Je n'ai pas trouvé cet objectif actif.",
        "de": "Ich konnte dieses aktive Ziel nicht finden.",
    },
    "habit_frequency": {
        "pt": "Você registrou *{activity}* {n}x {period}.",
        "nl": "Je registreerde *{activity}* {n}x {period}.",
        "en": "You logged *{activity}* {n}x {period}.",
        "fr": "Tu as enregistré *{activity}* {n}x {period}.",
        "de": "Du hast *{activity}* {n}x {period} protokolliert.",
    },
    "habit_frequency_empty": {
        "pt": "Nenhum registro de *{activity}* {period}.",
        "nl": "Geen registraties van *{activity}* {period}.",
        "en": "No entries for *{activity}* {period}.",
        "fr": "Aucune entrée pour *{activity}* {period}.",
        "de": "Keine Einträge für *{activity}* {period}.",
    },
    "freq_period_week": {
        "pt": "nos últimos 7 dias",
        "nl": "in de afgelopen 7 dagen",
        "en": "in the last 7 days",
        "fr": "ces 7 derniers jours",
        "de": "in den letzten 7 Tagen",
    },
    "freq_period_month": {
        "pt": "este mês",
        "nl": "deze maand",
        "en": "this month",
        "fr": "ce mois-ci",
        "de": "diesen Monat",
    },
    "habit_streak": {
        "pt": "Já são *{n} dias seguidos* de {activity}. 🔥",
        "nl": "Jouw streak voor *{activity}*: *{n} dagen* op rij! 🔥",
        "en": "Your *{activity}* streak: *{n} days* in a row! 🔥",
        "fr": "Ta série pour *{activity}* : *{n} jours* consécutifs ! 🔥",
        "de": "Deine Serie für *{activity}*: *{n} Tage* in Folge! 🔥",
    },
    "habit_streak_none": {
        "pt": "Não achei registros recentes de *{activity}*. Que tal começar hoje? 💪",
        "nl": "Geen recente registraties gevonden voor *{activity}*. Begin vandaag! 💪",
        "en": "No recent entries found for *{activity}*. Start today! 💪",
        "fr": "Aucune entrée récente pour *{activity}*. Commence aujourd'hui ! 💪",
        "de": "Keine aktuellen Einträge für *{activity}*. Fang heute an! 💪",
    },
    # ── M10 — extras ──────────────────────────────────────────────────────────
    "notes_list_header": {
        "pt": "Suas últimas notas ({n}):",
        "nl": "Jouw laatste notities ({n}):",
        "en": "Your recent notes ({n}):",
        "fr": "Tes dernières notes ({n}) :",
        "de": "Deine letzten Notizen ({n}):",
    },
    "notes_list_empty": {
        "pt": "Você ainda não tem notas guardadas.",
        "nl": "Nog geen opgeslagen notities.",
        "en": "No notes saved yet.",
        "fr": "Pas encore de notes enregistrées.",
        "de": "Noch keine gespeicherten Notizen.",
    },
    "notes_list_row": {
        "pt": "• {body}",
        "nl": "• {body}",
        "en": "• {body}",
        "fr": "• {body}",
        "de": "• {body}",
    },
    "task_deleted": {
        "pt": "Tarefa apagada: *{body}*",
        "nl": "Taak verwijderd: *{body}*",
        "en": "Task deleted: *{body}*",
        "fr": "Tâche supprimée : *{body}*",
        "de": "Aufgabe gelöscht: *{body}*",
    },
    "task_delete_not_found": {
        "pt": "Não achei essa tarefa. Diga “minhas tarefas” para ver a lista.",
        "nl": "Ik kon die taak niet vinden.",
        "en": "I couldn't find that task.",
        "fr": "Je n'ai pas trouvé cette tâche.",
        "de": "Ich konnte diese Aufgabe nicht finden.",
    },
    "tasks_list_overdue": {
        "pt": "• {n}. \u26a0\ufe0f {body}{due}",
        "nl": "• {n}. \u26a0\ufe0f {body}{due}",
        "en": "• {n}. \u26a0\ufe0f {body}{due}",
        "fr": "• {n}. \u26a0\ufe0f {body}{due}",
        "de": "• {n}. \u26a0\ufe0f {body}{due}",
    },
    # M11 — Dashboard
    "dashboard_link": {
        "pt": "Aqui está o seu painel:\n{url}",
        "nl": "\U0001f4ca Jouw persoonlijk dashboard:\n{url}",
        "en": "\U0001f4ca Your personal dashboard:\n{url}",
        "fr": "\U0001f4ca Ton tableau de bord personnel :\n{url}",
        "de": "\U0001f4ca Dein pers\u00f6nliches Dashboard:\n{url}",
    },
    "dashboard_cta": {
        "pt": "Aqui está o seu painel. O link vale por até 7 dias.",
        "nl": "Hier is je dashboard. De link is maximaal 7 dagen geldig.",
        "en": "Here is your dashboard. The link is valid for up to 7 days.",
        "fr": "Voici ton tableau de bord. Le lien est valable jusqu'à 7 jours.",
        "de": "Hier ist dein Dashboard. Der Link ist bis zu 7 Tage gültig.",
    },
    "btn_open_panel": {
        "pt": "Abrir meu painel",
        "nl": "Open mijn dashboard",
        "en": "Open my dashboard",
        "fr": "Ouvrir mon tableau",
        "de": "Dashboard öffnen",
    },
    "dashboard_no_base_url": {
        "pt": "O painel ainda não está configurado. Fale com o administrador.",
        "nl": "Het dashboard is nog niet geconfigureerd. Neem contact op met de beheerder.",
        "en": "The dashboard is not configured yet. Contact the administrator.",
        "fr": "Le tableau de bord n'est pas encore configuré. Contacte l'administrateur.",
        "de": "Das Dashboard ist noch nicht konfiguriert. Kontaktiere den Administrator.",
    },
    "rate_limited": {
        "pt": "Recebi muitas mensagens em pouco tempo. Vou esperar um minuto antes de responder de novo.",
        "nl": "Ik kreeg veel berichten in korte tijd. Ik wacht een minuut voordat ik weer antwoord.",
        "en": "I got a lot of messages in a short time. I'll wait a minute before answering again.",
        "fr": "J'ai reçu beaucoup de messages en peu de temps. J'attends une minute avant de répondre.",
        "de": "Ich habe viele Nachrichten in kurzer Zeit bekommen. Ich warte eine Minute, bevor ich wieder antworte.",
    },
    "dashboard_revoked": {
        "pt": "Pronto: o link antigo do painel parou de funcionar. Este é o novo, só para você.",
        "nl": "Klaar: de oude dashboardlink werkt niet meer. Dit is de nieuwe, alleen voor jou.",
        "en": "Done: the old dashboard link no longer works. Here is the new one, just for you.",
        "fr": "C'est fait : l'ancien lien du tableau de bord ne marche plus. Voici le nouveau, rien que pour toi.",
        "de": "Erledigt: Der alte Dashboard-Link funktioniert nicht mehr. Hier ist der neue, nur für dich.",
    },
    "not_understood": {
        "pt": "Não entendi. Pode dizer de outro jeito?",
        "nl": "Ik begreep je niet. Kun je het anders formuleren?",
        "en": "I didn't understand that. Could you rephrase?",
        "fr": "Je n'ai pas compris. Peux-tu reformuler ?",
        "de": "Das habe ich nicht verstanden. Kannst du es anders formulieren?",
    },
    # M14 — Viagem
    "trip_started": {
        "pt": "Boa viagem para {dest}! Tudo o que você registrar até dizer “voltei” entra nessa viagem.",
        "nl": "Reis naar {dest} gestart! Uitgaven worden automatisch gekoppeld.",
        "en": "Trip to {dest} started! Expenses will be tagged automatically.",
        "fr": "Voyage à {dest} commencé ! Les dépenses seront associées automatiquement.",
        "de": "Reise nach {dest} gestartet! Ausgaben werden automatisch zugeordnet.",
    },
    "trip_ended": {
        "pt": "Bem-vindo de volta! A viagem para {dest} custou {total} em {count} despesas.",
        "nl": "Reis naar {dest} beëindigd. Totaal: {total} ({count} uitgaven).",
        "en": "Trip to {dest} ended. Total spent: {total} ({count} expenses).",
        "fr": "Voyage à {dest} terminé. Total : {total} ({count} dépenses).",
        "de": "Reise nach {dest} beendet. Gesamt: {total} ({count} Ausgaben).",
    },
    "trip_already_active": {
        "pt": "Você já tem uma viagem ativa para {dest}. Diga “voltei” para encerrar antes.",
        "nl": "Je hebt een actieve reis naar {dest}. Zeg 'terug' om die eerst te beëindigen.",
        "en": "You have an active trip to {dest}. Say 'back home' to end it first.",
        "fr": "Tu as un voyage actif vers {dest}. Dis 'de retour' pour le terminer d'abord.",
        "de": "Du hast eine aktive Reise nach {dest}. Sag „zuhause“, um sie zuerst zu beenden.",
    },
    "trip_none_active": {
        "pt": "Você não tem nenhuma viagem ativa.",
        "nl": "Je hebt geen actieve reis.",
        "en": "You have no active trip.",
        "fr": "Tu n'as pas de voyage actif.",
        "de": "Du hast keine aktive Reise.",
    },
    "trip_no_expenses": {
        "pt": "Ainda não há despesas nesta viagem.",
        "nl": "Nog geen uitgaven geregistreerd voor deze reis.",
        "en": "No expenses recorded for this trip yet.",
        "fr": "Aucune dépense enregistrée pour ce voyage.",
        "de": "Keine Ausgaben für diese Reise erfasst.",
    },
    "trip_list_empty": {
        "pt": "Você ainda não tem viagens registradas.",
        "nl": "Je hebt nog geen reizen geregistreerd.",
        "en": "You have no trips recorded yet.",
        "fr": "Tu n'as pas encore de voyages enregistrés.",
        "de": "Du hast noch keine Reisen erfasst.",
    },
    "trip_started_budget": {
        "pt": "Boa viagem para {dest}! Orçamento: {budget}. Tudo o que você registrar até dizer “voltei” entra nessa viagem.",
        "nl": "Reis naar {dest} gestart! Budget: {budget}. Uitgaven worden automatisch gekoppeld.",
        "en": "Trip to {dest} started! Budget: {budget}. Expenses will be tagged automatically.",
        "fr": "Voyage à {dest} commencé ! Budget : {budget}. Les dépenses seront associées automatiquement.",
        "de": "Reise nach {dest} gestartet! Budget: {budget}. Ausgaben werden automatisch zugeordnet.",
    },
    "trip_budget_left": {
        "pt": " · restante {left}",
        "nl": " · nog over {left}",
        "en": " · {left} left",
        "fr": " · reste {left}",
        "de": " · noch {left}",
    },
    "trip_budget_over": {
        "pt": " · {over} acima do orçamento",
        "nl": " · {over} boven budget",
        "en": " · {over} over budget",
        "fr": " · {over} au-dessus du budget",
        "de": " · {over} über dem Budget",
    },
    "trip_summary_header": {
        "pt": "Viagem: {dest} ({start} → {end})",
        "nl": "Reis: {dest} ({start} → {end})",
        "en": "Trip: {dest} ({start} → {end})",
        "fr": "Voyage : {dest} ({start} → {end})",
        "de": "Reise: {dest} ({start} → {end})",
    },
    "trip_summary_total": {
        "pt": "Total: {total}  ({n} despesas)",
        "nl": "Totaal: {total}  ({n} uitgaven)",
        "en": "Total: {total}  ({n} expenses)",
        "fr": "Total : {total}  ({n} dépenses)",
        "de": "Gesamt: {total}  ({n} Ausgaben)",
    },
    "trip_summary_budget": {
        "pt": "Orçamento: {budget}",
        "nl": "Budget: {budget}",
        "en": "Budget: {budget}",
        "fr": "Budget : {budget}",
        "de": "Budget: {budget}",
    },
    "trip_ongoing": {
        "pt": "em andamento",
        "nl": "lopend",
        "en": "ongoing",
        "fr": "en cours",
        "de": "laufend",
    },
    "trip_today": {
        "pt": "hoje",
        "nl": "vandaag",
        "en": "today",
        "fr": "aujourd'hui",
        "de": "heute",
    },
    "trip_expense_total": {
        "pt": "\n✈️ Viagem {dest}: {total} gastos até agora",
        "nl": "\n✈️ Reis {dest}: {total} uitgegeven tot nu toe",
        "en": "\n✈️ Trip {dest}: {total} spent so far",
        "fr": "\n✈️ Voyage {dest} : {total} dépensés jusqu'ici",
        "de": "\n✈️ Reise {dest}: bisher {total} ausgegeben",
    },
    "category_hint_overig": {
        "pt": "\nNão tenho certeza da categoria de *{merchant}*. Se quiser mudar: “{merchant} é lazer”.",
        "nl": "\nIk weet de categorie voor {merchant} niet. Verbeter met '{merchant} is [categorie]'.",
        "en": "\nNot sure about {merchant}'s category. You can fix it: '{merchant} is [category]'.",
        "fr": "\nJe ne suis pas sûr de la catégorie de {merchant}. Corrige avec '{merchant} est [catégorie]'.",
        "de": "\nUnsicher bei der Kategorie für {merchant}. Korrigiere mit '{merchant} ist [Kategorie]'.",
    },
    "trip_active_tag": {
        "pt": " [viagem: {dest}]",
        "nl": " [reis: {dest}]",
        "en": " [trip: {dest}]",
        "fr": " [voyage : {dest}]",
        "de": " [Reise: {dest}]",
    },
}


def _derive_singular(base: str, key: str, pt: str, **other: str) -> None:
    """A copy of ``base`` for the singular case (n == 1).

    Portuguese is always given; other languages only when their plural wording does not
    work for one (e.g. "1 days")."""
    _STRINGS[key] = {**_STRINGS[base], "pt": pt, **other}


_derive_singular(
    "trip_ended",
    "trip_ended_one",
    "Bem-vindo de volta! A viagem para {dest} custou {total} em 1 despesa.",
)
_derive_singular("trip_summary_total", "trip_summary_total_one", "Total: {total}  (1 despesa)")
_derive_singular(
    "habit_streak",
    "habit_streak_one",
    "Você está com *1 dia* de {activity}. Continue assim! 🔥",
    nl="Jouw streak voor *{activity}*: *1 dag*! Hou vol! 🔥",
    en="Your *{activity}* streak: *1 day*! Keep it going! 🔥",
    fr="Ta série pour *{activity}* : *1 jour* ! Continue ! 🔥",
    de="Deine Serie für *{activity}*: *1 Tag*! Weiter so! 🔥",
)


_derive_singular(
    "transactions_count",
    "transactions_count_one",
    "_1 transação_",
    nl="_1 transactie_",
    en="_1 transaction_",
    fr="_1 transaction_",
    de="_1 Buchung_",
)

_STRINGS.update(_SIGNUP_STRINGS)  # sign-up page invite and welcome (signup.py)
_STRINGS.update(_GCAL_STRINGS)  # Google Calendar connect/disconnect (google_calendar.py)
_STRINGS.update(_BUDGET_STRINGS)  # V2-01 texts live next to their logic in budgets.py
_STRINGS.update(_RECURRING_STRINGS)  # V2-02, same idea
_STRINGS.update(_MSUM_STRINGS)  # V2-04
_STRINGS.update(_AGENDA_STRINGS)  # V2-06
_STRINGS.update(_SCORE_STRINGS)  # V2-09
_STRINGS.update(_ANALYSIS_STRINGS)  # V2-14
_STRINGS.update(_INSIGHT_STRINGS)  # V2-15
_STRINGS.update(_IOU_STRINGS)  # V2-15
_STRINGS.update(_COUPLE_STRINGS)  # V2-10
_STRINGS.update(_STATEMENT_STRINGS)  # V2-05
_STRINGS.update(_ACCT_STRINGS)  # V2-12
_STRINGS.update(_TRAINING_STRINGS)  # V2-35
_STRINGS.update(_TRAINING_MEDIA_STRINGS)  # V2-35b
_STRINGS.update(_MEDIA_STRINGS)  # V2-35b / V2-07 photo and PDF intake
_STRINGS.update(_RECEIPT_STRINGS)  # V2-07 receipt photo
_STRINGS.update(_TRIPPLAN_STRINGS)  # V2-35
_STRINGS.update(_LEDGER_STRINGS)  # V2-16
_STRINGS.update(_BATCH_STRINGS)  # V2-17
_STRINGS.update(_FOLLOWUP_STRINGS)  # V2-20 / V2-23
_STRINGS.update(_INVOICE_STRINGS)  # V2-12 part 2
_STRINGS.update(_SERVICE_STRINGS)  # V2-36
_STRINGS.update(_HOME_STRINGS)  # V2-27
_STRINGS.update(_GOALS_STRINGS)
_STRINGS.update(questions.STRINGS)
_STRINGS.update(habits_ext.STRINGS)
_STRINGS.update(balance_view.STRINGS)
_STRINGS.update(bills.STRINGS)  # financial goals + week marks
_STRINGS.update(_CAL_STRINGS)  # V2-21
_STRINGS.update(_OUTBOX_STRINGS)  # V2-18
_STRINGS.update(_LANG_STRINGS)  # language switch (hard test 01/10)

# The help text only knew the first version: add the V2 commands before the "to stop" footer.
_HELP_MORE = {
    "pt": '*E também*\n• "a pagar luz 120 dia 20" — contas a pagar e a receber\n• "orçamento mercado 300" — teto mensal por categoria\n• "dentista amanhã às 14h" — agenda com aviso\n• "lembrete: tomar o remédio às 08:00"\n• "tarefa: comprar pão"\n• "meu dashboard" — painel com gráficos\n• "idioma inglês" — mudar de idioma',
    "nl": '*Ook handig*\n• "te betalen huur 900 dag 1" — openstaande rekeningen\n• "budget supermarkt 300" — maandlimiet per categorie\n• "tandarts morgen om 14u" — agenda met herinnering\n• "herinnering: medicijnen om 08:00"\n• "taak: brood kopen"\n• "mijn dashboard" — overzicht met grafieken\n• "taal Engels" — andere taal',
    "en": '*Also*\n• "to pay rent 900 day 1" — bills to pay and to receive\n• "budget groceries 300" — monthly cap per category\n• "dentist tomorrow at 2pm" — calendar with a reminder\n• "reminder: take medication at 08:00"\n• "task: buy bread"\n• "my dashboard" — charts\n• "language Dutch" — change language',
    "fr": '*Aussi*\n• "à payer loyer 900 jour 1" — factures à payer et à recevoir\n• "budget courses 300" — plafond mensuel par catégorie\n• "dentiste demain à 14h" — agenda avec rappel\n• "rappel : médicament à 08:00"\n• "tâche : acheter du pain"\n• "mon tableau de bord" — graphiques\n• "langue anglais" — changer de langue',
    "de": '*Außerdem*\n• "zu zahlen Miete 900 Tag 1" — offene Rechnungen\n• "Budget Supermarkt 300" — Monatslimit pro Kategorie\n• "Zahnarzt morgen um 14 Uhr" — Kalender mit Erinnerung\n• "Erinnerung: Medikament um 08:00"\n• "Aufgabe: Brot kaufen"\n• "mein Dashboard" — Diagramme\n• "Sprache Englisch" — Sprache ändern',
}
for _lg, _more in _HELP_MORE.items():
    _head, _sep, _tail = str(_STRINGS["help"][_lg]).rpartition("\n\n_")
    _STRINGS["help"][_lg] = f"{_head}\n\n{_more}{_sep}{_tail}"


def _t(key: str, lang: str, **kwargs: object) -> str:
    """Translate a string key to the given language, with optional format args."""
    lang = lang if lang in _SUPPORTED_LANGS else "en"
    tmpl = _STRINGS[key].get(lang) or _STRINGS[key]["en"]
    if isinstance(
        tmpl, tuple
    ):  # several equivalent phrasings: pick one so replies feel less canned
        tmpl = random.choice(tmpl)
    text = tmpl.format(**kwargs) if kwargs else tmpl
    if lang != "fr":  # one quote style across the app ("..."); French keeps « »
        text = text.replace("\u201c", '"').replace("\u201d", '"')
    return _pt_singular(text) if lang == "pt" else text


_PT_ONE = re.compile(r"(?<![\d.,])\b1 (dias|pontos|lançamentos|vezes|itens)\b")
_PT_SINGULAR = {
    "dias": "dia",
    "pontos": "ponto",
    "lançamentos": "lançamento",
    "vezes": "vez",
    "itens": "item",
}


def _pt_singular(text: str) -> str:
    """ "1 dias" → "1 dia": a count of one must not read as a plural (hard test, block M)."""
    return _PT_ONE.sub(lambda m: f"1 {_PT_SINGULAR[m.group(1)]}", text)


async def _month_category_context(
    session: AsyncSession, member: Member, when: datetime, category: str | None, lang: str
) -> str:
    """One-line month-to-date total after a just-recorded expense — always present (defect 21).

    The category total when the category has two or more entries this month; otherwise (unknown
    category "overig", or the first of its category) the total spent in the whole month, so every
    expense confirmation carries a month figure. Expects the new row to be flushed already.
    """
    from sqlalchemy import func as sa_func

    local = to_local(when)
    start = datetime.combine(month_start(local.date()), time.min, tzinfo=local.tzinfo)
    end = datetime.combine(
        month_start(month_start(local.date()) + timedelta(days=32)), time.min, tzinfo=local.tzinfo
    )
    base = (
        Expense.member_id == member.id,
        Expense.transaction_type == "expense",
        Expense.status.in_(SETTLED),
        Expense.expense_date >= start,
        Expense.expense_date < end,
    )
    if category and category != "overig":
        res = await session.execute(
            select(
                sa_func.coalesce(sa_func.sum(Expense.amount), 0), sa_func.count(Expense.id)
            ).where(*base, Expense.category == category)
        )
        total, count = res.one()
        if int(count) >= 2:
            return _t(
                "month_category_context",
                lang,
                total=_fmt_eur(float(total)),
                category=category_label(category, lang),
            )
    # M-12 (J, 20/10): the whole-month figure confused ("No mês, você já gastou €2.070,44" after
    # a cinema ticket); the day's total says something the member recognises.
    day0 = datetime.combine(local.date(), time.min, tzinfo=local.tzinfo)
    day_total = await session.scalar(
        select(sa_func.coalesce(sa_func.sum(Expense.amount), 0)).where(
            *base, Expense.expense_date >= day0, Expense.expense_date < day0 + timedelta(days=1)
        )
    )
    key = "day_total_context" if local.date() == today_local() else "day_total_context_other"
    return _t(key, lang, total=_fmt_eur(float(day_total or 0)))


# Whole-word greetings the substring lists above miss ("oi" alone has no space around it).
_FIRST_WORDS = {
    "pt": {"oi", "olá", "ola", "opa", "obrigado", "obrigada", "bomdia", "bom", "boa", "tarde"},
    "nl": {"hoi", "hallo", "goedemorgen", "dankjewel"},
    "en": {"hi", "hello", "hey", "hiya", "thanks", "help"},
}


def _detect_language(text: str) -> str | None:
    """Heuristic: detect the language from first-message keywords; None when nothing shows it
    (the member is then asked, see ``pending_language``)."""
    t = unicodedata.normalize("NFC", text).lower()
    words = set(re.findall(r"[a-zà-ÿ]+", t))
    for lang, known in _FIRST_WORDS.items():  # whole words first: "hallo" contains "allo"
        if words & known:
            return lang
    # whole words: "comercial" contains "merci" and was read as French (A-20)
    if words & {
        "bonjour",
        "salut",
        "allô",
        "allo",
        "merci",
        "bilan",
        "solde",
        "aide",
        "oui",
        "dépense",
        "depense",
        "résumé",
    }:
        return "fr"
    if any(
        w in t
        for w in [
            "guten",
            "danke",
            "bitte",
            "übersicht",
            "ubersicht",
            "ausgaben",
            "hilfe",
            "nein",
            "bezahlt",
            "erhalten",
        ]
    ):
        return "de"
    if any(
        w in t
        for w in [
            "oi ",
            " oi",
            "olá",
            "ola",
            "obrigad",
            "ajuda",
            "gastei",
            "paguei",
            "recebi",
            " sim",
            "sim ",
            "não",
            "nao",
            "resumo",
        ]
    ):
        return "pt"
    if any(
        w in t
        for w in [
            " dag",
            "dag ",
            "hallo",
            "bedankt",
            "overzicht",
            "hulp",
            "samenvatting",
            "uitgegeven",
            "ontvangen",
            "betaald",
            "hoi ",
        ]
    ):
        return "nl"
    return None


# ── Command keywords ──────────────────────────────────────────────────────────

_SUMMARY_WORDS = {
    "resumo",
    # A-69: the everyday ways to ask for it
    "meus gastos",
    "minhas despesas",
    "despesas",
    "meu resumo",
    "my expenses",
    "expenses",
    "mijn uitgaven",
    "uitgaven",
    "mes dépenses",
    "mes depenses",
    "meine ausgaben",
    "overzicht",
    "summary",
    "samenvatting",
    "gastos",
    "résumé",
    "resume",
    "bilan",
    "dépenses",
    "depenses",
    "übersicht",
    "ubersicht",
    "zusammenfassung",
    "ausgaben",
}
_SUMMARY_TIME_QUALIFIERS = (
    # today
    "hoje",
    "today",
    "vandaag",
    "heute",
    "aujourd'hui",
    # yesterday
    "ontem",
    "yesterday",
    "gisteren",
    "gestern",
    "hier",
    # this week
    "esta semana",
    "this week",
    "deze week",
    "diese woche",
    "cette semaine",
    # last week
    "semana passada",
    "last week",
    "vorige week",
    "letzte woche",
    # this month
    "este mês",
    "este mes",
    "this month",
    "deze maand",
    "diesen monat",
    "ce mois",
    # last month
    "mês passado",
    "mes passado",
    "last month",
    "vorige maand",
    "letzten monat",
    "mois dernier",
)

_SALDO_WORDS = {
    "saldo",
    "balance",
    "balanço",
    "balancete",
    "balanso",
    "solde",
    "bilanz",
    "kontostand",
}
_HELP_WORDS = {
    "ajuda",
    "help",
    "hulp",
    "comandos",
    "commands",
    "aide",
    "commandes",
    "hilfe",
    "befehle",
    # natural questions about what the assistant can do (a habit was logged for "what can you do?")
    "o que voce faz",
    "me ajuda",
    "preciso de ajuda",
    "menu",
    "help me",
    "o que voce sabe fazer",
    "o que voce pode fazer",
    "como funciona",
    "como usar",
    "what can you do",
    "what do you do",
    "how does this work",
    "how do i use this",
    "wat kun je",
    "wat doe je",
    "hoe werkt dit",
    "que peux tu faire",
    "que peux-tu faire",
    "que sais tu faire",
    "que sais-tu faire",
    "comment ca marche",
    "was kannst du",
    "was machst du",
    "wie funktioniert das",
}
# Words that re-open the consent flow after "stop" (the user must accept again).
_RESUME_WORDS = {
    "start",
    "iniciar",
    "começar",
    "comecar",
    "retomar",
    "voltar",
    "hervat",
    "hervatten",
    "resume",
    "restart",
    "reprendre",
    "fortsetzen",
}

_STOP_WORDS = {
    "stop",
    "pare",
    "parar",
    "stoppen",
    "ophouden",
    "arrêter",
    "arreter",
    "aufhören",
    "aufhoren",
}


# bare greetings (accent-free) → the greeting as the member would read it back, by language
def _keyboard_mash(body_plain: str, lang: str = "pt") -> bool:
    """One word of letters that no language here would write: no vowel, or 5+ consonants in a row
    ("asdfjklqwerty", "ahshdjejabs"). Laughter ("kkkkk", "hahaha") is left to the reply."""
    word = body_plain.strip(" .!?")
    if not re.fullmatch(r"[a-z]{6,40}", word) or len(set(word)) <= 2:
        return False
    run = max((len(r) for r in re.findall(r"[bcdfghjklmnpqrstvwxz]+", word)), default=0)
    limit = 6 if lang in ("nl", "de") else 5  # Dutch/German compounds: "kunststof", "angstschrei"
    return not re.search(r"[aeiouy]", word) or run >= limit


_GREETINGS: dict[str, str] = {
    "oi": "Oi", "ola": "Olá", "opa": "Opa", "oie": "Oi", "e ai": "E aí", "bom dia": "Bom dia",
    "boa tarde": "Boa tarde", "boa noite": "Boa noite", "hoi": "Hoi", "hallo": "Hallo",
    "goedemorgen": "Goedemorgen", "goedemiddag": "Goedemiddag", "goedenavond": "Goedenavond",
    "hi": "Hi", "hello": "Hello", "hey": "Hey", "good morning": "Good morning",
    "good afternoon": "Good afternoon", "good evening": "Good evening", "bonjour": "Bonjour",
    "salut": "Salut", "bonsoir": "Bonsoir", "coucou": "Coucou", "guten morgen": "Guten Morgen",
    "guten tag": "Guten Tag", "guten abend": "Guten Abend", "servus": "Servus", "moin": "Moin",
}  # fmt: skip
_GREET_LANG = {
    "pt": {"Oi", "Olá", "Opa", "E aí", "Bom dia", "Boa tarde", "Boa noite"},
    "nl": {"Hoi", "Hallo", "Goedemorgen", "Goedemiddag", "Goedenavond", "Hey"},
    "en": {"Hi", "Hello", "Hey", "Good morning", "Good afternoon", "Good evening"},
    "fr": {"Bonjour", "Salut", "Bonsoir", "Coucou"},
    "de": {"Hallo", "Guten Morgen", "Guten Tag", "Guten Abend", "Servus", "Moin", "Hey"},
}


def greet_lang_matches(word: str, lang: str) -> bool:
    return word in _GREET_LANG.get(lang, set())


_STOP_PHRASES = {
    "parar de enviar", "pare de enviar", "parar de mandar", "pare de mandar", "pare de me mandar mensagens",
    "parar mensagens", "nao quero mais receber mensagens", "nao quero mais mensagens",
    "stop sending", "stop messaging me", "stop messages", "unsubscribe",
    "stop met sturen", "stuur niets meer", "arrete d envoyer", "arrete de m envoyer des messages",
    "hor auf zu schreiben", "keine nachrichten mehr",
}  # fmt: skip
_LEAVE_WORDS = {"sair", "quero sair", "sai", "leave", "quit", "exit", "afmelden", "uitschrijven",
                "quitter", "verlassen", "abmelden"}  # fmt: skip
_CONSENT_YES = {
    "sim",
    "yes",
    "s",
    "y",
    "ok",
    "aceito",
    "aceitar",
    "ja",
    "oui",
}
_CONSENT_NO = {
    "não",
    "nao",
    "no",
    "n",
    "stop",
    "nee",
    "non",
    "nein",
}


_LEMBRETE_WORDS = {
    "configura lembrete",
    "configura lembrete:",
    "herinnering:",
    "herinnering",
    "reminder:",
    "set reminder",
    "rappel:",
    "configurer rappel",
    "erinnerung:",
    "erinnerung setzen",
}

# Matches "lembrete: <text> às/at/om/à/um HH:MM"
_LEMBRETE_RE = re.compile(
    r"(?:configura\s+lembrete|lembrete|set\s+reminder|reminder|herinnering|"
    r"rappel|configurer\s+rappel|erinnerung(?:\s+setzen)?)"
    r"[:\s]+(.+?)\s+(?:às|as|at|om|à|um|a)\s+(\d{1,2}:\d{2})\b",
    re.IGNORECASE,
)
# B-025: "lembrete: reunião às 9h" / "às 9h30" / "om 9 uur" (the canonical form wanted "09:00",
# so the model answered "Vou te lembrar … amanhã, às 09:00" and nothing was created)
_LEMBRETE_LOOSE_RE = re.compile(
    r"^\s*(?:configura\s+lembrete|lembrete|set\s+reminder|reminder|herinnering|"
    r"rappel|configurer\s+rappel|erinnerung(?:\s+setzen)?)\s*:\s*"
    r"(.+?)\s+(?:às|as|at|om|à|um|a)\s+(\d{1,2})(?:\s*h\s*(\d{2})?|\s*(?:uur|uhr|heures?))\b(.*)$",
    re.IGNORECASE,
)


_LEMBRETE_NATURAL_RE = re.compile(
    r"^\s*(?:me\s+lembra(?:r)?(?:\s+de)?|lembre-me(?:\s+de)?|lembra-me(?:\s+de)?|"
    r"remind\s+me(?:\s+to)?|herinner\s+me(?:\s+eraan)?(?:\s+om)?|"
    r"rappelle[-\s]moi(?:\s+de)?|erinnere\s+mich(?:\s+daran)?,?)\s+"
    r"(.+?)\s+(?:às|as|at|om|à|um)\s+(\d{1,2})(?:\s*[:h]\s*(\d{2})?|\s*(?:uur|uhr|heures?))?"
    r"(?:\s+(?:todo\s+dia|every\s+day|elke\s+dag|tous\s+les\s+jours|jeden\s+tag))?\s*[.!]?\s*$",
    re.IGNORECASE,
)


class _LembreteMatch:
    """Quacks like ``re.Match`` for the reminder handler: group(1) = text, group(2) = HH:MM."""

    def __init__(self, text: str, hhmm: str) -> None:
        self._groups = (None, text, hhmm)

    def group(self, i: int) -> str:
        return self._groups[i]


def _match_lembrete(body: str):
    """ "lembrete: X às 08:00" (canonical) or a natural "me lembra de X às 8h".

    A message that names a DAY ("amanhã", "sexta", "15/11") is a one-off: the agenda takes it
    (A-43: "lembrete: dentista amanhã às 14:00" became a daily reminder).
    """
    from alfred.agenda import parse_when

    if re.match(r"^\s*(?:nota|note|notitie|notiz)\s*:", body, re.IGNORECASE):
        return None  # A-63: "nota: lembrete trocar o filtro às 08:00" is a note
    rec = parse_recurrence(body)
    cadence = bool(rec.day_of_month) or rec.mask != 127 or rec.text != body.strip()
    if not cadence and parse_when(strip_accents(body.lower()), now_local()).day is not None:
        return None
    m = _LEMBRETE_RE.search(body)
    if m:
        return m
    loose = _LEMBRETE_LOOSE_RE.match(body)
    if loose and 0 <= int(loose.group(2)) <= 23:
        text = (loose.group(1) + " " + (loose.group(4) or "")).strip()
        return _LembreteMatch(text, f"{int(loose.group(2)):02d}:{int(loose.group(3) or 0):02d}")
    n = _LEMBRETE_NATURAL_RE.match(body)
    if not n:
        return None
    hh, mm = int(n.group(2)), int(n.group(3) or 0)
    return _LembreteMatch(n.group(1).strip(), f"{hh:02d}:{mm:02d}")


_VALID_CATEGORIES = frozenset(
    {
        "supermarkt",
        "restaurant",
        "transport",
        "gezondheid",
        "entertainment",
        "wonen",
        "kleding",
        "abonnement",
        "inkomen",
        "overig",
        # common aliases
        "supermercado",
        "supermercaat",
        "supermarché",
        "alimentação",
        "food",
        "eten",
        "nourriture",
        "reizen",
        "viagem",
        "voyage",
        "reise",
        "gezondheidszorg",
        "saúde",
        "santé",
        "gesundheit",
        "divertissement",
        "unterhaltung",
        "wohnen",
        "loyer",
        "habitation",
        "moradia",
        "kleren",
        "roupas",
        "vêtements",
        "kleidung",
        "subscription",
        "assinatura",
        "abo",
        "income",
        "renda",
        "revenu",
        "einkommen",
        "other",
        "outros",
        "anderen",
        "autre",
    }
)

# Canonical mapping for alias→canonical category
_CAT_ALIAS: dict[str, str] = {
    "supermercado": "supermarkt",
    # A-54: the older pt-PT names keep working in corrections
    "subscrições": "abonnement",
    "subscricoes": "abonnement",
    "rendimento": "inkomen",
    "habitação": "wonen",
    "habitacao": "wonen",
    "vestuário": "kleding",
    "vestuario": "kleding",
    "supermercaat": "supermarkt",
    "supermarché": "supermarkt",
    "alimentação": "restaurant",
    "food": "restaurant",
    "eten": "restaurant",
    "nourriture": "restaurant",
    "reizen": "transport",
    "viagem": "transport",
    "voyage": "transport",
    "reise": "transport",
    "gezondheidszorg": "gezondheid",
    "saúde": "gezondheid",
    "santé": "gezondheid",
    "gesundheit": "gezondheid",
    "divertissement": "entertainment",
    "unterhaltung": "entertainment",
    "wohnen": "wonen",
    "loyer": "wonen",
    "habitation": "wonen",
    "moradia": "wonen",
    "kleren": "kleding",
    "roupas": "kleding",
    "vêtements": "kleding",
    "kleidung": "kleding",
    "subscription": "abonnement",
    "assinatura": "abonnement",
    "abo": "abonnement",
    "income": "inkomen",
    "renda": "inkomen",
    "revenu": "inkomen",
    "einkommen": "inkomen",
    "other": "overig",
    "outros": "overig",
    "anderen": "overig",
    "autre": "overig",
}


# A correction only rewrites an expense recorded moments ago; older ones are deleted/re-entered.
CORRECTION_WINDOW_HOURS = 2

_WIPE_VERB = (
    r"(?:apag(?:a|ar|ue)|exclu(?:i|ir)|delet(?:a|ar|e)|elimin(?:a|ar)|remov(?:e|er)|"
    r"erase|remove|wipe|verwijder(?:en)?|wis(?:sen)?|supprim(?:e|er)|efface(?:r)?|"
    r"loesch(?:e|en)|losch(?:e|en))"
)
_DATA_OBJ = (
    r"(?:dados|data|conta|account|gegevens|donnees|compte|daten|konto)"
    r"(?:\s+(?:pessoais|personal|persoonlijke|personnelles|personlichen))?"
)
_POSSESSIVE = (
    r"(?:os|as|o|a|todos|todas|meus|minhas|meu|minha|my|all|the|mijn|mijn|al|alle|de|mes|mon|ma|"
    r"toutes|tous|les|meine|meinen|mein|die|der|das)"
)
_WANT = (
    r"(?:(?:eu\s+)?(?:quero|queria|gostaria\s+de|preciso|please|por\s+favor|i\s+want\s+to|i\s+would\s+like\s+to|"
    r"ik\s+wil|graag|je\s+veux|bitte|ich\s+mochte|ich\s+will)\s+)?"
)
_WIPE_RE = re.compile(
    rf"^{_WANT}(?:{_WIPE_VERB}\s+(?:{_POSSESSIVE}\s+){{0,2}}{_DATA_OBJ}|"
    rf"{_WIPE_VERB}\s+(?:tudo|everything|alles|tout)|"
    r"meine\s+daten\s+(?:loeschen|loschen))"
    r"\s*[.!]*$"
)
_EXPORT_VERB = (
    r"(?:exporta(?:r)?|export(?:eer|er|e)?|exportier(?:e|en)|baix(?:a|ar)|download|"
    r"telecharg(?:e|er)|descarreg(?:a|ar))"
)
_EXPORT_RE = re.compile(
    rf"^{_WANT}(?:{_EXPORT_VERB}\s+(?:{_POSSESSIVE}\s+){{0,2}}{_DATA_OBJ}|"
    r"meine\s+daten\s+exportieren)\s*[.!]*$"
)
WIPE_CONFIRM_SECONDS = 600
_WIPE_YES_RE = re.compile(
    r"^(?:sim|s|yes|yep|ja|jep|oui|si|confirmo|confirm|confirmar|apagar\s+tudo|delete\s+everything|"
    r"alles\s+verwijderen|tout\s+supprimer|alles\s+loschen)[\s.!]*$"
)
_WIPE_NO_RE = re.compile(
    r"^(?:nao|n|no|nee|non|nein|cancelar|cancela|cancel|annuleren|annuler|abbrechen)[\s.!]*$"
)


async def _wipe_state(session: AsyncSession, member: Member) -> tuple[str, int] | None:
    """The member's latest wipe event: ("requested" | "cancelled", epoch seconds), or None."""
    row = (
        await session.execute(
            select(AuditLog.event, AuditLog.detail)
            .where(
                AuditLog.member_id == member.id,
                AuditLog.event.in_(("wipe_requested", "wipe_cancelled")),
            )
            .order_by(AuditLog.created_at.desc())
            .limit(1)
        )
    ).first()
    if row is None:
        return None
    try:
        ts = int((row[1] or {}).get("ts") or 0)
    except (TypeError, ValueError):
        ts = 0
    return ("requested" if row[0] == "wipe_requested" else "cancelled", ts)


async def _wipe_pending(session: AsyncSession, member: Member) -> bool:
    """True while the last "delete my data" question is still open (asked, not cancelled, fresh)."""
    state = await _wipe_state(session, member)
    if state is None or state[0] != "requested":
        return False
    return 0 <= int(datetime.now(UTC).timestamp()) - state[1] <= WIPE_CONFIRM_SECONDS


_AMOUNT_CORRECTION_RE = re.compile(
    # The whole message must be the correction: "errei, foram 42€", "na verdade foram 32".
    # "actually I spent 20 at Lidl" is a new expense and must NOT rewrite the last one.
    r"^(?:errei|foi\s+na\s+verdade|na\s+verdade\s+foi|corrijo|na\s+verdade|"
    r"actually|was\s+actually|c\'?etait|war\s+eigentlich|was\s+eigenlijk)"
    r"\b[^0-9€$£]{0,25}?([€$£]?\s*(?:[0-9]{1,3}(?:\.[0-9]{3})+(?:,[0-9]{1,2})?|[0-9]+(?:[.,][0-9]{1,2})?))\s*(?:[€$£]|eur(?:os?)?)?\s*[.!]*$",
    re.IGNORECASE,
)


def _label_aliases() -> dict[str, str]:
    """Every shown category name ("Transporte", "Lazer", "Vervoer"…) points back to its id."""
    from alfred.labels import _LABELS

    out: dict[str, str] = {}
    for cat, names in _LABELS.items():
        for name in names.values():
            out.setdefault(name.lower(), cat)
            out.setdefault(strip_accents(name.lower()), cat)
    return out


def _canonical_category(raw: str) -> str | None:
    """Return canonical category for a raw user string, or None if not recognised."""
    lower = raw.lower().strip()
    if lower in _CAT_ALIAS:
        return _CAT_ALIAS[lower]
    if lower in _LABEL_ALIASES:  # "isso é transporte" (the name the member reads in replies)
        return _LABEL_ALIASES[lower]
    if lower in {c for c in _VALID_CATEGORIES if not _CAT_ALIAS.get(lower)}:
        return lower
    return None


_LABEL_ALIASES = _label_aliases()


# ── M7 — Treino keywords ────────────────────────────────────────────────────
_WORKOUT_WORDS = {
    "corri",
    "correr",
    "treino",
    "treinar",
    "ginásio",
    "ginasio",
    "gym",
    "exercício",
    "exercicio",
    "yoga",
    "pilates",
    "caminhei",
    "caminhada",
    "natação",
    "natacao",
    "ciclismo",
    "hiit",
    "alongamento",
    "musculação",
    "musculacao",
    "ran",
    "run",
    "walked",
    "walk",
    "trained",
    "workout",
    "exercise",
    "swam",
    "swim",
    "cycling",
    "jogged",
    "joggen",
    "fietste",
    "zwom",
    "trainde",
    "liep",
    "sportde",
    "couru",
    "marché",
    "nagé",
    "cyclisme",
    "gelaufen",
    "geschwommen",
    "nadei",
    "pedalei",
    "cycled",
    "biked",
    "zwemde",
    "gezwommen",
    "gefietst",
    "joguei",
}

# ── M8 — Saúde keywords ──────────────────────────────────────────────────────
_HEALTH_WORDS = {
    "tomei",
    "tomi",
    "took",
    "nam",
    "pris",
    "eingenommen",  # medication
    "humor",
    "mood",
    "humeur",
    "stimmung",  # mood
    "dormi",
    "slept",
    "sliep",
    "geschlafen",
    "dormido",  # sleep
    "bebi",
    "drank",
    "dronk",
    "bu",  # water
}

# Health logs are also written mid-sentence ("ontem dormi 6h30", "sinto-me um 6 em 10").
_HEALTH_HINT_RE = re.compile(
    r"\b(?:sinto[- ]me|estou\s+(?:a\s+)?sentir|feeling|i\s+feel|ik\s+voel\s+me|"
    r"dormi|slept|sliep|geschlafen|bebi|drank|dronk|tomei|took)\b|"
    r"\b(?:mais|another|nog)\s+\d+\s*(?:ml|cl|l)\b",
    re.IGNORECASE,
)
_BARE_YES_RE = re.compile(
    r"^(?:sim|s|yes|yep|yeah|ja|jep|oui|si|claro|ok|okay|okey)[\s.!]*$", re.IGNORECASE
)


_NOT_MONEY_UNIT_RE = re.compile(
    r"\d\s*(?:h|hs|hora|horas|hour|hours|uur|stunden?|min|mins|minutos?|minutes?|minuten|km|kg|"
    r"x\b|vezes|times|keer|fois|mal|reps?|series|séries|sets?|passos|steps)\b|"
    r"\b(?:academia|gym|treino|carga|supino|sportschool|training|workout)\b",
    re.IGNORECASE,
)


def _fallback_key(body: str) -> str:
    """Expense hint only when the message looks like an expense: a number that is not minutes,
    hours, kilos or a workout (B-035: "fui à academia 1 hora" got the expense example)."""
    if _NUMBER_RE.search(body) and not _NOT_MONEY_UNIT_RE.search(body):
        return "fallback_no_record"
    return "fallback_unknown"


def _norm_words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", strip_accents(text).lower())


def _fuzzy_contains(needle: str, haystack: str) -> bool:
    """Accent-insensitive; each word of ``needle`` matches by its 4-letter stem.

    "corrida" finds "correr" (corr), "aniversário" finds "aniversario".
    """
    hay = _norm_words(haystack)
    words = _norm_words(needle)
    if not words:
        return False
    if " ".join(words) in " ".join(hay):
        return True
    key = [w for w in words if len(w) > 2]
    return bool(key) and all(
        any(h.startswith(w[:4] if len(w) >= 5 else w) for h in hay) for w in key
    )


_SLEEP_RE = re.compile(
    r"(?:dormi|slept|sliep|geschlafen|dormido)\s+(\d+(?:[.,]\d+)?)\s*"
    r"(?:h(?:oras?|ours?|)?|uur|stunden?)?",
    re.IGNORECASE,
)
_MOOD_RE = re.compile(
    r"(?:humor|mood|humeur|stimmung)[:\s]+(\d{1,2})(?:\s*/\s*10)?",
    re.IGNORECASE,
)
_WATER_RE = re.compile(
    r"(?:bebi|drank|dronk|bu)\s+(\d+(?:[.,]\d+)?)\s*[Ll](?:\s+(?:de\s+)?(?:água|agua|water|wasser|eau))?",
    re.IGNORECASE,
)
_MED_RE = re.compile(
    r"(?:tomei|took|nam|pris|eingenommen|genommen)\s+(.+)",
    re.IGNORECASE,
)

# ── M9 — Metas & Hábitos keywords ────────────────────────────────────────────
_GOAL_CREATE_RE = re.compile(
    r"(?:meta|objetivo|goal|doel|ziel|objectif)\s*[:]\s*(.+)",
    re.IGNORECASE,
)
_GOALS_QUERY_WORDS = {
    "minhas metas",
    "as minhas metas",
    "meus objetivos",
    "my goals",
    "mijn doelen",
    "meine ziele",
    "mes objectifs",
    "como vão as metas",
    "como vão as minhas metas",
    "como vai a minha meta",
}
_HABIT_WORDS = {
    "meditei",
    "meditated",
    "mediteerde",
    "meditiert",
    "bebi água",
    "drank water",
    "leste",
    "li",
    "estudei",
    "estudied",
}
_HABIT_LOG_RE = re.compile(
    # The habit word must OPEN the message (after "hoje"/"eu"): searching anywhere turned
    # "gastei 12 no tivoli", "ravioli 12" and "criar viagem Bali" into the habit "li".
    r"^\s*(?:(?:hoje|today|vandaag|heute|aujourd'hui|eu|ik|i|ich|je|ja|já)\s+){0,2}"
    r"(?P<activity>meditei|meditated|mediteerde|meditiert|li|leste|estudei|fiz yoga|"
    r"bebi água|drank water|fiz pilates|fiz alongamento)\b\s*(?:hoje|today|vandaag|heute|aujourd'hui)?",
    re.IGNORECASE,
)

# ── M10 — Produtividade keywords ─────────────────────────────────────────────
_NOTE_RE = re.compile(
    r"^(?:nota|note|notitie|notiz|remarque|anotação|anotacao)\s*[:]\s*(.+)",
    re.IGNORECASE,
)
_TASK_RE = re.compile(
    r"^(?:tarefa|task|taak|aufgabe|tâche)(?:\s*:\s*|\s+"
    # A-63: "tarefa difícil essa" is a comment about a task, not a new one (a colon still makes it one)
    r"(?!(?:dif[ií]cil|f[aá]cil|chata|boa|ruim|complicada|longa|pesada|essa|esta|dessa|desta|"
    r"feita|conclu[ií]da|hard|easy|done|moeilijk|makkelijk|difficile|facile|schwer|leicht)\b))(.+)",
    re.IGNORECASE,
)
_DONE_RE = re.compile(
    r"^(?:feito|done|klaar|erledigt|fait|concluido|concluído)\s*[:]\s*(.+)",
    re.IGNORECASE,
)
_TASKS_QUERY_WORDS = {
    "minhas tarefas",
    "as minhas tarefas",
    "o que tenho para fazer",
    "my tasks",
    "mijn taken",
    "meine aufgaben",
    "mes tâches",
    "lista de tarefas",
    "task list",
}


# ── M5 — days_mask NL parsing + list/cancel ──────────────────────────────────
_DAY_BITS: dict[str, int] = {
    # Monday = 1
    "segunda": 1,
    "segunda-feira": 1,
    "monday": 1,
    "maandag": 1,
    "lundi": 1,
    "montag": 1,
    # Tuesday = 2
    "terca": 2,
    "terca-feira": 2,
    "tuesday": 2,
    "dinsdag": 2,
    "mardi": 2,
    "dienstag": 2,
    # Wednesday = 4
    "quarta": 4,
    "quarta-feira": 4,
    "wednesday": 4,
    "woensdag": 4,
    "mercredi": 4,
    "mittwoch": 4,
    # Thursday = 8
    "quinta": 8,
    "quinta-feira": 8,
    "thursday": 8,
    "donderdag": 8,
    "jeudi": 8,
    "donnerstag": 8,
    # Friday = 16
    "sexta": 16,
    "sexta-feira": 16,
    "friday": 16,
    "vrijdag": 16,
    "vendredi": 16,
    "freitag": 16,
    # Saturday = 32
    "sabado": 32,
    "saturday": 32,
    "zaterdag": 32,
    "samedi": 32,
    "samstag": 32,
    # Sunday = 64
    "domingo": 64,
    "sunday": 64,
    "zondag": 64,
    "dimanche": 64,
    "sonntag": 64,
}

_DAYS_LABEL: dict[int, str] = {
    1: "Mon",
    2: "Tue",
    4: "Wed",
    8: "Thu",
    16: "Fri",
    32: "Sat",
    64: "Sun",
}


def _parse_days_mask(text: str) -> int:
    """Parse days bitmask from free-text. Returns 127 (all days) if no days specified."""
    lower = unicodedata.normalize("NFD", text.lower())
    lower = "".join(c for c in lower if unicodedata.category(c) != "Mn")
    if any(
        w in lower
        for w in (
            "dias uteis",
            "dias de semana",
            "weekdays",
            "werkdagen",
            "jours ouvrables",
            "werktage",
        )
    ):
        return 31  # Mon–Fri
    if any(w in lower for w in ("fim de semana", "fins de semana", "weekend", "wochenende")):
        return 96  # Sat+Sun
    if any(
        w in lower
        for w in (
            "todos os dias",
            "every day",
            "elke dag",
            "tous les jours",
            "jeden tag",
            "diariamente",
            "daily",
        )
    ):
        return 127
    mask = 0
    for day, bit in _DAY_BITS.items():
        day_norm = unicodedata.normalize("NFD", day)
        day_norm = "".join(c for c in day_norm if unicodedata.category(c) != "Mn")
        if day_norm in lower:
            mask |= bit
    return mask if mask else 127


def _mask_to_label(mask: int) -> str:
    """Convert days_mask int to human-readable short label."""
    if mask == 127:
        return "daily"
    if mask == 31:
        return "weekdays"
    if mask == 96:
        return "weekend"
    return ",".join(v for k, v in sorted(_DAYS_LABEL.items()) if mask & k)


_LIST_LEMBRETES_WORDS = {
    "os meus lembretes",
    "meus lembretes",
    "lembretes activos",
    "my reminders",
    "mijn herinneringen",
    "mes rappels",
    "meine erinnerungen",
    "ver lembretes",
    "lista de lembretes",
}
_CANCEL_LEMBRETE_RE = re.compile(
    r"(?:cancela|cancel|annuler|abbrechen|annuleer)\s+(?:lembrete|reminder|herinnering|rappel|erinnerung)\s+(?:de|of|van|du|von|sobre)?\s*(.+)",
    re.IGNORECASE,
)

# ── M7 — extra workout patterns ──────────────────────────────────────────────
_WORKOUT_DELETE_RE = re.compile(
    # Whole message: "apaga o treino", "apaga o treino de hoje/ontem". Anything else
    # ("apaga o treino de segunda") must NOT delete the latest workout by accident.
    r"^(?:apaga|apagar|delete|verwijder|supprimer|losch)\s+"
    r"(?:o\s+(?:meu\s+)?|the\s+|het\s+|le\s+|mein\s+)?"
    r"(?:(?:ultimo|last|laatste|dernier|letzte[nrs]?)\s+)?"
    r"(?:treino|workout|training|entrainement)\s*"
    r"(?:de\s+)?(?:hoje|ontem|today|yesterday|vandaag|gisteren|heute|gestern|"
    r"aujourd.hui|hier|ultimo|last|laatste|dernier|letzte[nrs]?)?\s*[.!?]*$",
    re.IGNORECASE,
)
_WORKOUT_DELETE_DAY_RE = re.compile(
    r"^(?:apaga|apagar|apague|delete|verwijder|supprime|supprimer|losch|loesche)\s+"
    r"(?:(?:o|a|the|het|de|le|la|mein|meine)\s+)?(?P<what>[a-z]+)\s+"
    r"(?:de|da|do|on|van|op|du|vom|am)\s+(?P<day>segunda|terca|quarta|quinta|sexta|sabado|domingo|"
    r"monday|tuesday|wednesday|thursday|friday|saturday|sunday|maandag|dinsdag|woensdag|donderdag|"
    r"vrijdag|zaterdag|zondag|lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche|montag|dienstag|"
    r"mittwoch|donnerstag|freitag|samstag|sonntag)(?:[- ]feira)?\s*[.!?]*$"
)
_WORKOUT_ACTIVITY_RE = re.compile(
    r"(?:quantas\s+vezes|how\s+many\s+times|hoe\s+vaak|combien\s+de\s+fois|wie\s+oft)\s+"
    r"(?:corri|ran|liep|couru|gelaufen|fui\s+ao\s+ginasio|went\s+to\s+gym|"
    r"nadei|swam|zwom|nage|geschwommen|treinei|trained|trainde)",
    re.IGNORECASE,
)
_WORKOUT_MONTH_RE = re.compile(
    r"(?:treinos|workouts|trainingen|trainings)\s+"
    r"(?:de\s+|do\s+|em\s+|no\s+|in\s+|im\s+|en\s+)?"
    r"(?:(?:este|deste|neste)\s+mes|this\s+month|deze\s+maand|ce\s+mois|diesen\s+monat|"
    r"mes\s+passado|last\s+month|vorige\s+maand|mois\s+dernier|letzten\s+monat|"
    r"janeiro|fevereiro|marco|abril|maio|junho|julho|agosto|setembro|outubro|novembro|dezembro|"
    r"january|february|march|april|may|june|july|august|september|october|november|december)",
    re.IGNORECASE,
)

# ── M8 — health history query patterns ────────────────────────────────────────
_HEALTH_MOOD_QUERY_RE = re.compile(
    r"(?:como\s+(?:foi|esta)\s+o\s+(?:meu\s+)?humor|humor\s+(?:esta\s+semana|this\s+week)|"
    r"my\s+mood\s+(?:this\s+week|history)|mood\s+(?:this\s+week|history)|"
    r"stemming\s+deze\s+week|humeur\s+(?:cette\s+semaine)|stimmung\s+(?:diese\s+woche))",
    re.IGNORECASE,
)
_HEALTH_SLEEP_QUERY_RE = re.compile(
    r"(?:quantas\s+horas\s+dormi|sono\s+medio|average\s+sleep|gem(?:iddelde)?\s+slaap|"
    r"how\s+(?:many\s+hours|much)\s+(?:did\s+i\s+sleep|sleep)|"
    r"sommeil\s+moyen|durchschnittlicher\s+schlaf)",
    re.IGNORECASE,
)
_HEALTH_MED_QUERY_RE = re.compile(
    r"(?:tomei\s+(?:a\s+)?medicacao\s+todos\s+os\s+dias|aderencia\s+(?:da\s+)?medicacao|"
    r"medication\s+adherence|medicatie\s+bijgehouden|took\s+medication\s+(?:every\s+day|all\s+week)|"
    r"observance\s+medicament|medikamenten\s+einhaltung)",
    re.IGNORECASE,
)
_HEALTH_WATER_QUERY_RE = re.compile(
    r"(?:bebi\s+agua\s+suficiente|agua\s+(?:de\s+)?hoje|water\s+(?:today|intake)|"
    r"hoeveel\s+water|water\s+vandaag|eau\s+(?:aujourd.hui|d.aujourd.hui)|wasser\s+heute)",
    re.IGNORECASE,
)

# ── M9 — goal completion + habit frequency ────────────────────────────────────
_GOAL_COMPLETE_RE = re.compile(
    r"(?:meta|objetivo|goal|doel|ziel|objectif)\s+(?:de\s+)?(.+?)\s+"
    r"(?:concluida|concluido|feita|feito|done|klaar|erledigt|fait|accomplie?)",
    re.IGNORECASE,
)
# Health metrics the app does not store (weight, blood pressure, glucose): never file them as medication.
_UNSUPPORTED_HEALTH_RE = re.compile(
    r"\b(?:peso|pressão|pressao|glicose|glicemia|weight|blood\s+pressure|glucose|gewicht|bloeddruk|"
    r"poids|tension|blutdruck)\b|\b\d+(?:[.,]\d+)?\s*kg\b",
    re.IGNORECASE,
)
_MEDICATION_TALK_RE = re.compile(
    r"\b(?:tomei|tomar|rem[eé]dio|medica[cç][aã]o|comprimido|took|medicine|pill|ingenomen|medicijn|"
    r"pris|m[eé]dicament|genommen|tablette)\b",
    re.IGNORECASE,
)


def _unsupported_health(text: str) -> bool:
    """Weight / blood pressure (not tracked) — but "tomei o remédio da pressão" is medication."""
    if not _UNSUPPORTED_HEALTH_RE.search(text):
        return False
    return not (_MEDICATION_TALK_RE.search(text) and not re.search(r"\d", text))


_HABIT_FREQ_RE = re.compile(
    r"(?:quantas\s+vezes|how\s+many\s+times|hoe\s+vaak|combien\s+de\s+fois|wie\s+oft)\s+"
    r"(?:(?:eu\s+)?fiz|did\s+i\s+do|deed\s+ik|ai-je\s+fait|habe\s+ich\s+gemacht|"
    r"meditei|meditated|li|leste|estudei)\b\s*(.+)?",
    re.IGNORECASE,
)
_FREQ_MONTH_WORDS = (
    "este mes",
    "deste mes",
    "neste mes",
    "this month",
    "deze maand",
    "ce mois",
    "diesen monat",
)
_FREQ_WORKOUT_WORDS = {
    "musculacao": "strength", "krachttraining": "strength", "musculation": "strength",
    "krafttraining": "strength", "gym": "strength", "ginasio": "strength",
    "corrida": "running", "correr": "running", "hardlop": "running", "course": "running", "laufen": "running",
    "natacao": "swimming", "zwemmen": "swimming", "swimming": "swimming", "natation": "swimming",
    "ciclismo": "cycling", "fietsen": "cycling", "cycling": "cycling",
    "caminhada": "walking", "wandelen": "walking", "walking": "walking",
}  # fmt: skip
_HABIT_STREAK_RE = re.compile(
    r"(?:"
    r"quantos\s+dias\s+(?:seguidos|consecutivos|em\s+sequencia)"
    r"|streak\s+de\b"
    r"|how\s+many\s+days\s+in\s+a\s+row"
    r"|current\s+streak"
    r"|dagen\s+op\s+rij"
    r"|(?:hoeveel|mijn)\s+streak\b"
    r"|combien\s+de\s+jours\s+cons[ée]cutifs"
    r"|ma\s+s[ée]rie"
    r"|wie\s+viele\s+tage\s+in\s+folge"
    r"|meine\s+serie\b"
    r")\s*(.+)?",
    re.IGNORECASE,
)

# ── M10 — notes list + task delete ────────────────────────────────────────────
_NOTES_QUERY_WORDS = {
    "as minhas notas",
    "minhas notas",
    "as notas",
    "ver notas",
    "my notes",
    "mijn notities",
    "mes notes",
    "meine notizen",
    "lista de notas",
    "notes list",
}

# \u2500\u2500 M11 \u2014 Dashboard keywords \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
_PANEL_REVOKE_RE = re.compile(
    r"^(?:(?:gera|gerar|quero|manda|me manda)\s+)?(?:um\s+)?(?:novo\s+link|link\s+novo)\s+(?:do|para o)\s+"
    r"(?:painel|dashboard)|(?:revoga|revogar|cancela|cancelar|troca|trocar|invalida|invalidar)\s+"
    r"(?:o\s+)?link\s+do\s+(?:painel|dashboard)|new\s+(?:dashboard|panel)\s+link|"
    r"revoke\s+(?:the\s+)?(?:dashboard|panel)\s+link|nieuwe\s+(?:dashboard|paneel)\s*link|"
    r"nouveau\s+lien\s+du\s+(?:tableau|panneau)|neuer\s+(?:dashboard|panel)\s*link$"
)
_DASHBOARD_WORDS: set[str] = {
    "meu dashboard",
    "o meu dashboard",
    "dashboard",
    "my dashboard",
    "mijn dashboard",
    "mon tableau de bord",
    "mein dashboard",
    "link dashboard",
    "dashboard link",
    "abrir dashboard",
    "painel",
    "meu painel",
    "o meu painel",
    "abrir painel",
    "abre o painel",
    "abrir o painel",
    "link do painel",
}
_YESTERDAY_WORD_RE = re.compile(r"\b(?:ontem|yesterday|gisteren|hier|gestern)\b")
_DEL_VERB = (
    r"(?:apaga|apagar|apague|exclui|excluir|exclua|remove|remover|deleta|deletar|delete|tira|tirar|"
    r"verwijder|supprime|supprimer|efface|losch|losche|loesche|entferne)"
)
_TASK_DELETE_RE = re.compile(
    rf"^{_DEL_VERB}\s+(?:a\s+|minha\s+|the\s+|de\s+|la\s+|die\s+)?(?:tarefa|task|taak|aufgabe|tache)"
    r"\s+(?:n(?:umero|o|º|r)?\.?\s*)?(.+)",
    re.IGNORECASE,
)
# B-028: "apaga a nota do wifi" — the model said "Nota deletada" and nothing was deleted
_NOTE_DELETE_RE = re.compile(
    rf"^{_DEL_VERB}\s+(?:a\s+|minha\s+|the\s+|my\s+|de\s+|la\s+|die\s+)?"
    r"(?:nota|note|notitie|notiz)\s+(?:do\s+|da\s+|de\s+|dos\s+|das\s+|sobre\s+|about\s+|"
    r"over\s+|van\s+|sur\s+|du\s+|uber\s+|zum\s+|zur\s+)?(.+)",
    re.IGNORECASE,
)
# B-030: "apaga a meta correr 10 km" — same: the model claimed it, the goal stayed
_GOAL_DELETE_RE = re.compile(
    rf"^{_DEL_VERB}\s+(?:a\s+|minha\s+|the\s+|my\s+|de\s+|la\s+|die\s+|het\s+|mijn\s+)?"
    r"(?:meta|objetivo|goal|doel|ziel|objectif)\s+(?:de\s+|do\s+|da\s+)?(.+)",
    re.IGNORECASE,
)
# M-15: a note that looks like a password gets a short word of care
_SECRET_RE = re.compile(r"\b(?:senha|password|wachtwoord|mot de passe|passwort|pin|wifi|wi-fi)\b")

_JOB_TYPE_MAP: dict[str, str] = {
    "medicamento": "medication_reminder",
    "medicine": "medication_reminder",
    "medication": "medication_reminder",
    "medicatie": "medication_reminder",
    "médicament": "medication_reminder",
    "medikament": "medication_reminder",
    "pil": "medication_reminder",
    "treino": "workout_reminder",
    "treinar": "workout_reminder",
    "workout": "workout_reminder",
    "training": "workout_reminder",
    "sport": "workout_reminder",
    "objetivo": "goal_checkin",
    "goal": "goal_checkin",
    "doel": "goal_checkin",
    "objectif": "goal_checkin",
    "ziel": "goal_checkin",
}


def _detect_job_type(text: str) -> str:
    """Detect reminder job_type from text keywords; default medication_reminder."""
    lower = text.lower()
    for kw, jtype in _JOB_TYPE_MAP.items():
        # whole words: "transporte" contains "sport" and used to become a workout reminder (A-64)
        if re.search(rf"\b{re.escape(kw)}\b", lower):
            return jtype
    return "medication_reminder"


def _is_command(body: str, keywords: set[str]) -> bool:
    """True when body *starts with* a command keyword (accent- and case-insensitive)."""
    plain = strip_accents(body)
    for kw in keywords:
        k = strip_accents(kw)
        if plain == k or plain.startswith((k + " ", k + "?", k + "!")):
            return True
    return False


# Words that make a question an analysis (V2-14) rather than a plain list or total.
_ANALYSIS_HINT_RE = re.compile(
    r"compar|\bvs\b|\bversus\b|\bcontra\b|por mes|por categoria|media|per month|average|"
    r"per maand|gemiddeld|par mois|moyenne|pro monat|durchschnitt|trimestre|quarter|kwartaal"
)
# "saldo da conta 300" / "tenho 300 na conta" / "my bank balance is 300": a bank balance, which the
# app does not keep (its "saldo" is income minus spending of the month).
_BANK_BALANCE_RE = re.compile(
    r"^(?:meu\s+|o\s+)?saldo\s+(?:da|na|do|no)\s+(?:minha\s+|meu\s+)?(?:conta|banco)\b.*\d|"
    r"^tenho\s+[\d.,]+\s*(?:€|euros?)?\s+na\s+conta\b|"
    r"^(?:my\s+)?(?:bank|account)\s+balance\b.*\d|^(?:mijn\s+)?(?:banksaldo|saldo op mijn rekening)\b.*\d|"
    r"^(?:mon\s+)?solde\s+(?:bancaire|du\s+compte)\b.*\d|^(?:mein\s+)?kontostand\b.*\d"
)
ASK_AMOUNT_KIND = "ask_amount"  # PendingAction: "paguei gás" without a price waits for the amount
ASK_AMOUNT_MINUTES = 10
_PAID_NO_AMOUNT_RE = re.compile(
    r"^(?:eu\s+)?(?:paguei|gastei|paid|spent|betaalde|heb\s+betaald|paye|ai\s+paye|bezahlt|habe\s+bezahlt)\s+"
    r"(?:o\s+|a\s+|os\s+|as\s+|um\s+|uma\s+|de\s+|do\s+|da\s+|the\s+|for\s+|voor\s+|het\s+|de\s+|"
    r"le\s+|la\s+|les\s+|pour\s+|der\s+|die\s+|das\s+|fur\s+)?"
    r"(?P<item>[^\W\d_][^\d\u20ac$,.!?]{1,38}?)\s*[.!]?$"
)
_PAID_ITEM_STOP = frozenset(
    {"tudo", "ontem", "hoje", "agora", "ja", "nada", "isso", "isto", "it", "this", "that", "alles",
     "niets", "tout", "rien", "nichts", "tudo isso"}
)  # fmt: skip
_AMOUNT_ONLY_RE = re.compile(
    r"^\s*(?:\u20ac\s*)?(?P<amt>\d+(?:[.,]\d{1,2})?)\s*(?:\u20ac|euros?)?\s*$"
)
BARE_KIND = "bare_word"  # PendingAction: a lone "água"/"treino"/"remédio" waits for a yes
BARE_MINUTES = 10
# A-74: a word alone never records. Words that would log something get a yes/no first.
_BARE_RECORD_WORDS: dict[str, str] = {
    "agua": "water", "water": "water", "eau": "water", "wasser": "water",
    "treino": "workout", "treinar": "workout", "workout": "workout", "training": "workout",
    "trainen": "workout", "entrainement": "workout", "sport": "workout", "academia": "workout",
    "remedio": "med", "medicamento": "med", "medication": "med", "medicatie": "med",
    "medicijn": "med", "medicament": "med", "medikament": "med", "pilula": "med",
}  # fmt: skip
_BARE_PHRASES: dict[str, dict[str, str]] = {
    "water": {"pt": "tomei 1 copo de água", "nl": "ik dronk 1 glas water",
              "en": "I drank 1 glass of water", "fr": "j'ai bu 1 verre d'eau",
              "de": "ich habe 1 Glas Wasser getrunken"},
    "workout": {"pt": "treinei hoje", "nl": "ik heb vandaag getraind",
                "en": "I worked out today", "fr": "j'ai fait du sport aujourd'hui",
                "de": "ich habe heute trainiert"},
    "med": {"pt": "tomei o remédio", "nl": "ik heb mijn medicijn genomen",
            "en": "I took my medication", "fr": "j'ai pris mon médicament",
            "de": "ich habe mein Medikament genommen"},
}  # fmt: skip
_TZ_CMD_RE = re.compile(
    r"^(?:meu\s+|mijn\s+|my\s+|mon\s+|mein\s+)?(?:fuso(?:\s+horario)?|timezone|time\s+zone|tijdzone|"
    r"fuseau(?:\s+horaire)?|zeitzone)(?:\s+(?:e|eh|is|est|ist|para|to|em|in|auf|naar|=|:))?\s*"
    r"(?P<zone>[a-z][a-z /_.'-]*)?$"
)
_TZ_ALIASES: dict[str, str] = {
    "lisboa": "Europe/Lisbon", "lisbon": "Europe/Lisbon", "lissabon": "Europe/Lisbon",
    "lisbonne": "Europe/Lisbon", "portugal": "Europe/Lisbon",
    "amsterdam": "Europe/Amsterdam", "holanda": "Europe/Amsterdam", "netherlands": "Europe/Amsterdam",
    "nederland": "Europe/Amsterdam", "paises baixos": "Europe/Amsterdam", "pays-bas": "Europe/Amsterdam",
    "brasil": "America/Sao_Paulo", "brazil": "America/Sao_Paulo", "brasilia": "America/Sao_Paulo",
    "sao paulo": "America/Sao_Paulo", "rio": "America/Sao_Paulo", "rio de janeiro": "America/Sao_Paulo",
    "london": "Europe/London", "londres": "Europe/London", "londen": "Europe/London",
    "paris": "Europe/Paris", "parijs": "Europe/Paris", "franca": "Europe/Paris", "france": "Europe/Paris",
    "berlin": "Europe/Berlin", "berlim": "Europe/Berlin", "alemanha": "Europe/Berlin",
    "germany": "Europe/Berlin", "duitsland": "Europe/Berlin", "deutschland": "Europe/Berlin",
    "madrid": "Europe/Madrid", "espanha": "Europe/Madrid", "spain": "Europe/Madrid",
    "new york": "America/New_York", "nova york": "America/New_York", "nyc": "America/New_York",
    "los angeles": "America/Los_Angeles", "dublin": "Europe/Dublin",
}  # fmt: skip


def _timezone_from_text(text: str) -> str | None:
    """ "lisboa" / "são paulo" / "America/Sao_Paulo" → an IANA name, else None."""
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    key = " ".join(strip_accents(text.lower()).split())
    if key in _TZ_ALIASES:
        return _TZ_ALIASES[key]
    if "/" in key:
        name = "/".join(part.strip().replace(" ", "_").title() for part in key.split("/"))
        try:
            ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError, OSError):
            return None
        return name
    return None


_PURCHASE_NO_PRICE_RE = re.compile(
    r"^(?:eu\s+)?(?:comprei|compramos|bought|i\s+bought|gekocht|ik\s+heb\s+gekocht|j\s*ai\s+achete|"
    r"ich\s+habe\s+gekauft|gekauft)\s+\d+(?:[.,]\d+)?\s*(?:kg|g|gr|l|litros?|liters?|un|unidades?)\s+"
    r"(?:de\s+|of\s+|van\s+|d\s*|du\s+|von\s+)?(?P<item>[^\d]+?)\s*[.!]?$"
)
_SPEND_QUESTION_RE = re.compile(
    r"^(?:quanto\s+(?:eu\s+)?gastei|gastos?\b|minhas\s+despesas|despesas\b|how\s+much\s+did\s+i\s+spend|"
    r"my\s+(?:spending|expenses)|hoeveel\s+heb\s+ik\s+uitgegeven|uitgaven\b|combien\s+j\s*ai\s+depense|"
    r"depenses\b|wie\s*viel\s+habe\s+ich\s+ausgegeben|ausgaben\b)"
)
_SALDO_ELSEWHERE_RE = re.compile(r"\b(?:casa|ano|anual|year|jaar|annee|jahr)\b")


def _is_plain_command(body: str, keywords: set[str]) -> bool:
    """A command word at the start AND no number in the message: "ajuda com a mudança 50" or
    "saldo da conta 300" are expenses, not the help text or the balance."""
    return _is_command(body, keywords) and not re.search(r"\d", body)


_DID_LEAD_RE = re.compile(
    r"^(?:eu\s+)?(?:fiz|fui|faço|facas|did|had|ik\s+deed|deed|j'ai\s+fait|ich\s+habe)\b"
)


_DURATION_RE = re.compile(r"\b\d+\s*(?:min\w*|h\b|hora\w*|hour\w*|uur|uren|heure\w*|stunde\w*)")


def _is_workout_text(body: str) -> bool:
    """A workout line: starts with an activity word ("corri 5 km") or with a "did" verb
    followed by one ("fiz 50 minutos de musculação", "fiz yoga durante 20 minutos")."""
    if _is_command(body, _WORKOUT_WORDS):
        return True
    plain = strip_accents(body)
    if not _DID_LEAD_RE.match(plain):
        return False
    words = set(re.findall(r"[a-z']+", plain))
    return any(strip_accents(w) in words for w in _WORKOUT_WORDS if " " not in w)


_HABIT_NOUNS = {
    "pt": {
        "meditei": "meditação", "fiz yoga": "yoga", "fiz pilates": "pilates",
        "fiz alongamento": "alongamento", "bebi água": "água", "leste": "leitura",
        "li": "leitura", "estudei": "estudo",
    },
    "en": {"meditated": "meditation", "drank water": "water", "studied": "study"},
}  # fmt: skip


def _habit_label(activity: str, lang: str) -> str:
    """ "meditei" reads as "meditação" in a sentence like "dias seguidos de ___"."""
    return _HABIT_NOUNS.get(lang, {}).get(activity.strip().lower(), activity)


async def _habit_already_logged(session, member_id, activity: str, day) -> bool:
    """True when this habit already has a check-in today (one per day counts once)."""
    from sqlalchemy import func as _f
    from sqlalchemy import select as _s

    found = await session.scalar(
        _s(HabitLog.id)
        .where(
            HabitLog.member_id == member_id,
            _f.lower(HabitLog.activity) == activity.strip().lower(),
            HabitLog.log_date == day,
        )
        .limit(1)
    )
    return found is not None


def _goal_for_activity(goals, activity: str):
    """The active goal a habit log belongs to: a shared word, or a shared stem of 5+ letters
    ("meditei" × "meditar todos os dias")."""
    words = [w for w in strip_accents((activity or "").lower()).split() if w]
    for (
        nouns
    ) in _HABIT_NOUNS.values():  # "meditei" also reads as "meditação" (goals made by F10-11)
        label = nouns.get((activity or "").strip().lower())
        if label:
            words += [w for w in strip_accents(label.lower()).split() if w]
    for g in goals:
        title = strip_accents(g.title.lower())
        title_words = re.findall(r"[a-z]+", title)
        for w in words:
            if w in title_words or (len(w) >= 2 and w in title and len(w) > 3):
                return g
            if len(w) >= 5 and any(
                len(t) >= 5 and t[:5] == w[:5] and _common_prefix(t, w) >= 5 for t in title_words
            ):
                return g
    return None


def _common_prefix(a: str, b: str) -> int:
    n = 0
    for x, y in zip(a, b, strict=False):
        if x != y:
            break
        n += 1
    return n


_BARE_DAY_WORDS = frozenset(
    {"hoje", "ontem", "today", "yesterday", "vandaag", "gisteren", "heute", "gestern",
     "aujourd hui", "aujourdhui", "hier"}
)  # fmt: skip
_TOP_EXPENSES_RE = re.compile(
    r"^(?:quais (?:sao |foram )?)?(?:os )?(?:meus |minhas )?(?:maiores (?:gastos|despesas)|"
    r"(?:my )?(?:biggest|largest|top) expenses|(?:mijn )?grootste uitgaven|"
    r"(?:mes )?plus grosses depenses|(?:meine )?gro(?:ss|ß)ten ausgaben)\b[^?]{0,40}\??$"
)


# A habit is something done: a verb in the past ("meditei", "li 30 páginas", "I ran", "ik heb
# gelezen", "j'ai lu", "ich habe gelesen") or a habit noun with a time ("yoga 20 min").
_HABIT_VERB_RE = re.compile(
    r"\b(?:[a-z]{2,}(?:ei|ou|i)|li|fiz|fui|tomei|did|done|[a-z]{3,}ed|ran|read|went|"
    r"heb|ben|gedaan|ai|suis|habe|bin)\b"
)
_NOT_HABIT_WORDS = frozenset(
    "voltei apaga apagar desfaz desfazer hoje ontem amanha resumo saldo ajuda menu painel stop "
    "sair parar start cancela cancelar ok sim nao obrigado obrigada valeu".split()
)


def _could_be_habit(body_plain: str) -> bool:
    words = re.findall(r"[a-z]+", body_plain)
    if not words or (len(words) <= 2 and all(w in _NOT_HABIT_WORDS for w in words)):
        return False
    if TRIP_END_RE.match(body_plain.strip()) or DELETE_LAST_EXPENSE_RE.match(body_plain.strip()):
        return False
    return bool(_HABIT_VERB_RE.search(body_plain)) or len(words) >= 2


def _is_bare_command(body: str, keywords: set[str]) -> bool:
    """True when body is *only* a command keyword (plus punctuation / a polite word).

    Used where trailing words change the meaning: "gastos em restaurante" is a
    category query, not the plain "gastos" summary.
    """
    plain = strip_accents(body).strip(" .!?")
    for poss in (
        "my ",
        "the ",
        "mijn ",
        "het ",
        "mon ",
        "ma ",
        "le ",
        "mein ",
        "meine ",
        "das ",
        "o meu ",
        "meu ",
        "a minha ",
    ):
        if plain.startswith(poss) and plain != poss.strip():
            plain = plain[len(poss) :]
            break
    for polite in (" por favor", " please", " alsjeblieft", " s'il vous plait", " bitte"):
        plain = plain.removesuffix(polite)
    return plain in {strip_accents(k) for k in keywords}


def _grp(body: str, m: re.Match[str], n: int = 1) -> str:
    """Group ``n`` of a match made on ``body_plain``, sliced from the accented ``body``."""
    return body[m.start(n) : m.end(n)] if m.group(n) is not None else ""


# ── M14 — Viagem handler ─────────────────────────────────────────────────────


def _cents(value: float) -> float:
    """Round a Python-side float sum to whole cents (0.1 + 0.2 must not become -0.00)."""
    return round(float(value), 2)


def _budget_remaining(budget: float, spent: float, lang: str) -> str:
    """' · restante €380,00' or ' · €20,00 acima do orçamento'."""
    left = _cents(float(budget) - float(spent))
    if left >= 0:
        return _t("trip_budget_left", lang, left=_fmt_eur(left))
    return _t("trip_budget_over", lang, over=_fmt_eur(-left))


async def _handle_trip(
    body: str,
    member: Member,
    lang: str,
    session: AsyncSession,
) -> str | None:
    """Return a reply if the message is trip-related, else None."""
    from decimal import Decimal

    from sqlalchemy import func as sqlfunc
    from sqlalchemy import select

    # ── start trip ────────────────────────────────────────────────────────
    dest = match_trip_start(body)
    if dest:
        active = await session.scalar(
            select(Trip).where(Trip.member_id == member.id, Trip.active.is_(True))
        )
        if active:
            return _t("trip_already_active", lang, dest=active.destination)

        budget = parse_budget(body) or trip_bare_budget(body)
        trip = Trip(
            id=uuid.uuid4(),
            member_id=member.id,
            destination=dest,
            started_at=parse_trip_start_date(body, today_local()) or today_local(),
            active=True,
            budget=budget,
        )
        session.add(trip)
        if budget:
            return _t("trip_started_budget", lang, dest=dest, budget=_fmt_eur(budget))
        return _t("trip_started", lang, dest=dest)

    # ── end trip ──────────────────────────────────────────────────────────
    if TRIP_END_RE.match(body.strip()):
        active = await session.scalar(
            select(Trip).where(Trip.member_id == member.id, Trip.active.is_(True))
        )
        if not active:
            return _t("trip_none_active", lang)

        active.ended_at = today_local()
        if active.started_at > active.ended_at:  # trip announced for a future date, ended early
            active.started_at = active.ended_at
        active.active = False

        row = (
            await session.execute(
                select(
                    sqlfunc.sum(Expense.amount).label("total"),
                    sqlfunc.count(Expense.id).label("cnt"),
                ).where(
                    Expense.trip_id == active.id,
                    Expense.transaction_type == "expense",
                    Expense.status.in_(SETTLED),
                )
            )
        ).one()
        total = row.total or Decimal("0")
        count = row.cnt or 0
        return _t(
            "trip_ended_one" if count == 1 else "trip_ended",
            lang,
            dest=active.destination,
            total=_fmt_eur(total),
            count=count,
        )

    # ── trip summary ──────────────────────────────────────────────────────
    if TRIP_QUERY_RE.match(body.strip()):
        trip = await session.scalar(
            select(Trip).where(Trip.member_id == member.id, Trip.active.is_(True))
        )
        if not trip:
            trip = await session.scalar(
                select(Trip)
                .where(Trip.member_id == member.id)
                .order_by(Trip.created_at.desc())
                .limit(1)
            )
        if not trip:
            return _t("trip_none_active", lang)

        rows = (
            await session.execute(
                select(
                    Expense.merchant,
                    Expense.category,
                    Expense.amount,
                    Expense.expense_date,
                )
                .where(
                    Expense.trip_id == trip.id,
                    Expense.transaction_type == "expense",
                )
                .order_by(Expense.expense_date.asc())
            )
        ).all()

        if not rows:
            return _t("trip_no_expenses", lang)

        total = _cents(sum(r.amount for r in rows))
        end_str = trip.ended_at.strftime("%d/%m") if trip.ended_at else _t("trip_today", lang)
        lines = [
            _t(
                "trip_summary_header",
                lang,
                dest=trip.destination,
                start=trip.started_at.strftime("%d/%m"),
                end=end_str,
            )
        ]
        for r in rows:
            lines.append(
                # A-55: the category as the member reads it, never the internal "overig"
                f"  {to_local(r.expense_date).strftime('%d/%m')}  "
                f"{r.merchant or category_label(r.category, lang)}"
                f" ({category_label(r.category, lang)})  {_fmt_eur(r.amount)}"
            )
        lines.append(
            _t(
                "trip_summary_total_one" if len(rows) == 1 else "trip_summary_total",
                lang,
                total=_fmt_eur(total),
                n=len(rows),
            )
        )
        if trip.budget:
            lines.append(
                _t("trip_summary_budget", lang, budget=_fmt_eur(trip.budget))
                + _budget_remaining(trip.budget, total, lang)
            )
        return "\n".join(lines)

    # ── trip list ─────────────────────────────────────────────────────────
    if TRIP_LIST_RE.match(body.strip()):
        trips = (
            (
                await session.execute(
                    select(Trip)
                    .where(Trip.member_id == member.id)
                    .order_by(Trip.started_at.desc())
                    .limit(10)
                )
            )
            .scalars()
            .all()
        )

        if not trips:
            return _t("trip_list_empty", lang)

        from decimal import Decimal as _D

        lines_out: list[str] = []
        for t in trips:
            row = (
                await session.execute(
                    select(sqlfunc.sum(Expense.amount)).where(
                        Expense.trip_id == t.id,
                        Expense.transaction_type == "expense",
                        Expense.status.in_(SETTLED),
                    )
                )
            ).scalar()
            total = row or _D("0")
            status = "\u25b6" if t.active else "\u2713"
            end_str = t.ended_at.strftime("%d/%m/%y") if t.ended_at else _t("trip_ongoing", lang)
            lines_out.append(
                f"{status} {t.destination}  "
                f"{t.started_at.strftime('%d/%m/%y')} \u2192 {end_str}  "
                f"{_fmt_eur(total)}"
            )
        return "\n".join(lines_out)

    return None


# ── History window ────────────────────────────────────────────────────────────

_HISTORY_LIMIT = 10  # messages (pairs) to include in LLM context


async def _load_history(
    member: Member,
    session: AsyncSession,
    exclude_id: object | None = None,
) -> list[dict]:
    """Return the last N messages for this member as Claude-format dicts.

    ``exclude_id`` is the inbound message being answered: it is already stored
    in this transaction, and ``generate_reply`` appends it itself.
    """
    query = select(Message).where(Message.author_id == member.id)
    if exclude_id is not None:
        query = query.where(Message.id != exclude_id)
    result = await session.execute(
        query.order_by(Message.created_at.desc()).limit(_HISTORY_LIMIT * 2)
    )
    rows = result.scalars().all()
    rows = list(reversed(rows))  # chronological order

    history: list[dict] = []
    for msg in rows:
        role = "user" if msg.direction == "inbound" else "assistant"
        if msg.body:
            history.append({"role": role, "content": msg.body})
    return history


async def _save_outbound(
    member: Member,
    body: str,
    session: AsyncSession,
    kind: str = "reply",
) -> None:
    """Persist an outbound message so it appears in future history.

    When the send happened in this handler run, the row carries Meta's message id so the
    status webhook (sent/delivered/read/failed) can find it later (V2-18).
    """
    from alfred import delivery

    wamid = delivery.take_wamid()
    outbound = Message(
        id=uuid.uuid4(),
        wa_message_id=wamid or f"out-{uuid.uuid4()}",
        kind=kind,
        delivery_status="sent" if wamid else None,
        household_id=member.household_id,
        author_id=member.id,
        direction="outbound",
        body=body,
        wa_timestamp=datetime.now(UTC),
        processed=True,
    )
    session.add(outbound)


_RECORDED_CLAIM_RE = re.compile(
    r"registad[oa]s?|registrad[oa]s?|registei|registrei|anotei|anotad[oa]s?|guardad[oa]s?|"
    r"recorded|saved|logged|"
    r"geregistreerd|opgeslagen|genoteerd|vastgelegd|enregistr[ée]e?s?|not[ée]e?s?\b|erfasst|gespeichert|notiert|eingetragen|noted",
    re.IGNORECASE,
)
# B-004/B-028/B-029/B-030/B-051: on the free-reply path nothing was deleted, cancelled, switched
# off or previewed either ("Apaguei: €1.200,00 em cinema", "Nota deletada", "Você foi
# desconectado", a made-up "Entendi este contrato: …"). Such a reply is replaced.
_ACTION_CLAIM_RE = re.compile(
    r"apaguei|apagad[oa]s?|delet(?:ei|ad[oa]s?)|removi|removid[oa]s?|exclu[ií]d[oa]s?|"
    r"cancelei|cancelad[oa]s?|desconectad[oa]|vou parar|parei de|desativad[oa]|"
    r"seu saldo [ée]|guardei|salvei|marquei|gravei|gravad[oa]s?|confirmad[oa]s?:|marcad[oa]s? como pag|"
    r"vou te lembrar|vou lembrar voc|i'll remind you|ik herinner je|je te rappellerai|"
    r"desfeito|undone|ongedaan|"
    r"\bpag[oa]s? em \d|atualizad[oa]s?:|\bupdated:|bijgewerkt:|mis à jour :|aktualisiert:|"
    r"entendi est[ea] (?:contrato|servi[cç]o)|ainda n[ãa]o gravei|"
    r"\bdeleted\b|\bremoved\b|\bcancel+ed\b|disconnected|unsubscribed|i(?:'| wi)ll stop|"
    r"verwijderd|geannuleerd|supprim[ée]e?s?|annul[ée]e?s?|gel[öo]scht|storniert",
    re.IGNORECASE,
)
_NUMBER_RE = re.compile(r"\d+(?:[.,]\d{1,2})?")
# The LLM sometimes invents a confirmation flow ("aguarda confirmação", "reply sim/não"): the app
# has no pending state, so such a reply promises something that can never happen.
_FAKE_CONFIRM_RE = re.compile(
    r"aguarda\w*\s+confirma|confirma\s+(?:cada|com)|\"?sim\"?\s*(?:/|ou)\s*\"?n[ãa]o|"
    r"awaiting\s+confirm|confirm\s+each|yes\s*/\s*no|wacht\w*\s+op\s+bevestig|"
    r"en\s+attente\s+de\s+confirm|warte\w*\s+auf\s+best|"
    # "É isto correto?" / "Ginásio — quanto?": questions the app has no state to receive
    r"[ée]\s+isto\s+correto|is\s+this\s+correct|klopt\s+dit|est-ce\s+correct|ist\s+das\s+richtig|"
    r"[—-]\s*(?:quanto|how\s+much|hoeveel|combien|wie\s+viel)\s*\?",
    re.IGNORECASE,
)
# A bare 0 amount ("Café 0") or a leading-minus amount ("reembolso -15") the extractors drop.
_ZERO_OR_NEG_RE = re.compile(r"(?<!\S)-\d|(?<![\d.,-])0+(?:[.,]0+)?(?![\d.,])")


# "cinema 24", "café 3,50": words, then one bare number and nothing after it. No unit, so it is
# money (defect 19 of the 03/10 hard test: the habit classifier took it for a habit and the 24 EUR
# was lost). "leitura 30 min" has a unit and stays free text.
_BARE_AMOUNT_RE = re.compile(r"^\s*[^\W\d_][^\d]*?\s+\d+(?:[.,]\d{1,2})?\s*$")
# a lone amount ("25", "7,50", "€ 1.200"): the answer to "what is the right value?"
_LONE_AMOUNT_RE = re.compile(
    r"^\s*[€$£]?\s*(\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?|\d+(?:[.,]\d{1,2})?)\s*(?:€|eur(?:os?)?)?\s*$"
)
EDIT_KIND = "edit_amount"  # PendingAction kind: the entry whose [Editar] was tapped
LANG_MENU_KIND = "lang_menu"  # PendingAction kind: the 1–5 language menu is waiting for a number
NEG_KIND = "negative_income"  # PendingAction kind: "reembolso -15" offered as money in
_NEGATIVE_ITEM_RE = re.compile(
    r"^(?P<name>[^\W\d_][^\d-]*?)\s+-\s*€?\s*(?P<amt>\d+(?:[.,]\d{1,2})?)\s*(?:€|eur(?:os?)?)?\s*[.!]?$"
)
EDIT_MINUTES = 15
# Money in another currency named in the text; the model sometimes still answers EUR.
_FOREIGN_CURRENCY = (
    (re.compile(r"\breais\b|r\$|\bbrl\b"), "BRL"),
    (re.compile(r"\bd[oó]lar(?:es)?\b|\bdollars?\b|\busd\b|us\$|\$\s*\d|\d\s*\$"), "USD"),
    (re.compile(r"\blibras?\b|\bpounds?\b|\bgbp\b|£"), "GBP"),
)


def _foreign_currency(body: str) -> str | None:
    for rx, code in _FOREIGN_CURRENCY:
        if rx.search(body):
            return code
    return None


def _join_names(names: list[str], lang: str) -> str:
    """ "táxi", "táxi e uber", "táxi, uber e metrô" in the member's language."""
    word = {"pt": "e", "nl": "en", "en": "and", "fr": "et", "de": "und"}.get(lang, "and")
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + f" {word} " + names[-1]


def _fix_item_currencies(text: str, items: list[dict]) -> list[dict]:
    """B-007: in "mercado 20 e 10 dólares de táxi" the model marked BOTH items USD.

    A foreign currency belongs only to the item named next to it; when the text names fewer
    currencies than the model marked, the others are euros again.
    """
    plain = strip_accents(text.lower())
    spots = [m.start() for rx, _ in _FOREIGN_CURRENCY for m in rx.finditer(plain)]
    marked = [i for i in items if i["currency"] != "EUR"]
    if not marked or len(spots) >= len(marked):
        return items
    keep: set[int] = set()
    for pos in spots:
        best, best_d = None, None
        for idx, it in enumerate(items):
            name = strip_accents(str(it.get("merchant") or it.get("description") or "").lower())
            where = plain.find(name) if name else -1
            if where < 0:
                continue
            dist = abs(where - pos)
            if best_d is None or dist < best_d:
                best, best_d = idx, dist
        if best is not None:
            keep.add(best)
    return [
        it if (it["currency"] == "EUR" or idx in keep) else {**it, "currency": "EUR"}
        for idx, it in enumerate(items)
    ]


async def _lang_menu_open(session: AsyncSession, member: Member) -> bool:
    row = await session.scalar(
        select(PendingAction).where(
            PendingAction.member_id == member.id, PendingAction.kind == LANG_MENU_KIND
        )
    )
    return row is not None and row.expires_at >= datetime.now(UTC)


async def _take_edit_target(session: AsyncSession, member: Member) -> Expense | None:
    """The entry whose [Editar] was tapped in the last minutes (consumed), else None."""
    row = await session.scalar(
        select(PendingAction).where(
            PendingAction.member_id == member.id, PendingAction.kind == EDIT_KIND
        )
    )
    if row is None:
        return None
    await session.delete(row)
    if row.expires_at < datetime.now(UTC):
        return None
    try:
        eid = uuid.UUID(str(row.payload.get("id")))
    except (ValueError, AttributeError):
        return None
    return await session.scalar(
        select(Expense).where(Expense.id == eid, Expense.member_id == member.id)
    )


_HEALTH_WITHDRAW_RE = re.compile(
    r"^(?:retirar|revogar|retiro|revogo|cancelar)\s+(?:o\s+|a\s+|meu\s+|minha\s+)?"
    r"(?:consentimento|autoriza[cç][aã]o)\s+(?:de\s+|da\s+|dos\s+)?(?:dados\s+de\s+)?saude$"
    r"|^withdraw\s+(?:my\s+)?health\s+consent$"
    r"|^toestemming\s+gezondheid\s+intrekken$"
    r"|^retirer\s+(?:le\s+|mon\s+)?consentement\s+sante$"
    r"|^gesundheitszustimmung\s+widerrufen$"
)


def _original_text(message, start: int, end: int) -> str:
    """Slice of what the member typed (NFC, stripped) at the span found in the lower-cased body."""
    original = unicodedata.normalize("NFC", (message.body or "").strip())
    lowered = original.lower()
    if len(lowered) != len(original):  # a character whose lower case changes the length: keep it
        return lowered[start:end].strip()
    return original[start:end].strip()


# B-054: "<words> <number>" is not a purchase when it opens like a sentence ("o primeiro foi
# 40", "para 25") or carries a command, question or period word ("corrige para 25", "tira o 5",
# "gastos em 2025"). Prepositions inside a name stay ("almoço no Tivoli 30").
_BARE_LEAD_WORDS = frozenset(
    "o a os as um uma de do da dos das em no na nos nas para pra pro por com e é eu "
    "minha minhas meu meus esse essa este esta isso isto me te se lhe".split()
)
_BARE_STOP_WORDS = frozenset(
    "foi foram era tira tirar troca trocar muda mudar corrige corrigir errei erro apaga apagar "
    "cancela cancelar remove remover edita editar altera alterar desfaz desfazer volta voltar "
    "marca marcar põe gasto gastos gastei despesa despesas receita receitas recebi quanto "
    "quantos qual quais meta metas tarefa tarefas nota notas orçamento orçamentos lembrete "
    "lembretes saldo resumo carga treino plano viagem roteiro bagagem contrato serviço garantia "
    "agenda hábito primeiro primeira segundo segunda terceiro quarto quinto último última ano "
    "mês semana dias hoje ontem amanhã pagou paguei pago pagar deve devo devia recebeu "
    "emprestei humor sono dormi água agua bebi peso pressão km min minutos horas corri "
    "caminhei nadei musculação academia supino agachamento".split()
)


_WRITTEN_DATE_RE = re.compile(
    r"(?:\b(?:dia|data|em|no dia|on|op|le|am|date|datum)\s+|,\s*)(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b"
)


def _written_days_ago(body: str) -> int | None:
    """Days back for an explicit past date ("data 05/10", ", 05/10"), within the last year."""
    m = _WRITTEN_DATE_RE.search(body)
    if not m:
        return None
    today = today_local()
    try:
        year = int(m.group(3)) if m.group(3) else today.year
        year += 2000 if year < 100 else 0
        day = date(year, int(m.group(2)), int(m.group(1)))
        if not m.group(3) and day > today:
            day = date(year - 1, day.month, day.day)
    except ValueError:
        return None
    delta = (today - day).days
    return delta if 0 < delta <= 365 else None


def _bare_amount_expense(text: str) -> dict | None:
    """ "notebook 1210" → an expense dict for the normal recording path, else None."""
    m = re.match(r"^\s*(?P<name>[^\W\d_][^\d]*?)\s+(?P<amt>\d+(?:[.,]\d{1,2})?)\s*$", text)
    if not m:
        return None
    words = m.group("name").lower().split()
    words = [w.strip(".,:;!?") for w in words]
    if len(words) > 4 or words[0] in _BARE_LEAD_WORDS or _BARE_STOP_WORDS & set(words):
        return None
    amount = to_amount(m.group("amt"))
    name = " ".join(m.group("name").split())[:80]
    if amount is None or not 0 < amount <= MAX_AMOUNT or not name:
        return None
    return {
        "amount": round(amount, 2),
        "currency": "EUR",
        "merchant": name[0].upper() + name[1:],
        "category": "overig",
        "description": name,
        "type": "expense",
        "days_ago": 0,
    }


def _looks_like_bare_amount(body: str) -> bool:
    return bool(_BARE_AMOUNT_RE.match(body))


def _has_zero_or_negative_amount(body: str) -> bool:
    """A message with a bare 0 or a leading-minus amount that the extractors will drop."""
    return bool(_ZERO_OR_NEG_RE.search(body))


# On a question ("o que você tem anotado sobre mim?", "onde ficam meus dados?") words like
# "anotado" / "guardado" describe, they do not claim; only "I just did it" forms are blocked.
_FIRST_PERSON_CLAIM_RE = re.compile(
    r"\b(?:registei|registrei|anotei|guardei|salvei|gravei|apaguei|deletei|removi|cancelei|marquei|"
    r"i(?:'ve| have)? (?:saved|recorded|logged|deleted|removed|cancelled)|"
    r"ik heb (?:\w+ )?(?:opgeslagen|genoteerd|verwijderd)|j'ai (?:enregistr|not|supprim)\w*|"
    r"ich habe (?:\w+ )?(?:gespeichert|notiert|gelöscht))\b",
    re.IGNORECASE,
)
_QUESTION_START_RE = re.compile(
    r"^(?:o que|que|onde|como|quando|quanto|quantos|quantas|qual|quais|por ?que|porque|quem|"
    r"what|where|how|when|which|who|why|wat|waar|hoe|wanneer|welke|wie|waarom|"
    r"qu'est|où|comment|quand|quel|quelle|pourquoi|was|wo|wie|wann|welche|warum)\b"
)


def _is_question(body_plain: str) -> bool:
    t = body_plain.strip()
    return t.endswith("?") or bool(_QUESTION_START_RE.match(t))


def _claims_recorded(reply: str) -> bool:
    """True when free-form LLM text says something was recorded/saved.

    On the LLM fallback path nothing has been written, so such a sentence is always false;
    the caller replaces the whole reply instead of letting the model invent a confirmation.
    """
    return bool(_RECORDED_CLAIM_RE.search(reply))


def _looks_multi(body: str) -> bool:
    """A message carrying two or more amounts may be several transactions."""
    return len(_NUMBER_RE.findall(body)) >= 2


def _num(x: float | int, lang: str) -> str:
    """Localised plain number: 6.5 -> "6,5" (pt/nl/fr/de), 2.0 -> "2"."""
    f = float(x)
    text = str(int(f)) if f == int(f) else f"{f:.2f}".rstrip("0").rstrip(".")
    return text if lang == "en" else text.replace(".", ",")


def _num_str(value: str, lang: str) -> str:
    """Localise a stored numeric string ("6.5"); anything else is returned untouched."""
    v = (value or "").strip()
    return _num(float(v.replace(",", ".")), lang) if re.fullmatch(r"\d+(?:[.,]\d+)?", v) else v


def _workout_dur(minutes: int | None, km: float | None, lang: str) -> str:
    """ "30 min · 5 km": the distance is never dropped when the duration is known."""
    parts = []
    if minutes:
        parts.append(f"{minutes} min")
    if km:
        parts.append(f"{_num(km, lang)} km")
    return " · ".join(parts)


_ACTIVITY_TAIL_RE = re.compile(
    r"\s*[?!.]*\s*(?:(?:nos|nas)\s+[uú]ltim[oa]s\s+\d+\s+\w+|(?:este|esse|neste)\s+m[eê]s|"
    r"(?:esta|essa|nesta)\s+semana|hoje|this\s+(?:week|month)|deze\s+(?:week|maand))?\s*[?!.]*\s*$",
    re.IGNORECASE,
)


def _clean_activity(text: str) -> str:
    """Strip question marks and trailing time words captured together with an activity name."""
    prev = None
    out = (text or "").strip()
    while out != prev:
        prev = out
        out = _ACTIVITY_TAIL_RE.sub("", out).strip()
    return out


def _override_body(message, text: str) -> None:
    """Use ``text`` as the message body for this run without marking the stored row dirty."""
    if hasattr(message, "_sa_instance_state"):
        set_committed_value(message, "body", text)
    else:
        message.body = text


def _fmt_eur(amount: float) -> str:
    """Format a float as PT-style euro: €1.234,56"""
    sign = "-" if amount < 0 else ""  # "-€133,60", never "€-133,60"
    return f"{sign}€{abs(amount):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _looks_like_gibberish(text: str) -> bool:
    """Return True if text looks like random key-mashing (no meaningful content)."""
    t = text.strip()
    if len(t) < 7:
        return False
    if " " in t:  # multi-word is probably real content
        return False
    if any(c in t for c in "0123456789€$£.,@#%+"):
        return False
    if not t.replace("-", "").replace("'", "").isalpha():
        return False
    # Real words in any supported language have >15% vowels
    vowels = sum(1 for c in t.lower() if c in "aeiouáâãàéêèíïóôõòúùûüäöü")
    return (vowels / len(t)) < 0.15


def _period_range(period: str, lang: str = "en") -> tuple[datetime, datetime | None, str]:
    """Return (start, end_exclusive, label) — day boundaries in the user's timezone.

    end_exclusive=None means open (up to now).
    """
    today = today_local()
    if period == "today":
        return day_start(today), day_start(today + timedelta(days=1)), _t("period_today", lang)
    if period == "last_month":
        first_prev = prev_month_start(today)
        return (
            day_start(first_prev),
            day_start(month_start(today)),
            month_name(first_prev, lang, year=True),
        )
    if period == "current_week":
        return day_start(week_start(today)), None, _t("period_current_week", lang)
    if period == "last_week":
        this_week = week_start(today)
        return (
            day_start(this_week - timedelta(days=7)),
            day_start(this_week),
            _t("period_last_week", lang),
        )
    # default: current_month
    return day_start(month_start(today)), None, month_name(today, lang, year=True)


def _period_phrase(period: str, lang: str) -> str:
    """Sentence form of the LLM's coarse period ("today", "last_week", "current_month"…)."""
    today = today_local()
    if period == "today":
        return _t("period_today", lang)
    if period == "current_week":
        return _t("phrase_this_week", lang)
    if period == "last_week":
        return _t("phrase_last_week", lang)
    if period == "last_month":
        return _t("phrase_month", lang, month=_month_word(prev_month_start(today), lang))
    return _t("phrase_month", lang, month=_month_word(today, lang))


def _window_range(window, lang: str) -> tuple[datetime, datetime | None, str]:
    """(start, end_exclusive, label) for a parsed ``DateWindow``."""
    start, end = day_start(window.start), (day_start(window.end) if window.end else None)
    return start, end, _window_label(window, lang)


def _span(first: date, last: date) -> str:
    """ "12 a 18/10"-style day span (numbers only, the same in every language)."""
    if first.month == last.month:
        return f"{first.day:02d}–{last.strftime('%d/%m')}"
    return f"{first.strftime('%d/%m')}–{last.strftime('%d/%m')}"


def _window_label(window, lang: str) -> str:
    """Title label of a window: "esta semana, 12–18/10", "últimos 7 dias", "Outubro 2026"."""
    today = today_local()
    if window.kind == "today":
        return _t("period_today", lang)
    if window.kind == "yesterday":
        return _t("period_yesterday", lang)
    if window.kind == "today_yesterday":
        return _t("period_today_yesterday", lang)
    if window.kind == "days":
        return _t("period_last_days", lang, n=window.days)
    if window.kind == "current_week":
        return f"{_t('period_current_week', lang)}, {_span(window.start, today)}"
    if window.kind == "last_week":
        last = (window.end or today) - timedelta(days=1)
        return f"{_t('period_last_week', lang)}, {_span(window.start, last)}"
    if window.kind == "year":
        return str(window.start.year)
    return month_name(window.start, lang, year=True)  # current_month / last_month / month


def _window_phrase(window, lang: str) -> str:
    """The same window inside a sentence: "em outubro", "nos últimos 7 dias", "ontem"."""
    if window is None:
        return _t("phrase_month", lang, month=_month_word(today_local(), lang))
    kind = window.kind
    if kind in ("today", "yesterday", "today_yesterday"):
        return _window_label(window, lang)
    if kind == "days":
        return _t("phrase_last_days", lang, n=window.days)
    if kind == "current_week":
        return _t("phrase_this_week", lang)
    if kind == "last_week":
        return _t("phrase_last_week", lang)
    if kind == "year":
        return _t("phrase_year", lang, year=window.start.year)
    return _t("phrase_month", lang, month=_month_word(window.start, lang))


def _month_word(d: date, lang: str) -> str:
    """ "outubro" (this year) or "outubro de 2025"; lower case where the language writes it so."""
    name = month_name(d, lang)
    if lang in ("pt", "fr", "nl"):
        name = name.lower()
    if d.year != today_local().year:
        return _t("month_of_year", lang, month=name, year=d.year)
    return name


_LAST_MONTH_WORDS = (
    "mes passado",
    "last month",
    "vorige maand",
    "mois dernier",
    "letzten monat",
    "letzter monat",
)


def _month_window(text_plain: str, today: date) -> tuple[date, date | None]:
    """[start, end) dates for "este mês" / "mês passado" / a named month ("de agosto").

    ``text_plain`` is accent-free and lower-case. A named month resolves to its most
    recent occurrence (so "agosto" in January means last year's August).
    """
    if any(w in text_plain for w in _LAST_MONTH_WORDS):
        return prev_month_start(today), month_start(today)
    for word in re.findall(r"[a-z]+", text_plain):
        month = MONTH_WORDS.get(word)
        if (
            month and len(word) > 3
        ):  # skip 3-letter abbreviations that are also words ("mar", "set")
            year = today.year if month <= today.month else today.year - 1
            start = date(year, month, 1)
            nxt = date(year + (month == 12), month % 12 + 1, 1)
            return start, (None if start == month_start(today) else nxt)
    return month_start(today), None


async def _build_summary(
    member: Member,
    session: AsyncSession,
    *,
    period: str = "current_month",
    category: str | None = None,
    window=None,
    top: bool = False,
    bounds: tuple | None = None,
    buttons_out: list | None = None,
) -> str:
    """Query expenses/income for a period (optionally filtered by category).

    ``bounds`` is ``(start, end_exclusive, label)`` from a button tap. When ``buttons_out`` is a
    list it receives the "go deeper" buttons (V2-20) if there is something to go deeper into.
    """
    lang = member.language or "en"
    if bounds is not None:
        start, end_excl, period_label = bounds
    elif window is not None:
        start, end_excl, period_label = _window_range(window, lang)
    else:
        start, end_excl, period_label = _period_range(period, lang)

    filters = [
        Expense.member_id == member.id,
        Expense.status.in_(SETTLED),
        Expense.expense_date >= start,
    ]
    if end_excl:
        filters.append(Expense.expense_date < end_excl)
    if category:
        filters.append(Expense.category == category)

    result = await session.execute(
        select(Expense).where(*filters).order_by(Expense.expense_date.desc())
    )
    records = result.scalars().all()

    if not records:
        if bounds is not None:
            phrase = period_label
        elif window is not None:
            phrase = _window_phrase(window, lang)
        else:
            phrase = _period_phrase(period, lang)
        if category:
            return _t(
                "no_records_scope",
                lang,
                category=category_label(category, lang),
                phrase=phrase,
            )
        return _t("no_records_period", lang, phrase=phrase)

    outflows = [e for e in records if e.transaction_type != "income"]
    inflows = [e for e in records if e.transaction_type == "income"]
    if buttons_out is not None and outflows and not top:
        buttons_out.extend(deeper_buttons(start, end_excl, category, lang))
    total_out = _cents(sum(e.amount for e in outflows))
    total_in = _cents(sum(e.amount for e in inflows))

    if category:
        # Category view: list individual transactions
        title = _t(
            "category_title",
            lang,
            category=category_label(category, lang),
            period_label=period_label,
        )
        lines = [f"{title}\n"]
        # A-26: "quanto recebi" is the income category; its rows are inflows, not outflows
        shown = inflows if category == "inkomen" else outflows
        shown_total = total_in if category == "inkomen" else total_out
        for e in shown[:10]:
            date_str = to_local(e.expense_date).strftime("%d/%m")
            name = e.merchant or e.description or category_label(category, lang)
            lines.append(f"• {date_str} {name}: {_fmt_eur(e.amount)}")
        if len(shown) > 10:
            lines.append(_t("list_more", lang, n=len(shown) - 10))
        lines.append(f"\n{_t('category_total', lang, amount=_fmt_eur(shown_total))}")
        lines.append(
            _t(
                "transactions_count_one" if len(shown) == 1 else "transactions_count",
                lang,
                n=len(shown),
            )
        )
    else:
        # Full summary: group by category
        by_cat: dict[str, float] = {}
        for e in outflows:
            cat = e.category or "overig"
            by_cat[cat] = by_cat.get(cat, 0) + e.amount

        title_key = "top_categories_title" if top else "summary_title"
        lines = [f"{_t(title_key, lang, period_label=period_label)}\n"]
        ranked = sorted(by_cat.items(), key=lambda x: -x[1])
        for cat, amt in ranked[:3] if top else ranked:
            lines.append(f"• {category_label(cat, lang)}: {_fmt_eur(amt)}")

        lines.append(f"\n{_t('total_expenses', lang, amount=_fmt_eur(total_out))}")

        if inflows:
            balance = _cents(total_in - total_out)
            sign = "+" if balance >= 0 else "-"
            lines.append(_t("income_line", lang, amount=_fmt_eur(total_in)))
            lines.append(_t("balance_line", lang, sign=sign, amount=_fmt_eur(abs(balance))))

        lines.append(
            _t(
                "transactions_count_one" if len(outflows) == 1 else "transactions_count",
                lang,
                n=len(outflows),
            )
        )

    return "\n".join(lines)


async def _build_recent(
    member: Member, session: AsyncSession, n: int, *, income: bool = False
) -> str:
    """The ``n`` most recent expenses (or incomes), real rows, newest first."""
    lang = member.language or "en"
    res = await session.execute(
        select(Expense)
        .where(
            Expense.member_id == member.id,
            Expense.transaction_type == ("income" if income else "expense"),
            Expense.status.in_(SETTLED),
        )
        .order_by(Expense.expense_date.desc(), Expense.created_at.desc())
        .limit(n)
    )
    rows = res.scalars().all()
    if not rows:
        return _t("income_none" if income else "expense_delete_none", lang)
    title_key = "last_incomes_title" if income else "last_expenses_title"
    if len(rows) == 1:
        title_key += "_one"
    lines = [f"{_t(title_key, lang, n=len(rows))}\n"]
    for e in rows:
        name = e.merchant or e.description or category_label(e.category, lang)
        day = to_local(e.expense_date).strftime("%d/%m")
        lines.append(f"• {day} {name}: {_fmt_eur(e.amount)}")
    return "\n".join(lines)


async def _build_saldo(
    member: Member, session: AsyncSession, window=None, *, short: bool = False
) -> str:
    """Income/expense balance of the current month, or of the window the text named
    ("saldo de setembro", "saldo em 2025").

    ``short`` (F05-07): only the balance (and the pending line), for the current month; the
    detail (income, expenses, note and the month-end estimate) is one button away."""
    lang = member.language or "en"
    now = now_local()
    if window is not None and window.kind not in ("current_month",):
        start, end_excl, label = _window_range(window, lang)
    else:
        start, end_excl, label = day_start(month_start(now.date())), None, None
        window = None

    filters = [
        Expense.member_id == member.id,
        Expense.status.in_(SETTLED),
        Expense.expense_date >= start,
    ]
    if end_excl is not None:
        filters.append(Expense.expense_date < end_excl)
    result = await session.execute(select(Expense).where(*filters))
    records = result.scalars().all()

    if not records:
        if window is not None:
            return _t("no_records_period", lang, phrase=_window_phrase(window, lang))
        from alfred.ledger_status import pending_summary

        pend = await pending_summary(session, member.id)
        if pend.to_pay or pend.to_receive:  # A-70: only pending bills is still something to say
            return _t(
                "pending_forecast", lang, pay=_fmt_eur(pend.to_pay), recv=_fmt_eur(pend.to_receive)
            )
        return _t("no_records_month", lang)

    total_in = _cents(sum(e.amount for e in records if e.transaction_type == "income"))
    total_out = _cents(sum(e.amount for e in records if e.transaction_type != "income"))
    balance = _cents(total_in - total_out)
    sign = "+" if balance >= 0 else "-"

    month_label = label or month_name(now.date(), lang, year=True)
    lines = [f"{_t('saldo_title', lang, month=month_label)}\n"]
    if not (short and window is None):
        lines += [
            _t("saldo_income", lang, amount=_fmt_eur(total_in)),
            _t("saldo_expenses", lang, amount=_fmt_eur(total_out)),
        ]
    lines.append(_t("saldo_balance", lang, sign=sign, amount=_fmt_eur(abs(balance))))
    if not (short and window is None):
        lines.append(_t("saldo_note", lang))  # A-70: it is the period's in minus out, not the bank
        if window is None and (guess := balance_view.projection(total_out, now.date())):
            lines.append(_t("saldo_projection", lang, amount=_fmt_eur(guess)))
    from alfred.ledger_status import pending_summary

    pend = await pending_summary(session, member.id)
    if window is None and (pend.to_pay or pend.to_receive):
        lines.append(
            "\n"
            + _t(
                "pending_forecast",
                lang,
                pay=_fmt_eur(pend.to_pay),
                recv=_fmt_eur(pend.to_receive),
            )
        )
    return "\n".join(lines)


async def _build_comparison(member: Member, session: AsyncSession) -> str:
    """Compare current month vs previous month (expenses only)."""
    lang = member.language or "en"
    now = now_local()
    cur_start = day_start(month_start(now.date()))
    prev_end = cur_start
    prev_start = day_start(prev_month_start(now.date()))

    async def _total_out(start: datetime, end: datetime) -> tuple[float, int]:
        r = await session.execute(
            select(Expense).where(
                Expense.member_id == member.id,
                Expense.expense_date >= start,
                Expense.expense_date < end,
                Expense.transaction_type != "income",
                Expense.status.in_(SETTLED),
            )
        )
        rows = r.scalars().all()
        return _cents(sum(e.amount for e in rows)), len(rows)

    cur_total, cur_n = await _total_out(cur_start, now + timedelta(seconds=1))
    prev_total, prev_n = await _total_out(prev_start, prev_end)

    cur_label = month_name(now.date(), lang)
    prev_label = month_name(prev_start, lang)

    diff = _cents(cur_total - prev_total)
    if prev_total == 0:
        diff_str = _t("comparison_no_prev", lang)
    elif diff == 0:
        diff_str = _t("comparison_equal", lang)
    elif diff > 0:
        pct = diff / prev_total * 100
        diff_str = f"_+{_fmt_eur(diff)} (+{pct:.0f}%) vs {prev_label}_"
    else:
        pct = abs(diff) / prev_total * 100
        diff_str = f"_{_fmt_eur(diff)} (-{pct:.0f}%) vs {prev_label}_"

    lines = [
        f"{_t('comparison_title', lang)}\n",
        f"• {prev_label}: {_fmt_eur(prev_total)} ({prev_n})",
        f"• {cur_label}: {_fmt_eur(cur_total)} ({cur_n})",
        f"\n{diff_str}",
    ]
    return "\n".join(lines)


async def _load_merchant_overrides(
    member: Member,
    session: AsyncSession,
) -> dict[str, str]:
    """Return {lowercase_merchant: category} for this member (M12)."""
    result = await session.execute(
        select(MerchantCategoryOverride).where(
            MerchantCategoryOverride.member_id == member.id,
        )
    )
    return {row.merchant: row.category for row in result.scalars().all()}


async def _upsert_merchant_override(
    member: Member,
    merchant: str,
    category: str,
    session: AsyncSession,
) -> None:
    """Insert or update a merchant→category override for this member (M12)."""
    from sqlalchemy.dialects.postgresql import insert as _pg_insert

    key = merchant.lower().strip()
    stmt = (
        _pg_insert(MerchantCategoryOverride)
        .values(
            id=uuid.uuid4(),
            member_id=member.id,
            merchant=key,
            category=category,
        )
        .on_conflict_do_update(
            constraint="uq_mco_member_merchant",
            set_={"category": category, "updated_at": datetime.now(UTC)},
        )
    )
    await session.execute(stmt)


async def _handle_category_correction(
    body: str,
    member: Member,
    lang: str,
    session: AsyncSession,
    member_overrides: dict[str, str] | None,
) -> str | None:
    """Reply if ``body`` is a short "<merchant> é <categoria>" correction, else None.

    Conservative on purpose: the category must be valid AND the merchant must be
    one this member already has (an override or a past expense), or a pronoun
    ("isso é transport") that points at the most recent expense. Anything else
    falls through to the other handlers instead of being silently swallowed.
    """
    parsed = parse_category_correction(body)
    if parsed is None:
        return None
    raw_merchant, raw_cat = parsed
    canonical = _canonical_category(raw_cat)
    if canonical is None:
        return None

    merchant_key = raw_merchant.lower().strip()
    known = merchant_key in (member_overrides or {})

    if merchant_key in CORRECTION_PRONOUNS:
        last = (
            await session.execute(
                select(Expense)
                .where(Expense.member_id == member.id, Expense.transaction_type == "expense")
                .order_by(Expense.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if last is None or not last.merchant:
            return None
        if last.created_at < datetime.now(UTC) - timedelta(hours=6):
            return _t("category_which", lang)  # B-071: "isso" never reaches back to an old entry
        raw_merchant, merchant_key, known = last.merchant, last.merchant.lower(), True

    if not known:
        seen = await session.scalar(
            select(Expense.id)
            .where(
                Expense.member_id == member.id,
                Expense.merchant.ilike(like_escape(raw_merchant), escape="\\"),
            )
            .limit(1)
        )
        if seen is None:
            return None

    if canonical == "inkomen":  # A-24: an expense never moves into the income category
        return _t("category_income_refused", lang, merchant=raw_merchant.title())
    await _upsert_merchant_override(member, raw_merchant, canonical, session)
    # Patch the most recent expense with this merchant (last 7 days)
    cutoff = datetime.now(UTC) - timedelta(days=7)
    last_expense = (
        await session.execute(
            select(Expense)
            .where(
                Expense.member_id == member.id,
                Expense.merchant.ilike(like_escape(raw_merchant), escape="\\"),
                Expense.created_at >= cutoff,
            )
            .order_by(Expense.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if last_expense:
        last_expense.category = canonical
        session.add(last_expense)

    logger.info(
        "conversation.category_override_saved", member_id=str(member.id), category=canonical
    )
    return _t(
        "category_corrected",
        lang,
        merchant=raw_merchant.title(),
        category=category_label(canonical, lang),
    )


async def handle_flow_onboarding(
    member: Member,
    message: Message,
    session: AsyncSession,
) -> bool:
    """Process a completed WhatsApp Flow onboarding nfm_reply.

    Returns True if the message was a Flow response and was handled,
    False if it is a regular text message (caller continues normally).
    """
    import json as _json

    raw = message.raw or {}
    interactive = raw.get("interactive") or {}
    if interactive.get("type") != "nfm_reply":
        return False

    nfm = interactive.get("nfm_reply", {})
    try:
        payload = _json.loads(nfm.get("response_json", "{}"))
    except (_json.JSONDecodeError, TypeError):
        logger.warning("conversation.flow_invalid_json", member_id=str(member.id))
        return True  # consumed but invalid — ignore

    preferred_name = (payload.get("preferred_name") or "").strip()
    language_raw = payload.get("language")
    gdpr = payload.get("gdpr_consent") or []

    # Dropdown returns id as string; list form also accepted.
    if isinstance(language_raw, list):
        language = language_raw[0] if language_raw else "en"
    else:
        language = language_raw or "en"
    language = language if language in ("pt", "nl", "en", "fr", "de") else "en"

    consent_given = "accepted" in gdpr

    logger.info(
        "conversation.flow_onboarding_received",
        member_id=str(member.id),
        preferred_name=preferred_name,
        language=language,
        consent_given=consent_given,
    )

    if not consent_given:
        member.consent_state = "rejected"
        session.add(member)
        await send_text(member.wa_phone, _t("consent_rejected", language))
        return True

    # Update member profile
    member.preferred_name = preferred_name or None
    member.language = language
    member.consent_state = "accepted"
    member.disclosure_accepted_at = datetime.now(UTC)
    member.disclosure_version = "flow-1.0"
    session.add(member)

    # Personalised welcome confirmation
    name_part = f", {preferred_name}" if preferred_name else ""
    greeting_map = {
        "pt": f"Olá{name_part}! Conta activada.",
        "nl": f"Hallo{name_part}! Account geactiveerd.",
        "en": f"Hi{name_part}! Account activated.",
        "fr": f"Bonjour{name_part} ! Compte activé.",
        "de": f"Hallo{name_part}! Konto aktiviert.",
    }
    reply = (
        f"{greeting_map.get(language, greeting_map['en'])}\n\n{_t('consent_accepted', language)}"
    )
    await send_text(member.wa_phone, reply)

    # Persist outbound message
    from alfred import delivery as _delivery

    _wamid = _delivery.take_wamid()
    session.add(
        Message(
            id=uuid.uuid4(),
            wa_message_id=_wamid or f"out-{uuid.uuid4()}",
            kind="reply",
            delivery_status="sent" if _wamid else None,
            household_id=member.household_id,
            author_id=member.id,
            direction="outbound",
            body=reply,
            wa_timestamp=datetime.now(UTC),
            processed=True,
        )
    )
    return True


async def _send_reply(to: str, reply) -> None:
    """Send a ``Reply`` (or a plain str): buttons (a list above three) or plain text."""
    if isinstance(reply, str):
        await send_text(to, reply)
    elif reply.buttons:
        await send_buttons(to, reply.text, reply.buttons)
    else:
        await send_text(to, reply.text)


async def _handle_button_reply(
    member: Member,
    message: Message,
    session: AsyncSession,
    lang: str,
) -> bool:
    """Handle a tap on a reply button of a confirmation (``undo:``/``edit:``/``ok:<expense id>``).

    Returns True when the message was a button tap (handled, or deliberately ignored).
    The expense id only ever selects a row of THIS member, so a forged id does nothing.
    """
    interactive = (message.raw or {}).get("interactive") or {}
    if interactive.get("type") not in ("button_reply", "list_reply"):
        return False
    # A list row (more than 3 options) comes back like a button tap, with the row id.
    button_id = str(
        (interactive.get("button_reply") or interactive.get("list_reply") or {}).get("id") or ""
    )
    action, _, raw_id = button_id.partition(":")
    to = member.wa_phone

    if action == "bal_detail":  # F05-07: the full balance, one tap after the short one
        reply = await _build_saldo(member, session)
        await send_text(to, reply)
        await _save_outbound(member, reply, session)
        return True

    if action == "fu":  # an answer to a follow-up question (questions.py)
        fu_reply = await questions.on_button(session, member, lang, button_id, today_local())
        if fu_reply is None:
            fu_reply = Reply(_t("fu_gone", lang), [])
        await _send_reply(to, fu_reply)
        await _save_outbound(member, fu_reply.text, session)
        return True

    if action == "keep":
        audit(session, "wipe_cancelled", member.id, ts=int(datetime.now(UTC).timestamp()))
        reply = _t("wipe_cancelled", lang)
        await send_text(to, reply)
        await _save_outbound(member, reply, session)
        return True
    if action == "wipe":
        try:
            fresh = 0 <= int(datetime.now(UTC).timestamp()) - int(raw_id) <= WIPE_CONFIRM_SECONDS
        except ValueError:
            fresh = False
        if fresh:  # "Cancel" (button or typed) kills every older "Delete everything" button
            state = await _wipe_state(session, member)
            if state is not None and state[0] == "cancelled" and state[1] >= int(raw_id):
                fresh = False
        if not fresh:
            reply = _t("wipe_expired", lang)
            await send_text(to, reply)
            await _save_outbound(member, reply, session)
            return True
        from alfred.privacy import erase_member

        logger.info("conversation.member_erased", member_id=str(member.id))
        await erase_member(session, member)
        await send_text(to, _t("wipe_done", lang))  # nothing to save: the member is gone
        return True

    if action in ("hc_yes", "hc_no"):  # art. 9 consent for health data
        if action == "hc_yes":
            member.health_consent_at = datetime.now(UTC)
            session.add(member)
            audit(session, "health_consent_given", member.id)
            reply = _t("health_consent_given", lang)
        else:
            reply = _t("health_consent_refused", lang)
        await send_text(to, reply)
        await _save_outbound(member, reply, session)
        return True

    if action in ("dd_cat", "dd_top", "dd_bud"):  # V2-20 go deeper after a spending answer
        deeper_text = await handle_deeper_button(action, raw_id, member, lang, session)
        await send_text(to, deeper_text)
        await _save_outbound(member, deeper_text, session)
        return True

    if action in ("inv_undo", "inv_unpay"):  # V2-12 part 2 undo of an invoice step
        inv_text = await handle_invoice_button(action, raw_id, member, lang, session)
        await send_text(to, inv_text)
        await _save_outbound(member, inv_text, session)
        return True

    if action in ("svc_ok", "svc_exp", "svc_no", "svc_wd_yes", "svc_wd_no"):  # V2-36
        svc = await handle_service_button(action, raw_id, member, lang, session, today_local())
        if svc.buttons:
            await send_buttons(to, svc.text, svc.buttons)
        else:
            await send_text(to, svc.text)
        await _save_outbound(member, svc.text, session)
        return True

    if action in ("home_ok", "home_no"):  # V2-27 confirm / cancel a home contract draft
        home = await handle_home_button(action, raw_id, member, lang, session, today_local())
        await send_text(to, home.text)
        await _save_outbound(member, home.text, session)
        return True

    if action in ("fb_up", "fb_down", "fb_r"):  # V2-23 thumbs up/down and the reason
        fb = await handle_feedback_button(action, raw_id, member, lang, session)
        if fb.buttons:
            await send_buttons(to, fb.text, fb.buttons)
        else:
            await send_text(to, fb.text)
        await _save_outbound(member, fb.text, session)
        return True

    if action in ("lang_set", "lang_keep"):  # M-03 a bare language name, confirmed by tap
        if action == "lang_set" and raw_id in ("pt", "nl", "en", "fr", "de"):
            member.language = raw_id
            session.add(member)
            reply = _t("lang_changed", raw_id)
        else:
            reply = _t("lang_kept", lang)
        await send_text(to, reply)
        await _save_outbound(member, reply, session)
        return True

    if action in ("neg_in", "neg_no"):  # M-05 "reembolso -15" offered as income
        try:
            pa_id = uuid.UUID(raw_id)
        except ValueError:
            pa_id = None
        pa = (
            await session.scalar(
                select(PendingAction).where(
                    PendingAction.id == pa_id,
                    PendingAction.member_id == member.id,
                    PendingAction.kind == NEG_KIND,
                )
            )
            if pa_id
            else None
        )
        if pa is None or pa.expires_at < datetime.now(UTC):
            reply = _t("button_used", lang)
        elif action == "neg_no":
            await session.delete(pa)
            reply = _t("negative_dropped", lang)
        else:
            amount, name = float(pa.payload["amount"]), str(pa.payload.get("name") or "")
            await session.delete(pa)
            exp = Expense(
                id=uuid.uuid4(),
                member_id=member.id,
                household_id=member.household_id,
                transaction_type="income",
                amount=amount,
                currency="EUR",
                merchant=None,
                category="inkomen",
                description=name[:255] or None,
                expense_date=now_local(),
                scope=default_scope(member, "inkomen", "income"),
            )
            session.add(exp)
            audit(session, "expense_recorded", member.id)
            reply = _t("income_recorded", lang, amount=_fmt_eur(amount), name=name)
            await send_buttons(to, reply, [(f"undo:{exp.id}", _t("btn_undo", lang))])
            await _save_outbound(member, reply, session)
            return True
        await send_text(to, reply)
        await _save_outbound(member, reply, session)
        return True

    if action in ("rec_upd", "rec_keep"):  # B-018 a fixed bill typed again with a new amount
        rec_text = await handle_recurring_button(action, raw_id, member, lang, session)
        await send_text(to, rec_text)
        await _save_outbound(member, rec_text, session)
        return True

    if action in ("cal_on", "cal_no"):  # M-23 deadline reminders under "prazos"
        cal_text = await handle_calendar_button(action, member, lang, session)
        await send_text(to, cal_text)
        await _save_outbound(member, cal_text, session)
        return True

    if action in ("appt_ok", "appt_undo"):  # V2-06 agenda confirmation buttons
        reply = await handle_agenda_button(action, raw_id, member, lang, session)
        await send_text(to, reply)
        await _save_outbound(member, reply, session)
        return True

    if action in ("batch_ok", "batch_edit", "batch_cancel"):  # V2-17 draft of 2+ entries
        out = await handle_batch_button(action, raw_id, member, lang, session)
        if out.buttons:
            await send_buttons(to, out.text, out.buttons)
        else:
            await send_text(to, out.text)
        await _save_outbound(member, out.text, session)
        return True

    if action == "batch_undo":  # undo the whole just-confirmed batch
        from alfred.batch import undo_recorded

        undo_text = await undo_recorded(raw_id, member, lang, session)
        await send_text(to, undo_text)
        await _save_outbound(member, undo_text, session)
        return True

    if action in ("couple_yes", "couple_no", "couple_end", "couple_keep", "home"):  # V2-10
        couple_btn = await handle_couple_button(action, raw_id, member, lang, session)
        await send_text(to, couple_btn.text)
        await _save_outbound(member, couple_btn.text, session)
        return True

    if action in ("imp_ok", "imp_all", "imp_no"):  # V2-05 statement import preview
        imp_btn = await handle_statement_button(action, raw_id, member, lang, session)
        await send_text(to, imp_btn.text)
        await _save_outbound(member, imp_btn.text, session)
        return True

    if action in ("bare_yes", "bare_no"):  # A-74 a lone word, confirmed or not
        try:
            bare_id = uuid.UUID(raw_id)
        except ValueError:
            return True
        bare = await session.scalar(
            select(PendingAction).where(
                PendingAction.id == bare_id,
                PendingAction.member_id == member.id,
                PendingAction.kind == BARE_KIND,
                PendingAction.expires_at > datetime.now(UTC),
            )
        )
        if bare is None:
            reply = _t("button_used", lang)
        elif action == "bare_no":
            await session.delete(bare)
            reply = _t("bare_word_no", lang)
        else:
            phrase = str(bare.payload.get("phrase") or "")
            await session.delete(bare)
            await session.flush()
            again = Message(
                id=uuid.uuid4(),
                wa_message_id=f"bare-{uuid.uuid4()}",
                household_id=member.household_id,
                author_id=member.id,
                direction="inbound",
                body=phrase,
                created_at=datetime.now(UTC),
            )
            await handle_inbound(member, again, session)
            return True
        await send_text(to, reply)
        await _save_outbound(member, reply, session)
        return True

    if action in ("plan_ok", "plan_cancel", "plan_adjust"):  # V2-35 training plan preview
        plan_out = await handle_training_button(action, raw_id, member, lang, session)
        if plan_out.buttons:
            await send_buttons(to, plan_out.text, plan_out.buttons)
        else:
            await send_text(to, plan_out.text)
        await _save_outbound(member, plan_out.text, session)
        return True

    try:
        expense_id = uuid.UUID(raw_id)
    except ValueError:
        logger.info("conversation.button_unknown", member_id=str(member.id))
        return True
    if action not in ("undo", "edit", "ok"):
        logger.info("conversation.button_unknown", member_id=str(member.id))
        return True

    expense = await session.scalar(
        select(Expense).where(Expense.id == expense_id, Expense.member_id == member.id)
    )
    if expense is None:
        reply = _t("button_gone", lang)
    elif action == "undo":
        name = expense.merchant or expense.description or category_label(expense.category, lang)
        reply = _t(
            "expense_deleted",
            lang,
            amount=_fmt_eur(expense.amount),
            name=name,
            date=to_local(expense.expense_date).strftime("%d/%m"),
        )
        await session.delete(expense)
        audit(session, "expense_undone", member.id)
        logger.info("conversation.expense_undone", member_id=str(member.id))
    elif action == "edit":  # remember which entry: the next amount corrects THIS one
        await session.execute(
            delete(PendingAction).where(
                PendingAction.member_id == member.id, PendingAction.kind == EDIT_KIND
            )
        )
        session.add(
            PendingAction(
                member_id=member.id,
                kind=EDIT_KIND,
                payload={"id": str(expense.id)},
                expires_at=datetime.now(UTC) + timedelta(minutes=EDIT_MINUTES),
            )
        )
        reply = _t("button_edit_hint", lang)
    else:
        reply = _t("button_ok_reply", lang)
    await send_text(to, reply)
    await _save_outbound(member, reply, session)
    return True


async def _gdpr_reply(member: Member, body_plain: str, session: AsyncSession, lang: str) -> None:
    """ "apagar meus dados" (two steps, buttons) / "exportar meus dados" (single-use link)."""
    to = member.wa_phone
    if _WIPE_RE.match(body_plain.strip()):
        now_epoch = int(datetime.now(UTC).timestamp())
        audit(session, "wipe_requested", member.id, ts=now_epoch)
        await send_buttons(
            to,
            _t("wipe_ask", lang),
            [(f"wipe:{now_epoch}", _t("btn_wipe", lang)), ("keep:0", _t("btn_cancel", lang))],
        )
        await _save_outbound(member, _t("wipe_ask", lang), session)
        return
    from alfred.panel_tokens import issue_export_token

    base_url = (settings.base_url or "").rstrip("/")
    if not base_url:
        reply = _t("dashboard_no_base_url", lang)
    else:
        token = await issue_export_token(session, member)
        audit(session, "data_export_link", member.id)
        url = f"{base_url}/api/d/{token}/export"
        reply = _t("export_link", lang, url=url, minutes=settings.export_token_ttl_minutes)
    await send_text(to, reply)
    await _save_outbound(member, reply, session)


async def _inbound_last_minute(session: AsyncSession, member_id) -> int:
    if not isinstance(session, AsyncSession):  # unit tests with a stand-in session
        return 0
    return int(
        await session.scalar(
            select(func.count())
            .select_from(Message)
            .where(
                Message.author_id == member_id,
                Message.direction == "inbound",
                Message.created_at >= datetime.now(UTC) - timedelta(minutes=1),
            )
        )
        or 0
    )


async def handle_inbound(
    member: Member,
    message: Message,
    session: AsyncSession,
) -> None:
    """Decide what to reply based on consent state and message content."""
    # M6 — WhatsApp Flow onboarding: intercept nfm_reply before consent checks
    if await handle_flow_onboarding(member, message, session):
        return

    use_member_timezone(getattr(member, "timezone", None))  # A-66: "today" is the person's own
    to = member.wa_phone
    # A-81: a burst (a stuck phone, a script) is answered once, then ignored for the minute
    burst = await _inbound_last_minute(session, member.id)
    if burst > settings.messages_per_minute:
        if burst == settings.messages_per_minute + 1:
            reply = _t("rate_limited", member.language or "en")
            await send_text(to, reply)
            await _save_outbound(member, reply, session)
        logger.info("conversation.rate_limited", member_id=str(member.id), n=burst)
        return
    body = unicodedata.normalize("NFC", (message.body or "").strip()).lower()
    # Accent-free twin (same length as ``body``): command regexes are written without
    # accents so "está"/"água"/"medicação" match; slice captured text from ``body``.
    body_plain = strip_accents(body)

    # ── 1. Not registered yet: the only way in is the sign-up page ─────────────
    # (a number that never wrote, or one caught in the old "answer sim" flow). Everything here is
    # in English: the person picks the language on the page and from then on the chat follows it.
    # Nothing else is processed until the account exists.
    if member.consent_state in SIGNUP_WAITING_STATES:
        first = member.consent_state == "pending"
        if first:
            member.language = "en"  # the invite and the page open in English; the page changes it
        lang = member.language or "en"
        if not first and body in _CONSENT_NO:  # "stop" while waiting: honour it
            member.consent_state = "rejected"
            session.add(member)
            await send_text(to, _t("consent_rejected", lang))
            logger.info("conversation.consent_rejected", member_id=str(member.id))
            return
        member.consent_state = "pending_signup"
        session.add(member)
        await send_signup_invite(member, lang, again=not first)
        audit(session, "signup_link_sent", member.id, first=first)
        logger.info("conversation.signup_link_sent", member_id=str(member.id), first=first)
        return

    lang = member.language or "en"

    # ── 3. Rejected — honour their choice ────────────────────────────────────
    if member.consent_state == "rejected":
        # The data rights do not depend on having accepted: erase and export still work
        # after "stop" (the privacy policy tells people to use them in the chat).
        _inter = (message.raw or {}).get("interactive") or {}
        tapped = _inter.get("button_reply") or _inter.get("list_reply") or {}
        if str(tapped.get("id") or "").split(":")[0] in ("wipe", "keep"):
            await _handle_button_reply(member, message, session, lang)
            return
        if _WIPE_RE.match(body_plain.strip()) or _EXPORT_RE.match(body_plain.strip()):
            await _gdpr_reply(member, body_plain, session, lang)
            return
        if body.strip(" .!") in _RESUME_WORDS:
            if member.disclosure_accepted_at is not None:
                # M-25 (J, 20/10): an account that already exists comes back as it was; the
                # sign-up page is only for people who never finished it (or erased their data)
                member.consent_state = "accepted"
                session.add(member)
                audit(session, "resumed", member.id)
                reply = _t("resumed_welcome", lang)
                await send_text(to, reply)
                await _save_outbound(member, reply, session)
                logger.info("conversation.resumed", member_id=str(member.id))
                return
            member.consent_state = "pending_signup"
            session.add(member)
            await send_signup_invite(member, lang)
            audit(session, "signup_link_sent", member.id, first=False)
            logger.info("conversation.resume_requested", member_id=str(member.id))
            return
        logger.info("conversation.rejected_member_ignored", member_id=str(member.id))
        return

    # ── 4. Accepted — handle commands and LLM ────────────────────────────────
    if member.consent_state == "accepted":
        # 4-0. Reply-button taps (Undo / Edit / It's right on a confirmation)
        if await _handle_button_reply(member, message, session, lang):
            return

        # 4-0a0. V2-35b / V2-07 — a photo or a PDF: a training plan or a receipt
        if is_media_file(message.raw or {}):
            file_out = await handle_media_file(member, message, lang, session)
            if file_out.buttons:
                await send_buttons(to, file_out.text, file_out.buttons)
            else:
                await send_text(to, file_out.text)
            await _save_outbound(member, file_out.text, session)
            return

        # 4-0a. V2-05 — a bank statement sent as a document
        if (message.raw or {}).get("type") == "document":
            doc_out = await handle_document(member, message, lang, session)
            if doc_out.buttons:
                await send_buttons(to, doc_out.text, doc_out.buttons)
            else:
                await send_text(to, doc_out.text)
            await _save_outbound(member, doc_out.text, session)
            return

        # 4-0aaa. An open follow-up question ("Vence quando?"): typed answer or it is closed
        fu_out = await questions.on_text(session, member, lang, body_plain, today_local())
        if fu_out is not None:
            await _send_reply(to, fu_out)
            await _save_outbound(member, fu_out.text, session)
            return

        # 4-0aa. V2-17 — answers to a draft of 2+ entries ("sim" / "cancela" / "tira o segundo")
        batch_out = await handle_batch_text(body, body_plain, member, lang, session)
        if batch_out is not None:
            if batch_out.buttons:
                await send_buttons(to, batch_out.text, batch_out.buttons)
            else:
                await send_text(to, batch_out.text)
            await _save_outbound(member, batch_out.text, session)
            return

        # 4-0ab. "idioma inglês" / "change to English": switch the language of the replies
        if parse_language_choice(body) is not None and await _lang_menu_open(session, member):
            # the answer to the menu (M-04); the DB is only asked when the text could be one
            choice = parse_language_choice(body)
            await session.execute(
                delete(PendingAction).where(
                    PendingAction.member_id == member.id, PendingAction.kind == LANG_MENU_KIND
                )
            )
            if choice is not None:
                member.language = choice
                session.add(member)
                reply = _t("lang_changed", choice)
                await send_text(to, reply)
                await _save_outbound(member, reply, session)
                return
        if is_language_menu_request(body):  # M-04: "mudar idioma" shows the 1–5 menu
            session.add(
                PendingAction(
                    member_id=member.id,
                    kind=LANG_MENU_KIND,
                    payload={},
                    expires_at=datetime.now(UTC) + timedelta(minutes=EDIT_MINUTES),
                )
            )
            reply = _t("language_pick", lang)  # one tap on the list (a typed 1-5 still works)
            await send_buttons(to, reply, LANG_ROWS)
            await _save_outbound(member, reply, session)
            return
        new_lang = parse_language_command(body)
        if new_lang is not None and new_lang != lang and is_bare_language_name(body):
            # M-03: "português" alone may answer something else — ask before switching
            reply = _t("lang_confirm", new_lang)
            await send_buttons(
                to,
                reply,
                [
                    (f"lang_set:{new_lang}", _t("btn_lang_yes", new_lang)),
                    (f"lang_keep:{lang}", _t("btn_lang_keep", lang)),
                ],
            )
            await _save_outbound(member, reply, session)
            return
        if new_lang is not None:
            member.language = new_lang
            session.add(member)
            reply = _t("lang_changed", new_lang)
            await send_text(to, reply)
            await _save_outbound(member, reply, session)
            return

        # 4-0-. An open "delete my data" question can be answered by typing as well as by button.
        plain = body_plain.strip()
        if (_WIPE_YES_RE.match(plain) or _WIPE_NO_RE.match(plain)) and await _wipe_pending(
            session, member
        ):
            if _WIPE_YES_RE.match(plain):
                from alfred.privacy import erase_member

                logger.info("conversation.member_erased", member_id=str(member.id))
                await erase_member(session, member)
                await send_text(to, _t("wipe_done", lang))
                return
            audit(session, "wipe_cancelled", member.id, ts=int(datetime.now(UTC).timestamp()))
            reply = _t("wipe_cancelled", lang)
            await send_text(to, reply)
            await _save_outbound(member, reply, session)
            return

        # 4-0a. A bare "yes": there is no pending question, so say so instead of guessing.
        if _BARE_YES_RE.match(body_plain.strip()):
            reply = _t("bare_yes", lang)
            await send_text(to, reply)
            await _save_outbound(member, reply, session)
            return

        # 4-0g. M-02 / A-80: a bare greeting is answered here, by name, with no model call
        greet = _GREETINGS.get(" ".join(re.findall(r"[a-z]+", body_plain)))
        if greet is not None:
            name = (member.preferred_name or "").strip()
            word = greet if greet_lang_matches(greet, lang) else _t("greet_word", lang)
            reply = _t("greet_reply", lang, hello=word + (f", {name}" if name else ""))
            await send_text(to, reply)
            await _save_outbound(member, reply, session)
            return

        # 4-0h. BUG-08: a keyboard mash ("asdfjklqwerty") is answered here, with no model call
        if _keyboard_mash(body_plain, lang) or _looks_like_gibberish(body_plain):
            reply = _t("not_understood", lang)
            await send_text(to, reply)
            await _save_outbound(member, reply, session)
            return

        # 4-0i. A-74: a lone "água" / "treino" / "remédio" asks before anything is recorded
        bare_key = _BARE_RECORD_WORDS.get(body_plain.strip(" .!?"))
        if bare_key is not None:
            phrase = _BARE_PHRASES[bare_key].get(lang) or _BARE_PHRASES[bare_key]["en"]
            await session.execute(
                delete(PendingAction).where(
                    PendingAction.member_id == member.id, PendingAction.kind == BARE_KIND
                )
            )
            row = PendingAction(
                member_id=member.id,
                kind=BARE_KIND,
                payload={"phrase": phrase},
                expires_at=datetime.now(UTC) + timedelta(minutes=BARE_MINUTES),
            )
            session.add(row)
            await session.flush()
            reply = _t("bare_word_ask", lang, word=body.strip(" .!?"), phrase=phrase)
            await send_buttons(
                to,
                reply,
                [
                    (f"bare_yes:{row.id}", _t("btn_bare_yes", lang)),
                    (f"bare_no:{row.id}", _t("btn_bare_no", lang)),
                ],
            )
            await _save_outbound(member, reply, session)
            return

        # 4-0b. GDPR: "apagar meus dados" (two steps, buttons) / "exportar meus dados"
        if _WIPE_RE.match(body_plain.strip()) or _EXPORT_RE.match(body_plain.strip()):
            await _gdpr_reply(member, body_plain, session, lang)
            return

        # Withdraw the art. 9 consent: stop storing AND delete what was stored
        if _HEALTH_WITHDRAW_RE.match(" ".join(body_plain.split()).strip(" .!?")):
            res_del = await session.execute(
                delete(HealthLog).where(HealthLog.member_id == member.id)
            )
            member.health_consent_at = None
            session.add(member)
            audit(session, "health_consent_withdrawn", member.id)
            reply = _t("health_consent_withdrawn", lang, n=res_del.rowcount or 0)
            await send_text(to, reply)
            await _save_outbound(member, reply, session)
            return

        # M12 — load member's merchant→category overrides once per message
        member_overrides = await _load_merchant_overrides(member, session)

        # 4a-. "sair" / "quero sair": explain the two real exits instead of the model's
        # invented "Você foi desconectado" (B-051)
        if body_plain.strip(" .!") in _LEAVE_WORDS:
            reply = _t("leave_explain", lang)
            await send_text(to, reply)
            await _save_outbound(member, reply, session)
            return

        # 4a. Stop — re-enter rejected state ("stop.", "parar de enviar": A-75 / B-051)
        if body.strip(" .!") in _STOP_WORDS or body_plain.strip(" .!") in _STOP_PHRASES:
            member.consent_state = "rejected"
            session.add(member)
            reply = _t("consent_rejected", lang)
            await send_text(to, reply)
            logger.info("conversation.stop_requested", member_id=str(member.id))
            return

        # 4a2. A-66 — "fuso horário Lisboa": the person's own timezone
        if m_tz := _TZ_CMD_RE.match(body_plain.strip(" .!?")):
            zone_text = (m_tz.group("zone") or "").strip()
            zone = _timezone_from_text(zone_text) if zone_text else member.timezone
            if zone_text and zone is None:
                reply = _t("tz_unknown", lang)
            else:
                if zone_text:
                    member.timezone = zone
                    session.add(member)
                    audit(session, "timezone_set", member.id, zone=zone)
                use_member_timezone(zone)
                reply = _t(
                    "tz_set",
                    lang,
                    zone=zone or settings.timezone,
                    time=now_local().strftime("%H:%M"),
                )
            await send_text(to, reply)
            await _save_outbound(member, reply, session)
            return

        # 4b. Saldo command
        if _is_plain_command(body, _SALDO_WORDS) and not _SALDO_ELSEWHERE_RE.search(body_plain):
            period = parse_period(body_plain, today_local())
            saldo = await _build_saldo(member, session, period, short=True)
            if period is None or period.kind == "current_month":
                await send_buttons(to, saldo, [("bal_detail:x", _t("btn_balance_detail", lang))])
            else:
                await send_text(to, saldo)
            await _save_outbound(member, saldo, session)
            return

        # 4c. Help command
        if _is_plain_command(body, _HELP_WORDS):
            help_text = _t("help", lang)
            await send_text(to, help_text)
            await _save_outbound(member, help_text, session)
            return

        # 4d. Summary command — skip if message has a time qualifier (let classify_query handle it)
        if _is_bare_command(body, _SUMMARY_WORDS):
            deeper: list = []
            summary = await _build_summary(member, session, buttons_out=deeper)
            if deeper:
                await send_buttons(to, summary, deeper)
            else:
                await send_text(to, summary)
            await _save_outbound(member, summary, session)
            return

        # 4e-0. "configura lembrete" — proactive reminder setup (M5)
        m_lembrete = _match_lembrete(body)
        if m_lembrete or _is_command(body, _LEMBRETE_WORDS):
            if m_lembrete:
                reminder_text = m_lembrete.group(1).strip()
                time_str = m_lembrete.group(2).zfill(5)  # "8:00" → "08:00"
                # Validate HH:MM
                try:
                    hh, mm = time_str.split(":")
                    if not (0 <= int(hh) <= 23 and 0 <= int(mm) <= 59):
                        raise ValueError("time out of range")
                except Exception:
                    reply = _t("lembrete_invalid", lang)
                    await send_text(to, reply)
                    await _save_outbound(member, reply, session)
                    return

                rec = parse_recurrence(reminder_text)
                if rec.text:
                    reminder_text = rec.text
                rec_mask = (
                    rec.mask if rec.mask != 127 or rec.day_of_month else _parse_days_mask(body)
                )
                job_type = _detect_job_type(reminder_text)
                import uuid as _uuid

                from alfred.models import ScheduledJob

                job = ScheduledJob(
                    id=_uuid.uuid4(),
                    member_id=member.id,
                    job_type=job_type,
                    time_of_day=time_str,
                    days_mask=rec_mask,
                    payload=(
                        {"text": reminder_text, "day_of_month": rec.day_of_month}
                        if rec.day_of_month
                        else {"text": reminder_text}
                    ),
                    active=True,
                )
                session.add(job)
                reply = _t(
                    "lembrete_set",
                    lang,
                    text=reminder_text,
                    time=time_str,
                    when=cadence_label(rec_mask, rec.day_of_month, lang),
                )
                logger.info(
                    "conversation.lembrete_created",
                    member_id=str(member.id),
                    job_type=job_type,
                    time=time_str,
                )
            else:
                # Bare "lembrete" without full syntax → show format hint
                reply = _t("lembrete_invalid", lang)

            await send_text(to, reply)
            await _save_outbound(member, reply, session)
            return

        # 4e-0b. M5 — list active reminders
        if _is_command(body, _LIST_LEMBRETES_WORDS):
            from sqlalchemy import select as _sel

            from alfred.models import ScheduledJob as _SJ

            res = await session.execute(
                _sel(_SJ)
                .where(_SJ.member_id == member.id)
                .where(_SJ.active.is_(True))
                .where(_SJ.job_type != "monthly_summary")  # V2-04 bookkeeping row, not a reminder
                .order_by(_SJ.time_of_day.asc())
            )
            jobs = res.scalars().all()
            if not jobs:
                reply = _t("lembretes_list_empty", lang)
            else:
                lines = [_t("lembretes_list_header", lang, n=len(jobs))]
                for j in jobs:
                    days_label = cadence_label(
                        j.days_mask, (j.payload or {}).get("day_of_month"), lang
                    )
                    text_label = (j.payload or {}).get("text", j.job_type)
                    lines.append(
                        _t(
                            "lembretes_list_row",
                            lang,
                            time=j.time_of_day,
                            text=text_label,
                            days=days_label,
                        )
                    )
                reply = "\n".join(lines)
            await send_text(to, reply)
            await _save_outbound(member, reply, session)
            return

        # 4e-0c. M5 — cancel reminder
        m_cancel = _CANCEL_LEMBRETE_RE.search(body_plain)
        if m_cancel:
            cancel_kw = _grp(body, m_cancel).strip().lower()
            from sqlalchemy import select as _sel

            from alfred.models import ScheduledJob as _SJ

            res = await session.execute(
                _sel(_SJ)
                .where(_SJ.member_id == member.id)
                .where(_SJ.active.is_(True))
                .where(_SJ.job_type != "monthly_summary")
            )
            jobs = res.scalars().all()
            hits = [
                j
                for j in jobs
                if cancel_kw in (j.payload or {}).get("text", j.job_type).lower()
                or (j.payload or {}).get("text", j.job_type).lower() in cancel_kw
            ]
            exact = [j for j in hits if (j.payload or {}).get("text", "").lower() == cancel_kw]
            matched = (exact or hits)[0] if len(exact or hits) == 1 else None
            if len(exact or hits) > 1:  # B-027: "cancela lembrete tomar" fits two — ask which
                names = "\n".join(
                    f"• {(j.payload or {}).get('text', j.job_type)}" for j in (exact or hits)[:5]
                )
                reply = _t("lembrete_cancel_ambiguous", lang, items=names)
                await send_text(to, reply)
                await _save_outbound(member, reply, session)
                return
            if matched:
                matched.active = False
                session.add(matched)
                text_label = (matched.payload or {}).get("text", matched.job_type)
                reply = _t("lembrete_cancelled", lang, text=text_label)
            else:
                reply = _t("lembrete_cancel_not_found", lang)
            await send_text(to, reply)
            await _save_outbound(member, reply, session)
            return

        # 4e-0p. V2-35 — training plan / loads and trip itinerary / packing / planned budget
        # (before the budgets handler so "orçamento da viagem hospedagem 300" is ours)
        original = unicodedata.normalize("NFC", (message.body or "").strip())  # keeps the capitals
        train_out = await handle_training_command(original, body_plain, member, lang, session)
        if train_out is not None:
            if train_out.buttons:
                await send_buttons(to, train_out.text, train_out.buttons)
            else:
                await send_text(to, train_out.text)
            await _save_outbound(member, train_out.text, session)
            return
        tripplan_reply = await handle_tripplan_command(original, body_plain, member, lang, session)
        if tripplan_reply is not None:
            await send_text(to, tripplan_reply)
            await _save_outbound(member, tripplan_reply, session)
            return

        # 4e-0f. V2-01 — budgets: "orçamento mercado 400" / "meus orçamentos" / "tira o orçamento de lazer"
        budget_reply = await handle_budget_command(body_plain, member, lang, session)
        if budget_reply is not None:
            await _send_reply(to, budget_reply)
            await _save_outbound(
                member,
                budget_reply.text if hasattr(budget_reply, "text") else budget_reply,
                session,
            )
            return

        # 4e-0h. V2-04 — resumo mensal: "resumo mensal" / "sem resumo mensal" / "ativar resumo mensal"
        msum_reply = await handle_monthly_summary_command(body_plain, member, lang, session)
        if msum_reply is not None:
            await send_text(to, msum_reply)
            await _save_outbound(member, msum_reply, session)
            return

        # 4e-0l. V2-15 — "dias no positivo / negativo" / "mês contra mês por categoria" (before the generic comparison)
        insight_reply = await handle_insight_command(body_plain, member, lang, session)
        if insight_reply is not None:
            await send_text(to, insight_reply)
            await _save_outbound(member, insight_reply, session)
            return

        # 4e-0m. V2-15 — "Pedro me deve 25" / "quem me deve" / "Pedro pagou" (before "paguei X" of V2-02)
        iou_reply = await handle_iou_command(body, body_plain, member, lang, session)
        if iou_reply is not None:
            await send_text(to, iou_reply)
            await _save_outbound(member, iou_reply, session)
            return

        # 4e-0q. V2-10 — casal: "convidar parceiro" / "entrar casa ABC123" / "gastos da casa" / "foi da casa" / "acertamos"
        couple_out = await handle_couple_command(body_plain, member, lang, session)
        if couple_out is not None:
            if couple_out.buttons:
                await send_buttons(to, couple_out.text, couple_out.buttons)
            else:
                await send_text(to, couple_out.text)
            await _save_outbound(member, couple_out.text, session)
            return

        # 4e-0r. V2-05 — "importar extrato" / "desfazer importação"
        stmt_out = await handle_statement_command(body_plain, member, lang, session)
        if stmt_out is not None:
            await send_text(to, stmt_out.text)
            await _save_outbound(member, stmt_out.text, session)
            return

        # 4e-0s. V2-12 — contabilidade: "modo empresa" / "foi da empresa" / "btw 21" / "contabilidade"
        acct_out = await handle_accounting_command(body_plain, member, lang, session, today_local())
        if acct_out is not None:
            await send_text(to, acct_out.text)
            await _save_outbound(member, acct_out.text, session)
            return

        # 4e-0s2. V2-12 part 2 — client invoices: "fatura para Acme 1210 btw 21" / "faturas" / "fatura X paga"
        inv_out = await handle_invoice_command(
            message.body or "", body_plain, member, lang, session, today_local()
        )
        if inv_out is not None:
            if inv_out.buttons:
                await send_buttons(to, inv_out.text, inv_out.buttons)
            else:
                await send_text(to, inv_out.text)
            await _save_outbound(member, inv_out.text, session)
            return

        # 4e-0s3. V2-36 — services and deadlines: "guarda serviço: encanador, 180 €, garantia 12 meses" / "garantias"
        svc_out = await handle_service_command(
            message.body or "", body_plain, member, lang, session, today_local()
        )
        if svc_out is not None:
            if svc_out.buttons:
                await send_buttons(to, svc_out.text, svc_out.buttons)
            else:
                await send_text(to, svc_out.text)
            await _save_outbound(member, svc_out.text, session)
            return

        # 4e-0s4. V2-27 — home contracts: "contrato energia Vattenfall 120 até 31/12/2027" / "contratos"
        home_out = await handle_home_command(
            message.body or "", body_plain, member, lang, session, today_local()
        )
        if home_out is not None:
            if home_out.buttons:
                await send_buttons(to, home_out.text, home_out.buttons)
            else:
                await send_text(to, home_out.text)
            await _save_outbound(member, home_out.text, session)
            return

        # 4e-0s4b. financial goals: "guardei 300 na reserva" / "já tenho 1200 na reserva" / "metas financeiras"
        goal_out = await handle_goal_command(
            message.body or "", body_plain, member, lang, session, today_local()
        )
        if goal_out is not None:
            await send_text(to, goal_out.text)
            await _save_outbound(member, goal_out.text, session)
            return

        # 4e-0s5. V2-21 — Dutch deadlines: "prazos" / "prazo btw" / "ligar avisos de prazos"
        cal_out = await handle_calendar_command(body_plain, member, lang, session, today_local())
        if cal_out is not None:
            if cal_out.buttons:
                await send_buttons(to, cal_out.text, cal_out.buttons)
            else:
                await send_text(to, cal_out.text)
            await _save_outbound(member, cal_out.text, session)
            return

        # 4e-0o. V2-18 — "o que você me enviou hoje" / "lembretes que mandou"
        outbox_reply = await handle_outbox_command(body_plain, member, lang, session)
        if outbox_reply is not None:
            await send_text(to, outbox_reply)
            await _save_outbound(member, outbox_reply, session)
            return

        # 4e-0n. V2-16 — "luz 80 a pagar dia 5" / "paguei a luz" / "o que tenho a pagar"
        ledger_reply = await handle_ledger_command(body, body_plain, member, lang, session)
        if ledger_reply is not None:
            if isinstance(ledger_reply, Reply):
                await _send_reply(to, ledger_reply)
                await _save_outbound(member, ledger_reply.text, session)
            else:
                await send_text(to, ledger_reply)
                await _save_outbound(member, ledger_reply, session)
            return

        # 4e-0g. V2-02 — contas fixas: "aluguel 1200 todo dia 1" / "paguei o aluguel" / "minhas contas fixas"
        recurring_reply = await handle_recurring_command(
            body_plain=body_plain, body=body, member=member, lang=lang, session=session
        )
        if recurring_reply is not None:
            if isinstance(recurring_reply, str):
                await send_text(to, recurring_reply)
                await _save_outbound(member, recurring_reply, session)
            else:
                await send_buttons(to, recurring_reply.text, recurring_reply.buttons)
                await _save_outbound(member, recurring_reply.text, session)
            return

        # 4e-0h2. Google Calendar — "conectar google agenda" / "desconectar google agenda"
        gcal_cmd = _gcal.match_command(body_plain)
        if gcal_cmd == "connect":
            gtext, gbutton, gurl = await _gcal.connect_reply(member, lang, session)
            if gurl:
                await send_cta_url(to, gtext, gbutton or "Google", gurl)
            else:
                await send_text(to, gtext)
            await _save_outbound(member, gtext, session)
            return
        if gcal_cmd == "disconnect":
            gtext = await _gcal.disconnect(member, lang, session)
            await send_text(to, gtext)
            await _save_outbound(member, gtext, session)
            return

        # 4e-0i. V2-06 — agenda: "dentista quinta às 14h" / "minha agenda" / "cancela o dentista"
        agenda_reply = (
            None  # "nota: …" is always a note, even when it mentions a day and a time
            if _NOTE_RE.match(body)
            else await handle_agenda_command(body, body_plain, member, lang, session)
        )
        if agenda_reply is not None:
            if agenda_reply.buttons:
                await send_buttons(to, agenda_reply.text, agenda_reply.buttons)
            else:
                await send_text(to, agenda_reply.text)
            await _save_outbound(member, agenda_reply.text, session)
            return

        # 4e-0j. V2-09 — nota de saúde: "minha nota de saúde" / "health score"
        score_reply = await handle_score_command(body_plain, member, lang, session)
        if score_reply is not None:
            await send_text(to, score_reply)
            await _save_outbound(member, score_reply, session)
            return

        # 4e-0k. V2-14 — saved views: "salva essa visão como X" / "minhas visões" / "roda X"
        view_reply = await handle_view_command(body_plain, member, lang, session)
        if view_reply is not None:
            await send_text(to, view_reply)
            await _save_outbound(member, view_reply, session)
            return

        # 4e-1. M12 — category correction: "Jumbo é supermarkt"
        corr_reply = await _handle_category_correction(
            body, member, lang, session, member_overrides
        )
        if corr_reply is not None:
            await send_text(to, corr_reply)
            await _save_outbound(member, corr_reply, session)
            return

        # 4e-2. M10 — nota rápida: "nota: X"
        m_note = _NOTE_RE.match(body)
        if m_note:
            # the text as written (``body`` is lower case, matching only needs that)
            note_body = _original_text(message, m_note.start(1), m_note.end(1))
            note = Note(id=uuid.uuid4(), member_id=member.id, body=note_body)
            session.add(note)
            reply = _t("note_saved", lang)
            if _SECRET_RE.search(strip_accents(note_body.lower())):
                reply += "\n" + _t("note_secret_hint", lang)
            await send_text(to, reply)
            await _save_outbound(member, reply, session)
            return

        # 4e-3. M10 — tarefa: "tarefa: X" / "tarefa: X até sexta"
        m_task = _TASK_RE.match(body)
        if m_task:
            import re as _re

            raw_task = _original_text(message, m_task.start(1), m_task.end(1))
            # try to extract due_date ("até <date>" / "by <date>" / "voor <date>")
            due_m = _re.search(r"\s(?:até|by|voor|bis|avant)\s+(.+)$", raw_task, _re.IGNORECASE)
            task_body = raw_task
            due_date_val = None
            if due_m:
                from alfred.agenda import parse_when

                # words first ("sexta", "amanhã", "dia 20", "15/10"), then dateutil ("Oct 15")
                when = parse_when(strip_accents(due_m.group(1).lower()), now_local())
                due_date_val = when.day
                if due_date_val is None:
                    try:
                        from dateutil import parser as _dp

                        due_date_val = _dp.parse(
                            due_m.group(1),
                            default=datetime.combine(today_local(), datetime.min.time()),
                        ).date()
                    except Exception:
                        due_date_val = None  # not a date ("até logo"): keep the full text
                if due_date_val is not None:
                    task_body = raw_task[: due_m.start()].strip() or raw_task
            else:  # A-39: "tarefa: pagar o dentista amanhã às 14h" → due tomorrow
                from alfred.agenda import clean_title, parse_when

                when = parse_when(strip_accents(raw_task.lower()), now_local())
                if when.day is not None:
                    rest = clean_title(raw_task, when.spans)
                    if rest and len(rest) >= 2:
                        due_date_val, task_body = when.day, rest[0].lower() + rest[1:]
            task = Task(
                id=uuid.uuid4(),
                member_id=member.id,
                body=task_body,
                due_date=due_date_val,
            )
            session.add(task)
            reply = _t("task_saved", lang, body=task_body)
            if due_date_val is not None:
                when_txt = f"{weekday_abbr(due_date_val, lang)} {due_date_val:%d/%m}"
                reply += _t("task_due_suffix", lang, when=when_txt)
            await send_text(to, reply)
            await _save_outbound(member, reply, session)
            return

        # 4e-4. M10 — done: "feito: X" or "feito: 2" (by number)
        m_done = _DONE_RE.match(body)
        if m_done:
            done_text = m_done.group(1).strip()
            from sqlalchemy import select as _sel

            if done_text.isdigit():
                idx = int(done_text)
                res_all = await session.execute(
                    _sel(Task)
                    .where(Task.member_id == member.id)
                    .where(Task.done_at.is_(None))
                    .order_by(Task.created_at.asc())
                )
                open_list = res_all.scalars().all()
                task_row = open_list[idx - 1] if 0 < idx <= len(open_list) else None
                if task_row:
                    task_row.done_at = datetime.now(UTC)
                    session.add(task_row)
                    reply = _t("task_done", lang)
                else:
                    reply = _t("task_not_found", lang)
                await send_text(to, reply)
                await _save_outbound(member, reply, session)
                return
            done_text_lower = like_escape(done_text.lower())
            result = await session.execute(
                _sel(Task)
                .where(Task.member_id == member.id)
                .where(Task.done_at.is_(None))
                .where(Task.body.ilike(f"%{done_text_lower}%", escape="\\"))
                .order_by(Task.created_at.desc())
                .limit(1)
            )
            task_row = result.scalar_one_or_none()
            if task_row:
                task_row.done_at = datetime.now(UTC)
                session.add(task_row)
                reply = _t("task_done", lang)
            else:
                reply = _t("task_not_found", lang)
            await send_text(to, reply)
            await _save_outbound(member, reply, session)
            return

        # 4e-5. M10 — lista de tarefas: "minhas tarefas"
        if _is_command(body, _TASKS_QUERY_WORDS):
            from sqlalchemy import select as _sel

            result = await session.execute(
                _sel(Task)
                .where(Task.member_id == member.id)
                .where(Task.done_at.is_(None))
                .order_by(Task.created_at.asc())
            )
            open_tasks = result.scalars().all()
            if not open_tasks:
                reply = _t("tasks_list_empty", lang)
            else:
                lines = [_t("tasks_list_header", lang, n=len(open_tasks))]
                today_date = today_local()
                for i, t in enumerate(open_tasks, 1):
                    due_str = (  # A-63: the same "Qui 22/10" as when the task was saved
                        _t(
                            "tasks_list_due",
                            lang,
                            date=f"{weekday_abbr(t.due_date, lang)} {t.due_date:%d/%m}",
                        )
                        if t.due_date
                        else ""
                    )
                    is_overdue = bool(t.due_date and t.due_date < today_date)
                    tpl_key = "tasks_list_overdue" if is_overdue else "tasks_list_row"
                    lines.append(_t(tpl_key, lang, n=i, body=t.body, due=due_str))
                reply = "\n".join(lines)
            await send_text(to, reply)
            await _save_outbound(member, reply, session)
            return

        # 4e-5b. M10 — delete task: "apaga tarefa X" / "exclui a tarefa 2"
        m_tdel = _TASK_DELETE_RE.match(body_plain)
        if m_tdel:
            del_kw = _grp(body, m_tdel).strip().strip(".!")
            from sqlalchemy import select as _sel

            res_del = await session.execute(
                _sel(Task)
                .where(Task.member_id == member.id)
                .order_by(Task.done_at.is_(None).desc(), Task.created_at.desc())
            )
            all_tasks = res_del.scalars().all()
            del_task = None
            if del_kw.isdigit():  # the number shown by "minhas tarefas" (open tasks, oldest first)
                open_tasks = sorted(
                    (t for t in all_tasks if t.done_at is None), key=lambda t: t.created_at
                )
                idx = int(del_kw)
                del_task = open_tasks[idx - 1] if 0 < idx <= len(open_tasks) else None
            else:
                del_task = next((t for t in all_tasks if _fuzzy_contains(del_kw, t.body)), None)
            if del_task:
                del_body = del_task.body
                await session.delete(del_task)
                reply = _t("task_deleted", lang, body=del_body)
            else:
                reply = _t("task_delete_not_found", lang)
            await send_text(to, reply)
            await _save_outbound(member, reply, session)
            return

        # 4e-5b2. B-028 — delete a note: "apaga a nota do wifi"
        m_ndel = _NOTE_DELETE_RE.match(body_plain)
        if m_ndel:
            kw = _grp(body, m_ndel).strip().strip(".!")
            notes_all = (
                (
                    await session.execute(
                        select(Note)
                        .where(Note.member_id == member.id)
                        .order_by(Note.created_at.desc())
                    )
                )
                .scalars()
                .all()
            )
            hits = [n for n in notes_all if _fuzzy_contains(kw, n.body)]
            if not hits:
                reply = _t("note_delete_not_found", lang)
            elif len(hits) > 1:
                reply = _t(
                    "note_delete_ambiguous",
                    lang,
                    items="\n".join(f"• {n.body[:60]}" for n in hits[:5]),
                )
            else:
                reply = _t("note_deleted", lang, body=hits[0].body[:80])
                await session.delete(hits[0])
                audit(session, "note_deleted", member.id)
            await send_text(to, reply)
            await _save_outbound(member, reply, session)
            return

        # 4e-5c. M10 — list notes: "as minhas notas"
        if _is_command(body, _NOTES_QUERY_WORDS):
            from sqlalchemy import select as _sel

            res_notes = await session.execute(
                _sel(Note)
                .where(Note.member_id == member.id)
                .order_by(Note.created_at.desc())
                .limit(10)
            )
            notes_list = res_notes.scalars().all()
            if not notes_list:
                reply = _t("notes_list_empty", lang)
            else:
                lines = [_t("notes_list_header", lang, n=len(notes_list))]
                for n_obj in notes_list:
                    lines.append(_t("notes_list_row", lang, body=n_obj.body[:120]))
                reply = "\n".join(lines)
            await send_text(to, reply)
            await _save_outbound(member, reply, session)
            return

        # 4e-5d. M11 — dashboard link: "meu dashboard"; A-17: "novo link do painel" revokes
        revoke = bool(_PANEL_REVOKE_RE.match(body_plain.strip(" .!?")))
        if revoke or _is_plain_command(body, _DASHBOARD_WORDS):
            import uuid as _uuid

            from alfred.dashboard import ensure_dashboard_token
            from alfred.panel_tokens import revoke_panel_token
            from alfred.settings import settings as _settings

            if revoke:
                await revoke_panel_token(session, member)
            else:  # Missing or expired (TTL) → issue a fresh one; the old link stops working.
                await ensure_dashboard_token(session, member)
            import os as _os

            base_url = (
                getattr(_settings, "base_url", "") or _os.environ.get("BASE_URL", "")
            ).rstrip("/")
            if not base_url:
                reply = _t("dashboard_no_base_url", lang)
                await send_text(to, reply)
            else:
                url = f"{base_url}/d/{member.dashboard_token}"
                # Button first; send_cta_url itself falls back to ONE text with the link.
                reply = _t("dashboard_revoked", lang) if revoke else _t("dashboard_cta", lang)
                await send_cta_url(to, reply, _t("btn_open_panel", lang), url)
                reply = _t("dashboard_link", lang, url=url)  # what the history keeps
            await _save_outbound(member, reply, session)
            return

        # 4e-6. M9 — criar meta: "meta: quero X"
        m_goal = _GOAL_CREATE_RE.match(body)
        if m_goal:
            goal_title = m_goal.group(1).strip()
            goal = Goal(id=uuid.uuid4(), member_id=member.id, title=goal_title, active=True)
            goals_view.prepare_new_goal(goal, today_local())
            session.add(goal)
            reply = _t("goal_created", lang, title=goal_title)
            if goals_view.is_money_goal(goal):
                reply += "\n" + _t("goal_money_hint", lang)
            await send_text(to, reply)
            await _save_outbound(member, reply, session)
            return

        # 4e-7. M9 — query de metas: "minhas metas"
        if _is_command(body, _GOALS_QUERY_WORDS):
            from sqlalchemy import select as _sel

            result = await session.execute(
                _sel(Goal)
                .where(Goal.member_id == member.id)
                .where(Goal.active.is_(True))
                .order_by(Goal.created_at.asc())
            )
            active_goals = result.scalars().all()
            if not active_goals:
                reply = _t("goals_list_empty", lang)
            else:
                reply = await goals_view.render_goals(
                    session, member, lang, today_local(), list(active_goals)
                )
            await send_text(to, reply)
            await _save_outbound(member, reply, session)
            return

        # 4e-7a. B-030 — delete a goal: "apaga a meta correr 10 km"
        m_gdel = _GOAL_DELETE_RE.match(body_plain)
        if m_gdel:
            kw = _grp(body, m_gdel).strip().strip(".!")
            goals_all = (
                (
                    await session.execute(
                        select(Goal)
                        .where(Goal.member_id == member.id, Goal.active.is_(True))
                        .order_by(Goal.created_at.desc())
                    )
                )
                .scalars()
                .all()
            )
            hits = [g for g in goals_all if _fuzzy_contains(kw, g.title)]
            exact = [g for g in hits if strip_accents(g.title.lower()) == strip_accents(kw.lower())]
            hits = exact or hits  # the full name wins over a longer goal that contains it
            if not hits:
                reply = _t("goal_complete_not_found", lang)
            elif len(hits) > 1:
                reply = _t(
                    "goal_delete_ambiguous",
                    lang,
                    items="\n".join(f"• {g.title}" for g in hits[:5]),
                )
            else:
                hits[0].active = False  # habit logs keep pointing at it; it leaves the list
                session.add(hits[0])
                audit(session, "goal_deleted", member.id)
                reply = _t("goal_deleted", lang, title=hits[0].title)
            await send_text(to, reply)
            await _save_outbound(member, reply, session)
            return

        # 4e-7b. M9 — complete goal: "meta de correr concluida"
        m_gcomplete = _GOAL_COMPLETE_RE.search(body_plain)
        if m_gcomplete:
            kw = _grp(body, m_gcomplete).strip()
            from sqlalchemy import select as _sel

            res_gc = await session.execute(
                _sel(Goal)
                .where(Goal.member_id == member.id)
                .where(Goal.active.is_(True))
                .order_by(Goal.created_at.desc())
            )
            gc = next((g for g in res_gc.scalars().all() if _fuzzy_contains(kw, g.title)), None)
            if gc:
                gc.active = False
                session.add(gc)
                reply = _t("goal_completed", lang, title=gc.title)
            else:
                reply = _t("goal_complete_not_found", lang)
            await send_text(to, reply)
            await _save_outbound(member, reply, session)
            return

        # 4e-7c. M9 — habit frequency: "quantas vezes meditei esta semana"
        m_hfreq = _HABIT_FREQ_RE.search(body_plain)
        if m_hfreq:
            freq_raw = _clean_activity((_grp(body, m_hfreq) or body).strip())
            from datetime import timedelta as _td

            from sqlalchemy import select as _sel

            monthly = any(w in body_plain for w in _FREQ_MONTH_WORDS)
            since = month_start(today_local()) if monthly else today_local() - _td(days=6)
            period = _t("freq_period_month" if monthly else "freq_period_week", lang)
            freq_kw = (freq_raw or body).lower()
            # a named workout type ("musculação", "corrida") counts workout sessions
            wk_act = next(
                (act for kw, act in _FREQ_WORKOUT_WORDS.items() if kw in body_plain), None
            )
            res_hf = await session.execute(
                _sel(HabitLog)
                .where(HabitLog.member_id == member.id)
                .where(HabitLog.log_date >= since)
            )
            all_habits = res_hf.scalars().all()
            matched = [
                h
                for h in all_habits
                if freq_kw in h.activity.lower() or h.activity.lower() in freq_kw
            ]
            if wk_act and not matched:
                res_wk = await session.execute(
                    _sel(func.count())
                    .select_from(WorkoutSession)
                    .where(WorkoutSession.member_id == member.id)
                    .where(WorkoutSession.workout_date >= since)
                    .where(WorkoutSession.activity_type.ilike(f"%{wk_act}%"))
                )
                n_wk = res_wk.scalar_one()
                label = activity_name(wk_act, lang)
                reply = (
                    _t("habit_frequency", lang, activity=label, n=n_wk, period=period)
                    if n_wk
                    else _t("habit_frequency_empty", lang, activity=label, period=period)
                )
            else:
                if not matched and all_habits and not freq_raw:
                    matched = all_habits  # "quantas vezes fiz algo": count everything
                if matched:
                    reply = _t(
                        "habit_frequency",
                        lang,
                        activity=matched[0].activity,
                        n=len(matched),
                        period=period,
                    )
                else:
                    reply = _t("habit_frequency_empty", lang, activity=freq_kw, period=period)
            await send_text(to, reply)
            await _save_outbound(member, reply, session)
            return
        # 4e-7d. V1.1 — habit streak: "quantos dias seguidos meditei"
        m_hstreak = _HABIT_STREAK_RE.search(body_plain)
        if m_hstreak:
            streak_raw = _grp(body, m_hstreak).strip()
            streak_kw = _clean_activity(streak_raw).lower() if streak_raw else ""
            from datetime import timedelta as _td

            from sqlalchemy import select as _sel

            res_hs = await session.execute(
                _sel(HabitLog.log_date, HabitLog.activity)
                .where(HabitLog.member_id == member.id)
                .order_by(HabitLog.log_date.desc())
            )
            all_logs = res_hs.all()  # list of (log_date, activity)
            # Filter by activity keyword if provided
            if streak_kw:
                filtered = [
                    (d, a) for d, a in all_logs if streak_kw in a.lower() or a.lower() in streak_kw
                ]
            else:
                filtered = list(all_logs)
            # Deduplicate dates, keep sorted desc
            seen_dates: set = set()
            unique_dates = []
            activity_label = streak_kw or "hábito"
            for log_date, activity in filtered:
                if log_date not in seen_dates:
                    seen_dates.add(log_date)
                    unique_dates.append(log_date)
                    if not streak_kw:
                        activity_label = activity  # use first (most recent) activity
            if not unique_dates:
                reply = _t("habit_streak_none", lang, activity=streak_kw or "hábito")
            else:
                from datetime import timedelta as _td2

                today = today_local()  # A-65: Amsterdam date, like the log itself
                yesterday = today - _td2(days=1)
                most_recent = unique_dates[0]
                if most_recent < yesterday:
                    streak_count = 0
                else:
                    streak_count = 1
                    for i in range(1, len(unique_dates)):
                        if (unique_dates[i - 1] - unique_dates[i]).days == 1:
                            streak_count += 1
                        else:
                            break
                if streak_count == 0:
                    reply = _t(
                        "habit_streak_none", lang, activity=_habit_label(activity_label, lang)
                    )
                else:
                    reply = _t(
                        "habit_streak_one" if streak_count == 1 else "habit_streak",
                        lang,
                        activity=_habit_label(activity_label, lang),
                        n=streak_count,
                    )
            await send_text(to, reply)
            await _save_outbound(member, reply, session)
            return

        # B-032: "água de hoje" / "sono médio" are questions; they used to be logged as habits
        health_q = any(
            rx.search(body_plain)
            for rx in (
                _HEALTH_MOOD_QUERY_RE,
                _HEALTH_SLEEP_QUERY_RE,
                _HEALTH_MED_QUERY_RE,
                _HEALTH_WATER_QUERY_RE,
            )
        )

        # 4e-8-. B-031: "meditei hoje?" is a question about the log, answered from the data
        if (
            not health_q
            and body.rstrip().endswith("?")
            and (m_hq := _HABIT_LOG_RE.search(body.rstrip(" ?")))
        ):
            q_activity = m_hq.group("activity").strip()
            when = today_local()
            if _YESTERDAY_WORD_RE.search(body_plain):
                when = when - timedelta(days=1)
            done = await _habit_already_logged(session, member.id, q_activity, when)
            key = "habit_q_yes" if done else "habit_q_no"
            day = _t("period_today" if when == today_local() else "period_yesterday", lang)
            reply = _t(key, lang, activity=q_activity, day=day)
            await send_text(to, reply)
            await _save_outbound(member, reply, session)
            return

        # 4e-8. M9 — log de hábito: "meditei hoje" (regex fast-path)
        m_habit = None if body.rstrip().endswith("?") or health_q else _HABIT_LOG_RE.search(body)
        if m_habit and _is_workout_text(body) and _DURATION_RE.search(body_plain):
            m_habit = None  # "fiz yoga durante 20 minutos" is a workout, not the habit "fiz yoga"
        if m_habit:
            habit_activity = m_habit.group("activity").strip()
            # Try to link to a matching active goal
            from sqlalchemy import select as _sel

            res_hg = await session.execute(
                _sel(Goal).where(Goal.member_id == member.id).where(Goal.active.is_(True))
            )
            linked_goal = _goal_for_activity(res_hg.scalars().all(), habit_activity)
            qty = habits_ext.parse_quantity(body_plain)
            if qty is not None:  # F10-10: "li 30 páginas" keeps the 30; the week adds up
                reply = await habits_ext.log_with_quantity(
                    session, member, lang, habit_activity, linked_goal, *qty, today_local()
                )
                await send_text(to, reply)
                await _save_outbound(member, reply, session)
                return
            if await _habit_already_logged(session, member.id, habit_activity, today_local()):
                reply = _t("habit_already_today", lang, activity=habit_activity)
                await send_text(to, reply)
                await _save_outbound(member, reply, session)
                return
            habit_log = HabitLog(
                id=uuid.uuid4(),
                member_id=member.id,
                goal_id=linked_goal.id if linked_goal else None,
                activity=habit_activity,
                log_date=today_local(),
            )
            session.add(habit_log)
            if linked_goal:
                reply = _t(
                    "habit_logged_with_goal", lang, activity=habit_activity, goal=linked_goal.title
                ) + await goals_view.marks_line(session, member, linked_goal, lang, today_local())
            else:  # F10-11/12: no goal yet → offer to create one (once per habit)
                reply = await habits_ext.offer_goal(
                    session,
                    member,
                    lang,
                    habit_activity,
                    _t("habit_logged", lang, activity=habit_activity),
                )
            await _send_reply(to, reply)
            await _save_outbound(member, getattr(reply, "text", reply), session)
            return

        # 4e-8b. M9 — log de hábito via LLM (free-form)
        # Only attempt if message is short (< 80 chars) and doesn't look like expense/workout
        _body_lower = body.lower()
        _not_expense = not any(
            w in _body_lower
            for w in ("€", "$", "£", "gastei", "comprei", "paguei", "spent", "paid", "bought")
        )
        _not_workout = not _is_workout_text(body)
        _is_health = _is_command(body, _HEALTH_WORDS) or bool(
            _HEALTH_HINT_RE.search(body_plain) and not body.rstrip().endswith("?")
        )
        _not_health = not _is_health
        if (
            len(body) < 80
            and not health_q
            and _not_expense
            and _not_workout
            and _not_health
            and not _has_zero_or_negative_amount(body)  # "café 0" is an invalid amount, not a habit
            and not _looks_like_bare_amount(body)  # "cinema 24" is an expense, not a habit
            and not body.rstrip().endswith("?")  # a question is not a log
            and _could_be_habit(body_plain)  # A-49: "voltei", "apaga", "hoje" never reach the model
        ):
            habit_data = await extract_habit(body, lang=member.language or "en")
            if habit_data:
                h_activity = habit_data["activity"]
                h_days_ago = habit_data.get("days_ago", 0)
                from datetime import timedelta as _td

                from sqlalchemy import select as _sel

                h_date = today_local() - _td(days=h_days_ago)
                # Try to link to goal
                res_hg2 = await session.execute(
                    _sel(Goal).where(Goal.member_id == member.id).where(Goal.active.is_(True))
                )
                linked_goal2 = _goal_for_activity(res_hg2.scalars().all(), h_activity)
                if await _habit_already_logged(session, member.id, h_activity, h_date):
                    reply = _t("habit_already_today", lang, activity=h_activity)
                    await send_text(to, reply)
                    await _save_outbound(member, reply, session)
                    return
                hlog2 = HabitLog(
                    id=uuid.uuid4(),
                    member_id=member.id,
                    goal_id=linked_goal2.id if linked_goal2 else None,
                    activity=h_activity,
                    notes=habit_data.get("notes"),
                    log_date=h_date,
                )
                session.add(hlog2)
                if linked_goal2:
                    reply = _t(
                        "habit_logged_with_goal", lang, activity=h_activity, goal=linked_goal2.title
                    ) + await goals_view.marks_line(
                        session, member, linked_goal2, lang, today_local()
                    )
                else:
                    reply = await habits_ext.offer_goal(
                        session,
                        member,
                        lang,
                        h_activity,
                        _t("habit_logged", lang, activity=h_activity),
                    )
                await _send_reply(to, reply)
                await _save_outbound(member, getattr(reply, "text", reply), session)
                logger.info(
                    "conversation.habit_recorded_llm", member_id=str(member.id), activity=h_activity
                )
                return

        # 4e-9. M8 — health log: medicação, humor, sono, água
        if _PURCHASE_NO_PRICE_RE.match(body_plain.strip()):
            _is_health = False  # M-27: "comprei 2 kg de arroz" is shopping (asked below)
        if _is_health and _unsupported_health(body_plain):
            reply = _t("health_unsupported", lang)
            await send_text(to, reply)
            await _save_outbound(member, reply, session)
            return
        if health_q:
            _is_health = False  # a question: go to the queries below, never to the log
        if _is_health and member.health_consent_at is None:  # art. 9: ask before storing
            reply = _t("health_consent_ask", lang)
            await send_buttons(
                to,
                reply,
                [("hc_yes:0", _t("btn_hc_yes", lang)), ("hc_no:0", _t("btn_hc_no", lang))],
            )
            await _save_outbound(member, reply, session)
            return
        if _is_health:
            today = today_local()
            health_data = await extract_health_log(body, lang=member.language or "en")
            if health_data:
                log_date = today
                if health_data.get("days_ago", 0) > 0:
                    from datetime import timedelta as _td

                    log_date = today_local() - _td(days=health_data["days_ago"])
                if health_data["log_type"] == "mood":  # F10-22: a score above 10 is checked
                    mv = habits_ext.mood_value(health_data["value"])
                    if mv is not None and mv > 10:
                        over = await habits_ext.ask_mood_over(
                            session, member, lang, health_data, log_date
                        )
                        await _send_reply(to, over)
                        await _save_outbound(member, over.text, session)
                        return
                hlog = HealthLog(
                    id=uuid.uuid4(),
                    member_id=member.id,
                    log_type=health_data["log_type"],
                    value=health_data["value"],
                    unit=health_data.get("unit"),
                    notes=health_data.get("notes"),
                    log_date=log_date,
                )
                session.add(hlog)
                lt = health_data["log_type"]
                val = _num_str(str(health_data["value"]), lang)
                if lt == "medication":
                    reply = _t("health_saved_medication", lang, value=val)
                elif lt == "mood":
                    mv = habits_ext.mood_value(health_data["value"])
                    reply = (
                        habits_ext.mood_reply(mv, lang)
                        if mv is not None
                        else _t("health_saved_mood", lang, value=val)
                    )
                elif lt == "sleep":
                    reply = _t("health_saved_sleep", lang, value=val)
                else:
                    reply = _t("health_saved_water", lang, value=val)
                await send_text(to, reply)
                await _save_outbound(member, reply, session)
                return

        # 4e-9b. M8 — mood history query
        if _HEALTH_MOOD_QUERY_RE.search(body_plain):
            from datetime import timedelta as _td

            from sqlalchemy import select as _sel

            week_ago = today_local() - _td(days=6)  # 7-day window incl. today
            res_mq = await session.execute(
                _sel(HealthLog)
                .where(HealthLog.member_id == member.id)
                .where(HealthLog.log_type == "mood")
                .where(HealthLog.log_date >= week_ago)
                .order_by(HealthLog.log_date.desc())
            )
            mood_rows = res_mq.scalars().all()
            if not mood_rows:
                reply = _t("health_mood_empty", lang)
            else:
                entries = ", ".join(
                    f"{r.value}/10 ({weekday_abbr(r.log_date, lang)})" for r in mood_rows[:7]
                )
                reply = _t("health_mood_history", lang, entries=entries)
            await send_text(to, reply)
            await _save_outbound(member, reply, session)
            return

        # 4e-9c. M8 — sleep average query
        if _HEALTH_SLEEP_QUERY_RE.search(body_plain):
            from datetime import timedelta as _td

            from sqlalchemy import select as _sel

            week_ago = today_local() - _td(days=6)  # 7-day window incl. today
            res_sq = await session.execute(
                _sel(HealthLog)
                .where(HealthLog.member_id == member.id)
                .where(HealthLog.log_type == "sleep")
                .where(HealthLog.log_date >= week_ago)
            )
            sleep_rows = res_sq.scalars().all()
            if not sleep_rows:
                reply = _t("health_sleep_empty", lang)
            else:
                try:
                    total_h = sum(float(r.value.replace(",", ".")) for r in sleep_rows)
                    avg = round(total_h / len(sleep_rows), 1)
                except (ValueError, ZeroDivisionError):
                    avg = 0.0
                reply = _t(
                    "health_sleep_avg",
                    lang,
                    avg=_num(avg, lang),
                    n=habits_ext.count_word(len(sleep_rows), lang),
                )
            await send_text(to, reply)
            await _save_outbound(member, reply, session)
            return

        # 4e-9d. M8 — medication adherence query
        if _HEALTH_MED_QUERY_RE.search(body_plain):
            from datetime import timedelta as _td

            from sqlalchemy import select as _sel

            week_ago = today_local() - _td(days=6)  # 7-day window incl. today
            res_medq = await session.execute(
                _sel(HealthLog)
                .where(HealthLog.member_id == member.id)
                .where(HealthLog.log_type == "medication")
                .where(HealthLog.log_date >= week_ago)
            )
            med_rows = res_medq.scalars().all()
            if not med_rows:
                reply = _t("health_medication_empty", lang)
            else:
                unique_days = len({r.log_date for r in med_rows})
                total_days = min(7, (today_local() - week_ago).days + 1)
                day_labels = ", ".join(
                    weekday_abbr(d, lang) for d in sorted({r.log_date for r in med_rows})
                )
                reply = _t(
                    "health_medication_adherence",
                    lang,
                    n=unique_days,
                    total=total_days,
                    days=day_labels,
                )
            await send_text(to, reply)
            await _save_outbound(member, reply, session)
            return

        # 4e-9e. M8 — water intake query
        if _HEALTH_WATER_QUERY_RE.search(body_plain):
            from sqlalchemy import select as _sel

            today_wq = today_local()
            res_wq = await session.execute(
                _sel(HealthLog)
                .where(HealthLog.member_id == member.id)
                .where(HealthLog.log_type == "water")
                .where(HealthLog.log_date == today_wq)
            )
            water_rows = res_wq.scalars().all()
            if not water_rows:
                reply = _t("health_water_empty", lang)
            else:
                try:
                    total_l = round(sum(float(r.value.replace(",", ".")) for r in water_rows), 1)
                except ValueError:
                    total_l = 0.0
                reply = _t("health_water_today", lang, total=_num(total_l, lang), n=len(water_rows))
            await send_text(to, reply)
            await _save_outbound(member, reply, session)
            return

        # 4e-10. M7 — treino: "corri 30 min", "fiz yoga durante 20 minutos"
        if _is_workout_text(body):
            today = today_local()
            workout_data = await extract_workout(body, lang=member.language or "en")
            if workout_data:
                from datetime import timedelta as _td

                wo_date = today
                if workout_data.get("days_ago", 0) > 0:
                    wo_date = today_local() - _td(days=workout_data["days_ago"])
                ws = WorkoutSession(
                    id=uuid.uuid4(),
                    member_id=member.id,
                    activity_type=workout_data["activity_type"],
                    duration_minutes=workout_data.get("duration_minutes"),
                    distance_km=workout_data.get("distance_km"),
                    notes=workout_data.get("notes"),
                    workout_date=wo_date,
                )
                session.add(ws)
                dur_str = _workout_dur(
                    workout_data.get("duration_minutes"), workout_data.get("distance_km"), lang
                )
                detail = f", {dur_str}" if dur_str.strip() else ""  # B-034: no ", ." when empty
                if wo_date != today:  # M-17: say which day it went to
                    day_word = (
                        _t("period_yesterday", lang)
                        if wo_date == today - _td(days=1)
                        else f"{weekday_abbr(wo_date, lang)} {wo_date:%d/%m}"
                    )
                    detail += f" ({day_word})"
                reply = _t(
                    "workout_saved",
                    lang,
                    activity=activity_name(workout_data["activity_type"], lang),
                    duration=detail,
                )
                # the workout also ticks the routine goal it belongs to ("correr 10 km")
                routine = (
                    (
                        await session.execute(
                            select(Goal).where(Goal.member_id == member.id, Goal.active.is_(True))
                        )
                    )
                    .scalars()
                    .all()
                )
                wo_goal = goals_view.goal_for_workout(list(routine), workout_data["activity_type"])
                if wo_goal is not None:
                    if not await _habit_already_logged(
                        session,
                        member.id,
                        activity_name(workout_data["activity_type"], lang),
                        wo_date,
                    ):
                        session.add(
                            HabitLog(
                                id=uuid.uuid4(),
                                member_id=member.id,
                                goal_id=wo_goal.id,
                                activity=activity_name(workout_data["activity_type"], lang),
                                log_date=wo_date,
                            )
                        )
                    reply += (
                        f"\n{_t('goal_workout_line', lang, goal=wo_goal.title)}"
                        + await goals_view.marks_line(session, member, wo_goal, lang, today_local())
                    )
                await send_text(to, reply)
                await _save_outbound(member, reply, session)
                logger.info(
                    "conversation.workout_recorded",
                    member_id=str(member.id),
                    activity=workout_data["activity_type"],
                )
                return

        # 4e-11. M7 — query de treinos: "treinos desta semana"
        if _is_bare_command(
            body,
            {
                "treinos",
                "workouts",
                "trainingen",
                "trainings",
                "mes treinos",
                "my workouts",
                "treinos desta semana",
                "workouts this week",
            },
        ):
            from datetime import timedelta as _td

            from sqlalchemy import select as _sel

            week_ago = today_local() - _td(days=6)  # 7-day window incl. today
            result = await session.execute(
                _sel(WorkoutSession)
                .where(WorkoutSession.member_id == member.id)
                .where(WorkoutSession.workout_date >= week_ago)
                .order_by(WorkoutSession.workout_date.desc())
            )
            sessions_list = result.scalars().all()
            if not sessions_list:
                reply = _t("workout_summary_empty", lang)
            else:
                lines = [_t("workout_summary_header", lang, n=len(sessions_list))]
                for ws in sessions_list:
                    dur = _workout_dur(ws.duration_minutes, ws.distance_km, lang)
                    lines.append(
                        _t(
                            "workout_summary_row",
                            lang,
                            date=ws.workout_date.strftime("%d/%m"),
                            activity=activity_name(ws.activity_type, lang),
                            duration=dur,
                        )
                    )
                reply = "\n".join(lines)
            await send_text(to, reply)
            await _save_outbound(member, reply, session)
            return

        # 4e-11a. "apaga o treino de segunda" / "apaga a corrida de sábado": that weekday's
        # most recent date (today counts); several that day → ask which.
        m_wdel = _WORKOUT_DELETE_DAY_RE.match(body_plain.strip())
        if m_wdel:
            from alfred.labels import activity_label
            from alfred.training import WEEKDAYS

            wd = WEEKDAYS.get(m_wdel.group("day"))
            if wd is not None:
                day = today_local() - timedelta(days=(today_local().weekday() - wd) % 7)
                rows_wd = (
                    (
                        await session.execute(
                            select(WorkoutSession).where(
                                WorkoutSession.member_id == member.id,
                                WorkoutSession.workout_date == day,
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                what = m_wdel.group("what")
                if what not in ("treino", "workout", "training", "entrainement"):
                    rows_wd = [
                        w
                        for w in rows_wd
                        if what in strip_accents(activity_label(w.activity_type, lang).lower())
                        or what in strip_accents((w.activity_type or "").lower())
                    ]
                if len(rows_wd) == 1:
                    await session.delete(rows_wd[0])
                    reply = _t("workout_deleted", lang)
                elif not rows_wd:
                    reply = _t("workout_delete_not_found", lang)
                else:
                    names = _join_names(
                        [activity_label(w.activity_type, lang) for w in rows_wd], lang
                    )
                    reply = _t("workout_delete_which", lang, day=f"{day:%d/%m}", names=names)
                await send_text(to, reply)
                await _save_outbound(member, reply, session)
                return

        # 4e-11b. M7 — delete workout: "apaga o treino de hoje"
        if _WORKOUT_DELETE_RE.search(body_plain):
            from sqlalchemy import select as _sel

            wd_query = _sel(WorkoutSession).where(WorkoutSession.member_id == member.id)
            # A day word scopes the delete: "de hoje" must never remove another day's workout.
            if re.search(r"\b(?:hoje|today|vandaag|heute|aujourd)", body_plain):
                wd_query = wd_query.where(WorkoutSession.workout_date == today_local())
            elif re.search(r"\b(?:ontem|yesterday|gisteren|gestern|hier)\b", body_plain):
                wd_query = wd_query.where(
                    WorkoutSession.workout_date == today_local() - timedelta(days=1)
                )
            res_wd = await session.execute(
                wd_query.order_by(
                    WorkoutSession.workout_date.desc(), WorkoutSession.created_at.desc()
                ).limit(1)
            )
            wo_del = res_wd.scalar_one_or_none()
            if wo_del:
                await session.delete(wo_del)
                reply = _t("workout_deleted", lang)
            else:
                reply = _t("workout_delete_not_found", lang)
            await send_text(to, reply)
            await _save_outbound(member, reply, session)
            return

        # 4e-11c. M7 — workout monthly summary: "treinos deste mês"
        if _WORKOUT_MONTH_RE.search(body_plain):
            from datetime import timedelta as _td

            from sqlalchemy import select as _sel

            now_wm = now_local()
            start_wm, end_wm = _month_window(body_plain, now_wm.date())
            month_label = month_name(start_wm, lang, year=True)
            q = _sel(WorkoutSession).where(
                WorkoutSession.member_id == member.id,
                WorkoutSession.workout_date >= start_wm,
            )
            if end_wm:
                q = q.where(WorkoutSession.workout_date < end_wm)
            res_wm = await session.execute(q.order_by(WorkoutSession.workout_date.desc()))
            wm_sessions = res_wm.scalars().all()
            n_wm = len(wm_sessions)
            total_km = round(sum(s.distance_km or 0 for s in wm_sessions), 1)
            total_min = sum(s.duration_minutes or 0 for s in wm_sessions)
            reply = _t(
                "workout_month_header",
                lang,
                month=month_label,
                n=n_wm,
                km=_num(total_km, lang),
                min=total_min,
            )
            if wm_sessions:
                lines = [reply]
                for ws_m in wm_sessions[:5]:
                    dur_m = _workout_dur(ws_m.duration_minutes, ws_m.distance_km, lang)
                    lines.append(
                        _t(
                            "workout_summary_row",
                            lang,
                            date=f"{weekday_abbr(ws_m.workout_date, lang)} "
                            f"{ws_m.workout_date:%d/%m}",
                            activity=activity_name(ws_m.activity_type, lang),
                            duration=dur_m,
                        )
                    )
                reply = "\n".join(lines)
            await send_text(to, reply)
            await _save_outbound(member, reply, session)
            return

        # 4e-11d. M7 — workout activity count: "quantas vezes corri esta semana"
        if _WORKOUT_ACTIVITY_RE.search(body_plain):
            from datetime import timedelta as _td

            from sqlalchemy import select as _sel

            week_ago_wa = today_local() - _td(days=6)  # 7-day window incl. today
            res_wa = await session.execute(
                _sel(WorkoutSession)
                .where(WorkoutSession.member_id == member.id)
                .where(WorkoutSession.workout_date >= week_ago_wa)
            )
            wa_sessions = res_wa.scalars().all()
            _bl2 = body_plain
            # Detect activity keyword
            _act_map = {
                "corri": "running",
                "ran": "running",
                "liep": "running",
                "couru": "running",
                "nadei": "swimming",
                "swam": "swimming",
                "zwom": "swimming",
                "ginasio": "strength",
                "gym": "strength",
                "treino": "strength",
            }
            matched_act = None
            for kw, act in _act_map.items():
                if kw in _bl2:
                    matched_act = act
                    break
            if matched_act:
                count = sum(1 for s in wa_sessions if matched_act in s.activity_type.lower())
                activity_label_key = matched_act
            else:
                count = len(wa_sessions)
                activity_label_key = "workout"
            reply = _t(
                "workout_activity_summary",
                lang,
                activity=activity_name(activity_label_key, lang),
                n=count,
            )
            await send_text(to, reply)
            await _save_outbound(member, reply, session)
            return

            # 4e-pre. M14 — trip commands
        trip_reply = await _handle_trip(body, member, lang, session)
        if trip_reply is not None:
            await send_text(to, trip_reply)
            await _save_outbound(member, trip_reply, session)
            return

        # 4e-0c. Real commands the LLM used to improvise (and get wrong):
        # "apaga", "ultimas 5 despesas", "top categorias este mes".
        if DELETE_LAST_EXPENSE_RE.match(body_plain.strip()):
            last = await session.scalar(
                select(Expense)
                .where(Expense.member_id == member.id)
                .order_by(Expense.created_at.desc())
                .limit(1)
            )
            if last is None:
                reply = _t("expense_delete_none", lang)
                await send_text(to, reply)
            else:  # ask first: "apaga" used to delete at once, even a salary or a bill
                name = last.merchant or last.description or category_label(last.category, lang)
                reply = _t(
                    "expense_delete_ask",
                    lang,
                    amount=_fmt_eur(last.amount),
                    name=name,
                    date=to_local(last.expense_date).strftime("%d/%m"),
                )
                await send_buttons(
                    to,
                    reply,
                    [
                        (f"undo:{last.id}", _t("btn_delete", lang)),
                        (f"ok:{last.id}", _t("btn_keep", lang)),
                    ],
                )
            await _save_outbound(member, reply, session)
            return

        if _BANK_BALANCE_RE.search(body_plain):  # B-006: "saldo da conta 300" is not tracked
            reply = _t("bank_balance_not_tracked", lang)
            await send_text(to, reply)
            await _save_outbound(member, reply, session)
            return

        n_last = parse_last_n(body_plain)
        n_last_in = None if n_last else parse_last_n(body_plain, income=True)
        if n_last or n_last_in:
            reply = await _build_recent(
                member, session, n_last or n_last_in, income=bool(n_last_in)
            )
            await send_text(to, reply)
            await _save_outbound(member, reply, session)
            return

        if INCOME_QUERY_RE.match(body_plain.strip()) and not _ANALYSIS_HINT_RE.search(body_plain):
            # A-26: the income list, never the spending
            reply = await _build_summary(
                member,
                session,
                category="inkomen",
                window=parse_period(body_plain, today_local()),
            )
            await send_text(to, reply)
            await _save_outbound(member, reply, session)
            return

        if body_plain.strip(" .!?") in _BARE_DAY_WORDS:  # BUG-03: "hoje" alone is today's spending
            reply = await _build_summary(
                member, session, window=parse_period(body_plain.strip(" .!?"), today_local())
            )
            await send_text(to, reply)
            await _save_outbound(member, reply, session)
            return

        if _TOP_EXPENSES_RE.match(body_plain.strip()):
            from alfred.followup import top_text

            win = parse_period(body_plain, today_local())
            if win is not None:
                t_start, t_end, _lbl = _window_range(win, lang)
            else:
                t_start, t_end, _lbl = _period_range("current_month", lang)
            reply = await top_text(member, t_start, t_end, lang, session)
            await send_text(to, reply)
            await _save_outbound(member, reply, session)
            return

        if TOP_CATEGORIES_RE.match(body_plain.strip()):
            win = parse_period(body_plain, today_local())
            reply = await _build_summary(
                member, session, period="current_month", window=win, top=True
            )
            await send_text(to, reply)
            await _save_outbound(member, reply, session)
            return

        # 4e-0d. Amount correction: "errei foram 42€" / "na verdade foram 32"
        m_amt = _AMOUNT_CORRECTION_RE.search(body)
        m_lone = None if m_amt else _LONE_AMOUNT_RE.match(body)
        if m_amt or m_lone:
            raw_amt = (m_amt or m_lone).group(1).replace("€", "").replace("$", "").replace("£", "")
            new_amount = to_amount(raw_amt.strip())
            target = await _take_edit_target(session, member)
            if target is not None and new_amount and round(new_amount, 2) <= MAX_AMOUNT:
                old_amount = target.amount
                target.amount = round(new_amount, 2)
                session.add(target)
                audit(session, "expense_amount_corrected", member.id)
                reply = _t(
                    "expense_corrected",
                    lang,
                    name=target.merchant
                    or target.description
                    or category_label(target.category, lang),
                    old=_fmt_eur(old_amount),
                    amount=_fmt_eur(new_amount),
                )
                await send_text(to, reply)
                await _save_outbound(member, reply, session)
                return
        if m_amt:
            # bounded: NUMERIC(12,2) would overflow (and the reply was already sent)
            if new_amount and 0 < round(new_amount, 2) <= MAX_AMOUNT:
                cutoff = datetime.now(UTC) - timedelta(hours=CORRECTION_WINDOW_HOURS)
                res = await session.execute(
                    select(Expense)
                    .where(
                        Expense.member_id == member.id,
                        Expense.created_at >= cutoff,
                        Expense.transaction_type == "expense",
                    )
                    .order_by(Expense.created_at.desc())
                    .limit(1)
                )
                last_exp = res.scalar_one_or_none()
                if last_exp:
                    old_amount = last_exp.amount
                    last_exp.amount = new_amount
                    session.add(last_exp)
                    name = (  # B-005: "cinema", not its category "Lazer"
                        last_exp.merchant
                        or last_exp.description
                        or category_label(last_exp.category, lang)
                    )
                    reply = _t(
                        "expense_corrected",
                        lang,
                        name=name,
                        old=_fmt_eur(old_amount),
                        amount=_fmt_eur(new_amount),
                    )
                    await send_text(to, reply)
                    await _save_outbound(member, reply, session)
                    logger.info(
                        "conversation.expense_amount_corrected",
                        member_id=str(member.id),
                        new_amount=new_amount,
                    )
                    return
                else:
                    # m_amt matched but no recent expense found — don't create a new one
                    reply = _t("correction_no_expense", lang)
                    await send_text(to, reply)
                    await _save_outbound(member, reply, session)
                    return

        # 4e-0d. The amount that answers "Quanto foi X?" (B-070): becomes that expense
        if m_ans := _AMOUNT_ONLY_RE.match(body):
            ask_row = await session.scalar(
                select(PendingAction).where(
                    PendingAction.member_id == member.id,
                    PendingAction.kind == ASK_AMOUNT_KIND,
                    PendingAction.expires_at > datetime.now(UTC),
                )
            )
            if ask_row is not None:
                await session.delete(ask_row)
                await session.flush()
                rebuilt = f"{ask_row.payload.get('item', '')} {m_ans.group('amt')}"
                _override_body(message, rebuilt)  # the stored row keeps what was typed
                body = rebuilt.lower()
                body_plain = strip_accents(body)

        # 4e-0e. Several transactions in one message: "mercado 20 e farmácia 10".
        if _looks_multi(body):
            items = await extract_expenses_multi(message.body or "", lang=member.language or "en")
            if len(items) >= 2:
                items = _fix_item_currencies(message.body or "", items)
                skipped: list[str] = []
                usable: list[dict] = []
                foreign: dict[str, list[str]] = {}
                for item in items:
                    if item["currency"] != "EUR":
                        foreign.setdefault(item["currency"], []).append(
                            item["merchant"] or item["description"] or item["category"]
                        )
                    else:
                        usable.append(item)
                for cur, names in foreign.items():  # B-007: one line, not one per item
                    skipped.append(
                        _t("multi_skipped_currency", lang, cur=cur, name=_join_names(names, lang))
                    )
                from alfred.batch import MAX_ITEMS as _MAX_BATCH

                if len(usable) > _MAX_BATCH:  # said out loud: the rest used to vanish silently
                    usable = usable[:_MAX_BATCH]
                    skipped.append(_t("multi_truncated", lang, n=_MAX_BATCH))
                # V2-17: two or more entries are shown as a draft; nothing is written yet.
                draft = await create_draft(session, member, usable, lang)
                if draft is not None:
                    text = draft.text + ("\n" + "\n".join(skipped) if skipped else "")
                    await send_buttons(to, text, draft.buttons)
                    reply = text
                elif clean_items(usable):  # one valid entry left after the checks: record it
                    from alfred.batch import record_items

                    reply = await record_items(session, member, clean_items(usable), lang)
                    if skipped:
                        reply += "\n" + "\n".join(skipped)
                    await send_text(to, reply)
                else:
                    reply = "\n".join(skipped)
                    await send_text(to, reply)
                await _save_outbound(member, reply, session)
                logger.info(
                    "conversation.multi_expense_drafted",
                    member_id=str(member.id),
                    drafted=len(usable),
                    skipped=len(skipped),
                )
                return

        if (m_paid := _PAID_NO_AMOUNT_RE.match(body_plain.strip())) and (
            m_paid.group("item").strip() not in _PAID_ITEM_STOP
            and len(m_paid.group("item").split()) <= 4
        ):
            # B-070: "paguei gás" with no pending bill and no price remembers what it asked about
            item = _original_text(message, m_paid.start("item"), m_paid.end("item")).strip()
            await session.execute(
                delete(PendingAction).where(
                    PendingAction.member_id == member.id, PendingAction.kind == ASK_AMOUNT_KIND
                )
            )
            session.add(
                PendingAction(
                    member_id=member.id,
                    kind=ASK_AMOUNT_KIND,
                    payload={"item": item[:60]},
                    expires_at=datetime.now(UTC) + timedelta(minutes=ASK_AMOUNT_MINUTES),
                )
            )
            await session.flush()
            reply = _t("ask_amount_item", lang, item=item)
            await send_text(to, reply)
            await _save_outbound(member, reply, session)
            return
        if m_buy := _PURCHASE_NO_PRICE_RE.match(body_plain.strip()):
            # M-27: "comprei 2 kg de arroz" is a purchase without its price, not body weight
            item = _original_text(message, m_buy.start("item"), m_buy.end("item"))
            reply = _t("purchase_ask_price", lang, item=item)
            await send_text(to, reply)
            await _save_outbound(member, reply, session)
            return
        if m_neg := _NEGATIVE_ITEM_RE.match(body.strip()):
            # A-29: "reembolso -15" is never stored as an expense. M-05: offer it as money in.
            amount = to_amount(m_neg.group("amt"))
            name = _original_text(message, m_neg.start("name"), m_neg.end("name")) or "-"
            if amount and 0 < amount <= MAX_AMOUNT:
                await session.execute(
                    delete(PendingAction).where(
                        PendingAction.member_id == member.id, PendingAction.kind == NEG_KIND
                    )
                )
                pa = PendingAction(
                    member_id=member.id,
                    kind=NEG_KIND,
                    payload={"amount": round(amount, 2), "name": name[:80]},
                    expires_at=datetime.now(UTC) + timedelta(minutes=EDIT_MINUTES),
                )
                session.add(pa)
                await session.flush()
                reply = _t("negative_as_income_ask", lang, amount=_fmt_eur(amount), name=name)
                await send_buttons(
                    to,
                    reply,
                    [
                        (f"neg_in:{pa.id}", _t("btn_neg_income", lang)),
                        (f"neg_no:{pa.id}", _t("btn_neg_no", lang)),
                    ],
                )
                await _save_outbound(member, reply, session)
                return
        if re.search(r"(?<!\S)-\d", body):  # A-29: "reembolso -15" is never stored as an expense
            reply = _t("invalid_amount_check", lang)
            await send_text(to, reply)
            await _save_outbound(member, reply, session)
            logger.info("conversation.invalid_amount", member_id=str(member.id))
            return

        expense_data = await extract_expense(
            message.body or "", merchant_overrides=member_overrides, lang=member.language or "en"
        )
        if (
            expense_data is None
            and _looks_like_bare_amount(body)
            and not _NOT_MONEY_UNIT_RE.search(body)
        ):
            # B-035: "notebook 1210" — words and one bare number are money even when the model
            # says no; it is saved under "Outros" and the member is asked for the category.
            expense_data = _bare_amount_expense(message.body or "")
        if expense_data and expense_data["currency"] == "EUR" and _foreign_currency(body):
            expense_data = {**expense_data, "currency": _foreign_currency(body)}
        if expense_data and expense_data["currency"] != "EUR":
            reply = _t("currency_unsupported", lang, cur=expense_data["currency"])
            await send_text(to, reply)
            await _save_outbound(member, reply, session)
            return
        if expense_data:
            txn_type = expense_data.get("type", "expense")
            days_ago = expense_data.get("days_ago", 0)
            if not days_ago:  # "paguei 130 de seguro saúde, data 05/10": the date written
                days_ago = _written_days_ago(body) or 0
            expense_date = now_local() - timedelta(days=days_ago)
            pending_days = expense_data.get("pending_in_days")
            pending_status = None
            if (
                pending_days is not None
            ):  # "vou receber 300 em dois dias": due later, not in the balance
                pending_status = "to_receive" if txn_type == "income" else "to_pay"
                days_ago = 0
                expense_date = datetime.combine(
                    today_local() + timedelta(days=int(pending_days)),
                    time(12, 0),
                    tzinfo=local_tz(),
                )

            # M14 — auto-tag with the active trip. Looked up BEFORE the expense is
            # created so it is inserted once, already tagged.
            active_trip = await session.scalar(
                select(Trip).where(Trip.member_id == member.id, Trip.active.is_(True))
            )
            couple_link = await active_link(session, member.id) if txn_type == "expense" else None
            shared_now = couple_link is not None and await is_home_by_default(
                session, member, expense_data["category"]
            )
            expense = Expense(
                id=uuid.uuid4(),
                member_id=member.id,
                household_id=member.household_id,
                transaction_type=txn_type,
                amount=expense_data["amount"],
                currency=expense_data["currency"],
                merchant=expense_data["merchant"],
                category=expense_data["category"],
                description=expense_data["description"],
                expense_date=expense_date,
                trip_id=active_trip.id if active_trip else None,
                shared=shared_now,
                scope=default_scope(member, expense_data["category"], txn_type),
                **({"status": pending_status} if pending_status else {}),
            )
            session.add(expense)
            limit_q = None

            name = (
                expense_data["merchant"]
                or expense_data["description"]
                or category_label(expense_data["category"], lang)  # A-55: never "overig"
            )
            amt_fmt = _fmt_eur(expense_data["amount"])
            key = "income_recorded" if txn_type == "income" else "expense_recorded"
            if pending_status:
                reply = _t(
                    "ledger_added_recv" if txn_type == "income" else "ledger_added_pay",
                    lang,
                    name=name,
                    amount=amt_fmt,
                    due=f"{expense_date:%d/%m}",
                )
            else:
                reply = _t(key, lang, amount=amt_fmt, name=name)
            if shared_now:
                reply += _t("home_auto_suffix", lang)
            if days_ago > 0:
                reply += _t("days_ago_suffix", lang, n=days_ago)
            if txn_type == "expense" and not pending_status:
                await session.flush()  # make the new row visible to the month total below
                reply += await _month_category_context(
                    session, member, expense_date, expense_data.get("category"), lang
                )
                alert_text = await alert_after_expense(
                    session, member, expense_data.get("category"), expense_date, lang
                )
                reply += alert_text
                if alert_text:  # F06-31: at 100 % offer to raise the limit (second message)
                    limit_q = await budgets.limit_question(
                        session, member, expense_data.get("category"), lang
                    )
            # BUG-07 fix: show cumulative trip total when expense tagged to trip
            if active_trip and txn_type == "expense":
                from sqlalchemy import func as sa_func

                await session.flush()  # make the new row visible to the SUM below
                trip_total_res = await session.execute(
                    select(sa_func.coalesce(sa_func.sum(Expense.amount), 0)).where(
                        Expense.trip_id == active_trip.id,
                        Expense.transaction_type == "expense",
                        Expense.status.in_(SETTLED),
                    )
                )
                # The SUM already includes this expense (flushed above): do NOT add it again.
                trip_total = float(trip_total_res.scalar() or 0)
                reply += _t(
                    "trip_expense_total",
                    lang,
                    dest=active_trip.destination,
                    total=_fmt_eur(trip_total),
                )
                trip_budget = active_trip.budget
                if not trip_budget:  # B-038: a budget planned per category also counts
                    planned = await session.scalar(
                        select(sa_func.coalesce(sa_func.sum(TripBudgetLine.amount), 0)).where(
                            TripBudgetLine.trip_id == active_trip.id
                        )
                    )
                    trip_budget = float(planned or 0) or None
                if trip_budget:
                    reply += _budget_remaining(trip_budget, trip_total, lang)
            # BUG-09: prompt for category when merchant unknown and category is overig
            if (
                txn_type == "expense"
                and expense_data.get("category") == "overig"
                and expense_data.get("merchant")
                and expense_data["merchant"].lower() not in (member_overrides or {})
            ):
                reply += _t("category_hint_overig", lang, merchant=expense_data["merchant"])

            high_value = (
                expense_data["amount"] >= settings.high_value_threshold and txn_type != "income"
            )
            if high_value:
                reply += _t("high_value_hint", lang)
            # Reply buttons carry the expense id: no pending state needed to undo/edit.
            first = ("ok" if high_value else "edit", "btn_ok" if high_value else "btn_edit")
            expense_buttons = [
                (f"{first[0]}:{expense.id}", _t(first[1], lang)),
                (f"undo:{expense.id}", _t("btn_undo", lang)),
            ]
            if (
                couple_link is not None and not shared_now
            ):  # V2-10: a third button, only for couples
                expense_buttons.append((f"home:{expense.id}", _t("btn_home", lang)))
            await send_buttons(to, reply, expense_buttons)
            await _save_outbound(member, reply, session)
            if limit_q is not None:
                await _send_reply(to, limit_q)
                await _save_outbound(member, limit_q.text, session)
            logger.info(
                "conversation.expense_recorded",
                member_id=str(member.id),
                type=txn_type,
                amount=expense_data["amount"],
                category=expense_data["category"],
            )
            return

        # Gibberish / unrecognised input check — before LLM calls to save tokens
        if _looks_like_gibberish(body):
            reply = _t("not_understood", lang)
            await send_text(to, reply)
            await _save_outbound(member, reply, session)
            return

        # 4f-0. A question that asks for an analysis (comparison, average, per month, last N
        # months…) goes to V2-14 first: the coarse classifier answered them with the month
        # comparison card and the daily limit / "salva essa visão" never applied (B-013).
        q_window = parse_period(body_plain, today_local())
        if looks_like_analysis(body_plain) and (
            _ANALYSIS_HINT_RE.search(body_plain) or q_window is None
        ):
            analysis_first = await handle_analysis_question(
                message.body or "", body_plain, member, lang, session
            )
            if analysis_first is not None:
                await send_text(to, analysis_first)
                await _save_outbound(member, analysis_first, session)
                return

        # 4f. Try to classify as a financial query
        query = await classify_query(message.body or "")
        if query is None and q_window is not None and _SPEND_QUESTION_RE.search(body_plain):
            # "quanto gastei este ano?" / "gastos em 2025": the plain summary of that window
            query = {"query_type": "period", "period": "current_month", "category": None}
        if query:
            qtype = query.get("query_type")
            period = query.get("period") or "current_month"
            category = query.get("category")
            # Dates are parsed exactly here; the LLM only guesses today/week/month.
            window = parse_period(body_plain, today_local())
            deeper = []  # V2-20 buttons, filled by the spending answers

            if qtype == "balance":
                reply = await _build_saldo(member, session, window)
            elif qtype == "category" and category:
                reply = await _build_summary(
                    member,
                    session,
                    period=period,
                    category=category,
                    window=window,
                    buttons_out=deeper,
                )
            elif qtype == "period":
                reply = await _build_summary(
                    member, session, period=period, window=window, buttons_out=deeper
                )
            elif qtype == "comparison":
                reply = await _build_comparison(member, session)
            else:
                reply = None

            if reply:
                if deeper:
                    await send_buttons(to, reply, deeper)
                else:
                    await send_text(to, reply)
                await _save_outbound(member, reply, session)
                logger.info(
                    "conversation.query_replied", member_id=str(member.id), query_type=qtype
                )
                return

        # 4f-2. V2-14 — free-form analysis ("gastos por categoria este ano vs ano passado")
        analysis_reply = await handle_analysis_question(
            message.body or "", body_plain, member, lang, session
        )
        if analysis_reply is not None:
            await send_text(to, analysis_reply)  # M-10: no 👍/👎 buttons any more
            await _save_outbound(member, analysis_reply, session)
            return

            # 4g. General LLM reply with conversation history
        if _has_zero_or_negative_amount(body):
            # Zero/negative amounts are never stored: answer deterministically, skip the LLM.
            reply = _t("invalid_amount_check", lang)
            await send_text(to, reply)
            await _save_outbound(member, reply, session)
            logger.info("conversation.invalid_amount", member_id=str(member.id))
            return
        if _unsupported_health(body_plain):
            # Weight / blood pressure are not tracked: say so instead of letting the model "record" them.
            reply = _t("health_unsupported", lang)
            await send_text(to, reply)
            await _save_outbound(member, reply, session)
            return
        history = await _load_history(member, session, exclude_id=message.id)
        reply = await generate_reply(member, message, history=history)  # noqa: F821
        claim = (
            (_FIRST_PERSON_CLAIM_RE.search(reply) or _ACTION_CLAIM_RE.search(reply))  # A-28
            if _is_question(body_plain)
            else (_claims_recorded(reply) or _ACTION_CLAIM_RE.search(reply))
        )
        if claim:
            # Nothing was written on this path: never let the model confirm a phantom record.
            alert("conversation.llm_false_record_claim", member_id=str(member.id))
            reply = _t(_fallback_key(body), lang)
        elif _FAKE_CONFIRM_RE.search(reply):
            # There is no pending-confirmation state: replace the invented flow with a real ask.
            alert("conversation.llm_fake_confirmation", member_id=str(member.id))
            reply = _t(
                "invalid_amount_check"
                if _has_zero_or_negative_amount(body)
                else _fallback_key(body),
                lang,
            )
        await send_text(to, reply)  # M-10: the 👍/👎 buttons were removed (J, 20/10)
        await _save_outbound(member, reply, session)
        logger.info("conversation.reply_sent", member_id=str(member.id))
        return
