"""A delegated peer runs only its own stage.

Four runs (ac7c7e9f, bob-killer-snowflake 33f6c325) had a `/plan-update`
peer that ran `/plan-review` in its own context and wrote the verdict for its own update.
The trigger was the command text: "After you write the update, run `/plan-review`".
The orchestrator owns the loop. A peer doc may name another stage only as a step of the
orchestrator.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_BUNDLE = json.loads((ROOT / "references" / "bundled-peers.json").read_text(encoding="utf-8"))
_ROWS = _BUNDLE["peers"] + _BUNDLE["supporting_commands"]
PEER_DOCS = sorted({row["path"] for row in _ROWS})
STAGE_SLASHES = {row["slash"].lstrip("/") for row in _ROWS if "slash" in row}
_RUN_STAGE = re.compile(r"(?<!not )\brun `/([a-z0-9-]+)", re.IGNORECASE)


def _own_slash(rel: str) -> str:
    return next(row["slash"].lstrip("/") for row in _ROWS if row["path"] == rel)


@pytest.mark.parametrize("rel", PEER_DOCS)
def test_a_peer_doc_gives_other_stages_only_to_the_orchestrator(rel):
    own = _own_slash(rel)
    for number, line in enumerate((ROOT / rel).read_text(encoding="utf-8").splitlines(), 1):
        for match in _RUN_STAGE.finditer(line):
            stage = match.group(1)
            if stage == own or stage not in STAGE_SLASHES:
                continue
            assert "orchestrator" in line.lower(), f"{rel}:{number} tells the peer to run /{stage}: {line.strip()}"


@pytest.mark.parametrize(
    ("rel", "other"),
    [("commands/plan-update.md", "plan-review"), ("commands/plan-review.md", "plan-update")],
)
def test_the_plan_loop_peers_return_after_their_own_write(rel, other):
    text = (ROOT / rel).read_text(encoding="utf-8")
    assert f"Do not run `/{other}`" in text
    assert f"Do not call the `{other}` skill" in text
    assert "one-line return" in text
