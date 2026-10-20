"""cd-66: the one writer of call rows, join rows and attest rows in tests (cd-66-F20).

A verdict counts only when its writer joins to a peer call of the gate's stage:
call row (PreToolUse) -> join row (PostToolUse) -> attest row (the peer's write).
Tests build that chain only through these helpers. A hook test that runs the real
hook is the case under test, not setup.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for _p in (ROOT / "scripts", ROOT / "shared" / "scripts"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import peer_attest  # noqa: E402


def peer_call(context: Path, run_id: str, *, stage: str, tool_use_id: str, decision: str = "allow",
              model: str | None = None, model_slug: str | None = None, at: float | None = None,
              **extra) -> dict:
    """One call row, as the PreToolUse hook writes it."""
    row = {"tool_use_id": tool_use_id, "stage": stage, "model_slug": model_slug, "model": model,
           "decision": decision, "run_id": run_id, "at": time.time() if at is None else at, **extra}
    peer_attest.record_call_row(context, run_id, row)
    return row


def peer_join(context: Path, run_id: str, *, tool_use_id: str, agent_id: str, resolved_model: str | None = None,
              at: float | None = None, **extra) -> dict:
    """One join row, as the PostToolUse hook writes it."""
    row = {"tool_use_id": tool_use_id, "agent_id": agent_id, "resolved_model": resolved_model,
           "at": time.time() if at is None else at, **extra}
    peer_attest.record_join(context, run_id, row)
    return row


def verdict_join_session(context: Path, run_id: str = "run-1", **fields) -> Path:
    """An active Claude run with `verdict_join: true` (pass verdict_join=False for an old run)."""
    from conftest import make_session

    data = {"pipeline_run_id": run_id, "host": "claude", "orchestrator_source": "toplevel", "verdict_join": True,
            **fields}
    if data["verdict_join"] is False:
        data.pop("verdict_join")
    return make_session(context, **data)


def peer_wrote(context: Path, run_id: str, rel_path: str, *, agent_id: str, stage: str, body: str = "...",
               decision: str = "allow", model: str | None = None, join: bool = True, call: bool = True,
               tool_use_id: str | None = None, at: float | None = None, attest_run: str | None = None) -> Path:
    """A peer of `stage` wrote `rel_path`: the file, its call row, its join row and its attest row.

    The switches make each failure cause: join=False (no join row), call=False (a join row with
    no call row), decision="deny" (a denied call), or a wrong `stage`.
    """
    path = Path(context) / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    tool_use_id = tool_use_id or f"toolu_{agent_id}_{stage}"
    if call:
        peer_call(context, run_id, stage=stage, tool_use_id=tool_use_id, decision=decision, model=model, at=at)
    if join:
        peer_join(context, run_id, tool_use_id=tool_use_id, agent_id=agent_id, at=at)
    peer_attest.record(context, attest_run or run_id, rel_path, agent_id, peer_attest.sha256(path.read_bytes()))
    return path


def tamper(path: Path) -> Path:
    """Change the file after the peer wrote it (sha256 mismatch)."""
    with Path(path).open("a", encoding="utf-8") as handle:
        handle.write("x")
    return path
