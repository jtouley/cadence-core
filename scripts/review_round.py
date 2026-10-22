#!/usr/bin/env python3
"""A review round: the members of one round review one HEAD in parallel.

The members are the enabled rows of stages.json whose `review_round.round` names the round. A row with
a slash command is a peer: each peer whose gate fails is due, and the orchestrator sends the due peers
in one message, as parallel Agent or Task calls. Any other row is a script: the round runs it, then
reads its gate. The round passes when the gate of each member passes. One fix round then answers the
must-fix rows of every member (SKILL.md, review phase). A peer that the round sent at this HEAD is not
sent again: when its gate still fails, the round names the fix round
(`.context/gates/review-round-<round>-<run>.json`). The plan round has sections, so `scripts/plan_wave.py`
runs it.

    python3 scripts/review_round.py --round pr            # run the scripts, print the peers to send
    python3 scripts/review_round.py --round pr --check    # read each gate, run and write nothing
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from load_stages import is_peer, review_rounds, round_members, script_argv  # noqa: E402

ROOT = _SCRIPTS.parent


def _gate(row: dict, workspace: Path) -> tuple[bool, str]:
    """(passes, the first reason) of the member's gate, read with --check."""
    argv = script_argv(str(row["gate_script"]), ROOT)
    argv += [] if "--check" in argv else ["--check"]
    done = subprocess.run(argv, cwd=workspace, capture_output=True, text=True, check=False,
                          env={**os.environ, "CADENCE_GATES_READONLY": "1"})
    lines = [l.strip().lstrip("- ") for l in (done.stdout + done.stderr).splitlines() if l.strip()]
    reason = next((l for l in lines if not l.startswith(("FAIL", "OK"))), lines[0] if lines else "")
    return done.returncode == 0, reason


def _commands(rows: list[dict], workspace: Path) -> dict[str, str]:
    """{slug: the command that the orchestrator sends}, from each row and the inputs of the run."""
    import handoff_inputs
    from gate_common import ctx
    from pipeline_run_binding import run_for

    context = ctx()
    session = run_for(context, cwd=workspace) or {}
    run = handoff_inputs.Run(root=ROOT, ctx=context, workspace=workspace, session=session)
    return {row["slug"]: handoff_inputs.resolve(row, run).command for row in rows}


def _sent_path(name: str, workspace: Path) -> Path | None:
    from gate_common import ctx
    from pipeline_run_binding import run_for

    run = (run_for(ctx(), cwd=workspace) or {}).get("pipeline_run_id")
    return ctx() / "gates" / f"review-round-{name}-{run}.json" if run else None


def _head(workspace: Path) -> str:
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=workspace, capture_output=True, text=True,
                          check=False).stdout.strip()


def _sent(name: str, workspace: Path) -> set[str]:
    """The peers that the round sent at this HEAD. A peer that reviewed this HEAD and still fails its gate has
    findings for the fix round: it is not sent again. Without this, R2 was sent at the same HEAD forever."""
    path = _sent_path(name, workspace)
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path else {}
    except (OSError, ValueError):
        data = {}
    return set(data.get("sent", [])) if isinstance(data, dict) and data.get("head") == _head(workspace) else set()


def _record_sent(name: str, workspace: Path, slugs: set[str]) -> None:
    path = _sent_path(name, workspace)
    if path is not None and os.environ.get("CADENCE_GATES_READONLY") != "1":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"head": _head(workspace), "sent": sorted(slugs)}, indent=2) + "\n", encoding="utf-8")


def _run_scripts(rows: list[dict], workspace: Path) -> None:
    """Run each script member of the round; its gate is read after."""
    for row in rows:
        if not is_peer(row):
            subprocess.run(script_argv(str(row["command"]), ROOT), cwd=workspace, capture_output=True, text=True)


def _walk(rows: list[dict], workspace: Path, sent: set[str]) -> tuple[list[str], list[str], list[dict]]:
    """(passed lines, failed lines, the peers that are due): a failing peer not yet sent at this HEAD is due."""
    passed: list[str] = []
    failed: list[str] = []
    due: list[dict] = []
    for row in rows:
        ok, reason = _gate(row, workspace)
        label = f"{row['block']} ({row['slug']})"
        (passed if ok else failed).append(label if ok else f"{label}: {reason}")
        if not ok and is_peer(row) and row["slug"] not in sent:
            due.append(row)
    return passed, failed, due


def _after_review(workspace: Path) -> str:
    """The NEXT line when no peer is due and a member still fails."""
    repair = _acceptance_repair(workspace)
    if repair:
        return (f"NEXT: send `{repair}` first: a pinned acceptance check has a defect, and no fix round may change "
                "it. Then one fix round answers the other must-fix rows, then run this round again")
    return "NEXT: one fix round answers the must-fix rows of each member (SKILL.md, review phase), then run this round again"


def run_round(name: str, workspace: Path, *, check: bool) -> tuple[list[str], list[str], str | None]:
    """(passed lines, failed lines, the NEXT line) of round `name`."""
    driver = str(review_rounds(ROOT)[name].get("driver", "")) if name in review_rounds(ROOT) else ""
    if driver and "review_round.py" not in driver:
        return [], [f"review round {name!r} runs with `{driver}`"], f"NEXT: run {driver}"
    rows = round_members(name, ROOT, workspace)
    if not check:
        _run_scripts(rows, workspace)
    sent = _sent(name, workspace)
    passed, failed, due = _walk(rows, workspace, sent)
    if due:
        if not check:
            _record_sent(name, workspace, sent | {row["slug"] for row in due})
        commands = _commands(due, workspace)
        return passed, failed, "NEXT: send these peers in one message, in parallel: " + " | ".join(
            commands[row["slug"]] for row in due)
    return passed, failed, _after_review(workspace) if failed else None


def _acceptance_repair(workspace: Path) -> str | None:
    """`/plan-update <plan> --acceptance AC-n` for the first acceptance check with an open defect, else None.

    Without it, R1 recorded a check defect and the round named only the fix round, which may not change a
    pinned check, so the orchestrator sent fix rounds."""
    import acceptance
    import handoff_inputs
    from gate_common import ctx
    from load_stages import load_stages
    from pipeline_run_binding import run_for

    context = ctx()
    session = run_for(context, cwd=workspace) or {}
    defects = acceptance.open_defects(context, session)
    if not defects:
        return None
    row = next((r for r in load_stages(ROOT, workspace) if r.get("block") == "P4"), None)
    if row is None:
        return None
    run = handoff_inputs.Run(root=ROOT, ctx=context, workspace=workspace, session=session)
    found = handoff_inputs.resolve(row, run, acceptance=sorted(defects)[0])
    return found.command if not found.missing else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--round", required=True, help="a round name of stages.json review_rounds")
    parser.add_argument("--check", action="store_true", help="read each gate; run no script")
    args = parser.parse_args(argv)
    try:
        passed, failed, nxt = run_round(args.round, Path.cwd(), check=args.check)
    except ValueError as exc:
        print(f"FAIL  review-round: {exc}", file=sys.stderr)
        return 1
    report = [f"OK    {line}" for line in passed] + [f"FAIL  {line}" for line in failed] + ([nxt] if nxt else [])
    if not failed:
        report.append(f"OK  review-round {args.round}: each member passed this HEAD")
    print("\n".join(report))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
