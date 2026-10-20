---
decision_id: ADR-0011
status: accepted
question: How does the P3 and P4 plan loop converge before its cycle limit?
choice: Each plan update ends with a `## Revision ledger (review <sha>)` table that has one row for each open MF-n of the review it answers. The P4 gate fails each cycle while an MF-n has no row in the ledger that names the review by its sha256. From cycle two on, the plan review gets the previous review and opens with a `## Status of earlier items` table. A new must-fix item cites only text that the newest update added or changed. The cycle limit stays at five.
rationale: On the benchmark trials, each run reached the five-cycle limit with READY WITH CHANGES each time. Each cycle, a fresh reviewer read the full plan, which grew by one append-only section. The reviewer found new must-fix items in the text that the previous cycle added. Nothing gave cycle N+1 the review of cycle N, and nothing checked in the loop that update N answered each MF-n of review N. The gate checked that map only at the limit.
rejected:
  - alt: Lower the cycle limit to two, as the predecessor harness did
    why_not: The cd-47 plan needed five reviews, and each one closed real holes (cd-47 D10). A lower limit hides the defect. It does not fix it
  - alt: Check the map of MF-n ids only at the limit (the rule before this change)
    why_not: An update could drop an item in silence for four cycles. The next reviewer then found it again, or found a new item in its place
  - alt: Let the reviewer read the full plan with no previous review
    why_not: A fresh reviewer has no record of what an earlier review accepted, so each cycle reopens text that already passed
  - alt: Write a second MF-n id reader for the ledger check
    why_not: The gate reads the open ids with `review_must_fix_ids`. A second reader can disagree with P9 about which ids are open
links:
  - rel: implements
    target_type: file
    target_id: commands/plan-update.md
  - rel: implements
    target_type: file
    target_id: commands/plan-review.md
  - rel: implements
    target_type: file
    target_id: scripts/artifact_requirements.py
  - rel: implements
    target_type: file
    target_id: scripts/plan_update_gate.py
  - rel: relates
    target_type: file
    target_id: docs/adr/0009-a-merge-ready-verdict-ends-the-review-loop.md
---
# ADR-0011: The plan loop has a ledger and a delta review

## Context

The plan loop is P3 (`/plan-review`) and P4 (`/plan-update`). The orchestrator repeats the two
until the newest review is `READY TO EXECUTE`, for at most five review cycles. At the limit, a
`READY WITH CHANGES` review clears when the plan maps each open `MF-n` to a phase under
`## Carried review findings`.

On the benchmark trials, each run reached the limit. Each review was `READY WITH CHANGES`. Each
cycle, a new reviewer in a fresh context read the full plan. The plan grew by one append-only
update section each cycle, and the reviewer found new must-fix items in that new text. Two parts
were missing:

1. Nothing gave review N+1 the review of cycle N. The reviewer could not tell an old item from a
   new one, or an accepted sentence from a new sentence.
2. Nothing checked in the loop that update N answered each `MF-n` of review N. The gate checked
   that map only at the limit.

ADR-0009 ends the R1 loop at a merge-ready verdict. It notes the same shape on the P3 loop. The
structure-only rule in `commands/plan-review.md` bounds what a must-fix item is, but not which
text a reviewer reads again.

## Decision

1. Each `/plan-update` appends a dated section `## Plan update <YYYY-MM-DD>`. The section ends with
   a `## Revision ledger` table with the columns
   `Id | Change | Where (phase or section) | Verification (the test or check that proves it)`.
   The table has one row for each open `MF-n` of the review that the update answers. An item that
   the update does not change gets a row with `not changed: <reason>`, so the update drops no item
   in silence.
2. `/plan-update` reads the review at `--from-review <review-path>`. Without the flag, it reads
   the newest `.context/reviews/plan_<basename>.md`.
3. The P4 gate (`plan_review_step`, which `plan_update_gate.py` calls) fails closed below the limit
   when the plan is newer than the blocking review and an id of `review_must_fix_ids(review)` has
   no row in the ledger that names that review. The ledger heading is `## Revision ledger (review
   <sha>)`, where `<sha>` is the first 12 or more characters of the review file's sha256, which the
   peer reads from its packet's `Input:` line. One source for both sides, no count, so an older
   review's ledger never answers a newer review (PR #186 R1 MF-2, MF-3). The fail text names each
   missing id, the review file and the heading to write. A review with no open must-fix item passes.
4. At the limit, the gate reads `## Carried review findings`, as before. That section already maps
   each open id to a phase, so a second check of the ledger adds nothing there.
5. From cycle two on, the orchestrator gives the reviewer the previous review as a second input.
   The review opens with a `## Status of earlier items` table: one row for each `MF-n` of the
   previous review, `closed` with the ledger row and the plan text as evidence, or `open` with
   what is still missing. A new must-fix item cites only text that the newest update section
   added or changed. A sentence that was in the plan when the previous review passed it is not a
   new must-fix item. A new item takes the next id after the highest id of the previous review.
6. The structure-only rule and the `## Must-fix items` heading stay. The reviewer writes the
   heading in each review, with `None.` when no item is open.
7. `PLAN_REVIEW_CYCLE_LIMIT` stays at 5 (cd-47 D10) (now `plan_review_cycles`, ADR-0016).

## Consequences

- The predecessor harness (its Stage 2 and Stage 3) used a limit of two review-update cycles.
  Cadence keeps five. The benchmark measures the cycle count again after this change lands. A
  lower limit needs a new decision record with that measurement.
- An update that skips the ledger, or skips one id, now stops at P4 in the cycle where it happens,
  not at the limit. The fail text tells the peer which ids to answer.
- A ledger with no `(review <sha>)` in its heading, or with a sha of another review, counts as no
  ledger, and the gate fails. That is the fail-closed side.
- A review with no `## Must-fix items` heading counts every `MF-n` in the file as open
  (`review_must_fix_ids`), which includes the ids of its status table. The ledger then needs a row
  for each. That is also the fail-closed side.
- The handoff that gives the reviewer the previous review is a separate change. This change writes
  what the reviewer does with it.
- The runtime size budget in `tests/contract/test_source_size_budget.py` goes from 16,550 to
  16,900 (merged with ADR-0010) lines. The tree has 16,604 lines after this change.
- Tests: `tests/gates/test_plan_review_step.py` (the gate) and
  `tests/contract/test_plan_loop_has_a_ledger_and_a_delta_review.py` (the command text).
