"""duplication.py finds copied blocks of normalized Python lines (ADR-0008)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import duplication as dup  # noqa: E402

BLOCK = """def load(path):
    text = path.read_text()
    rows = text.splitlines()
    kept = [r for r in rows if r]
    total = len(kept)
    print(total, "rows")
    return kept
"""
# The same seven lines with new names and values, a comment and blank lines.
RENAMED = """import os
from pathlib import Path


def read_all(where):
    # a comment is not a unit
    body = where.read_text()

    lines = body.splitlines()
    good = [x for x in lines if x]
    n = len(good)
    print(n, "lines in all")  # nor is this one
    return good
"""


def test_a_renamed_copy_across_two_files_is_found():
    reports = dup.measure({"a.py": BLOCK, "b.py": RENAMED})
    assert reports["a.py"].blocks == reports["b.py"].blocks == [{"units": 7, "a": "a.py:1-7", "b": "b.py:5-13"}]
    assert (reports["a.py"].duplicated_lines, reports["a.py"].total_lines) == (7, 7)
    # Comments, blank lines and imports are not code lines: 7 of the 13 rows are code.
    assert (reports["b.py"].duplicated_lines, reports["b.py"].total_lines) == (7, 7)


@pytest.mark.parametrize(("sources", "want"), [
    pytest.param({"a.py": BLOCK, "b.py": "x = 1\n" + BLOCK.replace("    return kept\n", "")}, 6,
                 id="six-lines-still-a-block"),
    pytest.param({"a.py": "\n".join(BLOCK.splitlines()[:5]) + "\n", "b.py": BLOCK}, 0, id="five-lines-are-not"),
    pytest.param({"a.py": BLOCK + "\n\n" + BLOCK.replace("load", "other")}, 14, id="a-copy-in-the-same-file"),
    pytest.param({"a.py": BLOCK.replace("[r for r in rows if r]", "sorted(rows)"), "b.py": BLOCK}, 0,
                 id="a-changed-shape-breaks-the-block"),
])
def test_what_counts_as_a_block(sources, want):
    assert dup.measure(sources)["a.py"].duplicated_lines == want


@pytest.mark.parametrize(("source", "want"), [
    pytest.param("@pytest.mark.parametrize('a', [\n    1,\n    2,\n])\ndef test_x(a):\n    assert a\n",
                 ["def N ( N ) :", "assert N"], id="a-parametrize-table"),
    pytest.param("CASES = [\n    ('a', 1, X),\n    pytest.param('b', 2, id='b'),\n]\nx = (a + b) * c\n",
                 ["N = ( N + N ) * N"], id="a-literal-data-table"),
    pytest.param("class K:\n    path: str\n    line: int = 0\n    blocks: list = field(default_factory=list)\nMAX = 5\n",
                 ["class N :", "N : N = N ( N = N )"], id="fields-and-constants"),
    pytest.param("rows = [r for r in rows if r]\nf = {k: lambda: k for k in ks}\n",
                 ["N = [ N for N in N if N ]", "N = { N : lambda : N for N in N }"], id="a-comprehension-is-code"),
])
def test_data_tables_are_not_units(source, want):
    assert [u.text for u in dup.units(source)] == want


def test_an_f_string_is_one_literal():
    [unit] = dup.units('x = f"{a} and {b!r:>4}"\n')
    assert unit.text == "N = L"


def test_a_source_that_does_not_tokenize_raises():
    with pytest.raises(SyntaxError):
        dup.units("def f(:\n    x = (\n")


def test_the_cli_prints_the_report_as_json(tmp_path, capsys):
    for name, text in (("a.py", BLOCK), ("b.py", RENAMED)):
        (tmp_path / name).write_text(text, encoding="utf-8")
    assert dup.main([str(tmp_path / "a.py"), str(tmp_path / "b.py")]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out[str(tmp_path / "a.py")]["percent"] == 100.0 and out[str(tmp_path / "b.py")]["blocks"][0]["units"] == 7
