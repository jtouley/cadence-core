---
description: Cadence R4: review the fix commits since the last reviewed commit.
argument-hint: '<PR-number>'
---

# PR review update (Cadence R4: re-review of the fixed head)

Load `$CADENCE_HOME/references/peer-contract.md` and
`$CADENCE_HOME/references/final-consolidate.md`.

## Trigger

`/pr-review-update <PR>` after fix commits on the PR branch.

## Steps

1. Read `.context/prs/reviews/PR_REVIEW_#{N}.md`, where `{N}` is the PR number and the `#` is part of the file name (`PR_REVIEW_#42.md`). Find its last `Last reviewed commit:` line.
2. List the review threads of the PR on GitHub:

```bash
python3 "$CADENCE_HOME/scripts/pr_threads.py" <PR>
```

   The script prints JSON. With `"status": "supported"`, start the update section with a
   `### Review threads` table. Write one row for each thread: its `id`, `author`, `path`, `state`
   (`open`, `outdated` or `resolved`) and status (`answered by <sha>` or `open`).
   An unresolved thread from a human is a must-fix (`"must_fix": true`). Quote the `first_line`
   of the thread in the finding. A thread from a bot is a nit. With `"status": "unsupported"`
   (exit 2: `adapter: none`, no `gh`, or offline), start the update section with one line:
   `Review threads: unsupported (<reason>).` Do not skip this step in silence.
   When the JSON has `truncated: true`, the PR has more than 100 threads: write the first 100 and add
   the line `Review threads: truncated at 100`.
3. Review every commit from that commit to HEAD (`git log <last>..HEAD`, `git diff <last>..HEAD`).
   Check that each must-fix finding is fixed and that the fixes add no new defect. Reproduce each claim.
4. Append to `.context/prs/reviews/PR_REVIEW_#{N}.md` a section `## Update <date>` with:
   - the `### Review threads` table, or the `Review threads: unsupported` line,
   - the status of each earlier finding (fixed, open, or deferred with a ticket),
   - the new findings, each as must-fix or nit. Put the new must-fix items under a `### Must-fix items` heading,
     with the next free `MF-n` ids,
   - the reuse and extension result of the fix commits, with the items of `## Reuse and extension` in `$CADENCE_HOME/references/review-rigor.md`,
   - the verdict,
   - a last line `Last reviewed commit: <full HEAD sha>`.
5. Append one must-fix ledger row for each new `MF-n` with `findings_io.py append`, before you return. The row
   id is the item id (`MF-4`). `findings_io.py` makes it the row id of the run. `commands/pr-review-local.md`
   shows the row.
   The R1 gate fails while an `MF-n` of the review has no ledger row.
6. Decide whether the loop ends. A verdict of `MERGE-READY` with no open must-fix ends the review
   loop. Write each open nit as a tracker issue with the PR number, or under `## Follow-ups` in the
   update, and do not ask for a fix commit. A fix commit after a merge-ready verdict starts a new
   round, because the gate needs a review of HEAD. A new must-fix opens the loop again: the
   orchestrator fixes it, pushes, and runs `/pr-review-update` once more.
7. Run the consolidate gate:

```bash
python3 "$CADENCE_HOME/scripts/final_consolidate.py" --check
```

The gate fails while a must-fix is open, or while the review's last `Last reviewed commit:` line is not HEAD.
Each `## Update` section is one fix round. The loop goes on until the gate passes: a must-fix is fixed in this PR.
Defer a must-fix only when a code change in this PR cannot clear it: ripeness `blocked-by`, `prod-mutation` or `fleet-ops`, as `references/deferral-framework.md` says.
Then add its row under `## Review deferrals` in `.context/deferrals/<task_id>_deferral-register.md`, with the finding id first in the Item cell.
Do not set the finding to deferred. The orchestrator runs P8 and sends `/deferral-review <manifest>`. That peer rules on the row and sets a `GREEN` finding. A defect of the change is never deferred.
If the check reports `no join row`, that is information only: the join row comes after you return. Do not change the review file for it.
The ledger stays the SSOT for findings. Do not invent a second consolidate format.

On Claude Code, save the review with the Write or Edit tool, not a shell redirect or a temp-file rename. The gate accepts only a review whose content the hook attested (references/peer-contract.md).

Chat: C.O.R.E. with the open must-fix count. When a Cadence run delegates this peer, replace the C.O.R.E. close with the one-line return in `peer-contract.md`: `ARTIFACT: <path> VERDICT: MERGE-READY (0 must-fix, N nits to the tracker)` ends the loop. `VERDICT: NO MERGE (M must-fix)` asks for a fix commit.
