---
decision_id: ADR-0019
status: accepted
question: What does a run do with a blocking gap that A5 finds in the request?
choice: The run carries every blocking gap into the plan. The vibe-test report labels each one `G-n` under `## Blocking gaps` with a `Verdict:` line. P1, P3 and P4 get the report as a pinned input. The plan peer logs an assumption for each `G-n` under `## Assumptions` in the register, which already logs the run's deferrals. P9 fails while a `G-n` has no row, and the R6 comment lists the assumptions for the reviewer. A5 fails a NOT READY report that labels no gap.
rationale: On two end-to-end runs the vibe-test peer reported NOT READY FOR PLANNING, with five and seven blocking gaps, and the A5 gate passed both, because it checked only that the report exists. The orchestrator then told the planner about the gaps, or did not, and nothing checked that the plan answered them. A5 is there to find the gaps before the planning starts; a gate must then hold the run until each gap has an answer. The run is autonomous, so the planner answers, and the reviews judge the answer. An answer to a gap is an assumption that the user did not state, so it is logged where the run logs what it defers: a person reads both in one register and in the PR comment.
rejected:
  - alt: Stop the run at A5 on any blocking gap
    why_not: Most gaps have an answer that the planner can state and a review can judge. A stop on each one ends most autonomous runs before the plan
  - alt: Stop on a gap that only the user can answer, and carry the others
    why_not: The person who owns the work chose to carry every gap. A gap that needs a person shows in the plan's answer, and P3 and P6 can judge that answer
  - alt: Answer the gaps in a section of the plan
    why_not: The plan is the build order. An assumption in it is read once by the reviews and then lost in the phases. The register is the log that a person and the PR comment read
  - alt: Keep the gap check in the plan review only
    why_not: A review is a peer's judgment. P9 checks each id with a script, so a plan cannot reach E1 with a gap that it never answered
  - alt: Run the adversarial spec-pressure review on the request as a new block
    why_not: P6 already reviews the plan, with the gaps and their answers in it, before E1. A second adversarial block before the plan adds a peer call and finds what P6 finds
links:
  - rel: implements
    target_type: file
    target_id: scripts/artifact_requirements.py
  - rel: implements
    target_type: file
    target_id: scripts/gate_common.py
  - rel: implements
    target_type: file
    target_id: scripts/handoff_inputs.py
  - rel: implements
    target_type: file
    target_id: skills/vibe-test/SKILL.md
  - rel: implements
    target_type: file
    target_id: stages.json
  - rel: relates
    target_type: file
    target_id: docs/adr/0018-a-block-writes-its-own-markdown.md
---
# ADR-0019: A5 gaps are carried into the plan

## Context

A5 runs `/vibe-test` on the user's request, which activation saves as
`.context/proposals/<task_id>.md` (ADR-0018). The peer traces scenarios through the request and
marks each gap blocking or not. The A5 gate checked only that the report exists, with its marker and
its tracker header. A report that said NOT READY FOR PLANNING passed. No later gate read the gaps.

## Decision

1. The vibe-test report lists each blocking gap under `## Blocking gaps`, one line each, as
   `G-n: <the gap>`, and ends with `Verdict: READY FOR PLANNING` or `Verdict: NOT READY FOR PLANNING`.
2. A5 fails a report whose verdict is NOT READY and whose `## Blocking gaps` names no `G-n`.
3. The handoff pins the request and the newest vibe-test report of the run for P1, and the report
   for P3 and P4 (input kinds `request` and `vibetest`, both optional: A5 is in the spec-review pack).
4. The plan peer logs one row for each `G-n` under `## Assumptions` in
   `.context/deferrals/<task_id>_deferral-register.md`: the gap, the assumption, and the phase or
   test that settles it. P3 checks each row. P9 fails while a `G-n` of the report has no row. R6
   writes the rows into the PR comment, and the session summary peer reads them.
5. The run never stops on a gap. The hard-stop class `a5-gap` stays for a person who stops the
   run at A5.

## Consequences

- A gap reaches E1 only with a logged assumption that a review read. A wrong assumption is a P3
  must-fix item. The reviewer of the PR sees each assumption next to the fixes and the deferrals.
- A report with no `## Blocking gaps` section carries nothing. A READY report carries nothing.
- The size budget raise of these checks is recorded in ADR-0018.
