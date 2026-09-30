"""LLM integration — supports Anthropic direct API and AWS Bedrock."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import structlog

from alfred.models import Member, Message
from alfred.settings import settings
from alfred.validation import (
    parse_llm_json,
    sanitize_expense,
    sanitize_habit,
    sanitize_health,
    sanitize_query,
    sanitize_workout,
)

logger = structlog.get_logger()

# One shared async client per process: reuses the HTTP connection pool and never
# blocks the event loop (the old code built a sync client inside every call).
_client: Any = None
_client_key: str | None = None


def _get_client(api_key: str) -> Any:
    """Return a cached ``anthropic.AsyncAnthropic`` with timeout + retries."""
    global _client, _client_key
    if _client is None or _client_key != api_key:
        import anthropic

        _client = anthropic.AsyncAnthropic(
            api_key=api_key,
            timeout=settings.llm_timeout_seconds,
            max_retries=2,
        )
        _client_key = api_key
    return _client


_SYSTEM_PROMPT = """Je bent Alfred, een persoonlijke assistent via WhatsApp.
Je helpt met uitgaven bijhouden, afspraken en herinneringen.
Je bent een AI — geen mens.
Antwoord altijd in de taal van de gebruiker.

STIJLREGELS (VERPLICHT):
- Antwoord kort en direct. Geen welkomst-intro, geen herhaling van wat de gebruiker zei.
- Maximaal 3-4 zinnen tenzij de gebruiker uitleg vraagt.
- Gebruik GEEN markdown-opmaak: geen #, ##, geen | tabellen, geen ```.
- Gebruik WEL: *vetgedrukt* voor nadruk, _cursief_ voor bijzaken, • voor lijstjes.
- Begin NOOIT met "Hallo!", "Goedemiddag!" of vergelijkbare begroetingen na het eerste bericht.
- GEBRUIK ABSOLUUT GEEN EMOJI. Geen 👋, geen ✅, geen 💰, geen enkel emoji-karakter. Nooit. Altijd platte tekst.

BELANGRIJK — je registreert zelf NIETS en je hebt geen toegang tot gegevens:
- Als dit bericht een uitgave of inkomen lijkt: het is NIET geregistreerd. Bevestig nooit een registratie.
  Vraag de gebruiker het opnieuw te sturen met bedrag en omschrijving, bijvoorbeeld: "Jumbo 23,50".
- Als de gebruiker om een overzicht vraagt: verzin geen cijfers. Verwijs naar het commando "resumo" / "overzicht" / "summary" of "saldo".
- Je zegt NOOIT dat je geen toegang hebt of iets niet kunt verwijderen als het een van deze commando's is;
  verwijs er dan naar. Bestaande commando's: "saldo", "gastos de hoje/ontem/setembro/este ano",
  "ultimas 5 despesas", "top categorias", "apaga" (wist de laatste uitgave),
  "errei foram 42" (corrigeert de laatste uitgave), "dashboard"."""


_LLM_ERROR: dict[str, str] = {
    "pt": "Não foi possível processar a tua mensagem. Tenta de novo.",
    "nl": "Sorry, ik kan je bericht nu niet verwerken. Probeer het later opnieuw.",
    "en": "Sorry, I couldn't process your message. Please try again.",
    "fr": "Désolé, je n'ai pas pu traiter ton message. Réessaie.",
    "de": "Entschuldigung, ich konnte deine Nachricht nicht verarbeiten. Versuche es erneut.",
}

_LANG_INSTRUCTION: dict[str, str] = {
    "pt": "Responde SEMPRE em português. Nunca mistures com inglês ou neerlandês, mesmo que o histórico contenha outras línguas.",
    "nl": "Antwoord ALTIJD in het Nederlands. Gebruik nooit Engels of Portugees, ook niet als de geschiedenis andere talen bevat.",
    "en": "ALWAYS respond in English. Never mix in Portuguese or Dutch, even if the conversation history contains other languages.",
    "fr": "Réponds TOUJOURS en français. Ne mélange jamais avec l'anglais ou le néerlandais, même si l'historique contient d'autres langues.",
    "de": "Antworte IMMER auf Deutsch. Vermische niemals mit Englisch oder Niederländisch, auch wenn der Gesprächsverlauf andere Sprachen enthält.",
}


def _bedrock_model_id(model: str) -> str:
    """Normalise model name for Bedrock (adds prefix if needed)."""
    if model.startswith("anthropic."):
        return model
    return f"anthropic.{model}"


def _anthropic_model_id(model: str) -> str:
    """Normalise model name for Anthropic direct API (strips Bedrock prefix)."""
    if model.startswith("anthropic."):
        m = model.removeprefix("anthropic.")
        m = m.split(":")[0]
        m = m.removesuffix("-v2")
        m = m.removesuffix("-v1")
        return m
    return model


async def generate_reply(
    member: Member,
    message: Message,
    history: list[dict] | None = None,
) -> str:
    """Generate a reply using the configured LLM provider.

    Args:
        member:  The member sending the message.
        message: The current inbound message.
        history: Prior messages in Claude format
                 [{"role": "user"|"assistant", "content": "..."}].
                 The current message is appended by this function.
    """
    provider = settings.llm_provider.lower()

    if provider == "anthropic":
        return await _reply_anthropic(member, message, history)
    elif provider == "bedrock":
        return await _reply_bedrock(member, message, history)
    else:
        logger.warning("llm.unknown_provider", provider=provider)
        return f"[echo] {message.body}"


async def _reply_anthropic(
    member: Member,
    message: Message,
    history: list[dict] | None,
) -> str:
    """Call Anthropic API directly."""
    try:
        api_key = settings.llm_api_key.get_secret_value()
        if not api_key:
            logger.warning("llm.anthropic_key_missing")
            return _LLM_ERROR.get(getattr(member, "language", "en"), _LLM_ERROR["en"])

        model = _anthropic_model_id(settings.llm_model)
        client = _get_client(api_key)

        messages: list[dict] = list(history or [])
        messages.append({"role": "user", "content": message.body or ""})

        lang = getattr(member, "language", "en") or "en"
        lang_instr = _LANG_INSTRUCTION.get(lang, _LANG_INSTRUCTION["en"])
        system_prompt = f"{_SYSTEM_PROMPT}\n\n{lang_instr}"

        response = await client.messages.create(
            model=model,
            max_tokens=512,
            system=system_prompt,
            messages=messages,
        )

        reply = response.content[0].text
        logger.info(
            "llm.reply_generated",
            provider="anthropic",
            model=model,
            member_id=str(member.id),
            history_turns=len(history) if history else 0,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )
        return reply

    except ImportError:
        logger.warning("llm.anthropic_not_installed")
        return f"[echo] {message.body}"
    except Exception as exc:
        logger.error("llm.error", error=str(exc))
        return _LLM_ERROR.get(getattr(member, "language", "en"), _LLM_ERROR["en"])


async def _reply_bedrock(
    member: Member,
    message: Message,
    history: list[dict] | None,
) -> str:
    """Call Claude via AWS Bedrock."""
    try:
        import boto3  # type: ignore[import]

        model = _bedrock_model_id(settings.llm_model)
        client = boto3.client("bedrock-runtime", region_name=settings.aws_region)

        messages: list[dict] = list(history or [])
        messages.append({"role": "user", "content": message.body or ""})

        lang = getattr(member, "language", "en") or "en"
        lang_instr = _LANG_INSTRUCTION.get(lang, _LANG_INSTRUCTION["en"])
        system_prompt = f"{_SYSTEM_PROMPT}\n\n{lang_instr}"

        body = json.dumps(
            {
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 512,
                "system": system_prompt,
                "messages": messages,
            }
        )

        response = await asyncio.to_thread(
            client.invoke_model,
            modelId=model,
            body=body,
            contentType="application/json",
            accept="application/json",
        )

        result = json.loads(response["body"].read())
        reply = result["content"][0]["text"]

        logger.info(
            "llm.reply_generated",
            provider="bedrock",
            model=model,
            member_id=str(member.id),
            history_turns=len(history) if history else 0,
            input_tokens=result.get("usage", {}).get("input_tokens"),
            output_tokens=result.get("usage", {}).get("output_tokens"),
        )
        return reply

    except ImportError:
        logger.warning("llm.boto3_not_installed")
        return f"[echo] {message.body}"
    except Exception as exc:
        logger.error("llm.error", error=str(exc))
        return _LLM_ERROR.get(getattr(member, "language", "en"), _LLM_ERROR["en"])


async def classify_query(text: str) -> dict | None:
    """Detect if a message is a financial query (not a new transaction).

    Returns a dict with keys: query_type, category, period — or None.
    query_type values: "balance" | "category" | "period" | "comparison"
    period values: "current_month" | "last_month" | "current_week" | "last_week"
    """
    provider = settings.llm_provider.lower()
    if provider not in ("anthropic", "bedrock"):
        return None
    return await _classify_query_anthropic(text)


async def _classify_query_anthropic(text: str) -> dict | None:
    try:
        api_key = settings.llm_api_key.get_secret_value()
        if not api_key:
            return None

        model = _anthropic_model_id(settings.llm_model)
        client = _get_client(api_key)

        system = """You are a financial query classifier for a WhatsApp assistant.
The user writes in Portuguese, Dutch, English, French, or German.

Detect if the message is a financial QUERY (asking about past data). NOT a new transaction.

If it IS a query, return ONLY valid JSON:
{"is_query": true, "query_type": "balance"|"category"|"period"|"comparison", "category": "<category or null>", "period": "current_month"|"last_month"|"current_week"|"last_week"|null}

query_type:
- "balance"     — balanço receitas − despesas ("saldo", "quanto tenho?", "balanço")
- "category"    — gastos numa categoria ("quanto gastei em supermercado?")
- "period"      — gastos num período específico ("gastos desta semana", "mês passado")
- "comparison"  — comparar dois períodos ("compara este mês com o mês passado")

Categories: supermarkt, restaurant, transport, gezondheid, entertainment, wonen, kleding, abonnement, inkomen, overig

period values:
- "today"         — hoje, vandaag, today, aujourd'hui, heute
- "current_month" — este mês (default for vague queries)
- "last_month"    — mês passado, vorige maand
- "current_week"  — esta semana, deze week
- "last_week"     — semana passada, vorige week

If NOT a query: {"is_query": false}

EXAMPLES:
"quanto gastei em supermercado?" → {"is_query": true, "query_type": "category", "category": "supermarkt", "period": "current_month"}
"o que gastei em transporte?" → {"is_query": true, "query_type": "category", "category": "transport", "period": "current_month"}
"gastos em restaurante na semana passada" → {"is_query": true, "query_type": "category", "category": "restaurant", "period": "last_week"}
"quanto gastei hoje?" → {"is_query": true, "query_type": "period", "category": null, "period": "today"}
"o que gastei hoje?" → {"is_query": true, "query_type": "period", "category": null, "period": "today"}
"quanto gastei esta semana?" → {"is_query": true, "query_type": "period", "category": null, "period": "current_week"}
"gastos da semana passada" → {"is_query": true, "query_type": "period", "category": null, "period": "last_week"}
"gastos do mês passado" → {"is_query": true, "query_type": "period", "category": null, "period": "last_month"}
"compara este mês com o mês passado" → {"is_query": true, "query_type": "comparison", "category": null, "period": null}
"hoeveel heb ik uitgegeven aan eten?" → {"is_query": true, "query_type": "category", "category": "restaurant", "period": "current_month"}
"qual é o meu saldo?" → {"is_query": true, "query_type": "balance", "category": null, "period": "current_month"}
"gastei €45 no Jumbo" → {"is_query": false}
"qual é o tempo hoje?" → {"is_query": false}
"resumo" → {"is_query": false}"""

        response = await client.messages.create(
            model=model,
            max_tokens=128,
            system=system,
            messages=[{"role": "user", "content": text}],
        )

        raw = response.content[0].text
        data = parse_llm_json(raw)
        if data is None:
            logger.info("llm.no_json_object", chars=len(raw))
            return None

        if not data.get("is_query"):
            return None

        logger.info(
            "llm.query_classified",
            query_type=data.get("query_type"),
            category=data.get("category"),
            period=data.get("period"),
        )
        return sanitize_query(data)

    except Exception as exc:
        logger.warning("llm.classify_query_failed", error=str(exc))
        return None


async def extract_expense(
    text: str,
    merchant_overrides: dict[str, str] | None = None,
    lang: str = "en",
) -> dict | None:
    """Try to extract expense data from a message using Claude.

    Args:
        text: The raw user message.
        merchant_overrides: Optional dict mapping lowercase merchant name →
            category, pre-fetched from merchant_category_overrides for this
            member.  When provided, the LLM result's category is replaced with
            the stored override if the merchant matches.

    Returns a dict with keys: amount, currency, merchant, category,
    description — or None if the message is not an expense.
    """
    provider = settings.llm_provider.lower()
    if provider in ("anthropic", "bedrock"):
        result = await _extract_expense_anthropic(text, lang)
    else:
        result = None

    # M12 — apply member-specific merchant→category overrides
    if result and merchant_overrides and result.get("merchant"):
        key = result["merchant"].lower()
        if key in merchant_overrides:
            old_cat = result["category"]
            result["category"] = merchant_overrides[key]
            if old_cat != result["category"]:
                logger.info(
                    "llm.category_override_applied",
                    merchant=result["merchant"],
                    old=old_cat,
                    new=result["category"],
                )
    return result


async def _extract_expense_anthropic(text: str, lang: str = "en") -> dict | None:
    try:
        api_key = settings.llm_api_key.get_secret_value()
        if not api_key:
            return None

        model = _anthropic_model_id(settings.llm_model)
        client = _get_client(api_key)

        system = """You are a financial transaction parser for a WhatsApp assistant.
The user writes in Portuguese, Dutch, English, French, or German — handle all five.

If the message contains a financial transaction, return ONLY valid JSON (no other text):
{"is_expense": true, "type": "expense"|"income", "amount": <float>, "currency": "EUR"|"USD"|"GBP", "merchant": "<store/person or null>", "category": "<category>", "description": "<short description>", "days_ago": <int, 0=today, 1=yesterday>}

Categories: supermarkt, restaurant, transport, gezondheid, entertainment, wonen, kleding, abonnement, inkomen, overig

If NOT a financial transaction: {"is_expense": false}

EXAMPLES:
User: "gastei €45 no Jumbo"
{"is_expense": true, "type": "expense", "amount": 45.0, "currency": "EUR", "merchant": "Jumbo", "category": "supermarkt", "description": "supermarkt", "days_ago": 0}

User: "Uber 23,90"
{"is_expense": true, "type": "expense", "amount": 23.9, "currency": "EUR", "merchant": "Uber", "category": "transport", "description": "Uber", "days_ago": 0}

User: "paguei 180 de luz no pix"
{"is_expense": true, "type": "expense", "amount": 180.0, "currency": "EUR", "merchant": null, "category": "wonen", "description": "luz", "days_ago": 0}

User: "esqueci de anotar o almoço de ontem, 39"
{"is_expense": true, "type": "expense", "amount": 39.0, "currency": "EUR", "merchant": null, "category": "restaurant", "description": "almoço", "days_ago": 1}

User: "recebi 800 do freela"
{"is_expense": true, "type": "income", "amount": 800.0, "currency": "EUR", "merchant": null, "category": "inkomen", "description": "freelance", "days_ago": 0}

User: "recebi €2800 de salário"
{"is_expense": true, "type": "income", "amount": 2800.0, "currency": "EUR", "merchant": null, "category": "inkomen", "description": "salário", "days_ago": 0}

User: "tankde 60 euro bij Shell"
{"is_expense": true, "type": "expense", "amount": 60.0, "currency": "EUR", "merchant": "Shell", "category": "transport", "description": "brandstof", "days_ago": 0}

User: "Netflix 15,99 este mês"
{"is_expense": true, "type": "expense", "amount": 15.99, "currency": "EUR", "merchant": "Netflix", "category": "abonnement", "description": "Netflix", "days_ago": 0}

User: "almoço €45, cartão"
{"is_expense": true, "type": "expense", "amount": 45.0, "currency": "EUR", "merchant": null, "category": "restaurant", "description": "almoço", "days_ago": 0}

User: "boodschappen gedaan, 67,40 bij Albert Heijn"
{"is_expense": true, "type": "expense", "amount": 67.4, "currency": "EUR", "merchant": "Albert Heijn", "category": "supermarkt", "description": "boodschappen", "days_ago": 0}

User: "qual é o tempo hoje?"
{"is_expense": false}

User: "resumo"
{"is_expense": false}"""

        lang_instr = _LANG_INSTRUCTION.get(lang, _LANG_INSTRUCTION["en"])
        system = (
            system
            + f"\n\n{lang_instr}"
            + "\nIMPORTANT: The 'category' field MUST always use the Dutch canonical values listed above."
            + " The 'description' field MUST be in the user's language."
        )
        response = await client.messages.create(
            model=model,
            max_tokens=256,
            system=system,
            messages=[{"role": "user", "content": text}],
        )

        raw = response.content[0].text
        data = parse_llm_json(raw)
        if data is None:
            logger.info("llm.no_json_object", chars=len(raw))
            return None

        if not data.get("is_expense"):
            return None

        return sanitize_expense(data)

    except Exception as exc:
        logger.warning("llm.extract_expense_failed", error=str(exc))
        return None


_MULTI_SYSTEM = """You split ONE WhatsApp message into its separate financial transactions.
The user writes in Portuguese, Dutch, English, French, or German.

Return ONLY valid JSON (no other text):
{"items": [{"type": "expense"|"income", "amount": <float>, "currency": "EUR"|"USD"|"GBP", "merchant": "<store/person or null>", "category": "<category>", "description": "<short description>", "days_ago": <int>}]}

Categories: supermarkt, restaurant, transport, gezondheid, entertainment, wonen, kleding, abonnement, inkomen, overig
Only include items that have BOTH an amount and something it was spent on / received for.
If the message is not a list of transactions, return {"items": []}.
Do not invent amounts. Amounts are positive numbers; the sign lives in "type"."""

MAX_MULTI_ITEMS = 10


async def extract_expenses_multi(text: str, lang: str = "en") -> list[dict]:
    """Split a message with several transactions ("mercado 20 e farmácia 10").

    Returns the sanitized items (possibly empty). Items the model got wrong are dropped by
    ``sanitize_expense`` rather than guessed at.
    """
    try:
        api_key = settings.llm_api_key.get_secret_value()
        if not api_key or settings.llm_provider.lower() not in ("anthropic", "bedrock"):
            return []
        client = _get_client(api_key)
        lang_instr = _LANG_INSTRUCTION.get(lang, _LANG_INSTRUCTION["en"])
        response = await client.messages.create(
            model=_anthropic_model_id(settings.llm_model),
            max_tokens=768,
            system=(
                _MULTI_SYSTEM
                + f"\n\n{lang_instr}"
                + "\nThe 'category' field MUST use the Dutch canonical values above;"
                + " 'description' MUST be in the user's language."
            ),
            messages=[{"role": "user", "content": text}],
        )
        data = parse_llm_json(response.content[0].text)
        if not isinstance(data, dict) or not isinstance(data.get("items"), list):
            return []
        items = []
        for raw in data["items"][:MAX_MULTI_ITEMS]:
            clean = sanitize_expense(raw) if isinstance(raw, dict) else None
            if clean:
                items.append(clean)
        return items
    except Exception as exc:
        logger.warning("llm.extract_multi_failed", error=str(exc))
        return []


# ── M7 — extract_workout ────────────────────────────────────────────────────

_WORKOUT_SYSTEM = """You extract workout/exercise session data from a user message.
Return JSON only, no markdown fences. Fields:
- is_workout: bool (true if this describes an exercise/workout session)
- activity_type: string (e.g. "running", "strength", "cycling", "yoga", "swimming", "walking", "football", "basketball", "pilates", "hiit", "other")
- duration_minutes: integer or null
- distance_km: float or null
- notes: string or null (any extra detail)
- days_ago: integer (0=today, 1=yesterday, etc.)
If not a workout entry, return {"is_workout": false}.
"""


async def extract_workout(text: str, lang: str = "en") -> dict | None:
    """Extract workout session data from natural language text."""
    try:
        api_key = settings.llm_api_key.get_secret_value()
        if not api_key:
            return None
        model = _anthropic_model_id(settings.llm_model)
        client = _get_client(api_key)
        lang_instr = _LANG_INSTRUCTION.get(lang, _LANG_INSTRUCTION["en"])
        workout_system = _WORKOUT_SYSTEM + f"\n\n{lang_instr}"

        response = await client.messages.create(
            model=model,
            max_tokens=256,
            system=workout_system,
            messages=[{"role": "user", "content": text}],
        )
        raw = response.content[0].text
        data = parse_llm_json(raw)
        if data is None:
            logger.info("llm.no_json_object", chars=len(raw))
            return None

        if not data.get("is_workout"):
            return None

        return sanitize_workout(data)
    except Exception as exc:
        logger.warning("llm.extract_workout_failed", error=str(exc))
        return None


# ── M8 — extract_health_log ─────────────────────────────────────────────────

_HEALTH_SYSTEM = """You extract health log data from a user message.
Return JSON only, no markdown fences. Fields:
- is_health: bool (true if this is a health/medication/mood/sleep/water log)
- log_type: string — one of: "medication", "mood", "sleep", "water"
- value: string (the main value: medication name, mood score, hours slept, litres drunk)
- unit: string or null ("/10" for mood, "hours" for sleep, "L" for water, null for medication)
- notes: string or null
- days_ago: integer (0=today, 1=yesterday)
If not a health log, return {"is_health": false}.
Examples:
- "tomei omeprazol" → {is_health:true, log_type:"medication", value:"omeprazol", unit:null}
- "humor 7/10" → {is_health:true, log_type:"mood", value:"7", unit:"/10"}
- "dormi 6h" → {is_health:true, log_type:"sleep", value:"6", unit:"hours"}
- "bebi 2L de água" → {is_health:true, log_type:"water", value:"2", unit:"L"}
- "sinto-me um 6 em 10" → {is_health:true, log_type:"mood", value:"6", unit:"/10"}
- "ontem dormi 6h30" → {is_health:true, log_type:"sleep", value:"6.5", unit:"hours", days_ago:1}
- "mais 500ml de água" → {is_health:true, log_type:"water", value:"0.5", unit:"L"}
Convert ml to litres and minutes to decimal hours. Weight, blood pressure and anything else
are NOT supported: return {"is_health": false}.
"""


async def extract_health_log(text: str, lang: str = "en") -> dict | None:
    """Extract health log entry from natural language text."""
    try:
        api_key = settings.llm_api_key.get_secret_value()
        if not api_key:
            return None
        model = _anthropic_model_id(settings.llm_model)
        client = _get_client(api_key)
        lang_instr = _LANG_INSTRUCTION.get(lang, _LANG_INSTRUCTION["en"])
        health_system = _HEALTH_SYSTEM + f"\n\n{lang_instr}"

        response = await client.messages.create(
            model=model,
            max_tokens=256,
            system=health_system,
            messages=[{"role": "user", "content": text}],
        )
        raw = response.content[0].text
        data = parse_llm_json(raw)
        if data is None:
            logger.info("llm.no_json_object", chars=len(raw))
            return None

        if not data.get("is_health"):
            return None

        return sanitize_health(data)
    except Exception as exc:
        logger.warning("llm.extract_health_log_failed", error=str(exc))
        return None


# ── M9 — extract_habit ──────────────────────────────────────────────────────

_HABIT_SYSTEM = """You extract habit/routine log data from a user message.
Return JSON only, no markdown fences. Fields:
- is_habit: bool (true if this describes completing a habit, routine or personal activity)
- activity: string (short label, e.g. "meditação", "leitura", "exercício", "alemão")
- notes: string or null
- days_ago: integer (0=today, 1=yesterday)
If this is a financial transaction, workout or health log, return {"is_habit": false}.
Examples:
- "meditei hoje" → {"is_habit":true,"activity":"meditação","days_ago":0}
- "li 30 páginas" → {"is_habit":true,"activity":"leitura","notes":"30 páginas","days_ago":0}
- "aprendi alemão" → {"is_habit":true,"activity":"aprender alemão","days_ago":0}
- "gastei 50€" → {"is_habit":false}
- "corri 5km" → {"is_habit":false}
"""


async def extract_habit(text: str, lang: str = "en") -> dict | None:
    """Extract habit log entry from natural language text."""
    try:
        api_key = settings.llm_api_key.get_secret_value()
        if not api_key:
            return None
        model = _anthropic_model_id(settings.llm_model)
        client = _get_client(api_key)
        lang_instr = _LANG_INSTRUCTION.get(lang, _LANG_INSTRUCTION["en"])
        habit_system = _HABIT_SYSTEM + f"\n\n{lang_instr}"

        response = await client.messages.create(
            model=model,
            max_tokens=256,
            system=habit_system,
            messages=[{"role": "user", "content": text}],
        )
        raw = response.content[0].text
        data = parse_llm_json(raw)
        if data is None:
            logger.info("llm.no_json_object", chars=len(raw))
            return None

        if not data.get("is_habit"):
            return None

        return sanitize_habit(data)
    except Exception as exc:
        logger.warning("llm.extract_habit_failed", error=str(exc))
        return None
