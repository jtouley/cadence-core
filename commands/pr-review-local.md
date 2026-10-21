---
description: Cadence R1: staff review of the PR branch with reproductions.
argument-hint: '<PR-number>'
---

# PR review local (Cadence R1)

Load `$CADENCE_HOME/references/peer-contract.md`,
`$CADENCE_HOME/references/architecture-core-rules.md`, and
`$CADENCE_HOME/references/review-rigor.md`.

## Trigger

`/pr-review-local <N>`

Need `gh` and a git remote. Create `.context/prs/reviews/` and
`.context/prs/diffs/`.

## Isolation

Do not `gh pr checkout` the user's tree. Attach a worktree at the PR head
unless this is already the Cadence working branch for that PR.

## Deliverable

`.context/prs/reviews/PR_REVIEW_#{N}.md` with:

```
RUN_BY_COMMAND: pr-review-local
Last reviewed commit: <full sha of the reviewed head>
```

After fix commits, `/pr-review-update` reviews the new commits and appends a later `Last reviewed commit:` line. `pr_review_local_gate.py` fails while the last `Last reviewed commit:` line of the newest review is not the HEAD of the run's worktree.

Staff review: contracts, blast radius, `core:dry-extension`,
`core:standalone-package`, test quality (no partial-success thresholds),
adversarial scenarios with evidence. Verdict must-fix vs nits.
## Must-fix items

Put every must-fix item under one `## Must-fix items` heading. Give each item an id: `MF-1`, `MF-2`, and so on.
Write `None.` under the heading when the review has no must-fix item.

Append one must-fix ledger row for each item before you return. Write the item id as the row id (`MF-1`).
`findings_io.py append` makes it the row id of the run, `<task prefix>-<run tag>-MF-1`, from the run of the
worktree, and prints the row. A later `set-status` takes that full id, never the bare `MF-1`.
Use `findings_io.py`, as R2 does for its scorecard findings:

```bash
python3 "$CADENCE_HOME/scripts/findings_io.py" append --json '{"id": "MF-1", "stage": "pr-review-local", "rule_ref": "core:x", "class": "bug", "severity": "must-fix", "location": "src/a.py:10", "evidence": "...", "check": "uv run pytest tests/test_a.py -q", "status": "open"}'
```

`pr_review_local_gate.py` fails while an `MF-n` of the review has no ledger row. It also fails when the verdict is
`NO MERGE` and the ledger has no `<task prefix>-<run>-MF-<n>` row of this run. R4 and R5 read only the ledger.

## Verdict

A verdict of `MERGE-READY` with no open must-fix ends the review loop. An open nit then goes to the
tracker with the PR number, or under `## Follow-ups`, not into a fix commit.

The file must carry every `##` section in `references/review-rigor.md`:
Reproductions, End-to-end walk, Adversarial inputs, Contracts, Design choices,
Scope, Lookback, Reuse and extension. `pr_review_local_gate.py` fails without them. Do the checks
yourself. Do not give the author a "please check" task.

In `## Reuse and extension`, cite the R2 and R3 measure records (`references/review-rigor.md`).
Read `verdict`. Do not judge the code again.
Cite the R3 `bugs` (ruff) and `types` (mypy) records in `## Contracts` and `## Lookback`. Read `verdict`. Do not judge their findings again.

Chat: C.O.R.E. Do not rubber-stamp from memory of the implementation turn. When a Cadence run delegates this peer, replace the C.O.R.E. close with the one-line return in `peer-contract.md`.

On Claude Code, save the review with the Write or Edit tool, not a shell redirect or a temp-file rename. The gate accepts only a review whose content the hook attested (references/peer-contract.md).
