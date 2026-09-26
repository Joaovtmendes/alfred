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

from alfred.llm import classify_query, extract_expense, generate_reply
from alfred.models import Expense, Member, Message
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
                    days_mask=127,  # every day
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

        # 4e. Try to extract an expense or income from the message
        expense_data = await extract_expense(message.body or "")
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
