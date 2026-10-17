"""Guarda da marca: o nome visível é Jofie, e 'Alfred' fica só no código interno.

Os identificadores internos (pacote ``alfred``, banco, variáveis de ambiente, nomes dos
templates da Meta) continuam como estão. O que o usuário lê não pode mais dizer "Alfred".
"""

from __future__ import annotations

import pathlib
import re
import tokenize

from alfred import legal
from alfred.signup import WORDMARK_SVG, _brand

SRC = pathlib.Path(__file__).resolve().parent.parent / "src" / "alfred"


def _visible_string_literals():
    """Literais de texto (não docstrings) do código, que podem chegar ao usuário."""
    for path in SRC.rglob("*.py"):
        with open(path, "rb") as f:
            for tok in tokenize.tokenize(f.readline):
                if tok.type != tokenize.STRING:
                    continue
                if tok.string.lstrip("rbfuRBFU").startswith(('"""', "'''")):
                    continue  # docstring: texto de desenvolvedor
                yield path, tok.start[0], tok.string


def test_no_alfred_in_user_facing_strings():
    offenders = [
        (str(p), line)
        for p, line, text in _visible_string_literals()
        if re.search(r"\bAlfred\b", text)
    ]
    assert offenders == []


def test_legal_text_names_jofie_and_bumps_version():
    assert legal.POLICY_VERSION == "2.1"
    assert legal.POLICY_DATE == "2026-10-16"


def test_brand_block_is_wordmark_only():
    html = _brand()
    assert html.count("<svg") == 1
    assert WORDMARK_SVG in html and 'aria-label="Jofie"' in html
    assert "Alfred" not in html


def test_panel_shell_has_jofie_brand():
    shell = (SRC / "panel" / "shell.html").read_text(encoding="utf-8")
    assert WORDMARK_SVG.replace("\n", "") in shell
    assert "Alfred" not in shell.replace("AlfredPanel", "")
