---
description: Cadence E4: commit, push and open or update the draft PR.
argument-hint: '[--draft] [--message "..."] [--include-untracked]'
---

# Commit push PR (Cadence E4)

Load `$CADENCE_HOME/references/peer-contract.md`.

## Trigger

`/commit-push-pr [--draft] [--message "..."] [--include-untracked]`

## Ticket

Read the `adapter` key of the resolved config (`python3 "$CADENCE_HOME/scripts/cadence_config.py" resolve`) and `adapters/<adapter>.json`. If the
branch matches the adapter regex, use that id in the PR title. If it does
not, proceed and record `ticket: none` in the artifact — do not block on a
host tracker.

## Sequence

1. `git status`, `git diff`, `git log` for message style.
2. Stage intended files only (no secrets).
3. Commit. Write the commit message under the writing rules of `references/peer-contract.md`.
   If hooks rewrite files, commit again — do not `--no-verify`.
4. `git push -u origin HEAD`.
5. Write the PR title and body to a file, under the same writing rules.
6. `gh pr create --draft` (default for Cadence E4) with SUMMARY + test plan.

## Artifact

Each run has its own record. Get the run id from the run's worktree:
`python3 "$CADENCE_HOME/scripts/activate_session.py" --status` (field `pipeline_run_id`).
After the PR URL exists, write `.context/prs/draft-pr-<pipeline_run_id>.md` with the Write tool.
`scripts/draft_pr_gate.py` then writes `.context/gates/draft-pr-<pipeline_run_id>.json`.
The record starts with:

```
RUN_BY_COMMAND: commit-push-pr
```

Include the PR URL and the branch it was opened from:

```
PR: https://github.com/<owner>/<repo>/pull/<n>
Branch: <current branch>
```

Chat: C.O.R.E. When a Cadence run delegates this peer, replace the C.O.R.E. close with the one-line return in `peer-contract.md`.
