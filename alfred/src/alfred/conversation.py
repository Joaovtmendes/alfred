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

import re
import unicodedata

import uuid
from datetime import datetime, timedelta, timezone

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from alfred.llm import classify_query, extract_expense, extract_habit, extract_health_log, extract_workout, generate_reply
from alfred.models import (
    Expense,
    Goal,
    HabitLog,
    HealthLog,
    Member,
    MerchantCategoryOverride,
    Message,
    Note,
    Task,
    WorkoutSession,
)
from alfred.whatsapp import send_text

logger = structlog.get_logger()

# ── i18n ─────────────────────────────────────────────────────────────────────

_SUPPORTED_LANGS = ("pt", "nl", "en", "fr", "de")

_STRINGS: dict[str, dict[str, str]] = {
    "disclosure": {
        "pt": (
            "*Alfred* — assistente pessoal via WhatsApp.\n\n"
            "Sou uma inteligência artificial, não uma pessoa. "
            "As tuas mensagens são processadas para te dar suporte.\n\n"
            "Escreve *sim* para continuar ou *não* para cancelar."
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
            "Schreib *ja* um fortzufahren oder *nein* zum Abbrechen."
        ),
    },
    "consent_accepted": {
        "pt": (
            "Tudo pronto. Podes começar agora.\n\n"
            "• \"gastei €45 no Jumbo\" — registar despesa\n"
            "• \"recebi €2.800 de salário\" — registar receita\n"
            "• \"resumo\" — ver os gastos do mês\n"
            "• \"ajuda\" — ver todos os comandos"
        ),
        "nl": (
            "Alles klaar. Je kunt nu beginnen.\n\n"
            "• \"€45 uitgegeven bij Jumbo\" — uitgave registreren\n"
            "• \"€2.800 salaris ontvangen\" — inkomsten registreren\n"
            "• \"overzicht\" — uitgaven van de maand bekijken\n"
            "• \"hulp\" — alle commando's bekijken"
        ),
        "en": (
            "All set. You can start now.\n\n"
            "• \"spent €45 at Jumbo\" — record expense\n"
            "• \"received €2,800 salary\" — record income\n"
            "• \"summary\" — view monthly expenses\n"
            "• \"help\" — see all commands"
        ),
        "fr": (
            "Tout est prêt. Tu peux commencer maintenant.\n\n"
            "• \"dépensé €45 au Jumbo\" — enregistrer une dépense\n"
            "• \"reçu €2 800 de salaire\" — enregistrer un revenu\n"
            "• \"résumé\" — voir les dépenses du mois\n"
            "• \"aide\" — voir toutes les commandes"
        ),
        "de": (
            "Alles bereit. Du kannst jetzt beginnen.\n\n"
            "• \"€45 bei Jumbo ausgegeben\" — Ausgabe erfassen\n"
            "• \"€2.800 Gehalt erhalten\" — Einnahme erfassen\n"
            "• \"übersicht\" — Monatsausgaben anzeigen\n"
            "• \"hilfe\" — alle Befehle anzeigen"
        ),
    },
    "consent_rejected": {
        "pt": "Entendido. Se quiseres retomar, é só enviar uma mensagem.",
        "nl": "Begrepen. Als je wilt hervatten, stuur gewoon een bericht.",
        "en": "Understood. If you'd like to resume, just send a message.",
        "fr": "Compris. Si tu veux reprendre, envoie simplement un message.",
        "de": "Verstanden. Wenn du fortfahren möchtest, sende einfach eine Nachricht.",
    },
    "consent_unknown": {
        "pt": "Responde *sim* para continuar ou *não* para cancelar.",
        "nl": "Antwoord *ja* om door te gaan of *nee* om te annuleren.",
        "en": "Reply *yes* to continue or *no* to cancel.",
        "fr": "Réponds *oui* pour continuer ou *non* pour annuler.",
        "de": "Antworte *ja* um fortzufahren oder *nein* zum Abbrechen.",
    },
    "help": {
        "pt": (
            "*Alfred* — o que posso fazer por ti:\n\n"
            "*Registar despesas*\n"
            "• \"gastei €45 no Jumbo\"\n"
            "• \"Uber 12,50\"\n"
            "• \"paguei €180 de renda\"\n\n"
            "*Registar receitas*\n"
            "• \"recebi €2.800 de salário\"\n\n"
            "*Consultas*\n"
            "• \"resumo\" — gastos do mês\n"
            "• \"saldo\" — balanço receitas/despesas\n"
            "• \"gastos desta semana\" — por período\n"
            "• \"compara este mês com o mês passado\"\n"
            "• \"ajuda\" — esta mensagem\n\n"
            "_Para sair: \"stop\"_"
        ),
        "nl": (
            "*Alfred* — wat ik voor je kan doen:\n\n"
            "*Uitgaven registreren*\n"
            "• \"€45 uitgegeven bij Jumbo\"\n"
            "• \"Uber 12,50\"\n"
            "• \"€180 huur betaald\"\n\n"
            "*Inkomsten registreren*\n"
            "• \"€2.800 salaris ontvangen\"\n\n"
            "*Opvragen*\n"
            "• \"overzicht\" — uitgaven van de maand\n"
            "• \"saldo\" — inkomsten/uitgaven balans\n"
            "• \"uitgaven deze week\" — per periode\n"
            "• \"vergelijk deze maand met vorige maand\"\n"
            "• \"hulp\" — dit bericht\n\n"
            "_Om te stoppen: \"stoppen\"_"
        ),
        "en": (
            "*Alfred* — what I can do for you:\n\n"
            "*Record expenses*\n"
            "• \"spent €45 at Jumbo\"\n"
            "• \"Uber 12.50\"\n"
            "• \"paid €180 rent\"\n\n"
            "*Record income*\n"
            "• \"received €2,800 salary\"\n\n"
            "*Queries*\n"
            "• \"summary\" — monthly expenses\n"
            "• \"balance\" — income/expense balance\n"
            "• \"expenses this week\" — by period\n"
            "• \"compare this month with last month\"\n"
            "• \"help\" — this message\n\n"
            "_To stop: \"stop\"_"
        ),
        "fr": (
            "*Alfred* — ce que je peux faire pour toi:\n\n"
            "*Enregistrer des dépenses*\n"
            "• \"dépensé €45 au Jumbo\"\n"
            "• \"Uber 12,50\"\n"
            "• \"payé €180 de loyer\"\n\n"
            "*Enregistrer des revenus*\n"
            "• \"reçu €2 800 de salaire\"\n\n"
            "*Consultes*\n"
            "• \"résumé\" — dépenses du mois\n"
            "• \"solde\" — balance revenus/dépenses\n"
            "• \"dépenses cette semaine\" — par période\n"
            "• \"compare ce mois avec le mois dernier\"\n"
            "• \"aide\" — ce message\n\n"
            "_Pour arrêter: \"stop\"_"
        ),
        "de": (
            "*Alfred* — was ich für dich tun kann:\n\n"
            "*Ausgaben erfassen*\n"
            "• \"€45 bei Jumbo ausgegeben\"\n"
            "• \"Uber 12,50\"\n"
            "• \"€180 Miete bezahlt\"\n\n"
            "*Einnahmen erfassen*\n"
            "• \"€2.800 Gehalt erhalten\"\n\n"
            "*Abfragen*\n"
            "• \"übersicht\" — Monatsausgaben\n"
            "• \"bilanz\" — Einnahmen/Ausgaben-Balance\n"
            "• \"ausgaben diese woche\" — nach Zeitraum\n"
            "• \"vergleiche diesen monat mit letztem monat\"\n"
            "• \"hilfe\" — diese Nachricht\n\n"
            "_Zum Beenden: \"stop\"_"
        ),
    },
    "no_records_scope": {
        "pt": "Sem registos em *{category}* em {period_label}.",
        "nl": "Geen registraties in *{category}* in {period_label}.",
        "en": "No records in *{category}* in {period_label}.",
        "fr": "Aucun enregistrement dans *{category}* en {period_label}.",
        "de": "Keine Einträge in *{category}* in {period_label}.",
    },
    "no_records_period": {
        "pt": "Sem registos em {period_label}.",
        "nl": "Geen registraties in {period_label}.",
        "en": "No records in {period_label}.",
        "fr": "Aucun enregistrement en {period_label}.",
        "de": "Keine Einträge in {period_label}.",
    },
    "no_records_month": {
        "pt": "Sem registos este mês.",
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
        "fr": "*Total dépenses: {amount}*",
        "de": "*Gesamtausgaben: {amount}*",
    },
    "category_total": {
        "pt": "*Total: {amount}*",
        "nl": "*Totaal: {amount}*",
        "en": "*Total: {amount}*",
        "fr": "*Total: {amount}*",
        "de": "*Gesamt: {amount}*",
    },
    "income_line": {
        "pt": "Receitas: {amount}",
        "nl": "Inkomsten: {amount}",
        "en": "Income: {amount}",
        "fr": "Revenus: {amount}",
        "de": "Einnahmen: {amount}",
    },
    "balance_line": {
        "pt": "_Saldo: {sign}{amount}_",
        "nl": "_Saldo: {sign}{amount}_",
        "en": "_Balance: {sign}{amount}_",
        "fr": "_Solde: {sign}{amount}_",
        "de": "_Bilanz: {sign}{amount}_",
    },
    "transactions_count": {
        "pt": "_{n} transação(ões)_",
        "nl": "_{n} transactie(s)_",
        "en": "_{n} transaction(s)_",
        "fr": "_{n} transaction(s)_",
        "de": "_{n} Transaktion(en)_",
    },
    "saldo_title": {
        "pt": "*Saldo — {month}*",
        "nl": "*Saldo — {month}*",
        "en": "*Balance — {month}*",
        "fr": "*Solde — {month}*",
        "de": "*Bilanz — {month}*",
    },
    "saldo_income": {
        "pt": "• Receitas: {amount}",
        "nl": "• Inkomsten: {amount}",
        "en": "• Income: {amount}",
        "fr": "• Revenus: {amount}",
        "de": "• Einnahmen: {amount}",
    },
    "saldo_expenses": {
        "pt": "• Despesas: {amount}",
        "nl": "• Uitgaven: {amount}",
        "en": "• Expenses: {amount}",
        "fr": "• Dépenses: {amount}",
        "de": "• Ausgaben: {amount}",
    },
    "saldo_balance": {
        "pt": "\n*Saldo: {sign}{amount}*",
        "nl": "\n*Saldo: {sign}{amount}*",
        "en": "\n*Balance: {sign}{amount}*",
        "fr": "\n*Solde: {sign}{amount}*",
        "de": "\n*Bilanz: {sign}{amount}*",
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
        "pt": "Despesa registada — {amount} em *{name}*",
        "nl": "Uitgave geregistreerd — {amount} bij *{name}*",
        "en": "Expense recorded — {amount} at *{name}*",
        "fr": "Dépense enregistrée — {amount} chez *{name}*",
        "de": "Ausgabe erfasst — {amount} bei *{name}*",
    },
    "income_recorded": {
        "pt": "Receita registada — {amount} de *{name}*",
        "nl": "Inkomsten geregistreerd — {amount} van *{name}*",
        "en": "Income recorded — {amount} from *{name}*",
        "fr": "Revenu enregistré — {amount} de *{name}*",
        "de": "Einnahme erfasst — {amount} von *{name}*",
    },
    "category_corrected": {
        "pt": "✓ Percebido! {merchant} → *{category}*. Vou lembrar para a próxima.",
        "nl": "✓ Begrepen! {merchant} → *{category}*. Ik onthoud dit voor de volgende keer.",
        "en": "✓ Got it! {merchant} → *{category}*. I'll remember that.",
        "fr": "✓ Compris ! {merchant} → *{category}*. Je m'en souviendrai.",
        "de": "✓ Verstanden! {merchant} → *{category}*. Das merke ich mir.",
    },
    "category_corrected_no_merchant": {
        "pt": "Não encontrei nenhuma despesa recente com esse comerciante para corrigir. Podes registar novamente?",
        "nl": "Ik vond geen recente uitgave van die merchant om te corrigeren. Kun je het opnieuw invoeren?",
        "en": "I couldn't find a recent expense from that merchant to correct. Can you re-enter it?",
        "fr": "Je n'ai pas trouvé de dépense récente de ce marchand à corriger. Peux-tu la re-saisir ?",
        "de": "Ich fand keine aktuelle Ausgabe von diesem Händler zum Korrigieren. Kannst du sie erneut eingeben?",
    },
    "lembrete_set": {
        "pt": "⏰ Lembrete configurado: *{text}* às {time} UTC. Vou lembrar-te todos os dias.",
        "nl": "⏰ Herinnering ingesteld: *{text}* om {time} UTC. Ik herinner je elke dag.",
        "en": "⏰ Reminder set: *{text}* at {time} UTC. I'll remind you every day.",
        "fr": "⏰ Rappel configuré : *{text}* à {time} UTC. Je te rappellerai chaque jour.",
        "de": "⏰ Erinnerung eingestellt: *{text}* um {time} Uhr UTC. Ich erinnere dich täglich.",
    },
    "lembrete_invalid": {
        "pt": "Formato inválido. Tenta: *configura lembrete: toma medicamento às 08:00*",
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
        "pt": "Treino registado: {activity} — {duration}",
        "nl": "Training opgeslagen: {activity} — {duration}",
        "en": "Workout logged: {activity} — {duration}",
        "fr": "Entraînement enregistré : {activity} — {duration}",
        "de": "Training gespeichert: {activity} — {duration}",
    },
    "workout_summary_header": {
        "pt": "Treinos desta semana ({n} sessoes):",
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
        "pt": "Nenhum treino registado esta semana.",
        "nl": "Geen trainingen geregistreerd deze week.",
        "en": "No workouts logged this week.",
        "fr": "Aucun entraînement enregistré cette semaine.",
        "de": "Kein Training diese Woche eingetragen.",
    },
    # ── M8 — Saúde ───────────────────────────────────────────────────────────
    "health_saved_medication": {
        "pt": "Medicacao registada: {value}",
        "nl": "Medicatie gelogd: {value}",
        "en": "Medication logged: {value}",
        "fr": "Médicament enregistré : {value}",
        "de": "Medikament eingetragen: {value}",
    },
    "health_saved_mood": {
        "pt": "Humor registado: {value}/10",
        "nl": "Stemming gelogd: {value}/10",
        "en": "Mood logged: {value}/10",
        "fr": "Humeur enregistrée : {value}/10",
        "de": "Stimmung eingetragen: {value}/10",
    },
    "health_saved_sleep": {
        "pt": "Sono registado: {value}h",
        "nl": "Slaap gelogd: {value}h",
        "en": "Sleep logged: {value}h",
        "fr": "Sommeil enregistré : {value}h",
        "de": "Schlaf eingetragen: {value}h",
    },
    "health_saved_water": {
        "pt": "Agua registada: {value}L",
        "nl": "Water gelogd: {value}L",
        "en": "Water logged: {value}L",
        "fr": "Eau enregistrée : {value}L",
        "de": "Wasser eingetragen: {value}L",
    },
    # ── M9 — Metas & Hábitos ─────────────────────────────────────────────────
    "goal_created": {
        "pt": "Meta criada: *{title}*",
        "nl": "Doel aangemaakt: *{title}*",
        "en": "Goal created: *{title}*",
        "fr": "Objectif créé : *{title}*",
        "de": "Ziel erstellt: *{title}*",
    },
    "habit_logged": {
        "pt": "Habito registado: {activity}",
        "nl": "Gewoonte gelogd: {activity}",
        "en": "Habit logged: {activity}",
        "fr": "Habitude enregistrée : {activity}",
        "de": "Gewohnheit eingetragen: {activity}",
    },
    "goals_list_header": {
        "pt": "As tuas metas activas ({n}):",
        "nl": "Jouw actieve doelen ({n}):",
        "en": "Your active goals ({n}):",
        "fr": "Tes objectifs actifs ({n}) :",
        "de": "Deine aktiven Ziele ({n}):",
    },
    "goals_list_empty": {
        "pt": "Ainda nao tens metas. Cria uma com: *meta: quero X*",
        "nl": "Nog geen doelen. Maak er een met: *doel: ik wil X*",
        "en": "No goals yet. Create one with: *goal: I want to X*",
        "fr": "Pas encore d'objectifs. Crée-en un avec : *objectif : je veux X*",
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
        "pt": "Nota guardada.",
        "nl": "Notitie opgeslagen.",
        "en": "Note saved.",
        "fr": "Note enregistrée.",
        "de": "Notiz gespeichert.",
    },
    "task_saved": {
        "pt": "Tarefa adicionada: *{body}*",
        "nl": "Taak toegevoegd: *{body}*",
        "en": "Task added: *{body}*",
        "fr": "Tâche ajoutée : *{body}*",
        "de": "Aufgabe hinzugefügt: *{body}*",
    },
    "task_done": {
        "pt": "Tarefa concluida.",
        "nl": "Taak afgerond.",
        "en": "Task done.",
        "fr": "Tâche terminée.",
        "de": "Aufgabe erledigt.",
    },
    "task_not_found": {
        "pt": "Nao encontrei essa tarefa em aberto.",
        "nl": "Ik kon die openstaande taak niet vinden.",
        "en": "I couldn't find that open task.",
        "fr": "Je n'ai pas trouvé cette tâche ouverte.",
        "de": "Ich konnte diese offene Aufgabe nicht finden.",
    },
    "tasks_list_header": {
        "pt": "As tuas tarefas em aberto ({n}):",
        "nl": "Jouw openstaande taken ({n}):",
        "en": "Your open tasks ({n}):",
        "fr": "Tes tâches ouvertes ({n}) :",
        "de": "Deine offenen Aufgaben ({n}):",
    },
    "tasks_list_empty": {
        "pt": "Nao tens tarefas em aberto.",
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
        "pt": "Os teus lembretes activos ({n}):",
        "nl": "Jouw actieve herinneringen ({n}):",
        "en": "Your active reminders ({n}):",
        "fr": "Tes rappels actifs ({n}) :",
        "de": "Deine aktiven Erinnerungen ({n}):",
    },
    "lembretes_list_empty": {
        "pt": "Nao tens lembretes activos.",
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
        "fr": "Rappel annule : *{text}*",
        "de": "Erinnerung abgebrochen: *{text}*",
    },
    "lembrete_cancel_not_found": {
        "pt": "Nao encontrei esse lembrete activo.",
        "nl": "Ik kon die actieve herinnering niet vinden.",
        "en": "I couldn't find that active reminder.",
        "fr": "Je n'ai pas trouve ce rappel actif.",
        "de": "Ich konnte diese aktive Erinnerung nicht finden.",
    },
    # ── M7 — Treino extras ────────────────────────────────────────────────────
    "workout_deleted": {
        "pt": "Treino apagado.",
        "nl": "Training verwijderd.",
        "en": "Workout deleted.",
        "fr": "Entrainement supprime.",
        "de": "Training geloscht.",
    },
    "workout_delete_not_found": {
        "pt": "Nao encontrei treino recente para apagar.",
        "nl": "Geen recente training gevonden om te verwijderen.",
        "en": "No recent workout found to delete.",
        "fr": "Aucun entrainement recent trouve a supprimer.",
        "de": "Kein aktuelles Training zum Loschen gefunden.",
    },
    "workout_activity_summary": {
        "pt": "Fizeste *{activity}* {n}x nos ultimos 7 dias.",
        "nl": "Je deed *{activity}* {n}x in de afgelopen 7 dagen.",
        "en": "You did *{activity}* {n}x in the last 7 days.",
        "fr": "Tu as fait *{activity}* {n}x ces 7 derniers jours.",
        "de": "Du hast *{activity}* {n}x in den letzten 7 Tagen gemacht.",
    },
    "workout_month_header": {
        "pt": "Treinos de {month} — {n} sessoes, {km}km, {min}min:",
        "nl": "Trainingen {month} — {n} sessies, {km}km, {min}min:",
        "en": "Workouts {month} — {n} sessions, {km}km, {min}min:",
        "fr": "Entrainements {month} — {n} seances, {km}km, {min}min :",
        "de": "Trainings {month} — {n} Einheiten, {km}km, {min}min:",
    },
    # ── M8 — Saude queries ────────────────────────────────────────────────────
    "health_mood_history": {
        "pt": "Humor esta semana: {entries}",
        "nl": "Stemming deze week: {entries}",
        "en": "Mood this week: {entries}",
        "fr": "Humeur cette semaine : {entries}",
        "de": "Stimmung diese Woche: {entries}",
    },
    "health_mood_empty": {
        "pt": "Sem registos de humor esta semana.",
        "nl": "Geen stemmingsregistraties deze week.",
        "en": "No mood entries this week.",
        "fr": "Pas d'entrees d'humeur cette semaine.",
        "de": "Keine Stimmungseintraege diese Woche.",
    },
    "health_sleep_avg": {
        "pt": "Dormes em media *{avg}h* (ultimos 7 dias, {n} registos).",
        "nl": "Je slaapt gemiddeld *{avg}u* (laatste 7 dagen, {n} registraties).",
        "en": "You sleep an average of *{avg}h* (last 7 days, {n} entries).",
        "fr": "Tu dors en moyenne *{avg}h* (7 derniers jours, {n} entrees).",
        "de": "Du schlaefst im Schnitt *{avg}h* (letzte 7 Tage, {n} Eintraege).",
    },
    "health_sleep_empty": {
        "pt": "Sem registos de sono esta semana.",
        "nl": "Geen slaapregistraties deze week.",
        "en": "No sleep entries this week.",
        "fr": "Pas d'entrees de sommeil cette semaine.",
        "de": "Keine Schlafeintraege diese Woche.",
    },
    "health_medication_adherence": {
        "pt": "Medicacao: tomaste em {n}/{total} dias esta semana.",
        "nl": "Medicatie: je nam het {n}/{total} dagen deze week.",
        "en": "Medication: you took it {n}/{total} days this week.",
        "fr": "Medicament : tu l'as pris {n}/{total} jours cette semaine.",
        "de": "Medikament: Du hast es {n}/{total} Tage diese Woche genommen.",
    },
    "health_medication_empty": {
        "pt": "Sem registos de medicacao esta semana.",
        "nl": "Geen medicatieregistraties deze week.",
        "en": "No medication entries this week.",
        "fr": "Pas d'entrees de medicament cette semaine.",
        "de": "Keine Medikamenteneintraege diese Woche.",
    },
    "health_water_today": {
        "pt": "Agua hoje: *{total}L* ({n} registos).",
        "nl": "Water vandaag: *{total}L* ({n} registraties).",
        "en": "Water today: *{total}L* ({n} entries).",
        "fr": "Eau aujourd'hui : *{total}L* ({n} entrees).",
        "de": "Wasser heute: *{total}L* ({n} Eintraege).",
    },
    "health_water_empty": {
        "pt": "Sem registos de agua hoje.",
        "nl": "Geen waterregistraties vandaag.",
        "en": "No water entries today.",
        "fr": "Pas d'entrees d'eau aujourd'hui.",
        "de": "Keine Wassereintraege heute.",
    },
    # ── M9 — extras ───────────────────────────────────────────────────────────
    "habit_logged_with_goal": {
        "pt": "Habito registado: *{activity}* (meta: {goal})",
        "nl": "Gewoonte gelogd: *{activity}* (doel: {goal})",
        "en": "Habit logged: *{activity}* (goal: {goal})",
        "fr": "Habitude enregistree : *{activity}* (objectif : {goal})",
        "de": "Gewohnheit protokolliert: *{activity}* (Ziel: {goal})",
    },
    "goal_completed": {
        "pt": "Meta concluida: *{title}* \U0001f389",
        "nl": "Doel bereikt: *{title}* \U0001f389",
        "en": "Goal completed: *{title}* \U0001f389",
        "fr": "Objectif atteint : *{title}* \U0001f389",
        "de": "Ziel erreicht: *{title}* \U0001f389",
    },
    "goal_complete_not_found": {
        "pt": "Nao encontrei essa meta activa.",
        "nl": "Ik kon dat actieve doel niet vinden.",
        "en": "I couldn't find that active goal.",
        "fr": "Je n'ai pas trouve cet objectif actif.",
        "de": "Ich konnte dieses aktive Ziel nicht finden.",
    },
    "habit_frequency": {
        "pt": "Registaste *{activity}* {n}x nos ultimos 7 dias.",
        "nl": "Je registreerde *{activity}* {n}x in de afgelopen 7 dagen.",
        "en": "You logged *{activity}* {n}x in the last 7 days.",
        "fr": "Tu as enregistre *{activity}* {n}x ces 7 derniers jours.",
        "de": "Du hast *{activity}* {n}x in den letzten 7 Tagen protokolliert.",
    },
    "habit_frequency_empty": {
        "pt": "Nenhum registo de *{activity}* esta semana.",
        "nl": "Geen registraties van *{activity}* deze week.",
        "en": "No entries for *{activity}* this week.",
        "fr": "Aucune entree pour *{activity}* cette semaine.",
        "de": "Keine Eintraege fuer *{activity}* diese Woche.",
    },
    # ── M10 — extras ──────────────────────────────────────────────────────────
    "notes_list_header": {
        "pt": "As tuas ultimas notas ({n}):",
        "nl": "Jouw laatste notities ({n}):",
        "en": "Your recent notes ({n}):",
        "fr": "Tes dernieres notes ({n}) :",
        "de": "Deine letzten Notizen ({n}):",
    },
    "notes_list_empty": {
        "pt": "Ainda nao tens notas guardadas.",
        "nl": "Nog geen opgeslagen notities.",
        "en": "No notes saved yet.",
        "fr": "Pas encore de notes enregistrees.",
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
        "fr": "Tache supprimee : *{body}*",
        "de": "Aufgabe geloscht: *{body}*",
    },
    "task_delete_not_found": {
        "pt": "Nao encontrei essa tarefa.",
        "nl": "Ik kon die taak niet vinden.",
        "en": "I couldn't find that task.",
        "fr": "Je n'ai pas trouve cette tache.",
        "de": "Ich konnte diese Aufgabe nicht finden.",
    },
    "tasks_list_overdue": {
        "pt": "• {n}. \u26a0\ufe0f {body}{due}",
        "nl": "• {n}. \u26a0\ufe0f {body}{due}",
        "en": "• {n}. \u26a0\ufe0f {body}{due}",
        "fr": "• {n}. \u26a0\ufe0f {body}{due}",
        "de": "• {n}. \u26a0\ufe0f {body}{due}",
    },
}


def _t(key: str, lang: str, **kwargs: object) -> str:
    """Translate a string key to the given language, with optional format args."""
    lang = lang if lang in _SUPPORTED_LANGS else "en"
    tmpl = _STRINGS[key].get(lang) or _STRINGS[key]["en"]
    return tmpl.format(**kwargs) if kwargs else tmpl


def _detect_language(text: str) -> str:
    """Heuristic: detect language from first-message keywords."""
    t = unicodedata.normalize("NFC", text).lower()
    if any(w in t for w in ["bonjour", "salut", "allô", "allo", "merci",
                              "bilan", "solde", "aide", " oui", "oui ",
                              "dépense", "depense", "résumé"]):
        return "fr"
    if any(w in t for w in ["guten", "danke", "bitte", "übersicht", "ubersicht",
                              "ausgaben", "hilfe", "nein", "bezahlt", "erhalten"]):
        return "de"
    if any(w in t for w in ["oi ", " oi", "olá", "ola", "obrigad", "ajuda",
                              "gastei", "paguei", "recebi", " sim", "sim ",
                              "não", "nao", "resumo"]):
        return "pt"
    if any(w in t for w in [" dag", "dag ", "hallo", "bedankt", "overzicht",
                              "hulp", "samenvatting", "uitgegeven", "ontvangen",
                              "betaald", "hoi "]):
        return "nl"
    return "en"


# ── Command keywords ──────────────────────────────────────────────────────────

_SUMMARY_WORDS = {
    "resumo", "overzicht", "summary", "samenvatting", "gastos",
    "résumé", "resume", "bilan", "dépenses", "depenses",
    "übersicht", "ubersicht", "zusammenfassung", "ausgaben",
}
_SALDO_WORDS = {
    "saldo", "balance", "balanço", "balancete", "balanso",
    "solde", "bilanz", "kontostand",
}
_HELP_WORDS = {
    "ajuda", "help", "hulp", "comandos", "commands",
    "aide", "commandes", "hilfe", "befehle",
}
_STOP_WORDS = {
    "stop", "pare", "parar", "stoppen", "ophouden",
    "arrêter", "arreter", "aufhören", "aufhoren",
}
_CONSENT_YES = {
    "sim", "yes", "s", "y", "ok", "aceito", "aceitar",
    "ja", "oui",
}
_CONSENT_NO = {
    "não", "nao", "no", "n", "stop", "nee", "non", "nein",
}



_LEMBRETE_WORDS = {
    "configura lembrete", "configura lembrete:",
    "herinnering:", "herinnering",
    "reminder:", "set reminder",
    "rappel:", "configurer rappel",
    "erinnerung:", "erinnerung setzen",
}

# Matches "lembrete: <text> às/at/om/à/um HH:MM"
_LEMBRETE_RE = re.compile(
    r"(?:configura\s+lembrete|lembrete|set\s+reminder|reminder|herinnering|"
    r"rappel|configurer\s+rappel|erinnerung(?:\s+setzen)?)"
    r"[:\s]+(.+?)\s+(?:às|at|om|à|um|a)\s+(\d{1,2}:\d{2})\b",
    re.IGNORECASE,
)


# M12 — Category correction patterns
# "Jumbo é supermarkt" / "Albert Heijn is not restaurant, is supermarkt" / "isso não é wonen, é transport"
_CORRECTION_RE = re.compile(
    r"(?:(?P<merchant>[\w\s\-&\'\.]+?)\s+)?(?:não\s+é|nao\s+e|is\s+not|niet|n\'?est\s+pas|ist\s+nicht|ist\s+kein)\s+"
    r"[\w]+.*?(?:é|e|is|ist|est)\s+(?P<fix_cat>[\w]+)|"
    r"(?:(?P<merchant2>[\w\s\-&\'\.]+?)\s+)?(?:é|e|is|ist|est)\s+(?P<cat2>[\w]+)\b",
    re.IGNORECASE,
)

_VALID_CATEGORIES = frozenset({
    "supermarkt", "restaurant", "transport", "gezondheid", "entertainment",
    "wonen", "kleding", "abonnement", "inkomen", "overig",
    # common aliases
    "supermercado", "supermercaat", "supermarché", "supermarkt",
    "alimentação", "food", "eten", "nourriture",
    "reizen", "viagem", "voyage", "reise",
    "gezondheidszorg", "saúde", "santé", "gesundheit",
    "divertissement", "unterhaltung",
    "wohnen", "loyer", "habitation", "moradia",
    "kleren", "roupas", "vêtements", "kleidung",
    "subscription", "abonnement", "assinatura", "abo",
    "income", "inkomen", "renda", "revenu", "einkommen",
    "other", "outros", "anderen", "autre",
})

# Canonical mapping for alias→canonical category
_CAT_ALIAS: dict[str, str] = {
    "supermercado": "supermarkt", "supermercaat": "supermarkt", "supermarché": "supermarkt",
    "alimentação": "restaurant", "food": "restaurant", "eten": "restaurant", "nourriture": "restaurant",
    "reizen": "transport", "viagem": "transport", "voyage": "transport", "reise": "transport",
    "gezondheidszorg": "gezondheid", "saúde": "gezondheid", "santé": "gezondheid", "gesundheit": "gezondheid",
    "divertissement": "entertainment", "unterhaltung": "entertainment",
    "wohnen": "wonen", "loyer": "wonen", "habitation": "wonen", "moradia": "wonen",
    "kleren": "kleding", "roupas": "kleding", "vêtements": "kleding", "kleidung": "kleding",
    "subscription": "abonnement", "assinatura": "abonnement", "abo": "abonnement",
    "income": "inkomen", "renda": "inkomen", "revenu": "inkomen", "einkommen": "inkomen",
    "other": "overig", "outros": "overig", "anderen": "overig", "autre": "overig",
}


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
    "corri", "correr", "treino", "treinar", "ginásio", "ginasio", "gym",
    "exercício", "exercicio", "yoga", "pilates", "caminhei", "caminhada",
    "natação", "natacao", "ciclismo", "hiit", "alongamento", "musculação",
    "musculacao",
    "ran", "run", "walked", "walk", "trained", "workout", "exercise",
    "swam", "swim", "cycling", "jogged",
    "joggen", "fietste", "zwom", "trainde", "liep", "sportde",
    "couru", "marché", "nagé", "cyclisme", "gelaufen", "geschwommen",
}

# ── M8 — Saúde keywords ──────────────────────────────────────────────────────
_HEALTH_WORDS = {
    "tomei", "tomi", "took", "nam", "pris", "eingenommen",   # medication
    "humor", "mood", "humeur", "stimmung",                    # mood
    "dormi", "slept", "sliep", "geschlafen", "dormido",       # sleep
    "bebi", "drank", "dronk", "bu",                           # water
}

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
    "minhas metas", "as minhas metas", "meus objetivos",
    "my goals", "mijn doelen", "meine ziele", "mes objectifs",
    "como vão as metas", "como vai",
}
_HABIT_WORDS = {
    "meditei", "meditated", "mediteerde", "meditiert",
    "bebi água", "drank water", "leste", "li", "estudei", "estudied",
}
_HABIT_LOG_RE = re.compile(
    r"(?P<activity>meditei|meditated|mediteerde|meditiert|li|leste|estudei|fiz yoga|"
    r"bebi água|drank water|fiz pilates|fiz alongamento)\s*(?:hoje|today|vandaag|heute|aujourd'hui)?",
    re.IGNORECASE,
)

# ── M10 — Produtividade keywords ─────────────────────────────────────────────
_NOTE_RE = re.compile(
    r"^(?:nota|note|notitie|notiz|remarque|anotação|anotacao)\s*[:]\s*(.+)",
    re.IGNORECASE,
)
_TASK_RE = re.compile(
    r"^(?:tarefa|task|taak|aufgabe|tâche|tarefa:)\s*[:]\s*(.+)",
    re.IGNORECASE,
)
_DONE_RE = re.compile(
    r"^(?:feito|done|klaar|erledigt|fait|concluido|concluído)\s*[:]\s*(.+)",
    re.IGNORECASE,
)
_TASKS_QUERY_WORDS = {
    "minhas tarefas", "as minhas tarefas", "o que tenho para fazer",
    "my tasks", "mijn taken", "meine aufgaben", "mes tâches",
    "lista de tarefas", "task list",
}


# ── M5 — days_mask NL parsing + list/cancel ──────────────────────────────────
_DAY_BITS: dict[str, int] = {
    # Monday = 1
    "segunda": 1, "segunda-feira": 1, "monday": 1, "maandag": 1, "lundi": 1, "montag": 1,
    # Tuesday = 2
    "terca": 2, "terca-feira": 2, "tuesday": 2, "dinsdag": 2, "mardi": 2, "dienstag": 2,
    # Wednesday = 4
    "quarta": 4, "quarta-feira": 4, "wednesday": 4, "woensdag": 4, "mercredi": 4, "mittwoch": 4,
    # Thursday = 8
    "quinta": 8, "quinta-feira": 8, "thursday": 8, "donderdag": 8, "jeudi": 8, "donnerstag": 8,
    # Friday = 16
    "sexta": 16, "sexta-feira": 16, "friday": 16, "vrijdag": 16, "vendredi": 16, "freitag": 16,
    # Saturday = 32
    "sabado": 32, "saturday": 32, "zaterdag": 32, "samedi": 32, "samstag": 32,
    # Sunday = 64
    "domingo": 64, "sunday": 64, "zondag": 64, "dimanche": 64, "sonntag": 64,
}

_DAYS_LABEL: dict[int, str] = {
    1: "Mon", 2: "Tue", 4: "Wed", 8: "Thu", 16: "Fri", 32: "Sat", 64: "Sun",
}


def _parse_days_mask(text: str) -> int:
    """Parse days bitmask from free-text. Returns 127 (all days) if no days specified."""
    lower = unicodedata.normalize("NFD", text.lower())
    lower = "".join(c for c in lower if unicodedata.category(c) != "Mn")
    if any(w in lower for w in ("todos os dias", "every day", "elke dag", "tous les jours", "jeden tag", "diariamente", "daily")):
        return 127
    if any(w in lower for w in ("dias uteis", "dias de semana", "weekdays", "werkdagen", "jours ouvrables", "werktage")):
        return 31  # Mon–Fri
    if any(w in lower for w in ("fim de semana", "weekend", "wochenende")):
        return 96  # Sat+Sun
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
    "os meus lembretes", "meus lembretes", "lembretes activos",
    "my reminders", "mijn herinneringen", "mes rappels", "meine erinnerungen",
    "ver lembretes", "lista de lembretes",
}
_CANCEL_LEMBRETE_RE = re.compile(
    r"(?:cancela|cancel|annuler|abbrechen|annuleer)\s+(?:lembrete|reminder|herinnering|rappel|erinnerung)\s+(?:de|of|van|du|von|sobre)?\s*(.+)",
    re.IGNORECASE,
)

# ── M7 — extra workout patterns ──────────────────────────────────────────────
_WORKOUT_DELETE_RE = re.compile(
    r"(?:apaga|delete|verwijder|supprimer|losch|apagar)\s+"
    r"(?:o\s+)?(?:treino|workout|training|entrainement)\s*"
    r"(?:de\s+hoje|today|vandaag|heute|aujourd.hui|ultimo|last|laatste)?",
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
    r"(?:de\s+)?(?:este\s+mes|this\s+month|deze\s+maand|ce\s+mois|diesen\s+monat|"
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
_HABIT_FREQ_RE = re.compile(
    r"(?:quantas\s+vezes|how\s+many\s+times|hoe\s+vaak|combien\s+de\s+fois|wie\s+oft)\s+"
    r"(?:(?:eu\s+)?fiz|did\s+i\s+do|deed\s+ik|ai-je\s+fait|habe\s+ich\s+gemacht|"
    r"meditei|meditated|li|leste|estudei|corri|ran|nadei)\s*(.+)?",
    re.IGNORECASE,
)

# ── M10 — notes list + task delete ────────────────────────────────────────────
_NOTES_QUERY_WORDS = {
    "as minhas notas", "minhas notas", "as notas", "ver notas",
    "my notes", "mijn notities", "mes notes", "meine notizen",
    "lista de notas", "notes list",
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
    """True when body *starts with* a command keyword (ignores trailing punctuation/words)."""
    for kw in keywords:
        if body == kw or body.startswith(kw + " ") or body.startswith(kw + "?") or body.startswith(kw + "!"):
            return True
    return False


# ── History window ────────────────────────────────────────────────────────────

_HISTORY_LIMIT = 10  # messages (pairs) to include in LLM context


async def _load_history(member: Member, session: AsyncSession) -> list[dict]:
    """Return the last N messages for this member as Claude-format dicts."""
    result = await session.execute(
        select(Message)
        .where(Message.author_id == member.id)
        .order_by(Message.created_at.desc())
        .limit(_HISTORY_LIMIT * 2)
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
) -> None:
    """Persist an outbound message so it appears in future history."""
    outbound = Message(
        id=uuid.uuid4(),
        wa_message_id=f"out-{uuid.uuid4()}",
        household_id=member.household_id,
        author_id=member.id,
        direction="outbound",
        body=body,
        wa_timestamp=datetime.now(timezone.utc),
        processed=True,
    )
    session.add(outbound)


def _fmt_eur(amount: float) -> str:
    """Format a float as PT-style euro: €1.234,56"""
    return f"€{amount:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _period_range(period: str, lang: str = "en") -> tuple[datetime, datetime | None, str]:
    """Return (start, end_exclusive, label).  end_exclusive=None means open (up to now)."""
    now = datetime.now(timezone.utc)
    if period == "last_month":
        first_this = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        prev_last = first_this - timedelta(seconds=1)
        start = prev_last.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return start, first_this, prev_last.strftime("%B %Y").capitalize()
    if period == "current_week":
        start = (now - timedelta(days=now.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        return start, None, _t("period_current_week", lang)
    if period == "last_week":
        start_this = (now - timedelta(days=now.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        start = start_this - timedelta(days=7)
        return start, start_this, _t("period_last_week", lang)
    # default: current_month
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return start, None, now.strftime("%B %Y").capitalize()


async def _build_summary(
    member: Member,
    session: AsyncSession,
    *,
    period: str = "current_month",
    category: str | None = None,
) -> str:
    """Query expenses/income for a period (optionally filtered by category)."""
    lang = member.language or "en"
    start, end_excl, period_label = _period_range(period, lang)

    filters = [
        Expense.member_id == member.id,
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
            return _t("no_records_scope", lang, category=category.capitalize(), period_label=period_label)
        return _t("no_records_period", lang, period_label=period_label)

    outflows = [e for e in records if e.transaction_type != "income"]
    inflows = [e for e in records if e.transaction_type == "income"]
    total_out = sum(e.amount for e in outflows)
    total_in = sum(e.amount for e in inflows)

    if category:
        # Category view: list individual transactions
        title = _t("category_title", lang, category=category.capitalize(), period_label=period_label)
        lines = [f"{title}\n"]
        for e in outflows[:10]:
            date_str = e.expense_date.strftime("%d/%m")
            name = e.merchant or e.description or category
            lines.append(f"• {date_str} {name}: {_fmt_eur(e.amount)}")
        lines.append(f"\n{_t('category_total', lang, amount=_fmt_eur(total_out))}")
        lines.append(_t("transactions_count", lang, n=len(outflows)))
    else:
        # Full summary: group by category
        by_cat: dict[str, float] = {}
        for e in outflows:
            cat = e.category or "overig"
            by_cat[cat] = by_cat.get(cat, 0) + e.amount

        lines = [f"{_t('summary_title', lang, period_label=period_label)}\n"]
        for cat, amt in sorted(by_cat.items(), key=lambda x: -x[1]):
            lines.append(f"• {cat.capitalize()}: {_fmt_eur(amt)}")

        lines.append(f"\n{_t('total_expenses', lang, amount=_fmt_eur(total_out))}")

        if inflows:
            balance = total_in - total_out
            sign = "+" if balance >= 0 else "-"
            lines.append(_t("income_line", lang, amount=_fmt_eur(total_in)))
            lines.append(_t("balance_line", lang, sign=sign, amount=_fmt_eur(abs(balance))))

        lines.append(_t("transactions_count", lang, n=len(outflows)))

    return "\n".join(lines)


async def _build_saldo(member: Member, session: AsyncSession) -> str:
    """Show income/expense balance for the current month."""
    lang = member.language or "en"
    now = datetime.now(timezone.utc)
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    result = await session.execute(
        select(Expense).where(
            Expense.member_id == member.id,
            Expense.expense_date >= start,
        )
    )
    records = result.scalars().all()

    if not records:
        return _t("no_records_month", lang)

    total_in = sum(e.amount for e in records if e.transaction_type == "income")
    total_out = sum(e.amount for e in records if e.transaction_type != "income")
    balance = total_in - total_out
    sign = "+" if balance >= 0 else "-"

    month_label = now.strftime("%B %Y").capitalize()
    lines = [
        f"{_t('saldo_title', lang, month=month_label)}\n",
        _t("saldo_income", lang, amount=_fmt_eur(total_in)),
        _t("saldo_expenses", lang, amount=_fmt_eur(total_out)),
        _t("saldo_balance", lang, sign=sign, amount=_fmt_eur(abs(balance))),
    ]
    return "\n".join(lines)


async def _build_comparison(member: Member, session: AsyncSession) -> str:
    """Compare current month vs previous month (expenses only)."""
    lang = member.language or "en"
    now = datetime.now(timezone.utc)
    cur_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    prev_end = cur_start
    prev_last = cur_start - timedelta(seconds=1)
    prev_start = prev_last.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    async def _total_out(start: datetime, end: datetime) -> tuple[float, int]:
        r = await session.execute(
            select(Expense).where(
                Expense.member_id == member.id,
                Expense.expense_date >= start,
                Expense.expense_date < end,
                Expense.transaction_type != "income",
            )
        )
        rows = r.scalars().all()
        return sum(e.amount for e in rows), len(rows)

    cur_total, cur_n = await _total_out(cur_start, now + timedelta(seconds=1))
    prev_total, prev_n = await _total_out(prev_start, prev_end)

    cur_label = now.strftime("%B").capitalize()
    prev_label = prev_last.strftime("%B").capitalize()

    diff = cur_total - prev_total
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
    member: "Member",
    session: "AsyncSession",
) -> dict[str, str]:
    """Return {lowercase_merchant: category} for this member (M12)."""
    result = await session.execute(
        select(MerchantCategoryOverride).where(
            MerchantCategoryOverride.member_id == member.id,
        )
    )
    return {row.merchant: row.category for row in result.scalars().all()}


async def _upsert_merchant_override(
    member: "Member",
    merchant: str,
    category: str,
    session: "AsyncSession",
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
            set_={"category": category, "updated_at": datetime.now(timezone.utc)},
        )
    )
    await session.execute(stmt)



async def handle_inbound(
    member: Member,
    message: Message,
    session: AsyncSession,
) -> None:
    """Decide what to reply based on consent state and message content."""
    to = member.wa_phone
    body = unicodedata.normalize("NFC", (message.body or "").strip()).lower()

    # ── 1. First contact: detect language, send disclosure ───────────────────
    if member.consent_state == "pending":
        lang = _detect_language(body)
        member.language = lang
        session.add(member)
        disclosure = _t("disclosure", lang)
        await send_text(to, disclosure)
        member.consent_state = "pending_response"
        logger.info("conversation.disclosure_sent", wa_phone=to, lang=lang)
        return

    lang = member.language or "en"

    # ── 2. Awaiting consent response ─────────────────────────────────────────
    if member.consent_state == "pending_response":
        if body in _CONSENT_YES:
            member.consent_state = "accepted"
            member.disclosure_accepted_at = datetime.now(timezone.utc)
            member.disclosure_version = "1.0"
            session.add(member)
            reply = _t("consent_accepted", lang)
            await send_text(to, reply)
            await _save_outbound(member, reply, session)
            logger.info("conversation.consent_accepted", wa_phone=to)
        elif body in _CONSENT_NO:
            member.consent_state = "rejected"
            session.add(member)
            reply = _t("consent_rejected", lang)
            await send_text(to, reply)
            logger.info("conversation.consent_rejected", wa_phone=to)
        else:
            await send_text(to, _t("consent_unknown", lang))
        return

    # ── 3. Rejected — honour their choice ────────────────────────────────────
    if member.consent_state == "rejected":
        logger.info("conversation.rejected_member_ignored", wa_phone=to)
        return

    # ── 4. Accepted — handle commands and LLM ────────────────────────────────
    if member.consent_state == "accepted":

        # M12 — load member's merchant→category overrides once per message
        member_overrides = await _load_merchant_overrides(member, session)

        # 4a. Stop — re-enter rejected state
        if body in _STOP_WORDS:
            member.consent_state = "rejected"
            session.add(member)
            reply = _t("consent_rejected", lang)
            await send_text(to, reply)
            logger.info("conversation.stop_requested", wa_phone=to)
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

        # 4d. Summary command
        if _is_command(body, _SUMMARY_WORDS):
            summary = await _build_summary(member, session)
            await send_text(to, summary)
            await _save_outbound(member, summary, session)
            return

        # 4e-0. "configura lembrete" — proactive reminder setup (M5)
        m_lembrete = _LEMBRETE_RE.search(body)
        if m_lembrete or _is_command(body, _LEMBRETE_WORDS):
            if m_lembrete:
                reminder_text = m_lembrete.group(1).strip()
                time_str = m_lembrete.group(2).zfill(5)  # "8:00" → "08:00"
                # Validate HH:MM
                try:
                    hh, mm = time_str.split(":")
                    assert 0 <= int(hh) <= 23 and 0 <= int(mm) <= 59
                except Exception:
                    reply = _t("lembrete_invalid", lang)
                    await send_text(to, reply)
                    await _save_outbound(member, reply, session)
                    return

                job_type = _detect_job_type(reminder_text)
                from alfred.models import ScheduledJob
                import uuid as _uuid
                job = ScheduledJob(
                    id=_uuid.uuid4(),
                    member_id=member.id,
                    job_type=job_type,
                    time_of_day=time_str,
                    days_mask=_parse_days_mask(body),
                    payload={"text": reminder_text},
                    active=True,
                )
                session.add(job)
                reply = _t("lembrete_set", lang, text=reminder_text, time=time_str)
                logger.info(
                    "conversation.lembrete_created",
                    wa_phone=to,
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
            from alfred.models import ScheduledJob as _SJ
            from sqlalchemy import select as _sel
            res = await session.execute(
                _sel(_SJ)
                .where(_SJ.member_id == member.id)
                .where(_SJ.active.is_(True))
                .order_by(_SJ.time_of_day.asc())
            )
            jobs = res.scalars().all()
            if not jobs:
                reply = _t("lembretes_list_empty", lang)
            else:
                lines = [_t("lembretes_list_header", lang, n=len(jobs))]
                for j in jobs:
                    days_label = _mask_to_label(j.days_mask)
                    text_label = (j.payload or {}).get("text", j.job_type)
                    lines.append(_t("lembretes_list_row", lang, time=j.time_of_day, text=text_label, days=days_label))
                reply = "\n".join(lines)
            await send_text(to, reply)
            await _save_outbound(member, reply, session)
            return

        # 4e-0c. M5 — cancel reminder
        m_cancel = _CANCEL_LEMBRETE_RE.search(body)
        if m_cancel:
            cancel_kw = m_cancel.group(1).strip().lower()
            from alfred.models import ScheduledJob as _SJ
            from sqlalchemy import select as _sel
            res = await session.execute(
                _sel(_SJ)
                .where(_SJ.member_id == member.id)
                .where(_SJ.active.is_(True))
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

        # 4e-1. M12 — category correction: "Jumbo é supermarkt"
        m_corr = _CORRECTION_RE.search(body)
        if m_corr:
            raw_merchant = (m_corr.group("merchant") or m_corr.group("merchant2") or "").strip()
            raw_cat = (m_corr.group("fix_cat") or m_corr.group("cat2") or "").strip()
            canonical = _canonical_category(raw_cat) if raw_cat else None

            if raw_merchant and canonical:
                await _upsert_merchant_override(member, raw_merchant, canonical, session)
                # Patch the most recent expense with this merchant (last 7 days)
                cutoff = datetime.now(timezone.utc) - timedelta(days=7)
                res = await session.execute(
                    select(Expense)
                    .where(
                        Expense.member_id == member.id,
                        Expense.merchant.ilike(f"%{raw_merchant}%"),
                        Expense.created_at >= cutoff,
                    )
                    .order_by(Expense.created_at.desc())
                    .limit(1)
                )
                last_expense = res.scalar_one_or_none()
                if last_expense:
                    last_expense.category = canonical
                    session.add(last_expense)

                reply = _t("category_corrected", lang, merchant=raw_merchant.title(), category=canonical)
                logger.info(
                    "conversation.category_override_saved",
                    wa_phone=to, merchant=raw_merchant, category=canonical,
                )
                await send_text(to, reply)
                await _save_outbound(member, reply, session)
                return

        # 4e-2. M10 — nota rápida: "nota: X"
        m_note = _NOTE_RE.match(body)
        if m_note:
            note_body = m_note.group(1).strip()
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
            raw_task = m_task.group(1).strip()
            # try to extract due_date ("até <date>" / "by <date>" / "voor <date>")
            due_m = _re.search(
                r"(?:até|by|voor|bis|avant)\s+(.+)$", raw_task, _re.IGNORECASE
            )
            task_body = raw_task
            due_date_val = None
            if due_m:
                task_body = raw_task[: due_m.start()].strip()
                try:
                    from dateutil import parser as _dp
                    due_date_val = _dp.parse(due_m.group(1), default=datetime.now()).date()
                except Exception:
                    pass
            task = Task(
                id=uuid.uuid4(),
                member_id=member.id,
                body=task_body,
                due_date=due_date_val,
            )
            session.add(task)
            reply = _t("task_saved", lang, body=task_body)
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
                    task_row.done_at = datetime.now(timezone.utc)
                    session.add(task_row)
                    reply = _t("task_done", lang)
                else:
                    reply = _t("task_not_found", lang)
                await send_text(to, reply)
                await _save_outbound(member, reply, session)
                return
            done_text_lower = done_text.lower()
            result = await session.execute(
                _sel(Task)
                .where(Task.member_id == member.id)
                .where(Task.done_at.is_(None))
                .where(Task.body.ilike(f"%{done_text_lower}%"))
                .order_by(Task.created_at.desc())
                .limit(1)
            )
            task_row = result.scalar_one_or_none()
            if task_row:
                task_row.done_at = datetime.now(timezone.utc)
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
                today_date = datetime.now(timezone.utc).date()
                for i, t in enumerate(open_tasks, 1):
                    due_str = (
                        _t("tasks_list_due", lang, date=str(t.due_date))
                        if t.due_date else ""
                    )
                    is_overdue = bool(t.due_date and t.due_date < today_date)
                    tpl_key = "tasks_list_overdue" if is_overdue else "tasks_list_row"
                    lines.append(_t(tpl_key, lang, n=i, body=t.body, due=due_str))
                reply = "\n".join(lines)
            await send_text(to, reply)
            await _save_outbound(member, reply, session)
            return


        # 4e-5b. M10 — delete task: "apaga tarefa X"
        m_tdel = _TASK_DELETE_RE.match(body)
        if m_tdel:
            del_kw = m_tdel.group(1).strip().lower()
            from sqlalchemy import select as _sel
            res_del = await session.execute(
                _sel(Task)
                .where(Task.member_id == member.id)
                .where(Task.body.ilike(f"%{del_kw}%"))
                .order_by(Task.created_at.desc())
                .limit(1)
            )
            del_task = res_del.scalar_one_or_none()
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
        m_gcomplete = _GOAL_COMPLETE_RE.search(body)
        if m_gcomplete:
            kw = m_gcomplete.group(1).strip().lower()
            from sqlalchemy import select as _sel
            res_gc = await session.execute(
                _sel(Goal)
                .where(Goal.member_id == member.id)
                .where(Goal.active.is_(True))
                .where(Goal.title.ilike(f"%{kw}%"))
                .order_by(Goal.created_at.desc())
                .limit(1)
            )
            gc = res_gc.scalar_one_or_none()
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
        m_hfreq = _HABIT_FREQ_RE.search(body)
        if m_hfreq:
            freq_raw = (m_hfreq.group(1) or body).strip()
            # extract the activity from the whole body
            from sqlalchemy import select as _sel, func as _func
            from datetime import timedelta as _td
            week_ago = datetime.now(timezone.utc).date() - _td(days=7)
            res_hf = await session.execute(
                _sel(HabitLog)
                .where(HabitLog.member_id == member.id)
                .where(HabitLog.log_date >= week_ago)
            )
            all_habits = res_hf.scalars().all()
            # Find best matching activity
            freq_kw = freq_raw.lower() if freq_raw else body.lower()
            matched = [h for h in all_habits if freq_kw in h.activity.lower() or h.activity.lower() in freq_kw]
            if not matched and all_habits:
                # fallback: count all
                matched = all_habits
                freq_kw = "habits"
            if matched:
                activity_label = matched[0].activity if matched else "?"
                reply = _t("habit_frequency", lang, activity=activity_label, n=len(matched))
            else:
                reply = _t("habit_frequency_empty", lang, activity=freq_kw)
            await send_text(to, reply)
            await _save_outbound(member, reply, session)
            return

        # 4e-8. M9 — log de hábito: "meditei hoje" (regex fast-path)
        m_habit = _HABIT_LOG_RE.search(body)
        if m_habit:
            habit_activity = m_habit.group("activity").strip()
            # Try to link to a matching active goal
            from sqlalchemy import select as _sel
            res_hg = await session.execute(
                _sel(Goal)
                .where(Goal.member_id == member.id)
                .where(Goal.active.is_(True))
            )
            linked_goal = None
            for g in res_hg.scalars().all():
                if any(w in g.title.lower() for w in habit_activity.lower().split()):
                    linked_goal = g
                    break
            habit_log = HabitLog(
                id=uuid.uuid4(),
                member_id=member.id,
                goal_id=linked_goal.id if linked_goal else None,
                activity=habit_activity,
                log_date=datetime.now(timezone.utc).date(),
            )
            session.add(habit_log)
            if linked_goal:
                reply = _t("habit_logged_with_goal", lang, activity=habit_activity, goal=linked_goal.title)
            else:
                reply = _t("habit_logged", lang, activity=habit_activity)
            await send_text(to, reply)
            await _save_outbound(member, reply, session)
            return

        # 4e-8b. M9 — log de hábito via LLM (free-form)
        # Only attempt if message is short (< 80 chars) and doesn't look like expense/workout
        _body_lower = body.lower()
        _not_expense = not any(w in _body_lower for w in ("€", "$", "£", "gastei", "comprei", "paguei", "spent", "paid", "bought"))
        _not_workout = not _is_command(body, _WORKOUT_WORDS)
        _not_health = not _is_command(body, _HEALTH_WORDS)
        if len(body) < 80 and _not_expense and _not_workout and _not_health:
            habit_data = await extract_habit(body)
            if habit_data:
                h_activity = habit_data["activity"]
                h_days_ago = habit_data.get("days_ago", 0)
                from sqlalchemy import select as _sel
                from datetime import timedelta as _td
                h_date = (datetime.now(timezone.utc) - _td(days=h_days_ago)).date()
                # Try to link to goal
                res_hg2 = await session.execute(
                    _sel(Goal)
                    .where(Goal.member_id == member.id)
                    .where(Goal.active.is_(True))
                )
                linked_goal2 = None
                for g in res_hg2.scalars().all():
                    if any(w in g.title.lower() for w in h_activity.lower().split()):
                        linked_goal2 = g
                        break
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
                    reply = _t("habit_logged_with_goal", lang, activity=h_activity, goal=linked_goal2.title)
                else:
                    reply = _t("habit_logged", lang, activity=h_activity)
                await send_text(to, reply)
                await _save_outbound(member, reply, session)
                logger.info("conversation.habit_recorded_llm", wa_phone=to, activity=h_activity)
                return

        # 4e-9. M8 — health log: medicação, humor, sono, água
        if _is_command(body, _HEALTH_WORDS):
            from datetime import date as _date
            today = datetime.now(timezone.utc).date()
            health_data = await extract_health_log(body)
            if health_data:
                log_date = today
                if health_data.get("days_ago", 0) > 0:
                    from datetime import timedelta as _td
                    log_date = (datetime.now(timezone.utc) - _td(days=health_data["days_ago"])).date()
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
                val = health_data["value"]
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
        if _HEALTH_MOOD_QUERY_RE.search(body):
            from sqlalchemy import select as _sel
            from datetime import timedelta as _td
            week_ago = datetime.now(timezone.utc).date() - _td(days=7)
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
                entries = ", ".join(f"{r.value}/10 ({r.log_date.strftime('%a')})" for r in mood_rows[:7])
                reply = _t("health_mood_history", lang, entries=entries)
            await send_text(to, reply)
            await _save_outbound(member, reply, session)
            return

        # 4e-9c. M8 — sleep average query
        if _HEALTH_SLEEP_QUERY_RE.search(body):
            from sqlalchemy import select as _sel
            from datetime import timedelta as _td
            week_ago = datetime.now(timezone.utc).date() - _td(days=7)
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
                reply = _t("health_sleep_avg", lang, avg=avg, n=len(sleep_rows))
            await send_text(to, reply)
            await _save_outbound(member, reply, session)
            return

        # 4e-9d. M8 — medication adherence query
        if _HEALTH_MED_QUERY_RE.search(body):
            from sqlalchemy import select as _sel
            from datetime import timedelta as _td
            week_ago = datetime.now(timezone.utc).date() - _td(days=7)
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
                total_days = min(7, (datetime.now(timezone.utc).date() - week_ago).days + 1)
                day_labels = ", ".join(sorted({r.log_date.strftime("%a") for r in med_rows}))
                reply = _t("health_medication_adherence", lang, n=unique_days, total=total_days, days=day_labels)
            await send_text(to, reply)
            await _save_outbound(member, reply, session)
            return

        # 4e-9e. M8 — water intake query
        if _HEALTH_WATER_QUERY_RE.search(body):
            from sqlalchemy import select as _sel
            today_wq = datetime.now(timezone.utc).date()
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
                reply = _t("health_water_today", lang, total=total_l, n=len(water_rows))
            await send_text(to, reply)
            await _save_outbound(member, reply, session)
            return

        # 4e-10. M7 — treino: "corri 30 min"
        if _is_command(body, _WORKOUT_WORDS):
            from datetime import date as _date
            today = datetime.now(timezone.utc).date()
            workout_data = await extract_workout(body)
            if workout_data:
                from datetime import timedelta as _td
                wo_date = today
                if workout_data.get("days_ago", 0) > 0:
                    wo_date = (datetime.now(timezone.utc) - _td(days=workout_data["days_ago"])).date()
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
                dur_str = (
                    f"{workout_data['duration_minutes']}min"
                    if workout_data.get("duration_minutes")
                    else (
                        f"{workout_data['distance_km']}km"
                        if workout_data.get("distance_km")
                        else ""
                    )
                )
                reply = _t("workout_saved", lang,
                           activity=workout_data["activity_type"],
                           duration=dur_str)
                await send_text(to, reply)
                await _save_outbound(member, reply, session)
                logger.info(
                    "conversation.workout_recorded",
                    wa_phone=to,
                    activity=workout_data["activity_type"],
                )
                return

        # 4e-11. M7 — query de treinos: "treinos desta semana"
        if _is_command(body, {"treinos", "workouts", "trainingen", "trainings", "mes treinos",
                               "my workouts", "treinos desta semana", "workouts this week"}):
            from sqlalchemy import select as _sel
            from datetime import timedelta as _td
            week_ago = datetime.now(timezone.utc).date() - _td(days=7)
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
                    dur = f"{ws.duration_minutes}min" if ws.duration_minutes else (
                        f"{ws.distance_km}km" if ws.distance_km else ""
                    )
                    lines.append(_t("workout_summary_row", lang,
                                    date=str(ws.workout_date),
                                    activity=ws.activity_type,
                                    duration=dur))
                reply = "\n".join(lines)
            await send_text(to, reply)
            await _save_outbound(member, reply, session)
            return


        # 4e-11b. M7 — delete workout: "apaga o treino de hoje"
        if _WORKOUT_DELETE_RE.search(body):
            from sqlalchemy import select as _sel
            res_wd = await session.execute(
                _sel(WorkoutSession)
                .where(WorkoutSession.member_id == member.id)
                .order_by(WorkoutSession.workout_date.desc(), WorkoutSession.created_at.desc())
                .limit(1)
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
        if _WORKOUT_MONTH_RE.search(body):
            from sqlalchemy import select as _sel
            from datetime import timedelta as _td
            now_wm = datetime.now(timezone.utc)
            # Check if "mes passado" / "last month"
            _bl = body.lower()
            if any(w in _bl for w in ("mes passado", "last month", "vorige maand", "mois dernier", "letzten monat")):
                first_this = now_wm.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
                prev_last = first_this - _td(seconds=1)
                start_wm = prev_last.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
                end_wm = first_this.date()
                month_label = prev_last.strftime("%B %Y")
            else:
                start_wm = now_wm.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
                end_wm = None
                month_label = now_wm.strftime("%B %Y")
            q = _sel(WorkoutSession).where(
                WorkoutSession.member_id == member.id,
                WorkoutSession.workout_date >= start_wm.date(),
            )
            if end_wm:
                q = q.where(WorkoutSession.workout_date < end_wm)
            res_wm = await session.execute(q.order_by(WorkoutSession.workout_date.desc()))
            wm_sessions = res_wm.scalars().all()
            n_wm = len(wm_sessions)
            total_km = round(sum(s.distance_km or 0 for s in wm_sessions), 1)
            total_min = sum(s.duration_minutes or 0 for s in wm_sessions)
            reply = _t("workout_month_header", lang, month=month_label, n=n_wm, km=total_km, min=total_min)
            if wm_sessions:
                lines = [reply]
                for ws_m in wm_sessions[:5]:
                    dur_m = f"{ws_m.duration_minutes}min" if ws_m.duration_minutes else (
                        f"{ws_m.distance_km}km" if ws_m.distance_km else "")
                    lines.append(_t("workout_summary_row", lang, date=str(ws_m.workout_date), activity=ws_m.activity_type, duration=dur_m))
                reply = "\n".join(lines)
            await send_text(to, reply)
            await _save_outbound(member, reply, session)
            return

        # 4e-11d. M7 — workout activity count: "quantas vezes corri esta semana"
        if _WORKOUT_ACTIVITY_RE.search(body):
            from sqlalchemy import select as _sel
            from datetime import timedelta as _td
            week_ago_wa = datetime.now(timezone.utc).date() - _td(days=7)
            res_wa = await session.execute(
                _sel(WorkoutSession)
                .where(WorkoutSession.member_id == member.id)
                .where(WorkoutSession.workout_date >= week_ago_wa)
            )
            wa_sessions = res_wa.scalars().all()
            _bl2 = body.lower()
            # Detect activity keyword
            _act_map = {
                "corri": "running", "ran": "running", "liep": "running", "couru": "running",
                "nadei": "swimming", "swam": "swimming", "zwom": "swimming",
                "ginasio": "strength", "gym": "strength", "treino": "strength",
            }
            matched_act = None
            for kw, act in _act_map.items():
                if kw in _bl2:
                    matched_act = act
                    break
            if matched_act:
                count = sum(1 for s in wa_sessions if matched_act in s.activity_type.lower())
                activity_label = matched_act
            else:
                count = len(wa_sessions)
                activity_label = "workout"
            reply = _t("workout_activity_summary", lang, activity=activity_label, n=count)
            await send_text(to, reply)
            await _save_outbound(member, reply, session)
            return

                # 4e. Try to extract an expense or income from the message
        expense_data = await extract_expense(message.body or "", merchant_overrides=member_overrides)
        if expense_data:
            txn_type = expense_data.get("type", "expense")
            days_ago = expense_data.get("days_ago", 0)
            expense_date = datetime.now(timezone.utc) - timedelta(days=days_ago)

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
            )
            session.add(expense)

            name = expense_data["merchant"] or expense_data["category"] or expense_data["description"]
            amt_fmt = _fmt_eur(expense_data["amount"])
            key = "income_recorded" if txn_type == "income" else "expense_recorded"
            reply = _t(key, lang, amount=amt_fmt, name=name)
            if days_ago > 0:
                reply += _t("days_ago_suffix", lang, n=days_ago)

            await send_text(to, reply)
            await _save_outbound(member, reply, session)
            logger.info(
                "conversation.expense_recorded",
                wa_phone=to,
                type=txn_type,
                amount=expense_data["amount"],
                category=expense_data["category"],
            )
            return

        # 4f. Try to classify as a financial query
        query = await classify_query(message.body or "")
        if query:
            qtype = query.get("query_type")
            period = query.get("period") or "current_month"
            category = query.get("category")

            if qtype == "balance":
                reply = await _build_saldo(member, session)
            elif qtype == "category" and category:
                reply = await _build_summary(member, session, period=period, category=category)
            elif qtype == "period":
                reply = await _build_summary(member, session, period=period)
            elif qtype == "comparison":
                reply = await _build_comparison(member, session)
            else:
                reply = None

            if reply:
                await send_text(to, reply)
                await _save_outbound(member, reply, session)
                logger.info("conversation.query_replied", wa_phone=to, query_type=qtype)
                return

        # 4g. General LLM reply with conversation history
        history = await _load_history(member, session)
        reply = await generate_reply(member, message, history=history)  # noqa: F821
        await send_text(to, reply)
        await _save_outbound(member, reply, session)
        logger.info("conversation.reply_sent", wa_phone=to)
        return
