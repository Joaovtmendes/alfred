"""Hard test, block M — what the user actually receives, in all five languages.

Layer 1 lints the whole catalogue (no DB). Layer 2 walks a real conversation per language
through the router and lints every reply it produced: composed strings can break rules the
catalogue cannot show ("1 dias", a leaked ``None``, a bare ``{name}``).
"""

from __future__ import annotations

import importlib.util
import os
import pathlib
import sys

import pytest
from sqlalchemy import update

from alfred.conversation import _pt_singular, _t
from alfred.db import AsyncSessionLocal
from alfred.models import Member
from tests.labkit import Lab, lab  # noqa: F401

_spec = importlib.util.spec_from_file_location(
    "message_audit", pathlib.Path(__file__).parent.parent / "scripts" / "message_audit.py"
)
audit = importlib.util.module_from_spec(_spec)
sys.modules["message_audit"] = audit
_spec.loader.exec_module(audit)

db = pytest.mark.skipif(os.environ.get("ALFRED_TEST_DB") != "1", reason="needs test DB")


def test_catalogue_and_templates_have_no_errors() -> None:
    findings, strings = audit.run()
    errors = [str(f) for f in findings if f.level == "error"]
    assert not errors, "\n".join(errors)
    assert len(strings) > 250


def test_portuguese_one_is_singular() -> None:
    assert _pt_singular("atrasada há 1 dias") == "atrasada há 1 dia"
    assert _pt_singular("sobe cerca de 1 pontos.") == "sobe cerca de 1 ponto."
    assert _pt_singular("faltam 11 dias") == "faltam 11 dias"
    assert _pt_singular("R$ 1,5 dias") == "R$ 1,5 dias"
    assert _t("ledger_overdue", "pt", days=1) == "atrasada há 1 dia"
    assert _t("ledger_overdue", "pt", days=3) == "atrasada há 3 dias"


BATTERY = [
    "ajuda",
    "saldo",
    "meu dashboard",
    "a pagar luz 120 dia 20",
    "a receber 300 do João",
    "paguei a luz",
    "orçamento mercado 300",
    "dentista amanhã às 14h",
    "minha agenda",
    "minha pontuação",
    "meu score",
    "o que você me enviou",
    "exportar meus dados",
    "lembrete tomar remédio às 8h",
    "resumo do mês",
    "quanto gastei esta semana",
    "tarefa comprar pão",
    "minhas tarefas",
    "desfazer",
]


@db
@pytest.mark.parametrize("lang", ["pt", "nl", "en", "fr", "de"])
async def test_every_reply_is_clean(lab: Lab, lang: str) -> None:  # noqa: F811
    async with AsyncSessionLocal() as s:
        await s.execute(update(Member).where(Member.id == lab.member_id).values(language=lang))
        await s.commit()
    for body in BATTERY:
        try:
            await lab.say(body)
        except AssertionError:  # a handler that sends two messages: still lint what was sent
            pass
    assert lab.sent, "the battery produced no reply at all"
    problems: list[str] = []
    for i, text in enumerate(lab.sent):
        where = f"{lang} reply#{i}"
        problems += [str(f) for f in audit.lint_text(where, lang, text) if f.level == "error"]
        if "None" in text or "{" in text or "}" in text:
            problems.append(f"{where}: leaked placeholder/None: {text[:80]!r}")
        if lang == "pt":
            assert not audit._word_hit(text, audit.PT_PT), (where, text)
    assert not problems, "\n".join(problems)
