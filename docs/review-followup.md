# Review follow-up protocol

Use this after an adversarial review leaves findings open on a Cadence PR.
Inside the pipeline, [`references/review-rigor.md`](../references/review-rigor.md)
is the enforced version: the R1 gate requires its sections, the R2 gate
requires `adversarial_inputs` and computes the DRY lists of the changed tests,
and the R3 gate measures complexity, code shape and duplication. The
reviewer carries the load: reproduce, fix, and choose between designs before
asking the author anything.

## 1. Reproduce every finding yourself

Write a throwaway script that shows the failure (a fresh repo, the real CLI,
the real branch name). Never hand the author a "please check whether X". If you
can't reproduce it, drop it.

## 2. Sort the findings

| Kind | Action |
|---|---|
| Bug with one right answer | Fix now, with a regression test that failed first |
| Design choice (several reasonable fixes) | Simulate (below) |
| Contradicts a written contract (`SKILL.md`, `references/`) | The contract wins, or the author changes the contract |

## 3. Simulate a design choice

1. **Scenario matrix first.** Write the scenarios and expected outcomes before
   any option exists. Always include: the reported failure, the happy path,
   another ticket's leftovers, `--resume`, an adjacent id (`cd-1` vs `cd-12`),
   an offline / Pi host, and the repo's documented contract. If you add a
   scenario after seeing results, label it post-hoc and re-score every option.
2. **Three options from the same base, each in its own `git worktree`:**
   simple, medium, over-engineered.
3. **Strict TDD per option:** red for the right reason → minimal green → full
   suite → matrix → `git diff --shortstat`.
4. **Score fit**, in this order:
   - misses, weighted by cost: a fail-closed false positive that blocks every
     run is worse than a false negative a later stage catches
   - existing tests broken (each one is a contract change to justify)
   - written contracts: `core:dry-extension`, stdlib-only, offline hosts,
     `references/*`
   - diff size and new modules
5. **Pick the winner, then take the cheap ideas from the losers.** The
   over-engineered option is often best at finding requirements.
6. **Land the winners together**, re-run the suite and every matrix on the
   combined tree, and update the contract docs the fix changed. Then re-read
   the combined diff as an adversary. A green matrix only covers the input
   shapes you thought of. In PR #2 the empty-`Code paths:` rule scored 9/9 but
   treated a markdown list on the following lines as empty.
7. **Look back.** Name the failure class, not just the bug.

## Failure classes seen so far

- **"An artifact exists" is not the same as "it belongs to this ticket."**
  Every gate that trusts a file must bind it to the ticket or branch
  (`artifact_requirements.ticket_scope`, the `Branch:` line in the draft-PR
  record).
- **Timestamps and run ids are not identity.** `activate_session.py` re-stamps
  `activated_at` and mints a new `pipeline_run_id` on every activation,
  including `--resume`. Bind by ticket token instead.
- **Fail-closed has a cost.** Check that an option doesn't block where the docs
  send people: `.context/debug/` notes, hosts without `gh`, repos that never
  ran Cadence.
- **Globally installed behavior must be tested outside Cadence**: a workspace
  with no session, and a user's own symlink with the same name.
- **Untrusted text used as a path** (for example a `task_id` from a branch name in
  `.context/issues/<task_id>.md`)
  is checked where it becomes a path.

## Harden before development

- [x] CI runs `pytest` on every PR (`.github/workflows/ci.yml`).
- [ ] A new or changed gate is tested with missing, corrupt, other-ticket,
      resumed, and adjacent-id input.
- [ ] Installer or hook changes are tested in a workspace that never ran Cadence.
- [ ] The PR title type matches the diff. Split PRs that mix docs with
      runtime changes.
- [ ] The PR's test plan is fully checked. Walking `quickstart.md` end to end is
      what found the ticket-gate break in PR #2.
