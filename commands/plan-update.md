---
description: Cadence P4: apply the newest plan review to the plan.
argument-hint: '<plan-name-or-path> [--from-review <review-path>]'
---

# Plan update (Cadence P4)

Load `$CADENCE_HOME/references/peer-contract.md`.

## Trigger

`/plan-update <plan-name-or-path> [--from-review <review-path>]`

## Inputs

- The plan. Stop if the plan is missing.
- The review at `--from-review <review-path>`. Read that file.
  Without `--from-review`, read the newest `.context/reviews/plan_<basename>.md`. Stop if no review exists.

The open items of the review are the `MF-n` items under its `## Must-fix items` heading.
An id in its `## Status of earlier items` table, or in a note, is not open.

## Rules

- A plan review round file `.context/plan-findings/round-<n>.md` is a review that joins the must-fix
  rows of each reviewer by section. Follow `references/plan-review-round.md`: change only the
  sections that it names, and answer each of its ids in its ledger.
- Keep the plan's header lines as they are: `RUN_BY_COMMAND: plan-create`, `Ticket:`, `Pipeline-Run:`.
- Edit each section in place: the plan stays one specification, not a base and a list of changes.
  Change only the sections that the review names. Record what changed and why in the ledger rows.
- Apply the review's required changes. Do not silently drop a must-fix.
- Write a `## Revision ledger (review <sha>)` table after the phases. `<sha>` is the first 12 characters of the sha256 of the review file you answer. Your packet's `Input:` line for the review shows it. Without a packet, run `sha256sum <review>`. The gate reads the ledger that names the newest review, and no other.
  Write one row for each open `MF-n` of the review:

```markdown
## Revision ledger (review 5a76c1d2e3f4)

| Id | Change | Where (phase or section) | Verification (the test or check that proves it) |
|---|---|---|---|
| MF-1 | Add the test for an empty config | Phase 2 | `test_load_rejects_empty_config` |
| MF-2 | not changed: `scripts/x.py` already has the helper | Reuse and extension | `grep -n "def helper" scripts/x.py` |
```

- An `MF-n` that this update does not change also gets a row. Write `not changed: <reason>` in its Change cell.
- Write a new ledger in each update. A ledger of an earlier update does not answer a newer review.
- The gate finds the ledger by the review sha in its heading. A heading after the ledger
  does not change which ledger the gate reads.
- The P4 gate fails while an `MF-n` of the review has no row in the ledger that names the review.
  The fail text names each missing id and the review file.
- Mark the file:

```
RUN_BY_COMMAND: plan-update
```

- This update writes the plan, and the decision records and deferral rows that the review asks
  for (below). Do not start E1.
- Do not create or edit `reviews/plan_*.md`. Only a plan-review run writes that file.
- Do not run `/plan-review`. Do not call the `plan-review` skill. Do not review your own update.
  When a Cadence run delegates this peer, end after the plan write with the one-line return in `peer-contract.md`.

The orchestrator owns the loop. After this update, the orchestrator runs `/plan-review` in a new Agent call.
It repeats `/plan-update` and `/plan-review` until the newest review has `RUN_BY_COMMAND: plan-review` and `Verdict: READY TO EXECUTE`.
That review must come from a plan-review run after this update.
Each cycle writes the review's required contracts into the plan.
The plan has one review cycle (`plan_review_cycles`, default 1; a config layer may raise it). After the last blocking review, this update is the last one.
Add `## Carried review findings` and map each open `MF-n` to the phase whose test settles it. Do not request another review.
At the limit, the gate reads `## Carried review findings`, not the ledger. Write the ledger of this update too.
When the round file has adversarial rows, also add `## Carried adversarial findings (<report file> @ <first 12 characters
of its sha256>)` and map each open `B-n` and `C-n` to its phase. `plan_wave.py` and P9 name the exact heading.
`NOT READY` after the last review cycle: the orchestrator runs `activate_session.py --hard-stop retries --reason "..."`.
Do not ask the user to allow another cycle.

## Decisions and deferrals

- When a review asks for a decision record, write it as step 5 of `commands/plan-create.md` says.
- When a review or the request proposes a deferral, take it only when it meets each criterion of
  `references/deferral-framework.md`: the request does not ask for it (`Request trace` is `none`),
  a code change in this PR cannot do it, and the ripeness has its evidence and a follow-up ticket.
  Add its row under `## Approved deferrals` in `.context/deferrals/<task_id>_deferral-register.md`,
  and write the register with that heading when it does not exist. Add a deferral that you reject
  under `## Deferrals rejected`, and plan its work. The `/deferral-review` peer then rules on each row.
- When the packet pins a `/deferral-review` ruling (`.context/deferrals/<task_id>_deferral-review.md`):
  - `RED`: move the row under `## Deferrals rejected` in the register, and plan its work in a phase.
  - `AMBER`: keep the row. Add a `## Deferral conditions` section to the plan, with one line for each
    `AMBER` row: `- approved <#>: <the condition> (Phase N)`. Plan the condition in that phase.
  - `GREEN`: change nothing.
- P8 writes the register itself only when no deferral was proposed. The orchestrator writes none.
- Keep the `## Assumptions` row of each `G-n` of the vibe-test report in the register. When a
  review disputes an assumption, change its row and the phase that settles it.

## Adversarial review (P6)

When the newest P6 report of the run (`.context/adversarial/adversarial_{slug}*.md`, `Mode: plan-gate`)
is `REQUEST CHANGES` or `APPROVE WITH CONDITIONS`, take in its blocking items and conditions with their ids.
- Below the limit (`adversarial_cycles`, default one P6 cycle): apply each `B-n`. Then the orchestrator runs `/plan-review` if P3 is below its limit.
  Then the orchestrator runs `/de-adversarial-reviewer plan-gate`. Each review is a new Agent call.
- At the limit: this update is the carry. Do not run P6 again. Get the heading with
  `python3 $CADENCE_HOME/scripts/adversarial_review_gate.py --heading <report>`.
  Add that heading and map each `B-n` and `C-n` of that report to the phase whose test settles it.
- `APPROVE WITH CONDITIONS`: add the heading for that report and map each `C-n` under it before P9.

## Test benchmark (P7)

When the packet pins a plan scorecard (`.context/benchmarks/*_plan.json`), its verdict is not
`PASS`. Apply each must-fix finding of its `findings` list to the test spec of the plan, and write one
row for each finding id in the revision ledger. Write the ledger heading with the first 12 characters of
the sha256 of the scorecard. Then the orchestrator sends `/test-benchmark plan <plan>` again. A plan
finding has no row in the findings ledger, so do not write one.

One budget: a P3 review that a P6 round causes counts in the P3 limit. At the P3 limit the P3 carry rule applies.
A carry-only update (P3 or P6 at its limit) needs no new review. The gates check the carried ids.
Chat: C.O.R.E. When a Cadence run delegates this peer, replace the C.O.R.E. close with the one-line return in `peer-contract.md`.

## Acceptance check defect

`/plan-update <plan> --acceptance AC-n` comes from E1, R1 or R5. The log
(`.context/acceptance/<task_id>.repair.jsonl`) holds the defect of AC-n, with its repro and its reason.

1. Fix the check script, or the `run` command of AC-n, so that code that meets the claim passes.
2. Keep the claim of AC-n. Do not change another check.
3. Commit the script (`test: fix acceptance check AC-n`).
4. Run `python3 "$CADENCE_HOME/scripts/acceptance.py" repin --id AC-n`. It refuses a changed claim
   and a change to another check. It logs the old and the new sha256 for R1.

Do not change the plan for this. The plan is frozen after P9.
