---
decision_id: ADR-0003
status: proposed
question: How should the stop guard learn where a run stands?
choice: The guard reads the P9 marker for the task. With the marker, it walks the registered gates from the first execution row in read-only mode, and the first failing gate is the open block. A gate that times out or fails to run is unknown, and unknown blocks. When every gate passes and the session is active, the open block is R7. The stored resume_slug stays a lower bound.
rationale: Only activate_session.py writes resume_slug, and it writes it once, at activation. In a real run it stays at A1, so the old guard read "outside execution and review" and allowed every stop (issue #12). The marker and the gates already hold the true state, and headless.py already defines the open block as the first failing gate. One walker serves both.
rejected:
  - alt: Make each gate advance resume_slug
    why_not: Every gate script would write session state, and one missed write brings the bug back
  - alt: Run the registered gate commands as they are
    why_not: Gates without --check write breadcrumbs, and entry_invariant.py clears the P9 marker when a check fails, even with --check
  - alt: Hand-code marker and artifact checks in the guard
    why_not: It copies the stage knowledge of stages.json, so the two drift
  - alt: Walk from A1
    why_not: An early gate that fails for an environment reason (for example no gh on PATH) would hide the progress of the run
links:
  - rel: implements
    target_type: file
    target_id: hooks/pipeline_stop_guard.py
  - rel: supersedes
    target_type: file
    target_id: docs/adr/0002-subagent-stops-do-not-spend-the-run-stop-budget.md
---
# ADR-0003: The stop guard reads progress from evidence

## Context

`hooks/pipeline_stop_guard.py` read `resume_slug` from the session. It blocked a stop when that slug was in the execution or review phase. Only `activate_session.py` writes `resume_slug`, and it writes it once. In a real run the field stays at `activate-session`. The guard allowed every stop. Issue #12 has the evidence. A run that reached R6 still had `activate-session`. The benchmark fixture for "stop mid-execution" hid the failure, because it set `resume_slug: implement`.

## Decision

1. **Evidence.** The P9 marker of the run (`.context/gates/entry-invariant-<pipeline_run_id>.ok` since cd-76) shows that the run is in execution or later. `activate_session.py` clears it at each fresh activation. The hooks protect it from agent writes. A JSON marker that names another task does not count.
2. **The walk.** With the marker, the guard calls `headless.walk(since=<first execution row>, readonly=True)`. The first failing gate is the open block. The walk starts after P9 on purpose. A failed P9 clears the marker, even with `--check`, so the guard must never run it.
3. **Read-only.** `CADENCE_GATES_READONLY=1` makes `write_gate_breadcrumb` write nothing. The walk sets it in the environment of each gate process. It does not touch the P9 marker.
4. **Unknown blocks.** A timeout or an error does not say where the run stands. The limits are 4 s for a gate and 9 s for the walk, inside the 15 s hook timeout. With the marker present, the run is at least in execution, so the guard blocks. The refusal budget from ADR-0002 still ends the loop.
5. **Done is not closed.** When every gate passes and the session is still active, the open block is R7 (deactivate). The R6 and R7 gates pass without proof of work (follow-up #13).
6. **Lower bound.** A stop that the old guard blocked is still blocked. The guard blocks if the stored `resume_slug` or the open block is in execution or review.

## Consequences

- A stop in a real run is blocked from P9 until the session closes. The cost is about 0.2 s on the real registry.
- The guard depends on `scripts/headless.py` (#5).
- A gate that needs more than 4 s makes the guard report unknown and block. The refusal budget ends it after three stops. A repo with a very slow gate can raise `GATE_SECONDS` and `WALK_SECONDS`. The sum must stay under the 15 s hook timeout.
- A refusal belongs to the block where it happened (`stop_refusal_slug` in the session). When the open block moves, the count starts again.
- cd-76 (#76): the P9 marker is per run, `.context/gates/entry-invariant-<pipeline_run_id>.ok`. P9, activation, `--deactivate`, E1, the skill gate and the guard read or clear only the marker of their own run. Thus two runs in two worktrees do not clear the evidence of each other. A main-agent stop judges each top-level run that its chat owns, with one 9 s deadline for all of them. The skill gate's scope across worktrees stays as before (#79).
- An agent can still defeat the guard by deleting the marker with a shell command that the hooks do not detect (ADR-0001). The guard is a guard-rail against drift, not a security boundary.
