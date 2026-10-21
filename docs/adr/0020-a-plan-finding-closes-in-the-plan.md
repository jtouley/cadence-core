---
decision_id: ADR-0020
status: accepted
question: How does a must-fix finding of the P7 plan benchmark close?
choice: In the plan. The P7 gate fails while the plan scorecard does not pass, as R2 does for the code scorecard, and it names the route `/plan-update <plan>`, then `/test-benchmark plan <plan>`. While the scorecard fails, the P4 packet pins it, and the plan peer applies its must-fix findings with a revision ledger row for each id. A new plan scorecard that passes closes them. The plan benchmark writes no row in the findings ledger.
rationale: On an end-to-end run the plan benchmark wrote `verdict FAIL` with a must-fix DRY finding, and P7 passed, because the gate checked only that the scorecard exists. The finding went into the findings ledger with a check that reads the plan. The plan freezes at P9, so after E1 no commit could fix the plan and no fix round could close the row. The run then stopped at R2 with the row still open. A plan finding is about the plan, so the gate that holds the plan must hold it, and only a plan change closes it. The findings ledger is for code: a row closes with a fix commit.
rejected:
  - alt: Keep the ledger row and let a fix round close it with a code change
    why_not: The row's check reads the frozen plan, so it never passes. A check that reads the code instead tests a different claim than the finding
  - alt: Let P9 fail on the open plan row
    why_not: P9 is after P5 to P8. A plan change at P9 sends the run back through the plan review and P6. P7 is the block that judges the test spec, so P7 holds it
  - alt: A cycle limit for P7, as P6 has
    why_not: The orchestrator already has the `retries` hard stop. A second loop counter adds state for a loop that has one input and one fix
links:
  - rel: implements
    target_type: file
    target_id: scripts/test_benchmark_gate.py
  - rel: implements
    target_type: file
    target_id: scripts/handoff_inputs.py
  - rel: implements
    target_type: file
    target_id: commands/plan-update.md
  - rel: implements
    target_type: file
    target_id: skills/test-benchmark/SKILL.md
  - rel: relates
    target_type: file
    target_id: docs/adr/0017-the-orchestrator-routes.md
---
# ADR-0020: A plan finding closes in the plan

## Context

The test benchmark scores the test spec of the plan at P7 and the tests of the code at R2. The R2
gate failed a `FAIL` scorecard. The P7 gate did not: it checked only that a plan scorecard with the
skill marker exists. The skill told both modes to write a findings ledger row for each must-fix.

An end-to-end run showed the result. The plan scorecard was `FAIL` (DRY 6, floor 7),
with must-fix `F01` and a check that searched the plan for `conftest`. P7 passed, P9 froze the plan,
and E1 built the code. A fix round later added a shared `conftest.py`, but the plan stayed as it
was, so the check of `F01` still failed. The run stopped at R2 with `F01` open.

## Decision

1. In plan mode, the P7 gate applies the scorecard rules of R2: each must-fix dimension at or above
   7, a mean at or above 7, and `verdict` `PASS`. A failing scorecard fails P7 with the route
   `/plan-update <plan>`, then `/test-benchmark plan <plan>`.
2. The P4 packet has an optional input, `plan-benchmark`: the newest plan scorecard of the run,
   with any name that the P7 gate reads, while its verdict is not `PASS`. `/plan-update` applies
   its must-fix findings and writes a revision ledger row for each finding id.
3. A packet on disk stays only while each of its pins is current. A scorecard that now passes
   leaves the P4 packet, so the handoff check does not refuse the next P4 for a stale pin.
4. In plan mode, `/test-benchmark` writes no findings ledger row. A new plan scorecard closes the
   findings of the one before it.

## Consequences

- A run reaches P9 only with a plan scorecard that passes. The plan change goes through P3 again,
  because P9 fails when the plan changed after its `READY TO EXECUTE` review.
- The runtime size budget (`tests/contract/test_source_size_budget.py`) rises from 17,800 to 17,850
  lines. The tree had 17,833 lines after this change. This is the one record.
