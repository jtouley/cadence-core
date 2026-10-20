# Cadence PR review GitHub comment

**When to load:** before R6 (`pr-comment`).

## Preconditions

1. R5 `closure.py` exit 0 (all must-fix terminal: verified | deferred | spec_gap).
   Run `closure.py` without `--check` in the run's worktree. A pass writes
   `.context/gates/closure-<run id>.json` with HEAD.
2. Active cadence session for this worktree.
3. Pi/Cursor: run the gate check before `gh pr comment` or `gh pr review`:

```bash
python3 "$CADENCE_HOME/shared/skill_gate.py" check --skill cadence \
  --action gh_pr_comment --file "pr"
```

The shell hook denies `gh pr comment` and `gh pr review` until the R5 breadcrumb of the
run names HEAD. A commit after R5 needs R5 again. A review of HEAD or an E3 pass does not
open the comment. The hook lets a command that starts with `CADENCE_ALLOW_PR=1` through, and
it writes the command to `.context/issues/pr-guard-overrides.log`.

## Comment body

Write the body to `.context/prs/comments/pr-comment-<run id>.md`. Put a closure summary
first: the counts of open and closed findings, the ledger path, and the next action for a
person (ready for review, or blocked).

The body must hold these two tables:

```markdown
## Fixes

| Finding | Commit |
|---|---|
| gh-1-MF-1 | 3f2a9c1 |

## Deferrals

| Finding | Ticket |
|---|---|
| gh-1-F4 | #201 |
```

- `## Fixes` has one row for each `verified` must-fix with `fixed_in`. The commit cell is
  `fixed_in`, or its first 7 or more characters.
- `## Deferrals` has one row for each `deferred` or `spec_gap` must-fix. The ticket cell is
  the ticket of the row.
- Keep a table with no rows. Write the heading and the header row.

`scripts/guard_gh_pr_comment.py` checks each table against the ledger. It fails when a row
is missing, when a cell is not the ledger value, when a row names a finding that the ledger
does not hold in that status, and when the body or a table is missing. Use `--body-file`
for a body at another path.

Write the body under the writing rules of `references/peer-contract.md` before you post.
Then post the same file: `gh pr comment <N> --body-file <body-file>`.

## Forbidden

Commenting before R5 closure, or posting without ledger verification.
