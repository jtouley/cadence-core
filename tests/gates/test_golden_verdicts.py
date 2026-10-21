"""The golden corpus pins the verdict of each measure on small samples.

`good` samples pass and `bad` samples (a planted fault) fail. A `gamed` sample meets the
metric and is still wrong; it passes, and its docstring names the limit that the gate does not catch.
The `bugs` and `types` samples need the pinned ruff and mypy (the `pinned_tools` fixture).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import cognitive_complexity_gate as r3  # noqa: E402
import test_shape  # noqa: E402

GOLDEN = ROOT / "tests" / "fixtures" / "golden"
VERDICTS = json.loads((GOLDEN / "verdicts.json").read_text(encoding="utf-8"))
KIND = {"good": "pass", "bad": "fail", "gamed": "pass"}
TOOL_MEASURES = ("bugs", "types")
ORDER = ("pass", "warn", "fail")


def _verdict(sample: Path, measure: str) -> str:
    """The verdict of the measure; for a `test_shape` sample (a layout), the worst verdict of R2."""
    if measure in ("helpers_reimplemented", "thin_files", "test_shape"):
        records = test_shape.measure_path(sample)["measures"]
    else:
        records, unparsable = r3.measure_path(sample)
        assert unparsable == []
    verdicts = [r["verdict"] for r in records if measure in (r["measure"], "test_shape")]
    assert verdicts and (measure == "test_shape" or len(verdicts) == 1)
    return max(verdicts, key=ORDER.index)


@pytest.mark.parametrize("sample", sorted(VERDICTS))
def test_each_sample_gets_its_pinned_verdict(sample, request):
    kind, measure, _name = sample.split("/")
    if measure in TOOL_MEASURES:
        request.getfixturevalue("pinned_tools")
    assert VERDICTS[sample] == KIND[kind]
    assert _verdict(GOLDEN / sample, measure) == VERDICTS[sample]


def test_the_table_lists_every_sample():
    assert {str(p.relative_to(GOLDEN)) for p in GOLDEN.glob("*/*/*") if p.is_dir()} == set(VERDICTS)


@pytest.mark.parametrize("sample", sorted(s for s in VERDICTS if s.startswith("gamed/")))
def test_each_gamed_sample_names_the_limit_it_shows(sample):
    texts = [p.read_text(encoding="utf-8") for p in (GOLDEN / sample).rglob("*.py")]
    assert any(t.startswith('"""Gamed sample') and "Limit not caught:" in t for t in texts)


@pytest.mark.parametrize("sample", ["bad/statements/long", "good/statements/short", "bad/thin_files/one_test",
                                    "good/thin_files/full_file", "bad/bugs/bare_except_shell",
                                    "good/types/declared_return"])
def test_the_cli_exit_code_follows_the_verdict(sample, request):
    kind, measure, _name = sample.split("/")
    if measure in TOOL_MEASURES:
        request.getfixturevalue("pinned_tools")
    script, args = (("test_shape.py", [str(GOLDEN / sample)]) if measure == "thin_files"
                    else ("cognitive_complexity_gate.py", ["--path", str(GOLDEN / sample)]))
    proc = subprocess.run([sys.executable, str(ROOT / "scripts" / script), *args], capture_output=True, text=True)
    verdicts = {m["measure"]: m["verdict"] for m in json.loads(proc.stdout)["measures"]}
    assert verdicts[measure] == KIND[kind] and proc.returncode == (1 if kind == "bad" else 0)
