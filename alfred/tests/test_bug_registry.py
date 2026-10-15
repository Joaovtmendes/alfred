"""The regression rule itself: every bug in tests/bugs.json has a regression test.

``scripts/bugs.py check`` is the rule; these tests (a) run it on the real registry, so the
CI goes red when a bug is fixed without a test, and (b) prove the rule catches each way of
breaking it, so it cannot rot into a rubber stamp.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib

import pytest

SCRIPT = pathlib.Path(__file__).parent.parent / "scripts" / "bugs.py"


@pytest.fixture(scope="module")
def bugs_mod():
    spec = importlib.util.spec_from_file_location("bugs_script", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _bug(**kw):
    return {"id": "B-001", "title": "t", "status": "open", **kw}


def _mark(test="tests/x.py::test_a", xfail=False):
    return {"test": test, "xfail": xfail}


# ── the real registry ────────────────────────────────────────────────────────


def test_the_real_registry_follows_the_rule(bugs_mod):
    errors = bugs_mod.check(bugs_mod.load(), bugs_mod.scan_tests())
    assert errors == [], "\n".join(errors)


def test_every_fixed_bug_has_a_test_that_is_collected(bugs_mod):
    """The scanner reads the source; this makes sure each marked test really exists."""
    import ast

    marks = bugs_mod.scan_tests()
    root = SCRIPT.parent.parent
    for tests in marks.values():
        for t in tests:
            file, name = t["test"].split("::")
            tree = ast.parse((root / file).read_text(encoding="utf-8"))
            names = {n.name for n in ast.walk(tree) if hasattr(n, "name")}
            assert name in names, t["test"]


# ── the rule catches each violation ──────────────────────────────────────────


def test_fixed_without_a_test_fails(bugs_mod):
    errors = bugs_mod.check([_bug(status="fixed")], {})
    assert any("sem teste" in e for e in errors)


def test_fixed_with_a_test_passes(bugs_mod):
    assert bugs_mod.check([_bug(status="fixed")], {"B-001": [_mark()]}) == []


def test_fixed_but_the_test_still_has_xfail_fails(bugs_mod):
    errors = bugs_mod.check([_bug(status="fixed")], {"B-001": [_mark(xfail=True)]})
    assert any("xfail" in e for e in errors)


def test_a_new_open_bug_needs_a_failing_test(bugs_mod):
    errors = bugs_mod.check([_bug()], {})
    assert any("sem teste" in e for e in errors)


def test_a_new_open_bug_with_a_strict_xfail_passes(bugs_mod):
    assert bugs_mod.check([_bug()], {"B-001": [_mark(xfail=True)]}) == []


def test_an_open_bug_with_a_green_test_must_be_marked_fixed(bugs_mod):
    errors = bugs_mod.check([_bug()], {"B-001": [_mark(xfail=False)]})
    assert any("marque como fixed" in e for e in errors)


def test_a_legacy_open_bug_may_wait_for_its_test(bugs_mod):
    assert bugs_mod.check([_bug(id="A-99", legacy=True)], {}) == []


def test_a_marked_test_for_an_unknown_bug_fails(bugs_mod):
    errors = bugs_mod.check([], {"B-777": [_mark()]})
    assert any("não existe no registro" in e for e in errors)


def test_duplicate_ids_and_bad_status_fail(bugs_mod):
    errors = bugs_mod.check([_bug(status="fixed"), _bug(status="fixed")], {"B-001": [_mark()]})
    assert any("repetido" in e for e in errors)
    assert any("inválido" in e for e in bugs_mod.check([_bug(status="done")], {}))


def test_manual_and_wontfix_need_a_reason(bugs_mod):
    assert any("reason" in e for e in bugs_mod.check([_bug(status="manual")], {}))
    assert any("reason" in e for e in bugs_mod.check([_bug(status="wontfix")], {}))
    assert bugs_mod.check([_bug(status="manual", reason="depende da Meta")], {}) == []


# ── the scanner ──────────────────────────────────────────────────────────────


def test_scanner_reads_function_and_class_marks(bugs_mod, tmp_path):
    (tmp_path / "test_a.py").write_text(
        "import pytest\n\n"
        '@pytest.mark.bug("B-001")\n'
        "def test_one(): ...\n\n"
        '@pytest.mark.bug("B-002")\n'
        "@pytest.mark.xfail(strict=True)\n"
        "async def test_two(): ...\n\n"
        '@pytest.mark.bug("B-003")\n'
        "class TestGroup:\n"
        "    def test_three(self): ...\n"
        "    def helper(self): ...\n"
    )
    found = bugs_mod.scan_tests(tmp_path)
    assert found["B-001"][0]["xfail"] is False
    assert found["B-002"][0]["xfail"] is True
    assert [t["test"].split("::")[1] for t in found["B-003"]] == ["test_three"]


# ── opening bugs ─────────────────────────────────────────────────────────────


def test_next_id_continues_the_new_numbering(bugs_mod):
    assert bugs_mod.next_id([{"id": "A-05"}]) == "B-001"
    assert bugs_mod.next_id([{"id": "B-001"}, {"id": "B-009"}]) == "B-010"


def test_a_new_bug_gets_a_failing_test_that_passes_the_rule(bugs_mod, tmp_path, monkeypatch):
    monkeypatch.setattr(bugs_mod, "REGRESSION_DIR", tmp_path / "regression")
    monkeypatch.setattr(bugs_mod, "ROOT", tmp_path)
    bugs: list[dict] = []
    bug = bugs_mod.add_bug(bugs, "Gasto em reais vira euro", message="gastei 50 reais")
    created = tmp_path / bug["test_file"]
    text = created.read_text(encoding="utf-8")
    assert '@pytest.mark.bug("B-001")' in text and "xfail(strict=True" in text
    assert "gastei 50 reais" in text
    compile(text, str(created), "exec")  # the skeleton is valid Python
    marks = bugs_mod.scan_tests(tmp_path)
    assert bugs_mod.check(bugs, marks) == []


def test_a_report_opens_one_bug_per_failure_and_is_idempotent(bugs_mod, tmp_path, monkeypatch):
    monkeypatch.setattr(bugs_mod, "REGRESSION_DIR", tmp_path / "regression")
    monkeypatch.setattr(bugs_mod, "ROOT", tmp_path)
    bugs: list[dict] = []
    items = [
        {"title": "A", "source": "hard_test 1", "step": "A07", "message": "x 1"},
        {"title": "B", "source": "hard_test 1", "step": "A08", "message": "x 2"},
    ]
    assert len(bugs_mod.from_report(bugs, items)) == 2
    assert bugs_mod.from_report(bugs, items) == []  # same steps again: nothing new
    assert [b["id"] for b in bugs] == ["B-001", "B-002"]


def test_mark_fixed_stamps_the_date(bugs_mod):
    bugs = [_bug()]
    bugs_mod.mark_fixed(bugs, "B-001")
    assert bugs[0]["status"] == "fixed" and bugs[0]["fixed"]
    with pytest.raises(SystemExit):
        bugs_mod.mark_fixed(bugs, "B-404")


def test_the_registry_file_is_valid_json_with_a_bugs_list():
    path = SCRIPT.parent.parent / "tests" / "bugs.json"
    assert isinstance(json.loads(path.read_text(encoding="utf-8"))["bugs"], list)
