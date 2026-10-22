---
description: Cadence P3: staff review of a plan, with a READY TO EXECUTE verdict or blocking items.
argument-hint: '<plan-name-or-path>'
---

# Plan review (Cadence P3)

Load `$CADENCE_HOME/references/peer-contract.md` and
`$CADENCE_HOME/references/architecture-core-rules.md`.

## Trigger

`/plan-review <plan-name-or-path>`

Resolve `.cursor/plans/` then `.context/plans/`. Stop if missing.

## Deliverable

`.context/reviews/plan_{basename}.md` with:

```
RUN_BY_COMMAND: plan-review
```

Verdict: `READY TO EXECUTE` | `READY WITH CHANGES` | `REQUEST CHANGES` | `NOT READY`. Each verdict
except `READY TO EXECUTE` asks for a plan update. In each round, write this file again in place: do not
write a second file.

Write `Last reviewed plan sha256: <sha>` after the marker lines. `<sha>` is the sha256 of the plan on the `Input:` line of your packet.
P9 compares this sha256 with the plan. When the plan changed after a `READY TO EXECUTE` review, P9 fails and the orchestrator runs `/plan-review` again.
With no such line, P9 compares the plan mtime with the review mtime.

Give each must-fix item an id: `MF-1`, `MF-2`, and so on. At the cycle limit, the plan maps each id to a phase.
Below the limit, the plan update answers each id with a row in its `## Revision ledger (review <sha>)` table, where `<sha>` is the sha256 of your review file.
Put every open must-fix item under one `## Must-fix items` heading. An item from an earlier cycle that is still open goes there too.
Always write the `## Must-fix items` heading. Write `None.` under it when no item is open.
The P9 gate reads the open ids from that section only. An id named in a status list or a note is not open.
On Claude Code, write this file with the Write or Edit tool, not a shell redirect. The hook attests a peer's tool write. A gate does not accept an unattested review.

## Delta review (cycle two and later)

In a plan review round, record each finding and then `done`, as `references/plan-review-round.md` says (stage `plan-review`).

From cycle two on, the orchestrator gives you the previous review as a second input. Read it before the plan.
Start the review with a `## Status of earlier items` table, after the marker lines.
Write one row for each `MF-n` of the previous review:

```markdown
## Status of earlier items

| Id | Status | Evidence |
|---|---|---|
| MF-1 | closed | Revision ledger row MF-1. Phase 2 names `test_load_rejects_empty_config`. |
| MF-2 | open | The ledger row names Phase 3. Phase 3 has no test for an empty input. |
```

- `closed`: cite the Revision ledger row and the plan text that settles the item.
- `open`: write what is still missing. Put the item under `## Must-fix items` too, with its earlier id.
- Give a new item the next id after the highest id of the previous review.
- A new must-fix item may cite only a section that the newest update changed: a section that a row of its
  newest `## Revision ledger` names in its Where cell.
- A sentence that was in the plan when the previous review passed it is not a new must-fix item. Write it as a note.

## Review for

- Missing or mis-ordered phases vs `stages.json`
- Spec gaps and implicit assumptions. When the packet pins a vibe-test report, check the assumption that
  the register logs for each `G-n` of its `## Blocking gaps`, and the phase that settles it. A missing or
  weak assumption is a must-fix item.
- `core:dry-extension` (reuse, Rule of Three, R3 limit, extension point, cloned gates vs registry row)
- The `## Reuse and extension` section of the plan
- `core:standalone-package` (host path leaks)
- TDD + commit checkpoints + real quality gates. Each phase has its own failing test and the code that
  makes it pass. A phase of tests alone, or a phase whose test needs the code of a later phase, is a
  must-fix item: E1 cannot commit it green
- Weakened assertions or partial-success tests
- Rollback / blast radius

A must-fix item names a missing phase, a wrong order, a spec gap, a helper that exists, or a weak test.
A test name, a file name, a phase title or a word choice is not a must-fix item. Write it as a note.
The plan lists what to do and in which order. It does not fix the one way to do it.
A phase can have more than one option. Do not block the plan for that. E1 tries the options and keeps one.
A plan without a `## Reuse and extension` section gets `READY WITH CHANGES`.
Search the shared modules for each helper that the plan writes. In Cadence, they are `tests/conftest.py`, `scripts/` and `shared/`.
A plan that writes a helper that already exists gets `READY WITH CHANGES`.
`READY WITH CHANGES` or `NOT READY` blocks E1.
This review writes the review file only. Do not edit the plan.
Do not run `/plan-update`. Do not call the `plan-update` skill. Do not review again in this run.
When a Cadence run delegates this peer, end after the review write with the one-line return in `peer-contract.md`.

The orchestrator owns the loop. On a blocking verdict, the orchestrator runs `/plan-update` on the same plan.
Then it runs `/plan-review` in a new Agent call.
It repeats until the newest review has `RUN_BY_COMMAND: plan-review` and `Verdict: READY TO EXECUTE`.
That review must come from a plan-review run after the last plan update.
Only the plan-review run writes `reviews/plan_*.md`.
The plan has one review cycle (`plan_review_cycles`, default 1; a config layer may raise it). After the last blocking review, do not review again: P4 carries each open item.
`READY WITH CHANGES`: `/plan-update` maps each open `MF-n` to a phase under `## Carried review findings`.
`NOT READY`: the orchestrator runs `activate_session.py --hard-stop retries --reason "..."`.
Do not ask the user to allow another cycle. The orchestrator does not end the turn on a blocking verdict.
Do not report status when a background task finishes during this loop.
Do not implement while the verdict is not `READY TO EXECUTE`.
Chat: C.O.R.E. with the verdict. When a Cadence run delegates this peer, replace the C.O.R.E. close with the one-line return in `peer-contract.md`.
