"""R1 tries the generic adversarial inputs of any change, not only the inputs of a Cadence gate.

Runs with no plan review shipped defects that these inputs find: `export --out <the ledger>` wrote
over the ledger, a closed stdout pipe gave a traceback, a bad row of a stored file gave a traceback, and a
value with a trailing newline passed the month check. The PR round, not the plan round, finds this class.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RIGOR = (ROOT / "references" / "review-rigor.md").read_text(encoding="utf-8")
CLASSES = ("an output path that is also an input", "a closed output pipe", "a bad row in a stored file",
           "a trailing newline", "a very large number, NaN or a negative number", "text that does not encode")


def test_the_r1_section_names_each_generic_input_class():
    section = RIGOR.split("### `## Adversarial inputs`", 1)[1].split("\n### ", 1)[0]
    for name in CLASSES:
        assert name in section, name
