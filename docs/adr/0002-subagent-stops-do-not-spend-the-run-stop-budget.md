---
decision_id: ADR-0002
status: proposed
question: How should the stop guard treat subagent stops and corrupt sessions on hosts without a hook loop limit?
choice: Subagent stops are blocked once and never counted against the run's refusal budget; on Claude Code a stop that arrives with stop_hook_active true is let through for subagents and escalated for corrupt sessions; refusals are written to the session file the pointer resolves to
rationale: The R1 review of PR #10 (ticket cd-10) reproduced that three subagent stops spent the main agent's three refusals, letting it quit mid-run, and that a corrupt session blocked Claude Code stops forever because Claude Code has no loop_limit; stop_hook_active is Claude Code's own signal that a stop hook already sent the agent back once
rejected:
  - alt: Remove SubagentStop from the Claude Code hooks
    why_not: docs/pipeline.md keeps a subagent stop guard as part of the design
  - alt: A refusal counter per subagent id
    why_not: needs new persistent state for little gain over block-once
  - alt: Keep blocking corrupt sessions forever
    why_not: references/error-recovery.md forbids infinite loops and requires escalation to the user
links:
  - rel: implements
    target_type: file
    target_id: hooks/pipeline_stop_guard.py
---
# ADR-0002: Subagent stops don't spend the run's stop budget

Ticket: cd-10 (jtouley/cadence#10)

## Context

The stop guard counts refusals in the session and escalates after three. With
SubagentStop registered on Claude Code, every subagent finish spent that shared
budget. Claude Code also has no equivalent of Cursor's `loop_limit`.

## Decision

The guard reads the hook payload. Subagent stop events are blocked once without
counting. A repeat stop that Claude Code marks with `stop_hook_active: true` is
allowed for subagents, and escalated (allowed with a message to the user) when
the session is corrupt. Main-agent stops keep the existing three-refusal budget.
Refusals are written to the file a `{"ref": ...}` pointer resolves to, so the
pointer layout survives.

## Consequences

- cd-76 (#76): the guard judges a SubagentStop that names an `agent_id` only when that agent is an orchestrator of a run. For a peer's SubagentStop, the guard records the stop and allows it with no push back. A subagent stop with no `agent_id` (Cursor) keeps the rule above.
- A subagent may finish after one push back. That matches its role, since the main
  agent still holds the paceline.
- An empty or unreadable payload is treated as a main-agent stop, which keeps
  existing callers' behavior.
