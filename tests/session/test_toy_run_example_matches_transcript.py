"""examples/toy-run/run.py prints its committed transcript: the README example stays true."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "examples" / "toy-run"


def test_toy_run_prints_the_committed_transcript(tmp_path):
    done = subprocess.run(
        [sys.executable, str(EXAMPLE / "run.py"), "--keep", str(tmp_path / "sandbox")],
        capture_output=True, text=True, timeout=600, check=False,
    )
    assert done.returncode == 0, done.stderr
    expected = (EXAMPLE / "transcript.txt").read_text(encoding="utf-8")
    assert done.stdout == expected, (
        "The toy run changed. Read the diff, then write the transcript again: "
        "python3 examples/toy-run/run.py > examples/toy-run/transcript.txt"
    )


def test_readme_excerpt_comes_from_the_transcript():
    """Each line of the README's Quickstart output block is a line of the transcript, in order.

    `[...]` marks skipped lines, and ` (...)` cuts the end of a line.
    """
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    block = readme.split("## Quickstart", 1)[1].split("```text\n", 1)[1].split("```", 1)[0]
    transcript = (EXAMPLE / "transcript.txt").read_text(encoding="utf-8").splitlines()
    lines = [line for line in block.splitlines() if line != "[...]"]
    assert lines
    at = 0
    for line in lines:
        head, cut, _ = line.partition(" (...)")
        found = next((i for i in range(at, len(transcript))
                      if (transcript[i].startswith(head) if cut else transcript[i] == line)), None)
        assert found is not None, f"not in the transcript, in this order: {line!r}"
        at = found + 1
