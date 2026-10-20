# Cadence execution protocol

**When to load:** before E1 (`implement`) and when bridging E1 → E4.

## Entry (fail-closed)

Before any edit under gated prefixes:

```bash
python3 "$CADENCE_HOME/scripts/entry_invariant.py" --phase execution
python3 "$CADENCE_HOME/shared/skill_gate.py" check --skill cadence \
  --action strreplace --file "<target>"
```

Non-zero exit → not cleared for E1. Recovery: fresh A1 activation, or stop and
ask the user if artifacts were lost. **Forbidden:** backfilling plan/review
artifacts after code edits began.

## Run gates from the worktree

E1 and R4 judge only the run of the worktree they run in. They do not read another
worktree's run through the root redirect. Run the gates from the run's worktree, or
pass `--workspace <worktree>` to `scripts/headless.py`. When a gate finds the active
run in another worktree, its message names that worktree.

## E3 and the PR guard

P5 and E3 use the canonical `.context` (the `.context` of the primary checkout), as activation does. In a linked worktree they read the run's plan there, and they append to the ledger that R4 and R5 read.

- When E3 finds no must-fix item, it writes `gates/architecture-review-impl-<run_id>.json` with the run id and HEAD. A run with a must-fix item deletes the file of its own run only.
- An E3 or P5 pass in record mode closes the earlier must-fix rows of that stage and task. Each closed row gets `status: verified` through `ledger_verify` and a `superseded by` note.
- A must-fix row of another task stays open. Close it with `findings_io set-status`, or with a pass of the stage of that task. R4, R5 and R6 judge only the rows of the run's task. With no run for the cwd, they judge every row (#49).

The shell hook guards `gh pr create` and `gh pr ready` when the branch has commits ahead of its base. It applies only to a repository with a Cadence footprint: `.cadence.json`, a run record, or `.context/attest/`. It checks each `gh pr` command in a multi-line or chained shell command. It allows the command in one of these conditions:

1. The run of this worktree has the E3 file of its run, written after activation. For `gh pr ready`, the file must also name the current HEAD. When the architecture pack is off, E3 does not run. Then the run must be past E1: the P9 marker of the run and a commit for each plan phase. This condition opens `gh pr create` only. `gh pr ready` needs condition 2.
2. A PR review names HEAD in its `Last reviewed commit:` line. On Claude Code, a review peer that is not an orchestrator must write it.
3. The command starts with `CADENCE_ALLOW_PR=1`. The hook logs the command in `.context/issues/pr-guard-overrides.log`.

The guard reads only the current branch. It does not see `gh pr ready <n>` from another branch or `gh pr create --head <other>`.

## E1 → E4 same-turn rule

When E1 tests pass: write the E2 impl artifact, then continue E3 → E4 in the
same turn unless a hard stop applies (see SKILL.md Self-healing). Do not end
the turn asking permission to open the draft PR — `/cadence` already authorized
pipeline shipping.

Do not answer a finished or aborted background shell with status only.
Resume the open execution slice in that turn.

## Shortcuts (none for gates)

Conditional triggers (`on_review_changes`) may no-op when
not applicable — still record the gate outcome. Never skip registry order.

## Simplicity (E1 cycle)

Before each TDD cycle: what is the smallest change that satisfies this
cycle’s test? If the answer is a new helper that nothing else reuses,
inline it. Two clear lines beat a named abstraction with one caller.
Complexity creep is why PRs grow past the plan.

## Blast radius (E1 cycle)

Before changing a contract, list every call site. Migrate all of them
now, or list deferred paths with ticket ids in the plan. Do not ship a
single-call-site patch when the same failure exists elsewhere. For
Cadence gates: one runner, every marker-shaped stage — not a new file
per slug.

## Standalone (always)

Load `$CADENCE_HOME` only. Do not open `~/.cursor/commands`,
a host skill tree, or host `.mdc` rules. If a peer slash command
is missing, use the bundled reference and record the gate.
