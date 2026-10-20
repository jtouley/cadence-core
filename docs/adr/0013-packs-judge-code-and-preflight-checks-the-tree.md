---
decision_id: ADR-0013
status: accepted
question: How do P9, the pack gates and A2 close the three gaps that the comparison with the predecessor harness found (#176, #177, #181)?
choice: P9 fails when the plan changed after the READY TO EXECUTE review that decides P3. The review names the plan in a `Last reviewed plan sha256:` line, and with no such line P9 compares the mtimes. A pack gate runs in each phase that `phases` of its `pack.json` lists (planning for P9, impl for E3, review for R3). E3 and R3 give it the changed files as JSON on stdin. A gate that writes no `findings` list fails with the pack name. The pack list of the project's `.cadence.json` turns on the gate of a pack too. A2 checks `gh api user` for the github-issues adapter, a branch that is not the default branch, and a tree with no change outside Cadence's own state.
rationale: The predecessor harness failed a plan that changed after its review. It judged diffs with org gates, and it checked the branch and the tree before a run. In Cadence, a plan that changed between the passing review and P9 passed P9. A pack gate judged planning artifacts only, and a crash with no output passed. A pack that only the project's `.cadence.json` named had no gate run. `stages.json` said that A2 checks the environment, the branch and a clean state, and `preflight.py` did not check them.
rejected:
  - alt: Compare only the plan mtime with the review mtime
    why_not: A touch with no change fails P9, and a copy that keeps the mtime passes. The sha256 on the review names the bytes that the peer read. The mtime stays as the fallback for a review with no such line
  - alt: Give the changed files to the gate as `--files` arguments
    why_not: A long change can go over the argument limit, and a gate that does not know the flag stops with a usage error. JSON on stdin has no limit, and a gate that does not read stdin still runs
  - alt: Treat a pack gate that writes no output and exits 0 as a pass
    why_not: A gate fails closed. A gate that cannot give its evidence fails. It does not pass
  - alt: Write E3 pack findings to the findings ledger
    why_not: A pack finding has no id of this task and no check that ledger_verify can run. The finding fails E3 and stops the E3 breadcrumb, and the next E3 run judges the change again
  - alt: Fail A2 on any path that `git status` shows, `.cursor/plans/` too
    why_not: The stop guard runs A2 again during planning. A Cursor plan in `.cursor/plans/` is Cadence state, as `.context/` is, and it would make A2 the open block of each planning stop
links:
  - rel: implements
    target_type: file
    target_id: scripts/artifact_requirements.py
  - rel: implements
    target_type: file
    target_id: scripts/entry_invariant.py
  - rel: implements
    target_type: file
    target_id: commands/plan-review.md
  - rel: implements
    target_type: file
    target_id: scripts/pack_loader.py
  - rel: implements
    target_type: file
    target_id: scripts/architecture_review.py
  - rel: implements
    target_type: file
    target_id: scripts/cognitive_complexity_gate.py
  - rel: implements
    target_type: file
    target_id: references/pack-standard.md
  - rel: implements
    target_type: file
    target_id: scripts/preflight.py
  - rel: relates
    target_type: file
    target_id: docs/adr/0007-five-step-core-with-opt-in-blocks.md
  - rel: relates
    target_type: file
    target_id: docs/adr/0011-the-plan-loop-has-a-ledger-and-a-delta-review.md
---
# ADR-0013: Packs judge code, and preflight checks the tree

## Context

A comparison of the predecessor harness with Cadence found three gaps.

1. P9 and the plan (#176). `check_plan_review_verdict` compares the plan mtime with the review
   only in a blocking loop. A plan that changed after a `READY TO EXECUTE` review and before P9
   passed P9. After P9, the plan freeze protects the plan. The window
   between the passing review and P9 was open.
2. The pack gates (#177). Only P9 called `run_pack_gate`, so a pack could judge planning
   artifacts but not a code change. A gate that crashed with no output, in a pack with no rules,
   returned `[]` and passed. The extra pack list of the project's `.cadence.json` (now `packs` in the project
   layer, ADR-0016) turned on the stage rows of a pack, but its `gate.py` did not run.
3. A2 (#181). `stages.json` says that A2 checks the environment, the branch and a clean state.
   `preflight.py` checked the git toplevel and the peer shadows only.

## Decision

1. P9 reads the review that decides P3 (`select_plan_review`). When its verdict is
   `READY TO EXECUTE`, P9 compares it with the plan of the run (`plan_changed_after_review`):
   - When the review has a `Last reviewed plan sha256: <sha>` line, the sha256 of the plan must
     start with that value (12 to 64 hex characters).
   - With no such line, the plan mtime must not be newer than the review mtime. A tie passes.
   - The fail text is `the plan changed after its review; run /plan-review again`.
   - `commands/plan-review.md` tells the peer to write the line. The peer reads the sha256 of the
     plan from the `Input:` line of its packet.
   - A blocking review is not this check. `check_plan_review_verdict` and the carried findings
     at the limit decide it, as before.
2. `pack.json` has `phases`, a list of `planning` (P9), `impl` (E3) and `review` (R3). A value
   outside that set fails the load. So does a key outside the manifest's nine keys, a
   `gate_script` with an empty `phases` list, or phases with no `gate_script`: no typo loads as a pack whose
   gate never runs. A finding
   blocks unless its severity is `nit`: no severity, or a misspelling, is a must-fix. A
   manifest with no `phases` key has `["planning"]`, so a pack from before this change keeps
   its P9 gate. A value that is not a list fails the load.
3. `run_pack_gate(pack, phase, worktree, files)` writes `{"phase", "worktree", "files"}` as JSON
   on the stdin of the gate. The gate writes `{"findings": [...]}` on stdout.
   `references/pack-standard.md` has the contract.
   - No output, output that is not JSON, or JSON with no `findings` list of objects raises an
     error with the pack name.
   - A non-zero exit with no must-fix finding adds the must-fix finding `pack:<name>:exit`.
4. E3 (`architecture_review.py --phase impl`) and R3 (`cognitive_complexity_gate.py`) run the
   gate of each enabled pack that lists `impl` or `review`. The files are the changed files of
   the run, from the merge base with the base branch: committed, uncommitted and untracked
   (`change_under_review`, the same reader for both). A pack error is a must-fix finding of E3.
   E3 does not write it to the findings ledger. It fails E3 in both modes and stops the E3
   breadcrumb. In R3, a pack error is one more problem line.
5. `load_enabled_packs(config, packs_dir=..., workspace=...)` adds the extra pack names of
   `workspace/.cadence.json`, after `enabled_packs`, each name once. (Now `enabled_packs()` reads
   `packs` of the resolved config, ADR-0016.)
   With no `workspace`, it reads no project file. P9, E3 and R3 give the workspace. A name with
   no manifest, or a `.cadence.json` that is not valid, is an error of the gate.
6. `packs/_sample_impl_gate/` has the phases `impl` and `review`. Its gate flags a file with the
   name `SAMPLE_IMPL_GATE_PLANTED.txt` in the change. It is the fixture of E3, R3 and
   the project pack list.
7. A2 does three more checks after the peer shadows. It reports each failure at once, and each
   failure text names the command to run next:
   - `gh api user` must pass when `adapter` in the config is `github-issues`. `gh auth status`
     is not the probe: with a token in `GH_TOKEN` it can report the token as invalid while `gh api`
     works (PR #188 MF-2). With
     another adapter, such as `none`, A2 writes a note and does not run `gh`. No `gh` on `PATH`
     is a failure on the `github-issues` adapter.
   - HEAD must be on a branch that is not the default branch. A2 reads the default branch from
     `git symbolic-ref refs/remotes/origin/HEAD`. With no `origin/HEAD`, `main` and `master` are
     the default branches. A detached HEAD fails.
   - `git status --porcelain` must show no path outside `.context/` and `.cursor/plans/`. The
     failure lists each path with its status, untracked files too, up to five.

## Consequences

- A plan review peer that does not write the sha256 line still gets the mtime check. A plan that
  changed after its review needs one more `/plan-review` before P9.
- A pack gate that printed a bare list, or nothing, now fails. The two packs with a gate in this
  repo print `{"findings": [...]}`.
- A2 now fails on the default branch and on a dirty tree. To clear a hard stop, SKILL.md says to
  run `preflight.py` until it passes. During execution, that means a commit or a stash first.
- The six stage packs keep `"phases": []` and no gate script, so P9, E3 and R3 run no gate for
  them.
- Tests: `tests/gates/test_plan_changed_after_review.py` (#176),
  `tests/gates/test_pack_gates_judge_code.py` (#177) and `tests/gates/test_preflight.py` (#181).
