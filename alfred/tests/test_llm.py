"""LLM layer — shared async client and prompt safety."""
from __future__ import annotations

from unittest.mock import patch

from alfred import llm


def test_client_is_async_shared_and_has_timeout() -> None:
    llm._client = None
    with patch("anthropic.AsyncAnthropic") as ctor:
        a = llm._get_client("key-1")
        b = llm._get_client("key-1")
    assert a is b
    ctor.assert_called_once()
    assert ctor.call_args.kwargs["timeout"] == llm.settings.llm_timeout_seconds
    assert ctor.call_args.kwargs["max_retries"] == 2
    llm._client = None


def test_reply_prompt_never_claims_expense_was_saved() -> None:
    """generate_reply only runs when nothing was recorded — it must not confirm a save."""
    assert "verloopt automatisch" not in llm._SYSTEM_PROMPT
    assert "NIET geregistreerd" in llm._SYSTEM_PROMPT
