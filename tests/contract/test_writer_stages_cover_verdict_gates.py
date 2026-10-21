"""stages.json names the writer stages of each verdict gate, and each caller passes a real slug.

`writer_stages` on a stages.json row is the one list of delegation stages whose peer may
write that verdict (PD20). Each gate passes a stages.json slug, never a gate key (PD14). A
slug typo on an old run is not read at all, so this test is the control for it.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from conftest import code_paths

ROOT = Path(__file__).resolve().parents[2]
code_paths()

import gate_common  # noqa: E402
import load_stages  # noqa: E402
import prompt_shape  # noqa: E402
import test_benchmark_gate  # noqa: E402

VERDICT_SLUGS = {"validate", "plan-review", "adversarial-review", "test-benchmark-plan", "pr-review-local",
                 "test-benchmark-pr", "final-consolidate"}
STAGE_CALLS = {"peer_written_filter", "_stage_check", "stage_rejections"}
CODE_DIRS = ("scripts", "hooks", "shared/scripts")


def _stage_rows() -> list[dict]:
    return json.loads((ROOT / "stages.json").read_text(encoding="utf-8"))["stages"]


def _python_files():
    for folder in CODE_DIRS:
        yield from sorted((ROOT / folder).glob("*.py"))


def test_the_seven_verdict_rows_and_no_other_have_writer_stages():
    assert {r["slug"] for r in _stage_rows() if "writer_stages" in r} == VERDICT_SLUGS


def test_each_writer_stage_is_the_stage_of_a_peer_command():
    commands = [r["command"] for r in _stage_rows() if str(r["command"]).startswith("/")]
    commands += [r["command"] for r in prompt_shape.EXTRA_PEER_ROWS]
    peers = {prompt_shape.delegation_stage_for_prompt(c) for c in commands} - {None}
    for row in _stage_rows():
        assert set(row.get("writer_stages", [])) <= peers, row["slug"]


def test_r1_and_r4_accept_the_same_writers():
    assert load_stages.writer_stages("pr-review-local") == load_stages.writer_stages("final-consolidate")
    assert "pr-review-update" in load_stages.writer_stages("final-consolidate")


@pytest.mark.parametrize("slug", ["plan-review-gate", "preflight"])
def test_a_gate_key_or_a_row_with_no_field_is_a_key_error(slug):
    with pytest.raises(KeyError):
        load_stages.writer_stages(slug)


def test_each_gate_spec_names_a_verdict_slug():
    specs = [*gate_common.ARTIFACT_GATES.values(), *test_benchmark_gate._MODES.values()]
    assert all(spec["stage_slug"] in VERDICT_SLUGS for spec in specs)


def _call_name(node: ast.Call) -> str:
    return node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")


def _slug_text(value: ast.expr) -> object:
    return value.value if isinstance(value, ast.Constant) else ast.unparse(value)


def _stage_calls(path: Path):
    """Each stage call or ReviewLoop call in one file."""
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Call) and _call_name(node) in STAGE_CALLS | {"ReviewLoop"}:
            yield node


def _constant_slugs() -> list[tuple[str, str, object]]:
    """(file, call, slug) for each stage call or ReviewLoop with a `slug` keyword."""
    return [(path.name, _call_name(node), _slug_text(k.value))
            for path in _python_files() for node in _stage_calls(path) for k in node.keywords if k.arg == "slug"]


def test_each_stage_call_passes_a_verdict_slug_or_the_spec_field():
    calls = _constant_slugs()
    names = {name for _f, name, _s in calls}
    assert {"peer_written_filter", "stage_rejections", "ReviewLoop"} <= names
    allowed = VERDICT_SLUGS | {"spec['stage_slug']", "slug", "loop.slug"}
    bad = [c for c in calls if c[2] not in allowed and c[2] is not None]
    assert bad == []
    constants = {s for _f, _n, s in calls if isinstance(s, str) and s in VERDICT_SLUGS}
    assert {"plan-review", "adversarial-review", "final-consolidate"} <= constants


def _set_literals(tree: ast.AST):
    """Set and frozenset literals of a module, outside the LEAN_STAGES vocabulary (PD4)."""
    vocabulary = {id(n) for a in ast.walk(tree) if isinstance(a, ast.Assign)
                  and any(getattr(t, "id", "") == "LEAN_STAGES" for t in a.targets) for n in ast.walk(a.value)}
    for node in ast.walk(tree):
        if id(node) in vocabulary:
            continue
        if isinstance(node, ast.Set):
            yield node
        elif isinstance(node, ast.Call) and getattr(node.func, "id", "") == "frozenset":
            yield from node.args


def test_no_python_set_lists_pr_review_update():
    """The stage lists live in stages.json (C-7): no set or frozenset literal names an R4 writer.

    prompt_shape.LEAN_STAGES is the lean-prompt vocabulary (PD4), not a writer-stage list.
    """
    for path in _python_files():
        for literal in _set_literals(ast.parse(path.read_text(encoding="utf-8"))):
            text = ast.unparse(literal)
            assert "'pr-review-update'" not in text and '"pr-review-update"' not in text, path.name


# --- R9 C-1: writer_stages reads its own tree, never SKILL_ROOT -----------------------------

_READERS = {"get_stage", "load_stages", "load_registry", "stages_path"}


def _root_of(call: ast.Call) -> ast.expr | None:
    if len(call.args) > 1:
        return call.args[1]
    return next((k.value for k in call.keywords if k.arg == "skill_root"), None)


def _bad_root(root: ast.expr | None) -> bool:
    none = root is None or (isinstance(root, ast.Constant) and root.value is None)
    return none or (isinstance(root, ast.Name) and root.id == "skill_root")


def _root_rule(source: str) -> bool:
    """Each reader call in the function passes a root that is not None and not the bare skill_root."""
    function = next(n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.FunctionDef))
    readers = [n for n in ast.walk(function) if isinstance(n, ast.Call) and getattr(n.func, "id", "") in _READERS]
    return not any(_bad_root(_root_of(call)) for call in readers)


def test_writer_stages_passes_its_own_root_to_each_reader():
    source = ast.get_source_segment((ROOT / "scripts" / "load_stages.py").read_text(encoding="utf-8"), next(
        n for n in ast.parse((ROOT / "scripts" / "load_stages.py").read_text(encoding="utf-8")).body
        if isinstance(n, ast.FunctionDef) and n.name == "writer_stages"))
    assert _root_rule(source)


@pytest.mark.parametrize("body", ['return frozenset(get_stage(slug, skill_root)["writer_stages"])',
                                  'return frozenset(get_stage(slug)["writer_stages"])'])
def test_the_root_rule_fails_on_the_old_bodies(body):
    assert not _root_rule(f"def writer_stages(slug, *, skill_root=None):\n    {body}\n")


# --- cd-66-F20: one row writer in tests --------------------------------------------------------

NEW_TEST_FILES = ("tests/gates/test_verdict_writer_stage.py", "tests/hooks/test_verdict_join.py",
                  "tests/contract/test_writer_stages_cover_verdict_gates.py")


@pytest.mark.parametrize("rel", NEW_TEST_FILES)
def test_new_test_files_write_rows_only_through_the_fixture(rel):
    text = (ROOT / rel).read_text(encoding="utf-8")
    for writer in ("record_call_row(", "record_join(", "peer_attest.record("):
        assert writer not in text or rel.endswith("cover_verdict_gates.py"), (rel, writer)
