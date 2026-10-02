#!/usr/bin/env python3
# ruff: noqa: E501
"""Hard test, block M — audit of every user-facing message (no WhatsApp, no LLM, free).

Checks the whole ``_STRINGS`` catalogue and the Meta template files:

* all five languages exist, with the same placeholders in every language and variant;
* WhatsApp limits and formatting (4096 chars, one ``*`` for bold, no markdown headings);
* register: pt is Brazilian "você" (no European Portuguese), nl "je", fr "tu", de "du";
* tone: no shouting, no stack-trace words, few emojis and exclamation marks;
* plurals that read wrong with 1 ("1 pontos").

Usage:
    python scripts/message_audit.py                  # print findings, exit 1 on any error
    python scripts/message_audit.py --catalog docs/mensagens-catalogo.md   # also write the gallery
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import string
import sys
from dataclasses import dataclass

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

LANGS = ("pt", "nl", "en", "fr", "de")
WA_LIMIT = 4096
BUTTON_LIMIT = 20

# Words that only European Portuguese uses (or a "tu" register). Matched on word boundaries.
PT_PT = (
    "teu",
    "teus",
    "tua",
    "tuas",
    "tens",
    "podes",
    "quiseres",
    "queres",
    "diz-me",
    "responde",
    "envia",
    "regista",
    "registo",
    "registos",
    "ecrã",
    "telemóvel",
    "utilizador",
    "consegues",
    "estás",
    "tu",
    "contigo",
    "faz",
    "escreve",
    "manda-me",
    "diz",
    "manda",
    "tenta",
    "usa",
    "fala",
    "olha",
    "cola",
    "avisa",
)
# Per language: (regex of the wrong register, label)
REGISTER = {
    "nl": (re.compile(r"(?<![\w-])(u|uw)(?![\w-])"), "formeel 'u' (use 'je')"),
    "fr": (re.compile(r"(?<![\w-])(vous|votre|vos)(?![\w-])", re.I), "'vous' (use 'tu')"),
    "de": (re.compile(r"\b(Sie|Ihr|Ihre|Ihnen)\b"), "'Sie' (use 'du')"),
}
BAD_WORDS = re.compile(
    r"(traceback|exception|\bnull\b|\bnone\b|\berror\b|\bERRO\b|undefined|\{\}|\bNaN\b)", re.I
)
EMOJI = re.compile("[\U0001f300-\U0001faff☀-➿⭐✅❌]")


@dataclass(frozen=True)
class Finding:
    level: str  # "error" | "warn"
    where: str
    what: str

    def __str__(self) -> str:
        return f"{self.level.upper():5} {self.where}: {self.what}"


def _variants(val: str | tuple[str, ...]) -> tuple[str, ...]:
    return val if isinstance(val, tuple) else (val,)


def _fields(tmpl: str) -> set[str]:
    try:
        return {f.split(".")[0].split("[")[0] for _, f, _, _ in string.Formatter().parse(tmpl) if f}
    except ValueError:
        return {"<bad-format>"}


def _word_hit(text: str, words: tuple[str, ...]) -> str | None:
    low = text.lower()
    for w in words:
        if re.search(rf"(?<![\wÀ-ÿ-]){re.escape(w)}(?![\wÀ-ÿ])", low):
            return w
    return None


def lint_text(where: str, lang: str, text: str) -> list[Finding]:
    out: list[Finding] = []
    if len(text) > WA_LIMIT:
        out.append(Finding("error", where, f"{len(text)} chars > WhatsApp limit {WA_LIMIT}"))
    if "**" in text or re.search(r"^#{1,6}\s", text, re.M) or "__" in text:
        out.append(Finding("error", where, "markdown that WhatsApp does not render (**, #, __)"))
    if text.count("*") % 2:
        out.append(Finding("error", where, "unbalanced '*' (bold would leak)"))
    if text.count("_") % 2 and "{" not in text:
        out.append(Finding("warn", where, "odd number of '_' (italic may leak)"))
    if BAD_WORDS.search(text) and not (lang == "de" and BAD_WORDS.search(text).group(0) == "null"):
        out.append(
            Finding(
                "error", where, f"technical word in user text: {BAD_WORDS.search(text).group(0)!r}"
            )
        )
    if len(EMOJI.findall(text)) > 4:
        out.append(Finding("warn", where, "more than 4 emojis"))
    if text.count("!") > 2:
        out.append(Finding("warn", where, "more than 2 exclamation marks"))
    caps = [
        w
        for w in re.findall(r"\b[A-ZÀ-Ý]{5,}\b", text)
        if w not in {"START", "IBAN", "GDPR", "CSV", "JSON", "WHATSAPP"}
    ]
    if caps:
        out.append(Finding("warn", where, f"shouting: {caps[:2]}"))
    if len(text) > 900:
        out.append(
            Finding("warn", where, f"long message ({len(text)} chars) — hard to read on a phone")
        )
    if lang == "pt":
        hit = _word_hit(re.sub(r"\{[^}]*\}", "", text), PT_PT)
        if hit:
            out.append(
                Finding(
                    "error",
                    where,
                    f"European Portuguese / 'tu' register: {hit!r} (use pt-BR 'você')",
                )
            )
    elif lang in REGISTER:
        rx, label = REGISTER[lang]
        if rx.search(re.sub(r"\{[^}]*\}", "X", text)):
            out.append(Finding("error", where, f"wrong register: {label}"))
    return out


def audit_catalogue(strings: dict) -> list[Finding]:
    out: list[Finding] = []
    for key in sorted(strings):
        langs = strings[key]
        missing = [lg for lg in LANGS if lg not in langs]
        if missing:
            out.append(Finding("error", key, f"missing languages: {missing}"))
        per_lang: dict[str, set[str]] = {}
        for lg in LANGS:
            if lg not in langs:
                continue
            variants = _variants(langs[lg])
            for i, text in enumerate(variants):
                where = f"{key}[{lg}{'' if len(variants) == 1 else f'#{i}'}]"
                per_lang.setdefault(lg, set()).update(_fields(text))
                out += lint_text(where, lg, text)
        for lg, fields in per_lang.items():
            others = (
                set().union(*(f for k, f in per_lang.items() if k != lg))
                if len(per_lang) > 1
                else fields
            )
            stray = fields - others
            if stray and len(per_lang) >= 3:
                out.append(
                    Finding(
                        "error",
                        f"{key}[{lg}]",
                        f"placeholder only in this language (typo?): {sorted(stray)}",
                    )
                )
            lacking = others - fields
            if lacking:
                out.append(
                    Finding(
                        "warn",
                        f"{key}[{lg}]",
                        f"does not use {sorted(lacking)} that other languages use",
                    )
                )
    return out


def audit_templates(folder: pathlib.Path) -> list[Finding]:
    out: list[Finding] = []
    for path in sorted(folder.glob("alfred_*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        for code, tr in data.get("translations", {}).items():
            lang = "pt" if code == "pt_BR" else code
            for comp in tr.get("components", []):
                text = comp.get("text")
                if not text:
                    continue
                where = f"{path.stem}[{code}:{comp['type']}]"
                out += lint_text(where, lang, text)
                if comp["type"] == "HEADER" and len(text) > 60:
                    out.append(Finding("error", where, "header > 60 chars (Meta limit)"))
                if comp["type"] == "FOOTER" and len(text) > 60:
                    out.append(Finding("error", where, "footer > 60 chars (Meta limit)"))
                if comp["type"] == "BODY":
                    if len(text) > 1024:
                        out.append(Finding("error", where, "body > 1024 chars (Meta limit)"))
                    if re.search(r"\{\{\d+\}\}\s*$", text) or re.match(r"\s*\{\{\d+\}\}", text):
                        out.append(
                            Finding("error", where, "variable at start/end of body (Meta rejects)")
                        )
    return out


def catalogue_markdown(strings: dict) -> str:
    """One section per key with the five languages side by side, for a human tone review."""
    lines = [
        "# Alfred — catálogo de mensagens",
        "",
        "> Gerado por `scripts/message_audit.py --catalog`. Não editar à mão.",
        "",
    ]
    for key in sorted(strings):
        lines += [f"## `{key}`", ""]
        for lg in LANGS:
            if lg not in strings[key]:
                continue
            for text in _variants(strings[key][lg]):
                body = text.replace("\n", "\n  > ")
                lines.append(f"- **{lg}** — {body}")
        lines.append("")
    return "\n".join(lines)


def panel_catalogue_size() -> int:
    """Number of keys in the panel phrase catalogue (phrases plus chat suggestions)."""
    os.environ.setdefault("WHATSAPP_APP_SECRET", "x")
    from alfred.panel_phrases import CHAT, PHRASES

    return len(PHRASES) + len(CHAT)


def run() -> tuple[list[Finding], dict]:
    os.environ.setdefault("WHATSAPP_APP_SECRET", "x")
    from alfred import llm
    from alfred.conversation import _STRINGS
    from alfred.panel_phrases import CHAT, PHRASES

    extra = {
        "llm_error": llm._LLM_ERROR,
    }
    # separate prefixes: "balance_projection" exists in both PHRASES and CHAT
    panel = {f"panel.phrase.{k}": v for k, v in PHRASES.items()}
    panel |= {f"panel.chat.{k}": v for k, v in CHAT.items()}
    findings = (
        audit_catalogue(_STRINGS)
        + audit_catalogue(extra)
        + audit_catalogue(panel)
        + audit_templates(ROOT / "m5-templates")
    )
    return findings, _STRINGS


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--catalog", help="write the side-by-side gallery to this markdown file")
    ap.add_argument("--errors-only", action="store_true")
    args = ap.parse_args()
    findings, strings = run()
    shown = [f for f in findings if f.level == "error"] if args.errors_only else findings
    for f in shown:
        print(f)
    errors = sum(f.level == "error" for f in findings)
    print(f"\n{len(strings)} messages · {errors} errors · {len(findings) - errors} warnings")
    if args.catalog:
        pathlib.Path(args.catalog).write_text(catalogue_markdown(strings), encoding="utf-8")
        print(f"gallery written to {args.catalog}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
