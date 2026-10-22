---
description: Cadence E1: TDD implementation of the plan, phase by phase.
argument-hint: '<plan-path>'
---

# Spec-driven (Cadence E1)

Load `$CADENCE_HOME/references/peer-contract.md` and
`$CADENCE_HOME/references/execution-protocol.md`.

## Role

Judgment first, TDD second. Do not write tests or code until Step 0 is
answered in chat (3–8 lines): problem, done, design decisions, blast radius,
whether the plan is wrong.

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
2. Delegate `$CADENCE_HOME/subagents/simplify-code.md` in one Agent call. The R3 record is its one input. Its `offenders` lists are the task list.
3. Run R3 again.

Do the fix step one time. Then the loop rule applies: a `MERGE-READY` verdict with no open must-fix ends the loop. A pass ends the step. An offender that stays is a must-fix: fix it, then run R3 again.

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
green. Write `.context/implementation/{task}_stage4.md` when tests pass:

```
RUN_BY_COMMAND: spec-driven
```

Include Implementation Summary and Verification (commands + passed). Before E2,
delegate `$CADENCE_HOME/subagents/deslop.md` on the branch in one Agent call. Then
continue E2→E4 in the same turn. Do not ask permission to open the draft PR.
Do not answer a finished or aborted shell with status only.
Resume the open cycle in that turn.
