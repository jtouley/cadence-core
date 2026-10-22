"""docs/peer-output-contract.md is the API of core: the lines that the gates read from peer artifacts.

The doc has a section for each peer block. The bundled peer docs tell the agent to write each
required line. Each `file:function` that the doc cites exists.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "docs" / "peer-output-contract.md"
STAGES = ROOT / "stages.json"
PEERS = ROOT / "references" / "bundled-peers.json"
PEER_CONTRACT = ROOT / "references" / "peer-contract.md"

# A supporting command has no stages.json row. The block whose gate reads its artifact.
SUPPORTING_BLOCKS = {"/pr-review-update": "R4", "/session-summary": "R7", "/deferral-review": "P8"}

# The required lines of each peer block, as literals. A str must appear in the bundled peer doc
# and in the block's section of DOC. A pair is (literal in the peer doc, literal in DOC), for a
# placeholder that the two docs spell differently. Keep this in sync with the `Yes` and `If ...`
# rows of DOC.
REQUIRED: dict[str, list[str | tuple[str, str]]] = {
    "A5": ["vibetest/", "RUN_BY_SKILL: vibe-test", "**Tracker:**", "**Ticket:**", "TICKET_SKIPPED", "**Linear:**"],
    "P1": ["RUN_BY_COMMAND: plan-create", ".plan.md", "### Phase N:", "acceptance/<task_id>.json"],
    "P3": ["RUN_BY_COMMAND: plan-review", "reviews/plan_", "Verdict:", "READY TO EXECUTE", "READY WITH CHANGES",
           "NOT READY", "## Must-fix items", "MF-"],
    "P4": ["RUN_BY_COMMAND: plan-update", "## Revision ledger (review", "not changed: <reason>",
           "## Carried review findings", "adversarial_review_gate.py --heading"],
    "P6": ["RUN_BY_SKILL: de-adversarial-reviewer", "Mode: plan-gate",
           ("adversarial_{slug}.md", "adversarial_<slug>.md"), ("adversarial_{slug}_r{n}.md", "adversarial_<slug>_r<n>.md"),
           "## Blocking issues", "## Conditions", ("### B-n.", "### B-<n>."), ("### C-n.", "### C-<n>."),
           "Verdict:", "APPROVE WITH CONDITIONS"],
    "P7": ['"RUN_BY_SKILL": "test-benchmark"', ("{task_id}_plan.json", "<task_id>_plan.json")],
    "E1": ["phase N:", "phase N: red"],
    "E4": ["RUN_BY_COMMAND: commit-push-pr", "prs/draft-pr-<pipeline_run_id>.md",
           ("https://github.com/", "https://github\\.com/"), "Branch:"],
    "R1": ["RUN_BY_COMMAND: pr-review-local", ("PR_REVIEW_#{N}.md", "PR_REVIEW_#<n>.md"), "Last reviewed commit:",
           "## Must-fix items", "findings_io.py", "NO MERGE",
           "Reproductions", "End-to-end walk", "Adversarial inputs", "Contracts", "Design choices", "Scope",
           "Lookback", "Reuse and extension"],
    "R2": ['"RUN_BY_SKILL": "test-benchmark"', "_code.json", "adversarial_inputs", "expected", "observed",
           "scores", "findings", "must-fix", "`PASS`"],
    # R4 appends to the R1 review, so the R1 marker of that file is the marker that R4 reads.
    "R4": [("Append to", "appends to the R1 review"), ("PR_REVIEW_#{N}.md", "PR_REVIEW_#<n>.md"),
           "Last reviewed commit:", "### Must-fix items", "findings_io.py"],
    "R7": ["summaries/<task_id>.md", "RUN_BY_COMMAND: session-summary"],
    "P8": ["RUN_BY_COMMAND: deferral-review", "Register sha256:", "Ruling", "Condition", "GREEN", "AMBER", "RED",
           "_deferral-review.md"],
}

# Each bundled peer doc loads references/peer-contract.md, which states the shared rules.
SHARED: list[str | tuple[str, str]] = [
    "RUN_BY_SKILL: <name>", "RUN_BY_COMMAND: <name>", "ARTIFACT: <path>", "STOPPED: <reason>",
    "Write or Edit tool", ("Name the ticket", "ticket token"),
]

CITATION = re.compile(r"`((?:scripts|hooks|shared)/[\w./-]+\.py):(\w+)`")


def _doc() -> str:
    return DOC.read_text(encoding="utf-8")


def _sections(text: str) -> dict[str, str]:
    """The block id that starts each `## ` heading, mapped to the text up to the next `## ` heading."""
    out: dict[str, str] = {}
    for chunk in re.split(r"(?m)^(?=## )", text):
        heading = re.match(r"## ([APER]\d+)\b", chunk)
        if heading:
            out[heading.group(1)] = chunk
    return out


def _peer_blocks() -> set[str]:
    rows = json.loads(STAGES.read_text(encoding="utf-8"))["stages"]
    return {r["block"] for r in rows if str(r.get("command", "")).startswith("/")} | set(SUPPORTING_BLOCKS.values())


def _peer_docs() -> dict[str, Path]:
    """Block id to the bundled peer doc that runs at it."""
    data = json.loads(PEERS.read_text(encoding="utf-8"))
    docs = {block: ROOT / row["path"] for row in data["peers"] for block in row["blocks"]}
    for row in data.get("supporting_commands") or []:
        docs[SUPPORTING_BLOCKS[row["slash"]]] = ROOT / row["path"]
    return docs


def _pair(marker: str | tuple[str, str]) -> tuple[str, str]:
    return (marker, marker) if isinstance(marker, str) else marker


def test_every_supporting_command_has_a_block():
    data = json.loads(PEERS.read_text(encoding="utf-8"))
    unknown = [row["slash"] for row in data.get("supporting_commands") or [] if row["slash"] not in SUPPORTING_BLOCKS]
    assert not unknown, f"add the block of each supporting command to SUPPORTING_BLOCKS: {unknown}"


def test_doc_has_a_section_for_each_peer_block():
    missing = sorted(_peer_blocks() - set(_sections(_doc())))
    assert not missing, f"{DOC.name} has no `## <block>` section for: {missing}"


def test_marker_data_covers_each_peer_block():
    assert set(REQUIRED) == _peer_blocks(), "REQUIRED must have one entry for each peer block"


@pytest.mark.parametrize("block", sorted(REQUIRED))
def test_bundled_peer_doc_states_each_required_line(block):
    peer_doc = _peer_docs()[block]
    text = peer_doc.read_text(encoding="utf-8")
    missing = [peer for peer, _ in map(_pair, REQUIRED[block]) if peer not in text]
    assert not missing, f"{peer_doc.relative_to(ROOT)} does not tell the peer to write: {missing}"


@pytest.mark.parametrize("block", sorted(REQUIRED))
def test_contract_section_lists_each_required_line(block):
    section = _sections(_doc())[block]
    missing = [ours for _, ours in map(_pair, REQUIRED[block]) if ours not in section]
    assert not missing, f"the {block} section of {DOC.name} does not list: {missing}"


def test_shared_rules_are_in_the_peer_contract_and_the_doc():
    contract, doc = PEER_CONTRACT.read_text(encoding="utf-8"), _doc()
    pairs = [_pair(m) for m in SHARED]
    assert not [p for p, _ in pairs if p not in contract], "references/peer-contract.md lost a shared rule"
    assert not [o for _, o in pairs if o not in doc], f"{DOC.name} lost a shared rule"


@pytest.mark.parametrize("block", sorted(REQUIRED))
def test_bundled_peer_doc_loads_the_peer_contract(block):
    assert "peer-contract.md" in _peer_docs()[block].read_text(encoding="utf-8")


def test_each_cited_function_exists():
    cites = sorted(set(CITATION.findall(_doc())))
    assert cites, f"{DOC.name} cites no `file:function`"
    bad = []
    for rel, name in cites:
        path = ROOT / rel
        if not path.is_file() or not re.search(rf"(?m)^\s*def {re.escape(name)}\(", path.read_text(encoding="utf-8")):
            bad.append(f"{rel}:{name}")
    assert not bad, f"{DOC.name} cites code that does not exist: {bad}"
