#!/usr/bin/env python3
"""Registro de bugs do Alfred e a regra "todo bug tem teste de regressão".

O registro é ``tests/bugs.json``. Cada bug tem um ID e um estado:

* ``open``    — conhecido, ainda não corrigido. Bug novo precisa de um teste marcado
                ``@pytest.mark.bug("ID")`` + ``@pytest.mark.xfail(strict=True)``: quando a
                correção entrar, o xfail passa a XPASS, o CI falha e obriga a remover o xfail
                e marcar o bug como ``fixed``.
* ``fixed``   — corrigido. Exige pelo menos um teste marcado e SEM xfail.
* ``manual``  — só se confirma fora do pytest (Meta, modelo, produção). Exige ``reason``.
* ``wontfix`` — decisão de não corrigir. Exige ``reason``.

Bugs herdados (``"legacy": true``) podem ficar ``open`` sem teste enquanto o backlog antigo é
coberto; bug NOVO (sem ``legacy``) nunca pode.

Uso (a partir de ``alfred/``)::

    python scripts/bugs.py check                     # a regra; roda no CI
    python scripts/bugs.py list [--status open]
    python scripts/bugs.py add --title "..." --message "gastei 50 reais" [--severity alta]
    python scripts/bugs.py from-report relatorio.json
    python scripts/bugs.py fixed B-001
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REGISTRY = ROOT / "tests" / "bugs.json"
TESTS_DIR = ROOT / "tests"
REGRESSION_DIR = TESTS_DIR / "regression"

STATUSES = ("open", "fixed", "manual", "wontfix")
SEVERITIES = ("alta", "media", "baixa")
REQUIRED = ("id", "title", "status")
NEW_ID_RE = re.compile(r"^B-(\d{3,})$")


# ── registry I/O ─────────────────────────────────────────────────────────────


def load(path: Path = REGISTRY) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data["bugs"]


def save(bugs: list[dict], path: Path = REGISTRY) -> None:
    path.write_text(
        json.dumps({"bugs": bugs}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


# ── scan the tests for @pytest.mark.bug("ID") ─────────────────────────────────


def _mark_name(node: ast.expr) -> str | None:
    """``pytest.mark.bug(...)`` -> ``"bug"``; ``pytest.mark.xfail(...)`` -> ``"xfail"``."""
    call = node.func if isinstance(node, ast.Call) else node
    if (
        isinstance(call, ast.Attribute)
        and isinstance(call.value, ast.Attribute)
        and call.value.attr == "mark"
    ):
        return call.attr
    return None


def _bug_ids(decorators: list[ast.expr]) -> list[str]:
    ids: list[str] = []
    for dec in decorators:
        if _mark_name(dec) == "bug" and isinstance(dec, ast.Call):
            ids += [
                a.value
                for a in dec.args
                if isinstance(a, ast.Constant) and isinstance(a.value, str)
            ]
    return ids


def scan_tests(tests_dir: Path = TESTS_DIR) -> dict[str, list[dict]]:
    """Map bug ID -> [{"test": "file::name", "xfail": bool}, ...].

    Looks at decorators of test functions and of classes (a class mark applies to its
    methods). A mark inside a ``pytest.param`` or a module-level ``pytestmark`` is not
    supported on purpose: keep the mark on the test so a reader sees it.
    """
    found: dict[str, list[dict]] = {}
    for path in sorted(tests_dir.rglob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        rel = path.relative_to(tests_dir.parent).as_posix()

        def visit(body, inherited_ids, inherited_xfail, rel=rel):
            for node in body:
                if isinstance(node, ast.ClassDef):
                    ids = inherited_ids + _bug_ids(node.decorator_list)
                    xf = inherited_xfail or any(
                        _mark_name(d) == "xfail" for d in node.decorator_list
                    )
                    visit(node.body, ids, xf)
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if not node.name.startswith("test"):
                        continue
                    ids = inherited_ids + _bug_ids(node.decorator_list)
                    xf = inherited_xfail or any(
                        _mark_name(d) == "xfail" for d in node.decorator_list
                    )
                    for bug_id in ids:
                        found.setdefault(bug_id, []).append(
                            {"test": f"{rel}::{node.name}", "xfail": xf}
                        )

        visit(tree.body, [], False)
    return found


# ── the rule ─────────────────────────────────────────────────────────────────


def check(bugs: list[dict], marks: dict[str, list[dict]]) -> list[str]:
    """Return the list of violations (empty = the rule holds)."""
    errors: list[str] = []
    seen: set[str] = set()
    for bug in bugs:
        bug_id = bug.get("id", "?")
        for field in REQUIRED:
            if not bug.get(field):
                errors.append(f"{bug_id}: falta o campo '{field}'")
        if bug_id in seen:
            errors.append(f"{bug_id}: ID repetido no registro")
        seen.add(bug_id)
        status = bug.get("status")
        if status not in STATUSES:
            errors.append(f"{bug_id}: estado '{status}' inválido (use {', '.join(STATUSES)})")
            continue
        if bug.get("severity") and bug["severity"] not in SEVERITIES:
            errors.append(f"{bug_id}: gravidade '{bug['severity']}' inválida")
        tests = marks.get(bug_id, [])
        legacy = bool(bug.get("legacy"))
        if status == "fixed":
            if not tests:
                errors.append(f"{bug_id}: corrigido sem teste de regressão (@pytest.mark.bug)")
            elif any(t["xfail"] for t in tests):
                names = ", ".join(t["test"] for t in tests if t["xfail"])
                errors.append(f"{bug_id}: corrigido mas o teste ainda tem xfail: {names}")
        elif status == "open":
            if not tests and not legacy:
                errors.append(
                    f"{bug_id}: bug novo aberto sem teste. Escreva o teste que falha "
                    f"(xfail strict) com @pytest.mark.bug('{bug_id}')"
                )
            elif tests and not all(t["xfail"] for t in tests):
                errors.append(
                    f"{bug_id}: aberto, mas há teste sem xfail (o CI ficaria vermelho): "
                    "marque como fixed ou ponha xfail(strict=True)"
                )
        elif status in ("manual", "wontfix") and not bug.get("reason"):
            errors.append(f"{bug_id}: estado '{status}' exige 'reason'")
    for bug_id, tests in marks.items():
        if bug_id not in seen:
            where = tests[0]["test"]
            errors.append(f"{bug_id}: marcado em {where}, mas não existe no registro")
    return errors


def summary(bugs: list[dict], marks: dict[str, list[dict]]) -> str:
    counts = {s: sum(1 for b in bugs if b.get("status") == s) for s in STATUSES}
    legacy_open = [b for b in bugs if b.get("status") == "open" and b.get("legacy")]
    uncovered = [b for b in legacy_open if not marks.get(b["id"])]
    covered = sum(1 for b in bugs if marks.get(b["id"]))
    return (
        f"{len(bugs)} bugs: {counts['fixed']} corrigidos, {counts['open']} abertos, "
        f"{counts['manual']} manuais, {counts['wontfix']} não serão corrigidos. "
        f"{covered} têm teste de regressão; {len(uncovered)} abertos antigos ainda sem teste."
    )


# ── commands ─────────────────────────────────────────────────────────────────


def next_id(bugs: list[dict]) -> str:
    nums = [int(m.group(1)) for b in bugs if (m := NEW_ID_RE.match(b["id"]))]
    return f"B-{(max(nums) if nums else 0) + 1:03d}"


def _slug(text: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return base[:50] or "bug"


def skeleton(bug: dict, message: str | None, expected: str | None) -> str:
    """The failing test written when a bug is opened (strict xfail until it is fixed)."""
    bug_id = bug["id"]
    say = message or "<mensagem exata que falhou>"
    want = expected or "<o que o Alfred deveria responder ou gravar>"
    return f'''"""{bug_id} — {bug["title"]}

Aberto em {bug.get("opened", date.today().isoformat())}. Fonte: {bug.get("source", "?")}.
Escreva a verificação real, corrija o código e então: remova o xfail e rode
``python scripts/bugs.py fixed {bug_id}``.
"""

from __future__ import annotations

import os

import pytest

from tests.labkit import Lab

db = pytest.mark.skipif(os.environ.get("ALFRED_TEST_DB") != "1", reason="needs test DB")


@db
@pytest.mark.bug("{bug_id}")
@pytest.mark.xfail(strict=True, reason="{bug_id} aberto: remova junto com a correção")
async def test_{_slug(bug_id)}_{_slug(bug["title"])}(lab: Lab) -> None:
    out = await lab.say({say!r})
    # Esperado: {want}
    raise AssertionError("preencher a verificação do {bug_id}")
'''


def add_bug(
    bugs: list[dict],
    title: str,
    *,
    source: str = "",
    severity: str | None = None,
    area: str | None = None,
    message: str | None = None,
    expected: str | None = None,
    step: str | None = None,
    write_test: bool = True,
) -> dict:
    bug: dict = {
        "id": next_id(bugs),
        "title": title,
        "status": "open",
        "opened": date.today().isoformat(),
    }
    if source:
        bug["source"] = source
    if severity:
        bug["severity"] = severity
    if area:
        bug["area"] = area
    if step:
        bug["step"] = step
    if message:
        bug["message"] = message
    bugs.append(bug)
    if write_test:
        REGRESSION_DIR.mkdir(exist_ok=True)
        (REGRESSION_DIR / "__init__.py").touch()
        path = REGRESSION_DIR / f"test_{_slug(bug['id'])}_{_slug(title)}.py"
        path.write_text(skeleton(bug, message, expected), encoding="utf-8")
        bug["test_file"] = path.relative_to(ROOT).as_posix()
    return bug


def from_report(bugs: list[dict], items: list[dict], *, write_test: bool = True) -> list[dict]:
    """Open one bug per failure in a report (hard_test --relatorio or the manual checklist)."""
    created = []
    known = {(b.get("source", ""), b.get("step", "")) for b in bugs if b.get("step")}
    for item in items:
        key = (item.get("source", ""), item.get("step", ""))
        if item.get("step") and key in known:
            continue
        created.append(
            add_bug(
                bugs,
                item["title"],
                source=item.get("source", ""),
                severity=item.get("severity"),
                area=item.get("area"),
                message=item.get("message"),
                expected=item.get("expected"),
                step=item.get("step"),
                write_test=write_test,
            )
        )
        known.add(key)
    return created


def mark_fixed(bugs: list[dict], bug_id: str) -> None:
    for bug in bugs:
        if bug["id"] == bug_id:
            bug["status"] = "fixed"
            bug["fixed"] = date.today().isoformat()
            return
    raise SystemExit(f"{bug_id}: não existe no registro")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Registro de bugs do Alfred")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check", help="verifica a regra (usado no CI)")
    p_list = sub.add_parser("list", help="lista os bugs")
    p_list.add_argument("--status", choices=STATUSES)
    p_add = sub.add_parser("add", help="abre um bug e cria o teste que falha")
    p_add.add_argument("--title", required=True)
    p_add.add_argument("--source", default="")
    p_add.add_argument("--severity", choices=SEVERITIES)
    p_add.add_argument("--area")
    p_add.add_argument("--message", help="mensagem exata que falhou")
    p_add.add_argument("--expected", help="o que deveria acontecer")
    p_rep = sub.add_parser("from-report", help="abre um bug por falha de um relatório JSON")
    p_rep.add_argument("file")
    p_fix = sub.add_parser("fixed", help="marca um bug como corrigido")
    p_fix.add_argument("id")
    args = parser.parse_args(argv)

    bugs = load()
    marks = scan_tests()

    if args.cmd == "check":
        errors = check(bugs, marks)
        print(summary(bugs, marks))
        for err in errors:
            print(f"  ✗ {err}")
        if errors:
            print(f"\n{len(errors)} violação(ões) da regra de regressão.")
            return 1
        print("Regra de regressão OK.")
        return 0
    if args.cmd == "list":
        for bug in bugs:
            if args.status and bug["status"] != args.status:
                continue
            n = len(marks.get(bug["id"], []))
            print(f"{bug['id']:<10} {bug['status']:<8} testes={n:<2} {bug['title']}")
        return 0
    if args.cmd == "add":
        bug = add_bug(
            bugs,
            args.title,
            source=args.source,
            severity=args.severity,
            area=args.area,
            message=args.message,
            expected=args.expected,
        )
        save(bugs)
        print(f"{bug['id']} aberto. Teste criado em {bug['test_file']}")
        return 0
    if args.cmd == "from-report":
        items = json.loads(Path(args.file).read_text(encoding="utf-8"))
        items = items["falhas"] if isinstance(items, dict) else items
        created = from_report(bugs, items)
        save(bugs)
        for bug in created:
            print(f"{bug['id']} aberto: {bug['title']}  ({bug['test_file']})")
        print(f"{len(created)} bug(s) novo(s).")
        return 0
    if args.cmd == "fixed":
        mark_fixed(bugs, args.id)
        save(bugs)
        print(f"{args.id} marcado como corrigido. Rode 'check' para confirmar.")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
