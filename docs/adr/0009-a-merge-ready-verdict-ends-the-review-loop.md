---
decision_id: ADR-0009
status: accepted
question: When does the R1 review loop end?
choice: A review verdict of MERGE-READY with no open must-fix ends the loop. An open nit after that verdict goes to the tracker or to a Follow-ups list in the review, not into a fix commit. A fix commit after a merge-ready verdict starts a new round, because the R1 gate requires the newest review to cite HEAD. A new must-fix opens the loop again.
rationale: PR #168 took eight peer rounds. Round four was merge-ready on the code. Rounds five to eight each fixed one nit from the round before, mostly edges of one regex, and each fix commit needed a review of HEAD, so each produced one more nit. Four rounds cost about two hours of review for a few lines. The loop had no end rule, so the orchestrator kept going. The gate rule that a review must cite HEAD is right (a fix after a review is unreviewed code); the missing rule was when to stop pushing.
rejected:
  - alt: Exempt a tests-only or docs-only commit from the HEAD rule
    why_not: A test can be weakened and a doc can change a contract. The gate would have to classify commits, and that is a hole
  - alt: Cap the loop at N rounds
    why_not: An arbitrary number. A must-fix found in round N+1 is still a must-fix
  - alt: Tell the peer to report no nits after a merge-ready verdict
    why_not: The nits were real. The fix is where they go, not whether they are written
links:
  - rel: implements
    target_type: file
    target_id: commands/pr-review-update.md
  - rel: implements
    target_type: file
    target_id: commands/pr-review-local.md
  - rel: relates
    target_type: file
    target_id: scripts/gate_common.py
---
# ADR-0009: A merge-ready verdict ends the review loop

## Context

PR #168 was reviewed by eight fresh-context peers, each invoked with only `/pr-review-local 168`
or `/pr-review-update 168`. Round one returned NO MERGE with one must-fix and nine nits; round two
a new must-fix; round three a new must-fix. Round four returned merge-ready on the code with four
nits. The orchestrator fixed the nits, pushed, and re-reviewed. Rounds five, six, seven and eight
each returned merge-ready with one new nit, each on the regex the previous round had just changed:
a word before an id, a continuation line, a word after a marker, a test row with no anchor item.

Two rules made the loop run. `pr_review_local_gate.py` fails while the newest review does not
cite HEAD (PR #168 N-1), so every fix commit needed another review. And nothing said when a
merge-ready verdict was enough. The peer did its job each time: each nit was real. The orchestrator
did its job each time: each nit was fixed. The system had no stop.

## Decision

1. A verdict of `MERGE-READY` (or `APPROVE`) with no open must-fix ends the loop.
2. An open nit after that verdict goes to the tracker as an issue, or under `## Follow-ups` in the
   review. The orchestrator does not push a fix commit for it to this PR.
3. A new must-fix opens the loop again: fix, push, `/pr-review-update`.
4. The HEAD rule of the R1 gate does not change. The review on file cites the merged head.
5. The peer's return line names the state: `VERDICT: MERGE-READY (0 must-fix, N nits to the tracker)`.

## Consequences

- `commands/pr-review-update.md` and `commands/pr-review-local.md` carry the rule.
- A contract test asserts the rule text in both commands.
- A nit found after merge-ready is a tracker issue with the PR number, so it is not lost.
- The eight rounds on PR #168 are the evidence; the sibling benchmark branch saw the same shape
  on the P3 loop (each cycle finds new items in the text the previous cycle added), which
  `commands/plan-review.md` already bounds to structure.
