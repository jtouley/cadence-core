---
decision_id: ADR-0015
status: accepted
question: How does E1 move a run from one plan phase to the next, and when does the R1 to R4 fix loop stop?
choice: The stop guard holds an open E1 with the next plan phase that phase_ledger.next_phase gives. The implement gate fails when a plan of three or more phases has two phase commits with one author time to the second. R4 counts the fix rounds of the PR review (its `## Update` sections). At review_fix_rounds rounds (default 2), a loop that is still open holds with the ADR-0009 text. A config value can only lower the budget.
rationale: On the benchmark's pipeline trial, the orchestrator wrote all nine phase commits in one shell at the end, with one timestamp. The gate counted a commit for each phase, but nothing drove the next phase, and nothing saw that the commits were not phased work. In R4, FIX_LOOP_BUDGET was a constant that one test asserted and no gate read.
rejected:
  - alt: Register each phase commit with a script, as the predecessor's register_stage4_phase.py did
    why_not: The commit subjects are the record. A second register can disagree with git
  - alt: Compare the committer time, not the author time
    why_not: A rebase gives every commit one committer time, so a phased branch would fail after a rebase. Git sets both times when the shell writes the commit. The author time stays through a rebase, so it tells one shell from phased work either way
  - alt: Hold at the budget also when the loop ended
    why_not: A MERGE-READY review of HEAD with no open must-fix is the end that ADR-0009 names. A hold then blocks a run that did its job
  - alt: Let a config value raise the budget
    why_not: As for the measured gates (ADR-0008), a config value makes a limit stricter only. ADR-0009 records that later rounds cost more than they find
links:
  - rel: implements
    target_type: file
    target_id: scripts/phase_ledger.py
  - rel: implements
    target_type: file
    target_id: scripts/implement_gate.py
  - rel: implements
    target_type: file
    target_id: hooks/pipeline_stop_guard.py
  - rel: implements
    target_type: file
    target_id: scripts/final_consolidate.py
  - rel: implements
    target_type: file
    target_id: references/error-recovery.md
  - rel: relates
    target_type: file
    target_id: docs/adr/0009-a-merge-ready-verdict-ends-the-review-loop.md
---
# ADR-0015: E1 drives the next phase, and R4 counts the fix rounds

## Context

E1 has a plan with numbered phases (`### Phase N: <title>`). The implement gate passes when a
commit since the base names each phase. On the benchmark's pipeline trial, the orchestrator wrote
all nine phase commits in one shell at the end of E1, with one timestamp. Each commit had the
size of a phase, but the work was not phased. The stop guard named the open work of the gate,
but it did not tell the agent to commit the next phase before it started the phase after it.

The predecessor harness had this mechanism. A script read the plan's phase headings and compared
them with the commit subjects of the branch. A stop hook answered a stop during E1 with the next
phase while phases stayed open.

The R1 to R4 fix loop had a budget, `FIX_LOOP_BUDGET = 2`, in `scripts/final_consolidate.py`.
One test asserted the value. No gate read it. ADR-0009 ends the loop at a merge-ready verdict.
It rejected a cap that ends the loop as a pass, because a must-fix after the cap is still a
must-fix.

## Decision

1. `phase_ledger.next_phase(plan, worktree)` gives the first plan phase that no commit since the
   base names, with its label. It gives None for a plan with no phase headings.
2. When the open block is E1 and `next_phase` is not None, the stop guard holds the stop with
   `Continue E1: phase N (<label>). Commit it as 'phase N: <subject>' before the next phase.`
   A plan with no phase headings keeps the old text, which names the missing headings.
3. The implement gate fails when the plan has three or more phases and two phase commits have
   the same author time to the second. The fail text says that this is one shell, not phased
   work. A revert does not count as a phase commit. A plan of one or two phases has no such check.
4. R4 counts the fix rounds of the PR review that it reads: one round for each `## Update`
   section. At the budget, while a must-fix is open or the review does not cite HEAD, the gate
   adds a hold with the ADR-0009 text. A `MERGE-READY` verdict with no open must-fix ends the
   loop, and a nit goes to the tracker. The orchestrator does not start another fix round. It
   records each open must-fix as `deferred` with a ticket after a register row (ADR-0012 §5), or
   it stops and tells the user.
5. The budget is the config key `review_fix_rounds` in the skill-root config file (now any config
   layer, ADR-0016), default 2. The floor rule of ADR-0008 applies: a config value can only lower the budget. The gate ignores a
   larger value. A value that is not an integer of at least 1 fails the gate.
6. `references/error-recovery.md` has one row for each failure that a gate or a hook reports,
   with the command to run next, and the cap of each loop. `docs/pipeline.md` and
   `docs/system-design.md` point at it.

## Consequences

- The cap does not change ADR-0009. At the cap, R4 does not pass an open loop. The hold sends
  each open must-fix to the ledger as `deferred` with a ticket, by the register step of ADR-0012
  §5 that the hold text names, or the run goes to the user.
- A phase commit names its phase at the start of the subject, after an optional conventional
  prefix: `phase 2: x`, `feat(x): phase 2 y`. `docs: a note on phase 2` is not one (review G-10).
- `git commit --date` can give each commit of one shell its own author time. The check reads
  the time that git recorded. It does not prove that the agent phased the work.
- The runtime size budget (`tests/contract/test_source_size_budget.py`) rises from 16,900 to
  17,900 lines for PR #188 and PR #191 together (ADR-0012 to ADR-0015). This is the one record.
- The stop guard and `scripts/final_consolidate.py` are hash-pinned. Each pin has a comment line
  that names its issue.
