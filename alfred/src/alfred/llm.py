"""LLM integration — supports Anthropic direct API and AWS Bedrock."""
from __future__ import annotations

import json
import structlog

from alfred.models import Member, Message
from alfred.settings import settings

logger = structlog.get_logger()

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

Als de gebruiker een uitgave meldt: bevestig kort, de registratie verloopt automatisch.
Als de gebruiker om een overzicht vraagt: zeg dat je het ophaalt."""



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
    "fr": "Réponds TOUJOURS em français. Ne mélange jamais avec l'anglais ou le néerlandais, même si l'historique contient d'autres langues.",
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
        import anthropic  # type: ignore[import]

        api_key = settings.llm_api_key.get_secret_value()
        if not api_key:
            logger.warning("llm.anthropic_key_missing")
            return _LLM_ERROR.get(getattr(member, "language", "en"), _LLM_ERROR["en"])

        model = _anthropic_model_id(settings.llm_model)
        client = anthropic.Anthropic(api_key=api_key)

        messages: list[dict] = list(history or [])
        messages.append({"role": "user", "content": message.body or ""})

        lang = getattr(member, "language", "en") or "en"
        lang_instr = _LANG_INSTRUCTION.get(lang, _LANG_INSTRUCTION["en"])
        system_prompt = f"{_SYSTEM_PROMPT}\n\n{lang_instr}"

        response = client.messages.create(
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
            wa_phone=member.wa_phone,
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

        body = json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 512,
            "system": system_prompt,
            "messages": messages,
        })

        response = client.invoke_model(
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
            wa_phone=member.wa_phone,
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
        import anthropic  # type: ignore[import]

        api_key = settings.llm_api_key.get_secret_value()
        if not api_key:
            return None

        model = _anthropic_model_id(settings.llm_model)
        client = anthropic.Anthropic(api_key=api_key)

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
- "current_month" — este mês (default for vague queries)
- "last_month"    — mês passado, vorige maand
- "current_week"  — esta semana, deze week
- "last_week"     — semana passada, vorige week

If NOT a query: {"is_query": false}

EXAMPLES:
"quanto gastei em supermercado?" → {"is_query": true, "query_type": "category", "category": "supermarkt", "period": "current_month"}
"o que gastei em transporte?" → {"is_query": true, "query_type": "category", "category": "transport", "period": "current_month"}
"gastos em restaurante na semana passada" → {"is_query": true, "query_type": "category", "category": "restaurant", "period": "last_week"}
"quanto gastei esta semana?" → {"is_query": true, "query_type": "period", "category": null, "period": "current_week"}
"gastos da semana passada" → {"is_query": true, "query_type": "period", "category": null, "period": "last_week"}
"gastos do mês passado" → {"is_query": true, "query_type": "period", "category": null, "period": "last_month"}
"compara este mês com o mês passado" → {"is_query": true, "query_type": "comparison", "category": null, "period": null}
"hoeveel heb ik uitgegeven aan eten?" → {"is_query": true, "query_type": "category", "category": "restaurant", "period": "current_month"}
"qual é o meu saldo?" → {"is_query": true, "query_type": "balance", "category": null, "period": "current_month"}
"gastei €45 no Jumbo" → {"is_query": false}
"qual é o tempo hoje?" → {"is_query": false}
"resumo" → {"is_query": false}"""

        response = client.messages.create(
            model=model,
            max_tokens=128,
            system=system,
            messages=[{"role": "user", "content": text}],
        )

        raw = response.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        data = json.loads(raw.strip())

        if not data.get("is_query"):
            return None

        logger.info(
            "llm.query_classified",
            query_type=data.get("query_type"),
            category=data.get("category"),
            period=data.get("period"),
        )
        return {
            "query_type": data.get("query_type", "period"),
            "category": data.get("category") or None,
            "period": data.get("period") or "current_month",
        }

    except Exception as exc:
        logger.warning("llm.classify_query_failed", error=str(exc))
        return None


async def extract_expense(
    text: str,
    merchant_overrides: dict[str, str] | None = None,
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
        result = await _extract_expense_anthropic(text)
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


async def _extract_expense_anthropic(text: str) -> dict | None:
    try:
        import anthropic  # type: ignore[import]

        api_key = settings.llm_api_key.get_secret_value()
        if not api_key:
            return None

        model = _anthropic_model_id(settings.llm_model)
        client = anthropic.Anthropic(api_key=api_key)

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

        response = client.messages.create(
            model=model,
            max_tokens=256,
            system=system,
            messages=[{"role": "user", "content": text}],
        )

        raw = response.content[0].text.strip()
        # Strip markdown code fences if present
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        data = json.loads(raw.strip())

        if not data.get("is_expense"):
            return None

        return {
            "amount": float(data.get("amount", 0)),
            "currency": data.get("currency", "EUR"),
            "merchant": data.get("merchant") or None,
            "category": data.get("category", "overig"),
            "description": data.get("description", ""),
            "type": data.get("type", "expense"),
            "days_ago": int(data.get("days_ago", 0)),
        }

    except Exception as exc:
        logger.warning("llm.extract_expense_failed", error=str(exc))
        return None
