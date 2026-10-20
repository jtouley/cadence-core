---
description: Cadence P1: write the phased execution plan for the change.
argument-hint: '<idea-or-ticket>'
---

# Plan create (Cadence P1)

Load `$CADENCE_HOME/references/peer-contract.md`.

## Purpose

Author a reviewable execution plan. Do not implement.

## Path

`.cursor/plans/{slug}_{short_id}.plan.md` (also accepted:
`.context/plans/{slug}.plan.md`).

Include:

```
RUN_BY_COMMAND: plan-create
```

## Sequence

1. Capture goal, constraints, non-goals, success signals.
2. Step 0: restate the problem, design decisions, blast radius, acceptance.
   Challenge weak scope in the plan text.
3. Write phases in registry order with exit criteria, TDD notes, and real
   quality-gate commands from the repo (do not invent make targets).
   Give each phase a header in this form: `### Phase N: <title>`.
   These headers are the E1 task list. The E1 gate fails when the plan has none.
4. Add a `## Reuse and extension` section (`core:dry-extension`).
   Name the existing helpers and modules that the plan reuses.
   Name the extension point that the plan adds or uses.
   When the plan adds none, write `None` and the reason.
5. Footer: next step is `/plan-review <plan>`. Do not tell the user to
   `/plan-update` before a review file exists.

Chat close is C.O.R.E. First action: `/plan-review`. When a Cadence run delegates this peer, replace the C.O.R.E. close with the one-line return in `peer-contract.md`.
