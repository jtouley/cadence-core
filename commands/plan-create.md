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
   Each phase holds its own failing test and the code that makes that test pass. E1 commits each
   phase red, then green, and runs the phase's test at both commits. Do not write a phase of tests
   alone: a phase whose tests need the code of a later phase can never be green, and E1 stops on it.
4. Add a `## Reuse and extension` section (`core:dry-extension`).
   Name the existing helpers and modules that the plan reuses.
   Name the extension point that the plan adds or uses.
   When the plan adds none, write `None` and the reason.
5. Write a decision record for each design decision of the plan: a storage format, an
   interface, a dependency, a rule that the code must keep. The path is
   `.context/proposals/<task_id>-<slug>.md`, with this frontmatter and the ticket line:

   ```markdown
   ---
   decision_id: <task_id>-<slug>
   status: proposed
   question: <the question that the decision answers>
   choice: <the choice>
   rationale: <why this choice, and the option that it rejects>
   ---
   Ticket: <task_id>
   ```

   P5 reads these records for decision coverage. The orchestrator writes no decision record.
6. When the packet pins a vibe-test report with `## Blocking gaps`, log an assumption for each
   `G-n` in the register `.context/deferrals/<task_id>_deferral-register.md`, which logs what the
   run assumes and what it defers. Write the register when it does not exist, with a `Ticket:`
   line, a `Pipeline-Run:` line and an `## Approved deferrals` section (`No approved deferrals.`
   when there is none). Plan each part of the request. A deferral row must meet each criterion of
   `references/deferral-framework.md`. Add this section, one row for each `G-n`:

   ```markdown
   ## Assumptions

   | Gap | Assumption | Settled by |
   |---|---|---|
   | G-1 | <what the plan assumes, in place of what the request does not say> | <the phase or the test> |
   ```

   The plan builds each assumption. P9 fails while a `G-n` has no row. R6 shows the rows to the reviewer.
   A blocking gap, or a vibe-test verdict of NOT READY, is never a P1 stop: write the plan, and settle
   each gap with an assumption row. When the request asks for an item and also asks to defer it, the
   item stays in this change: plan it in a phase, and put the deferral under `## Deferrals rejected`
   (step 7). No person decides it.
7. You write each deferral row of the register. P8 and `/deferral-review` only rule on the rows.
   When the request or the plan moves an item out of this change, and the item meets each
   criterion of `references/deferral-framework.md`, add it under `## Approved deferrals`:

   ```markdown
   | # | Item | Why deferred | Request trace | Ripeness | Evidence | Follow-up ticket |
   |---|---|---|---|---|---|---|
   | 1 | <the item> | <why it leaves this change> | none | blocked-by | <the open ticket or PR it waits on> | <ticket> |
   ```

   With the adapter `none`, also write the follow-up ticket as a `LOCAL-<n>` section in
   `.context/issues/<task_id>.md`. An item that the request asks for is not deferred: put it under
   `## Deferrals rejected` with its request trace, and plan it in a phase.
8. Write the acceptance checks: `.context/acceptance/<task_id>.json`. P1 fails without them.
   Each check proves one outcome that the request asks for. The packet pins the request when
   activation saved it (`.context/proposals/<task_id>.md`):

   ```json
   {"RUN_BY_COMMAND": "plan-create", "task_id": "<task_id>",
    "checks": [{"id": "AC-1", "claim": "<the words of the request that it proves>",
                "run": "python3 acceptance/check_marts.py"}]}
   ```

   - A check runs in the worktree and reads the delivered files, not the diff. Write it from the
     request, not from the plan. A check that runs a unit test of a helper proves nothing about the
     request.
   - `run` names a test runner or a script path. `true`, `:` and `manual` do not count.
   - Write a check script outside the gated prefixes (for example `acceptance/`), so P1 can write it.
     Commit it (`test: acceptance checks of the request`): A2 needs a clean tree, and the PR then holds
     the checks that the reviewer runs. The P1 gate fails while a script is not committed, and it gives
     the command.
   - Write one script for each check. Do not let two checks name one script: a repin of one check may
     change only its own script. P1 and P9 fail on a script that two checks name.
   - A check of a printed value makes the expected text from its own input (the rows that it writes).
     It compares the whole line or the whole file, not a pattern or a digit. A pattern passes code that
     prints a fixed value. In four live runs, the check of "prints the row count" was weak in this way.
   - A claim about bytes (line ends, an encoding) reads the output in binary mode. In text mode,
     Python changes `\r\n` to `\n`, and then no code can pass the check.
   - P9 pins the file and each script that a check names. E1 runs each check when the last phase is
     done, and R5 runs them again at HEAD. A change after P9 is a new plan review.
9. Footer: next step is `/plan-review <plan>`. Do not tell the user to
   `/plan-update` before a review file exists.

Chat close is C.O.R.E. First action: `/plan-review`. When a Cadence run delegates this peer, replace the C.O.R.E. close with the one-line return in `peer-contract.md`.
