"""LLM integration — supports Anthropic direct API and AWS Bedrock."""
from __future__ import annotations

import json
import structlog

from alfred.models import Member, Message
from alfred.settings import settings

logger = structlog.get_logger()

_SYSTEM_PROMPT = """Je bent Alfred, een behulpzame persoonlijke assistent via WhatsApp.
Je helpt gebruikers met dagelijkse taken zoals het bijhouden van uitgaven, herinneringen en planning.
Wees beknopt, vriendelijk en professioneel. Antwoord altijd in de taal van de gebruiker.
Je bent een AI-assistent — geen mens."""


def _bedrock_model_id(model: str) -> str:
    """Normalise model name for Bedrock (adds prefix if needed)."""
    if model.startswith("anthropic."):
        return model
    # e.g. "claude-3-5-sonnet-20241022" → Bedrock cross-region inference profile
    return f"anthropic.{model}"


def _anthropic_model_id(model: str) -> str:
    """Normalise model name for Anthropic direct API (strips Bedrock prefix)."""
    if model.startswith("anthropic."):
        # "anthropic.claude-3-5-sonnet-20241022-v2:0" → "claude-3-5-sonnet-20241022"
        m = model.removeprefix("anthropic.")
        m = m.split(":")[0]          # strip ":0" version suffix
        m = m.removesuffix("-v2")    # strip "-v2" if present
        m = m.removesuffix("-v1")
        return m
    return model


async def generate_reply(member: Member, message: Message) -> str:
    """Generate a reply using the configured LLM provider."""
    provider = settings.llm_provider.lower()

    if provider == "anthropic":
        return await _reply_anthropic(member, message)
    elif provider == "bedrock":
        return await _reply_bedrock(member, message)
    else:
        logger.warning("llm.unknown_provider", provider=provider)
        return f"[echo] {message.body}"


async def _reply_anthropic(member: Member, message: Message) -> str:
    """Call Anthropic API directly."""
    try:
        import anthropic  # type: ignore[import]

        api_key = settings.llm_api_key.get_secret_value()
        if not api_key:
            logger.warning("llm.anthropic_key_missing")
            return "Sorry, ik kan je bericht nu niet verwerken. Probeer het later opnieuw."

        model = _anthropic_model_id(settings.llm_model)
        client = anthropic.Anthropic(api_key=api_key)

        response = client.messages.create(
            model=model,
            max_tokens=512,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": message.body or ""}],
        )

        reply = response.content[0].text
        logger.info(
            "llm.reply_generated",
            provider="anthropic",
            model=model,
            wa_phone=member.wa_phone,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )
        return reply

    except ImportError:
        logger.warning("llm.anthropic_not_installed")
        return f"[echo] {message.body}"
    except Exception as exc:
        logger.error("llm.error", error=str(exc))
        return "Sorry, ik kan je bericht nu niet verwerken. Probeer het later opnieuw."


async def _reply_bedrock(member: Member, message: Message) -> str:
    """Call Claude via AWS Bedrock."""
    try:
        import boto3  # type: ignore[import]

        model = _bedrock_model_id(settings.llm_model)
        client = boto3.client("bedrock-runtime", region_name=settings.aws_region)

        body = json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 512,
            "system": _SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": message.body or ""}],
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
            input_tokens=result.get("usage", {}).get("input_tokens"),
            output_tokens=result.get("usage", {}).get("output_tokens"),
        )
        return reply

    except ImportError:
        logger.warning("llm.boto3_not_installed")
        return f"[echo] {message.body}"
    except Exception as exc:
        logger.error("llm.error", error=str(exc))
        return "Sorry, ik kan je bericht nu niet verwerken. Probeer het later opnieuw."
