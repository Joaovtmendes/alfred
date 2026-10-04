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
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from alfred.accounting import STRINGS as _ACCT_STRINGS
from alfred.accounting import default_scope, handle_accounting_command
from alfred.agenda import STRINGS as _AGENDA_STRINGS
from alfred.agenda import handle_agenda_command
from alfred.agenda import handle_button as handle_agenda_button
from alfred.analysis import STRINGS as _ANALYSIS_STRINGS
from alfred.analysis import handle_analysis_question, handle_view_command
from alfred.audit import audit
from alfred.batch import STRINGS as _BATCH_STRINGS
from alfred.batch import clean_items, create_draft, handle_batch_button, handle_batch_text
from alfred.budgets import STRINGS as _BUDGET_STRINGS
from alfred.budgets import alert_after_expense, handle_budget_command
from alfred.clock import (
    day_start,
    month_name,
    month_start,
    now_local,
    prev_month_start,
    to_local,
    today_local,
    week_start,
    weekday_abbr,
)
from alfred.couple import STRINGS as _COUPLE_STRINGS
from alfred.couple import (
    active_link,
    handle_couple_button,
    handle_couple_command,
    is_home_by_default,
)
from alfred.insights import STRINGS as _INSIGHT_STRINGS
from alfred.insights import handle_insight_command
from alfred.iou import STRINGS as _IOU_STRINGS
from alfred.iou import handle_iou_command
from alfred.labels import activity_label as activity_name
from alfred.labels import category_label
from alfred.lang_cmd import STRINGS as _LANG_STRINGS
from alfred.lang_cmd import parse_language_choice, parse_language_command
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
    Expense,
    Goal,
    HabitLog,
    HealthLog,
    Member,
    MerchantCategoryOverride,
    Message,
    Note,
    Task,
    Trip,
    WorkoutSession,
)
from alfred.monthly_summary import STRINGS as _MSUM_STRINGS
from alfred.monthly_summary import handle_monthly_summary_command
from alfred.observability import alert
from alfred.outbox import STRINGS as _OUTBOX_STRINGS
from alfred.outbox import handle_outbox_command
from alfred.parsing import (
    CORRECTION_PRONOUNS,
    DELETE_LAST_EXPENSE_RE,
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
)
from alfred.receipt import STRINGS as _RECEIPT_STRINGS
from alfred.recurrence import cadence_label, parse_recurrence
from alfred.recurring import STRINGS as _RECURRING_STRINGS
from alfred.recurring import handle_recurring_command
from alfred.score import STRINGS as _SCORE_STRINGS
from alfred.score import handle_score_command
from alfred.settings import settings
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
    "disclosure": {
        "pt": (
            "*Alfred* — assistente pessoal pelo WhatsApp.\n\nSou uma inteligência artificial, não uma pessoa. Para ajudar você, processo as suas mensagens.\n\nResponda *sim* para continuar ou *não* para cancelar."
        ),
        "nl": (
            "*Alfred* — persoonlijke assistent via WhatsApp.\n\n"
            "Ik ben een kunstmatige intelligentie, geen mens. "
            "Je berichten worden verwerkt om je te ondersteunen.\n\n"
            "Schrijf *ja* om door te gaan of *nee* om te annuleren."
        ),
        "en": (
            "*Alfred* — personal assistant via WhatsApp.\n\n"
            "I am an artificial intelligence, not a person. "
            "Your messages are processed to provide you support.\n\n"
            "Write *yes* to continue or *no* to cancel."
        ),
        "fr": (
            "*Alfred* — assistant personnel via WhatsApp.\n\n"
            "Je suis une intelligence artificielle, pas une personne. "
            "Tes messages sont traités pour t'apporter du soutien.\n\n"
            "Écris *oui* pour continuer ou *non* pour annuler."
        ),
        "de": (
            "*Alfred* — persönlicher Assistent via WhatsApp.\n\n"
            "Ich bin eine künstliche Intelligenz, kein Mensch. "
            "Deine Nachrichten werden verarbeitet, um dich zu unterstützen.\n\n"
            "Schreib *ja*, um fortzufahren, oder *nein* zum Abbrechen."
        ),
    },
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
    "consent_rejected": {
        "pt": "Entendido, não vou processar mais mensagens. Para voltar, envie *START*.",
        "nl": "Begrepen. Ik verwerk geen berichten meer. Stuur *START* om te hervatten.",
        "en": "Understood. I won't process any more messages. Send *START* to resume.",
        "fr": "Compris. Je ne traiterai plus de messages. Envoie *START* pour reprendre.",
        "de": "Verstanden. Ich verarbeite keine Nachrichten mehr. Sende *START*, um fortzufahren.",
    },
    "consent_unknown": {
        "pt": "Responda *sim* para continuar ou *não* para cancelar.",
        "nl": "Antwoord *ja* om door te gaan of *nee* om te annuleren.",
        "en": "Reply *yes* to continue or *no* to cancel.",
        "fr": "Réponds *oui* pour continuer ou *non* pour annuler.",
        "de": "Antworte *ja*, um fortzufahren, oder *nein* zum Abbrechen.",
    },
    "help": {
        "pt": (
            '*Alfred* — o que eu posso fazer por você:\n\n*Despesas*\n• "gastei €45 no Jumbo"\n• "Uber 12,50"\n• "paguei €180 de aluguel"\n\n*Receitas*\n• "recebi €2.800 de salário"\n\n*Consultas*\n• "resumo" — gastos do mês\n• "saldo" — receitas e despesas\n• "gastos desta semana" — por período\n• "compara este mês com o mês passado"\n• "ajuda" — esta mensagem\n\n_Para sair: "stop"_'
        ),
        "nl": (
            "*Alfred* — wat ik voor je kan doen:\n\n"
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
            "*Alfred* — what I can do for you:\n\n"
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
            "*Alfred* — ce que je peux faire pour toi :\n\n"
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
            "*Alfred* — was ich für dich tun kann:\n\n"
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
        "pt": "Ainda não há registros de *{category}* ({period_label}).",
        "nl": "Geen registraties in *{category}* in {period_label}.",
        "en": "No records in *{category}* in {period_label}.",
        "fr": "Aucun enregistrement dans *{category}* pour {period_label}.",
        "de": "Keine Einträge in *{category}* in {period_label}.",
    },
    "no_records_period": {
        "pt": "Ainda não há registros ({period_label}).",
        "nl": "Geen registraties in {period_label}.",
        "en": "No records in {period_label}.",
        "fr": "Aucun enregistrement pour {period_label}.",
        "de": "Keine Einträge in {period_label}.",
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
        "pt": 'Por enquanto só trabalho em euros, então não guardei os {cur}. Envie novamente convertido em € (ex.: "Jumbo 23,50")?',
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
        "pt": "Ainda não acompanho peso nem pressão, só medicação, humor, sono e água. Se quiser, guardo como nota: “nota: peso 75 kg”.",
        "nl": "Gewicht en bloeddruk volg ik nog niet, alleen medicatie, stemming, slaap en water. Wil je het als notitie bewaren: “notitie: gewicht 75 kg”?",
        "en": "I don't track weight or blood pressure yet, only medication, mood, sleep and water. I can save it as a note: “note: weight 75 kg”.",
        "fr": "Je ne suis pas encore le poids ni la tension, seulement médicaments, humeur, sommeil et eau. Je peux l'enregistrer en note : « note : poids 75 kg ».",
        "de": "Gewicht und Blutdruck verfolge ich noch nicht, nur Medikamente, Stimmung, Schlaf und Wasser. Ich kann es als Notiz speichern: „Notiz: Gewicht 75 kg“.",
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
        "pt": "Não guardei {cur} (só trabalho em euros): {name}.",
        "nl": "Niet opgeslagen ({cur}, alleen euro's): {name}.",
        "en": "Not saved ({cur}, euros only): {name}.",
        "fr": "Non enregistré ({cur}, euros uniquement) : {name}.",
        "de": "Nicht gespeichert ({cur}, nur Euro): {name}.",
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
        "pt": "Tudo certo, mas não tenho nada esperando confirmação. Quer registrar alguma coisa? Por exemplo: “Mercado 20”.",
        "nl": "Prima! Maar er staat niets open om te bevestigen. Wil je iets vastleggen, zeg het gerust, bijvoorbeeld: “Jumbo 20”.",
        "en": "Sure! But I don't have anything waiting for confirmation. To log something, just tell me, e.g. “Groceries 20”.",
        "fr": "D'accord ! Mais rien n'attend de confirmation. Pour enregistrer quelque chose, dis-le-moi, par ex. « Courses 20 ».",
        "de": "Alles klar! Es wartet aber nichts auf Bestätigung. Zum Erfassen sag mir einfach z. B. „Einkauf 20“.",
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
        "fr": "Annuler",
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
    "button_gone": {
        "pt": "Esse registro já tinha sido apagado.",
        "nl": "Die registratie bestaat niet meer.",
        "en": "That entry no longer exists.",
        "fr": "Cet enregistrement n'existe plus.",
        "de": "Dieser Eintrag existiert nicht mehr.",
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
    "top_categories_title": {
        "pt": "Top categorias — {period_label}",
        "nl": "Top categorieën — {period_label}",
        "en": "Top categories — {period_label}",
        "fr": "Top catégories — {period_label}",
        "de": "Top-Kategorien — {period_label}",
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
            "Muito bem! Treino anotado: {activity}, {duration}. 💪",
            "Treino anotado: {activity}, {duration}.",
            "Registrado: {activity}, {duration}. Bom trabalho!",
        ),
        "nl": (
            "Mooi! Training genoteerd: {activity}, {duration}. 💪",
            "Training genoteerd: {activity}, {duration}.",
            "Vastgelegd: {activity}, {duration}. Goed bezig!",
        ),
        "en": (
            "Nice! Workout noted: {activity}, {duration}. 💪",
            "Workout noted: {activity}, {duration}.",
            "Logged: {activity}, {duration}. Nice work!",
        ),
        "fr": (
            "Bravo ! Séance notée : {activity}, {duration}. 💪",
            "Séance notée : {activity}, {duration}.",
            "Enregistré : {activity}, {duration}. Beau travail !",
        ),
        "de": (
            "Stark! Training notiert: {activity}, {duration}. 💪",
            "Training notiert: {activity}, {duration}.",
            "Eingetragen: {activity}, {duration}. Gut gemacht!",
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
        "pt": "Treinos de {month}: {n} sessões, {km} km, {min} min:",
        "nl": "Trainingen {month} — {n} sessies, {km}km, {min}min:",
        "en": "Workouts {month} — {n} sessions, {km}km, {min}min:",
        "fr": "Entraînements {month} — {n} séances, {km}km, {min}min :",
        "de": "Trainings {month} — {n} Einheiten, {km}km, {min}min:",
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
        "pt": "Você dorme em média *{avg}h* (últimos 7 dias, {n} registros).",
        "nl": "Je slaapt gemiddeld *{avg} uur* (laatste 7 dagen, {n} registraties).",
        "en": "You sleep an average of *{avg}h* (last 7 days, {n} entries).",
        "fr": "Tu dors en moyenne *{avg}h* (7 derniers jours, {n} entrées).",
        "de": "Du schläfst im Schnitt *{avg}h* (letzte 7 Tage, {n} Einträge).",
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
        "pt": "{activity} já está anotado hoje. Uma vez por dia conta.",
        "nl": "{activity} staat vandaag al genoteerd. Eén keer per dag telt.",
        "en": "{activity} is already logged for today. Once a day counts.",
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
        "pt": "Meta cumprida: *{title}*! 🎉",
        "nl": "Doel bereikt: *{title}* \U0001f389",
        "en": "Goal completed: *{title}* \U0001f389",
        "fr": "Objectif atteint : *{title}* \U0001f389",
        "de": "Ziel erreicht: *{title}* \U0001f389",
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
        "pt": " [viagem {dest}: {total} no total]",
        "nl": " [reis {dest}: {total} totaal]",
        "en": " [trip {dest}: {total} total]",
        "fr": " [voyage {dest} : {total} au total]",
        "de": " [Reise {dest}: {total} gesamt]",
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
    month_total = await session.scalar(
        select(sa_func.coalesce(sa_func.sum(Expense.amount), 0)).where(*base)
    )
    return _t("month_total_context", lang, total=_fmt_eur(float(month_total or 0)))


# Whole-word greetings the substring lists above miss ("oi" alone has no space around it).
_FIRST_WORDS = {
    "pt": {"oi", "olá", "ola", "opa", "obrigado", "obrigada", "bomdia"},
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
    if any(
        w in t
        for w in [
            "bonjour",
            "salut",
            "allô",
            "allo",
            "merci",
            "bilan",
            "solde",
            "aide",
            " oui",
            "oui ",
            "dépense",
            "depense",
            "résumé",
        ]
    ):
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
    r"[:\s]+(.+?)\s+(?:às|at|om|à|um|a)\s+(\d{1,2}:\d{2})\b",
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
    """ "lembrete: X às 08:00" (canonical) or a natural "me lembra de X às 8h"."""
    m = _LEMBRETE_RE.search(body)
    if m:
        return m
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

_WIPE_RE = re.compile(
    r"^(?:apaga(?:r)?\s+(?:os\s+)?(?:meus|todos\s+os\s+meus)\s+dados|"
    r"delete\s+(?:all\s+)?my\s+data|erase\s+my\s+data|"
    r"verwijder\s+(?:al\s+)?mijn\s+gegevens|"
    r"supprime(?:r)?\s+(?:toutes\s+)?mes\s+donnees|"
    r"(?:loesche|loeschen|losche|loschen)\s+(?:alle\s+)?meine\s+daten|meine\s+daten\s+(?:loeschen|loschen))"
    r"\s*[.!]*$"
)
_EXPORT_RE = re.compile(
    r"^(?:exporta(?:r)?\s+(?:os\s+)?(?:meus|todos\s+os\s+meus)\s+dados|"
    r"export\s+my\s+data|exporteer\s+mijn\s+gegevens|"
    r"exporte(?:r)?\s+mes\s+donnees|meine\s+daten\s+exportieren)\s*[.!]*$"
)
WIPE_CONFIRM_SECONDS = 600

_AMOUNT_CORRECTION_RE = re.compile(
    # The whole message must be the correction: "errei, foram 42€", "na verdade foram 32".
    # "actually I spent 20 at Lidl" is a new expense and must NOT rewrite the last one.
    r"^(?:errei|foi\s+na\s+verdade|na\s+verdade\s+foi|corrijo|na\s+verdade|"
    r"actually|was\s+actually|c\'?etait|war\s+eigentlich|was\s+eigenlijk)"
    r"\b[^0-9€$£]{0,25}?([€$£]?\s*[0-9]+(?:[.,][0-9]{1,2})?)\s*(?:[€$£]|eur(?:os?)?)?\s*[.!]*$",
    re.IGNORECASE,
)


def _canonical_category(raw: str) -> str | None:
    """Return canonical category for a raw user string, or None if not recognised."""
    lower = raw.lower().strip()
    if lower in _CAT_ALIAS:
        return _CAT_ALIAS[lower]
    if lower in {c for c in _VALID_CATEGORIES if not _CAT_ALIAS.get(lower)}:
        return lower
    return None


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


def _fallback_key(body: str) -> str:
    """Expense hint only when the message looks like an expense (has a number)."""
    return "fallback_no_record" if _NUMBER_RE.search(body) else "fallback_unknown"


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
    r"(?P<activity>meditei|meditated|mediteerde|meditiert|li\b|leste|estudei|fiz yoga|"
    r"bebi água|drank water|fiz pilates|fiz alongamento)\s*(?:hoje|today|vandaag|heute|aujourd'hui)?",
    re.IGNORECASE,
)

# ── M10 — Produtividade keywords ─────────────────────────────────────────────
_NOTE_RE = re.compile(
    r"^(?:nota|note|notitie|notiz|remarque|anotação|anotacao)\s*[:]\s*(.+)",
    re.IGNORECASE,
)
_TASK_RE = re.compile(
    r"^(?:tarefa|task|taak|aufgabe|tâche)(?:\s*:\s*|\s+)(.+)",
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
}
_TASK_DELETE_RE = re.compile(
    r"(?:apaga|apagar|delete|verwijder|supprimer|losch)\s+"
    r"(?:a\s+)?(?:tarefa|task|taak|aufgabe|tache)\s+(.+)",
    re.IGNORECASE,
)

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
        if kw in lower:
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

        budget = parse_budget(body)
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
                f"  {to_local(r.expense_date).strftime('%d/%m')}  {r.merchant or r.category}"
                f" ({r.category})  {_fmt_eur(r.amount)}"
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


def _original_text(message, start: int, end: int) -> str:
    """Slice of what the member typed (NFC, stripped) at the span found in the lower-cased body."""
    original = unicodedata.normalize("NFC", (message.body or "").strip())
    lowered = original.lower()
    if len(lowered) != len(original):  # a character whose lower case changes the length: keep it
        return lowered[start:end].strip()
    return original[start:end].strip()


def _looks_like_bare_amount(body: str) -> bool:
    return bool(_BARE_AMOUNT_RE.match(body))


def _has_zero_or_negative_amount(body: str) -> bool:
    """A message with a bare 0 or a leading-minus amount that the extractors will drop."""
    return bool(_ZERO_OR_NEG_RE.search(body))


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


def _window_range(window, lang: str) -> tuple[datetime, datetime | None, str]:
    """(start, end_exclusive, label) for a parsed ``DateWindow``."""
    start, end = day_start(window.start), (day_start(window.end) if window.end else None)
    if window.kind == "today":
        label = _t("period_today", lang)
    elif window.kind == "yesterday":
        label = _t("period_yesterday", lang)
    elif window.kind == "current_week":
        label = _t("period_current_week", lang)
    elif window.kind == "last_week":
        label = _t("period_last_week", lang)
    elif window.kind == "year":
        label = str(window.start.year)
    else:  # current_month / last_month / month
        label = month_name(window.start, lang, year=True)
    return start, end, label


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
) -> str:
    """Query expenses/income for a period (optionally filtered by category)."""
    lang = member.language or "en"
    if window is not None:
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
        if category:
            return _t(
                "no_records_scope",
                lang,
                category=category_label(category, lang),
                period_label=period_label,
            )
        return _t("no_records_period", lang, period_label=period_label)

    outflows = [e for e in records if e.transaction_type != "income"]
    inflows = [e for e in records if e.transaction_type == "income"]
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
        for e in outflows[:10]:
            date_str = to_local(e.expense_date).strftime("%d/%m")
            name = e.merchant or e.description or category
            lines.append(f"• {date_str} {name}: {_fmt_eur(e.amount)}")
        lines.append(f"\n{_t('category_total', lang, amount=_fmt_eur(total_out))}")
        lines.append(
            _t(
                "transactions_count_one" if len(outflows) == 1 else "transactions_count",
                lang,
                n=len(outflows),
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


async def _build_recent(member: Member, session: AsyncSession, n: int) -> str:
    """The ``n`` most recent transactions (real rows, newest first)."""
    lang = member.language or "en"
    res = await session.execute(
        select(Expense)
        .where(
            Expense.member_id == member.id,
            Expense.transaction_type == "expense",
            Expense.status.in_(SETTLED),
        )
        .order_by(Expense.expense_date.desc(), Expense.created_at.desc())
        .limit(n)
    )
    rows = res.scalars().all()
    if not rows:
        return _t("expense_delete_none", lang)
    lines = [f"{_t('last_expenses_title', lang, n=len(rows))}\n"]
    for e in rows:
        name = e.merchant or e.description or category_label(e.category, lang)
        day = to_local(e.expense_date).strftime("%d/%m")
        lines.append(f"• {day} {name}: {_fmt_eur(e.amount)}")
    return "\n".join(lines)


async def _build_saldo(member: Member, session: AsyncSession) -> str:
    """Show income/expense balance for the current month."""
    lang = member.language or "en"
    now = now_local()
    start = day_start(month_start(now.date()))

    result = await session.execute(
        select(Expense).where(
            Expense.member_id == member.id,
            Expense.status.in_(SETTLED),
            Expense.expense_date >= start,
        )
    )
    records = result.scalars().all()

    if not records:
        return _t("no_records_month", lang)

    total_in = _cents(sum(e.amount for e in records if e.transaction_type == "income"))
    total_out = _cents(sum(e.amount for e in records if e.transaction_type != "income"))
    balance = _cents(total_in - total_out)
    sign = "+" if balance >= 0 else "-"

    month_label = month_name(now.date(), lang, year=True)
    lines = [
        f"{_t('saldo_title', lang, month=month_label)}\n",
        _t("saldo_income", lang, amount=_fmt_eur(total_in)),
        _t("saldo_expenses", lang, amount=_fmt_eur(total_out)),
        _t("saldo_balance", lang, sign=sign, amount=_fmt_eur(abs(balance))),
    ]
    from alfred.ledger_status import pending_summary

    pend = await pending_summary(session, member.id)
    if pend.to_pay or pend.to_receive:
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
    if interactive.get("type") != "button_reply":
        return False
    button_id = str((interactive.get("button_reply") or {}).get("id") or "")
    action, _, raw_id = button_id.partition(":")
    to = member.wa_phone

    if action == "keep":
        reply = _t("wipe_cancelled", lang)
        await send_text(to, reply)
        await _save_outbound(member, reply, session)
        return True
    if action == "wipe":
        try:
            fresh = 0 <= int(datetime.now(UTC).timestamp()) - int(raw_id) <= WIPE_CONFIRM_SECONDS
        except ValueError:
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

    if action in ("plan_ok", "plan_cancel"):  # V2-35 training plan preview
        plan_out = await handle_training_button(action, raw_id, member, lang, session)
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
    elif action == "edit":
        reply = _t("button_edit_hint", lang)
    else:
        reply = _t("button_ok_reply", lang)
    await send_text(to, reply)
    await _save_outbound(member, reply, session)
    return True


async def handle_inbound(
    member: Member,
    message: Message,
    session: AsyncSession,
) -> None:
    """Decide what to reply based on consent state and message content."""
    # M6 — WhatsApp Flow onboarding: intercept nfm_reply before consent checks
    if await handle_flow_onboarding(member, message, session):
        return

    to = member.wa_phone
    body = unicodedata.normalize("NFC", (message.body or "").strip()).lower()
    # Accent-free twin (same length as ``body``): command regexes are written without
    # accents so "está"/"água"/"medicação" match; slice captured text from ``body``.
    body_plain = strip_accents(body)

    # ── 1. First contact: detect language, send disclosure ───────────────────
    if member.consent_state == "pending":
        lang = _detect_language(body)
        if lang is None:  # nothing shows the language: ask, and let the answer rule chat and panel
            member.consent_state = "pending_language"
            session.add(member)
            await send_text(to, _t("language_ask", "en"))
            logger.info("conversation.language_asked", member_id=str(member.id))
            return
        member.language = lang
        session.add(member)
        disclosure = _t("disclosure", lang)
        await send_text(to, disclosure)
        member.consent_state = "pending_response"
        logger.info("conversation.disclosure_sent", member_id=str(member.id), lang=lang)
        return

    # ── 1b. Answer to "which language?" ──────────────────────────────────────
    if member.consent_state == "pending_language":
        chosen = parse_language_choice(body)
        if chosen is None:
            await send_text(to, _t("language_ask", "en"))
            return
        member.language = chosen
        member.consent_state = "pending_response"
        session.add(member)
        await send_text(to, _t("disclosure", chosen))
        logger.info("conversation.language_chosen", member_id=str(member.id), lang=chosen)
        return

    lang = member.language or "en"

    # ── 2. Awaiting consent response ─────────────────────────────────────────
    if member.consent_state == "pending_response":
        if body in _CONSENT_YES:
            member.consent_state = "accepted"
            member.disclosure_accepted_at = datetime.now(UTC)
            member.disclosure_version = "1.0"
            session.add(member)
            reply = _t("consent_accepted", lang)
            await send_text(to, reply)
            await _save_outbound(member, reply, session)
            audit(session, "consent_accepted", member.id)
            logger.info("conversation.consent_accepted", member_id=str(member.id))
        elif body in _CONSENT_NO:
            member.consent_state = "rejected"
            session.add(member)
            reply = _t("consent_rejected", lang)
            await send_text(to, reply)
            logger.info("conversation.consent_rejected", member_id=str(member.id))
        else:
            await send_text(to, _t("consent_unknown", lang))
        return

    # ── 3. Rejected — honour their choice ────────────────────────────────────
    if member.consent_state == "rejected":
        if body in _RESUME_WORDS:
            member.consent_state = "pending_response"
            session.add(member)
            await send_text(to, _t("disclosure", lang))
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
        new_lang = parse_language_command(body)
        if new_lang is not None:
            member.language = new_lang
            session.add(member)
            reply = _t("lang_changed", new_lang)
            await send_text(to, reply)
            await _save_outbound(member, reply, session)
            return

        # 4-0a. A bare "yes": there is no pending question, so say so instead of guessing.
        if _BARE_YES_RE.match(body_plain.strip()):
            reply = _t("bare_yes", lang)
            await send_text(to, reply)
            await _save_outbound(member, reply, session)
            return

        # 4-0b. GDPR: "apagar meus dados" (two steps, buttons) / "exportar meus dados"
        if _WIPE_RE.match(body_plain.strip()):
            now_epoch = int(datetime.now(UTC).timestamp())
            await send_buttons(
                to,
                _t("wipe_ask", lang),
                [(f"wipe:{now_epoch}", _t("btn_wipe", lang)), ("keep:0", _t("btn_cancel", lang))],
            )
            await _save_outbound(member, _t("wipe_ask", lang), session)
            return
        if _EXPORT_RE.match(body_plain.strip()):
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
            return

        # M12 — load member's merchant→category overrides once per message
        member_overrides = await _load_merchant_overrides(member, session)

        # 4a. Stop — re-enter rejected state
        if body in _STOP_WORDS:
            member.consent_state = "rejected"
            session.add(member)
            reply = _t("consent_rejected", lang)
            await send_text(to, reply)
            logger.info("conversation.stop_requested", member_id=str(member.id))
            return

        # 4b. Saldo command
        if _is_command(body, _SALDO_WORDS):
            saldo = await _build_saldo(member, session)
            await send_text(to, saldo)
            await _save_outbound(member, saldo, session)
            return

        # 4c. Help command
        if _is_command(body, _HELP_WORDS):
            help_text = _t("help", lang)
            await send_text(to, help_text)
            await _save_outbound(member, help_text, session)
            return

        # 4d. Summary command — skip if message has a time qualifier (let classify_query handle it)
        if _is_bare_command(body, _SUMMARY_WORDS):
            summary = await _build_summary(member, session)
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
            matched = None
            for j in jobs:
                text_label = (j.payload or {}).get("text", j.job_type).lower()
                if cancel_kw in text_label or text_label in cancel_kw:
                    matched = j
                    break
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
            await send_text(to, budget_reply)
            await _save_outbound(member, budget_reply, session)
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

        # 4e-0o. V2-18 — "o que você me enviou hoje" / "lembretes que mandou"
        outbox_reply = await handle_outbox_command(body_plain, member, lang, session)
        if outbox_reply is not None:
            await send_text(to, outbox_reply)
            await _save_outbound(member, outbox_reply, session)
            return

        # 4e-0n. V2-16 — "luz 80 a pagar dia 5" / "paguei a luz" / "o que tenho a pagar"
        ledger_reply = await handle_ledger_command(body, body_plain, member, lang, session)
        if ledger_reply is not None:
            await send_text(to, ledger_reply)
            await _save_outbound(member, ledger_reply, session)
            return

        # 4e-0g. V2-02 — contas fixas: "aluguel 1200 todo dia 1" / "paguei o aluguel" / "minhas contas fixas"
        recurring_reply = await handle_recurring_command(
            body_plain=body_plain, body=body, member=member, lang=lang, session=session
        )
        if recurring_reply is not None:
            await send_text(to, recurring_reply)
            await _save_outbound(member, recurring_reply, session)
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
                    due_str = _t("tasks_list_due", lang, date=str(t.due_date)) if t.due_date else ""
                    is_overdue = bool(t.due_date and t.due_date < today_date)
                    tpl_key = "tasks_list_overdue" if is_overdue else "tasks_list_row"
                    lines.append(_t(tpl_key, lang, n=i, body=t.body, due=due_str))
                reply = "\n".join(lines)
            await send_text(to, reply)
            await _save_outbound(member, reply, session)
            return

        # 4e-5b. M10 — delete task: "apaga tarefa X"
        m_tdel = _TASK_DELETE_RE.match(body_plain)
        if m_tdel:
            del_kw = _grp(body, m_tdel).strip()
            from sqlalchemy import select as _sel

            res_del = await session.execute(
                _sel(Task)
                .where(Task.member_id == member.id)
                .order_by(Task.done_at.is_(None).desc(), Task.created_at.desc())
            )
            del_task = next(
                (t for t in res_del.scalars().all() if _fuzzy_contains(del_kw, t.body)), None
            )
            if del_task:
                del_body = del_task.body
                await session.delete(del_task)
                reply = _t("task_deleted", lang, body=del_body)
            else:
                reply = _t("task_delete_not_found", lang)
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

        # 4e-5d. M11 — dashboard link: "meu dashboard"
        if _is_command(body, _DASHBOARD_WORDS):
            import uuid as _uuid

            from alfred.dashboard import ensure_dashboard_token
            from alfred.settings import settings as _settings

            # Missing or expired (TTL) → issue a fresh one; the old link stops working.
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
                reply = _t("dashboard_cta", lang)
                await send_cta_url(to, reply, _t("btn_open_panel", lang), url)
                reply = _t("dashboard_link", lang, url=url)  # what the history keeps
            await _save_outbound(member, reply, session)
            return

        # 4e-6. M9 — criar meta: "meta: quero X"
        m_goal = _GOAL_CREATE_RE.match(body)
        if m_goal:
            goal_title = m_goal.group(1).strip()
            goal = Goal(id=uuid.uuid4(), member_id=member.id, title=goal_title, active=True)
            session.add(goal)
            reply = _t("goal_created", lang, title=goal_title)
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
                lines = [_t("goals_list_header", lang, n=len(active_goals))]
                for g in active_goals:
                    lines.append(_t("goals_list_row", lang, title=g.title))
                reply = "\n".join(lines)
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
                from datetime import date as _date2
                from datetime import timedelta as _td2

                today = _date2.today()
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

        # 4e-8. M9 — log de hábito: "meditei hoje" (regex fast-path)
        m_habit = None if body.rstrip().endswith("?") else _HABIT_LOG_RE.search(body)
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
                )
            else:
                reply = _t("habit_logged", lang, activity=habit_activity)
            await send_text(to, reply)
            await _save_outbound(member, reply, session)
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
            and _not_expense
            and _not_workout
            and _not_health
            and not _has_zero_or_negative_amount(body)  # "café 0" is an invalid amount, not a habit
            and not _looks_like_bare_amount(body)  # "cinema 24" is an expense, not a habit
            and not body.rstrip().endswith("?")  # a question is not a log
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
                    )
                else:
                    reply = _t("habit_logged", lang, activity=h_activity)
                await send_text(to, reply)
                await _save_outbound(member, reply, session)
                logger.info(
                    "conversation.habit_recorded_llm", member_id=str(member.id), activity=h_activity
                )
                return

        # 4e-9. M8 — health log: medicação, humor, sono, água
        if _is_health and _UNSUPPORTED_HEALTH_RE.search(body_plain):
            reply = _t("health_unsupported", lang)
            await send_text(to, reply)
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
                    reply = _t("health_saved_mood", lang, value=val)
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
                reply = _t("health_sleep_avg", lang, avg=_num(avg, lang), n=len(sleep_rows))
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
                reply = _t(
                    "workout_saved",
                    lang,
                    activity=activity_name(workout_data["activity_type"], lang),
                    duration=dur_str,
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
                            date=ws_m.workout_date.strftime("%d/%m"),
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
            else:
                name = last.merchant or last.description or category_label(last.category, lang)
                reply = _t(
                    "expense_deleted",
                    lang,
                    amount=_fmt_eur(last.amount),
                    name=name,
                    date=to_local(last.expense_date).strftime("%d/%m"),
                )
                await session.delete(last)
                audit(session, "expense_deleted", member.id)
                logger.info("conversation.expense_deleted", member_id=str(member.id))
            await send_text(to, reply)
            await _save_outbound(member, reply, session)
            return

        n_last = parse_last_n(body_plain)
        if n_last:
            reply = await _build_recent(member, session, n_last)
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
        if m_amt:
            raw_amt = (
                m_amt.group(1)
                .replace("€", "")
                .replace("$", "")
                .replace("£", "")
                .replace(",", ".")
                .strip()
            )
            try:
                new_amount = float(raw_amt)
            except ValueError:
                new_amount = None
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
                    name = last_exp.merchant or category_label(last_exp.category, lang)
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

        # 4e-0e. Several transactions in one message: "mercado 20 e farmácia 10".
        if _looks_multi(body):
            items = await extract_expenses_multi(message.body or "", lang=member.language or "en")
            if len(items) >= 2:
                skipped: list[str] = []
                usable: list[dict] = []
                for item in items:
                    if item["currency"] != "EUR":
                        skipped.append(
                            _t(
                                "multi_skipped_currency",
                                lang,
                                cur=item["currency"],
                                name=item["merchant"] or item["description"] or item["category"],
                            )
                        )
                    else:
                        usable.append(item)
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

        expense_data = await extract_expense(
            message.body or "", merchant_overrides=member_overrides, lang=member.language or "en"
        )
        if expense_data and expense_data["currency"] != "EUR":
            reply = _t("currency_unsupported", lang, cur=expense_data["currency"])
            await send_text(to, reply)
            await _save_outbound(member, reply, session)
            return
        if expense_data:
            txn_type = expense_data.get("type", "expense")
            days_ago = expense_data.get("days_ago", 0)
            expense_date = now_local() - timedelta(days=days_ago)

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
            )
            session.add(expense)

            name = (
                expense_data["merchant"] or expense_data["description"] or expense_data["category"]
            )
            amt_fmt = _fmt_eur(expense_data["amount"])
            key = "income_recorded" if txn_type == "income" else "expense_recorded"
            reply = _t(key, lang, amount=amt_fmt, name=name)
            if shared_now:
                reply += _t("home_auto_suffix", lang)
            if days_ago > 0:
                reply += _t("days_ago_suffix", lang, n=days_ago)
            if txn_type == "expense":
                await session.flush()  # make the new row visible to the month total below
                reply += await _month_category_context(
                    session, member, expense_date, expense_data.get("category"), lang
                )
                reply += await alert_after_expense(
                    session, member, expense_data.get("category"), expense_date, lang
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
                if active_trip.budget:
                    reply = reply.rstrip("]") + (
                        _budget_remaining(active_trip.budget, trip_total, lang) + "]"
                    )
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

        # 4f. Try to classify as a financial query
        query = await classify_query(message.body or "")
        if query:
            qtype = query.get("query_type")
            period = query.get("period") or "current_month"
            category = query.get("category")
            # Dates are parsed exactly here; the LLM only guesses today/week/month.
            window = parse_period(body_plain, today_local())

            if qtype == "balance":
                reply = await _build_saldo(member, session)
            elif qtype == "category" and category:
                reply = await _build_summary(
                    member, session, period=period, category=category, window=window
                )
            elif qtype == "period":
                reply = await _build_summary(member, session, period=period, window=window)
            elif qtype == "comparison":
                reply = await _build_comparison(member, session)
            else:
                reply = None

            if reply:
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
            await send_text(to, analysis_reply)
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
        if _UNSUPPORTED_HEALTH_RE.search(body_plain):
            # Weight / blood pressure are not tracked: say so instead of letting the model "record" them.
            reply = _t("health_unsupported", lang)
            await send_text(to, reply)
            await _save_outbound(member, reply, session)
            return
        history = await _load_history(member, session, exclude_id=message.id)
        reply = await generate_reply(member, message, history=history)  # noqa: F821
        if _claims_recorded(reply):
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
        await send_text(to, reply)
        await _save_outbound(member, reply, session)
        logger.info("conversation.reply_sent", member_id=str(member.id))
        return
