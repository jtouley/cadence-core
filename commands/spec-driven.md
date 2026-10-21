---
description: Cadence E1 and the review fix rounds: TDD implementation of the plan, or the fixes of a PR review.
argument-hint: '<plan-path> | --fix <pr-review-path>'
---

# Spec-driven (Cadence E1)

Load `$CADENCE_HOME/references/peer-contract.md` and
`$CADENCE_HOME/references/execution-protocol.md`.

## Role

You are the peer that writes the code. You get one markdown file, the plan or the PR review, and
you give back one: the implementation artifact. Read every other file you need yourself: the
code, the tests and the ledger. The orchestrator reads none of them.

| Command | Input | Work |
|---|---|---|
| `/spec-driven <plan>` | the plan of the run | Each `### Phase N:` of the plan, in order (E1) |
| `/spec-driven --fix <review>` | the PR review, `.context/prs/reviews/PR_REVIEW_#<N>.md` | Each open must-fix finding of that review |

Judgment first, TDD second. Before you write a test or code, write Step 0 (3–8 lines) at the top
of the implementation artifact: the problem, what done means, the design decisions, the blast
radius, and whether the plan is wrong.

## Tasks

With a plan, make one task for each `### Phase N:` heading, in order, in the task list of your
host. Do the phases in order. `python3 $CADENCE_HOME/scripts/phase_ledger.py --json` names the
next open phase.

With `--fix <review>`, make one task for each open `must-fix` row of this run in
`.context/findings.jsonl`: the review's items, and each row that was added after the review. Make one
more for each `offenders` entry of a failing R3 record (`.context/gates/cognitive-complexity.json`).

## Cycle

1. Failing test (`test_unit_scenario_expectedBehavior`, AAA).
2. Run it. Make sure that it fails for the right reason.
3. Minimum code to pass.
4. Run it green.
5. Next cycle. Do not add a helper with one caller.

Commit each plan phase with a subject that starts with `phase N:`, or with a conventional prefix and then
`phase N` (`feat(x): phase 2 wiring`). The E1 gate reads the phase from the start of the subject.
Commit each phase when it is done, not all phases at the end: with three or more phases, the E1 gate
fails when two phase commits have the same author time.

## A fix round

For each finding of `--fix <review>`:

1. Commit a failing test that reproduces it: `test: <finding id> <behavior> (red)`.
2. Commit the fix: `fix: <finding id> <behavior>`.
3. Record it: `python3 $CADENCE_HOME/scripts/findings_io.py set-status --id <id> --status fixed --fixed-in <sha>`.

A finding that you cannot fix in this PR gets `--status deferred --ticket <ticket>` after its
register row (ADR-0012). Do not set `verified`: `ledger_verify.py` does.

## Rules

1. Each callback or Protocol wired across modules gets a seam-contract test through the entry point of the caller, because unit tests miss the mismatch.
2. In bug-fix mode, write a failing reproduction test first, then the fix, because only a test that fails before the fix proves the fix.
3. Mock at the process or network edge, never the module under test, because a test of a mock does not test the code.
4. A test name names the behavior, never a connector, a customer or a ticket, because the name tells the reader what broke.

## Options in a phase

A phase can have more than one way to pass. Try up to three options, one at a time:

1. Write the test for the phase.
2. Write the code of one option.
3. Run the tests and `python3 $CADENCE_HOME/scripts/cognitive_complexity_gate.py --check`.
4. When the gates pass, keep this option. Remove the code of the other options.
5. When the gates fail, remove the code and try the next option.

Record each option that you tried in the Implementation Summary.
Do not go back to P3 for a choice inside a phase. P3 reviews the plan, not the option.

## A failed R3

When the local R3 run still fails after the options of a phase, do the fix step:

1. Run `python3 $CADENCE_HOME/scripts/cognitive_complexity_gate.py` without `--check`. It writes the R3 record `.context/gates/cognitive-complexity.json`.
2. Follow `$CADENCE_HOME/subagents/simplify-code.md` yourself, with the R3 record as its one input. Its `offenders` lists are the task list. A peer cannot start another agent.
3. Run R3 again.

Do the fix step one time. Then apply the rule of ADR-0009. A pass ends the step. An offender that stays is a must-fix: fix it, then run R3 again.

## Reuse

Apply `core:dry-extension` in `$CADENCE_HOME/references/architecture-core-rules.md`.

1. Before you write a helper, search for one that exists. In Cadence, run this search:

   ```bash
   grep -rnE "def _?<name>\b" tests/conftest.py scripts/ shared/
   ```

   In another repo, search its shared test fixtures and shared modules.
2. Use the helper that you find. Do not write a second copy.
3. When a file that you change contains duplicate logic, remove the duplication in the same plan phase.
4. Use the extension point that the plan names under `## Reuse and extension`.

## Cadence binding

Before gated source edits, `entry_invariant.py --phase execution` must be
green. When the tests pass, follow `$CADENCE_HOME/subagents/deslop.md` yourself on the
branch. Then write `.context/implementation/{task}_stage4.md`:

```
RUN_BY_COMMAND: spec-driven
```

Include Step 0, the Implementation Summary, and Verification (commands + passed). A fix round
adds a `## Fix round <date>` section to the same file.

End with one line: `ARTIFACT: .context/implementation/{task}_stage4.md`. The orchestrator runs
E2 to E4. Do not open the PR. When a phase stays open, the stop hook names it: commit it, then end.
Do not answer a finished or aborted shell with status only.
Resume the open cycle in that turn.
