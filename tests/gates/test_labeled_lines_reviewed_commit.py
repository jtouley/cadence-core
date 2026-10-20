"""Every Markdown form of the reviewed-commit line (cd-47 D3, #41; P7 review F1).

Unit cross product on labeled_lines: the one grammar R4 uses. R4's own tests keep
a few seam rows on real repos.
"""

from __future__ import annotations

import itertools
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from final_consolidate import last_reviewed_sha  # noqa: E402

SHA = "8a1b2c3d4e5f60718293a4b5c6d7e8f901234567"
PREFIXES = ("", "- ", "* ", "1. ", "## ", "> ", "> - ")
LABELS = ("last reviewed commit", "Last Reviewed Commit", "LAST REVIEWED COMMIT")


def _forms():
    for prefix, label, bold, colon, tick in itertools.product(PREFIXES, LABELS, ("none", "out", "in"), (True, False), (False, True)):
        if bold == "in" and not colon:
            continue  # `**label:**` has the colon inside by definition
        sep = ":" if colon else ""
        head = {"none": f"{label}{sep}", "out": f"**{label}**{sep}", "in": f"**{label}{sep}**"}[bold]
        value = f"`{SHA}`" if tick else SHA
        yield f"{prefix}{head} {value}"


@pytest.mark.parametrize("line", list(_forms()))
def test_every_form_is_read(line):
    assert last_reviewed_sha(line) == SHA


@pytest.mark.parametrize("line", [
    f"Last reviewed commit: {SHA} (after fixes)",
    f"We last reviewed commit {SHA} yesterday.",
    "Last reviewed commit: abc12",
])
def test_prose_and_short_shas_are_not_read(line):
    assert last_reviewed_sha(line) is None


def test_the_last_line_in_the_file_wins():
    assert last_reviewed_sha(f"Last reviewed commit: {SHA[:7]}\n- **Last reviewed commit**: abcdef1") == "abcdef1"
