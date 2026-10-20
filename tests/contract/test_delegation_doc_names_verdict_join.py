"""references/delegation.md states the verdict join of gh-66 for the orchestrator.

The orchestrator reads this doc, not the code. It must know which logs prove a verdict,
that /pr-review-update is a lean peer command, how SendMessage keeps a stage, what the
activation warning asks, and the known limit of a session with no user settings.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOC = (ROOT / "references" / "delegation.md").read_text(encoding="utf-8")


def test_the_doc_names_the_call_and_join_logs_and_the_r4_peer():
    for needle in ("calls.jsonl", "joins.jsonl", "/pr-review-update", "writer_stages", "verdict_join", "#127"):
        assert needle in DOC, needle


def test_the_doc_states_the_send_message_rule():
    assert ("A peer that the orchestrator continues with SendMessage keeps the stage of its first call. "
            "The join uses the first join row of an agent. For a new review round, start a new peer.") in DOC


def test_the_doc_states_the_activation_warning_and_the_known_limit():
    assert "install.sh install: run install.sh" in DOC and "do not run install.sh" in DOC
    assert ("A session that does not load user settings must not run `activate_session.py` on a tree "
            "whose join hook is only in user settings.") in DOC
