---
description: Cadence P8 and review: rule GREEN, AMBER or RED on each deferral, from the run's manifest.
argument-hint: '<deferral-manifest-path>'
---

# Deferral review (Cadence P8 and R1 to R5)

Load `$CADENCE_HOME/references/peer-contract.md` and `$CADENCE_HOME/references/deferral-framework.md`.

## Purpose

A deferral moves work out of this PR. Give each deferral of the register one RAG ruling against
the criteria of `references/deferral-framework.md`. The default is to build the work in this PR.
The ruling goes back to the plan before E1 starts.

## Input

One file: the manifest `.context/manifests/<task_id>_deferrals.md`. P8 writes it. It names the
register, its sha256 and the rows to rule on, and it lists each document of the run with its
sha256: the request, the vibe-test report, the plan, the reviews, the adversarial reports, the plan
benchmark and the tracker file. In review it also lists the PR review and the findings ledger.

Read only the documents that the manifest lists. Do not read the code. Do not change a document
of the manifest.

## Rule on each row

Give each row that the `Rows:` line names one ruling:

| Ruling | When | What happens |
|---|---|---|
| `GREEN` | The row meets each criterion, and this PR needs nothing for the follow-up | The item is deferred with its ticket |
| `AMBER` | The row meets each criterion. This PR must build one thing for the follow-up: a seam, an interface or a test | The item is deferred. The plan builds the condition, and P9 fails until the plan carries it |
| `RED` | One criterion fails, or you are not sure | The item is built in this PR |

The row is `RED` when one of these is true:

1. The item is part of the request: a line of the request, an acceptance criterion, a `G-n`
   assumption, or the goal of the plan asks for it to be built in this change. Work that the user
   asked for is not deferred. A line that names the item only to put it out of this change ("it waits
   on LOCAL-8", "a later ticket does it") does not ask for it: that is not a reason for `RED`.
2. The ripeness is not true. Check the `Evidence` cell against the documents:
   - `blocked-by`: the named ticket or PR is open, and the item cannot land before it. With adapter
     `none`, the request's own words that name the blocker are the evidence.
   - `prod-mutation` or `fleet-ops`: the item needs an operator action that this PR cannot do.
   - `different-epic` or `different-layer`: the item belongs to the named epic, service or layer,
     and this change does not touch it.
3. A code change in this PR can do the item. Size, effort and time are not ripeness.
4. The row is a review row (`review <#>`), and the finding is a defect of the change: the code does
   not work, a test fails, or the change breaks a check of the repository.

A review row is `GREEN` or `RED`, never `AMBER`.

## Output

Write the path that the last line of the manifest names, `.context/deferrals/<task_id>_deferral-review.md`,
with the Write tool:

```markdown
# Deferral review: <task_id>

RUN_BY_COMMAND: deferral-review
Register sha256: <the Register sha256 line of the manifest>

| Row | Ruling | Reason | Condition |
|---|---|---|---|
| approved 1 | GREEN | <the document and the line that make each criterion true> | none |
| approved 2 | AMBER | <the reason> | <the one thing that this PR builds> |
| review 1 | RED | <the criterion that the row fails> | none |
```

Write one table row for each name on the `Rows:` line of the manifest.

For each `GREEN` review row, set its finding to deferred after you write the ruling:

```bash
python3 "$CADENCE_HOME/scripts/findings_io.py" set-status --id <finding id> --status deferred --ticket <ticket>
```

## Return

End with one line: `ARTIFACT: .context/deferrals/<task_id>_deferral-review.md VERDICT: <n> GREEN, <n> AMBER, <n> RED`.
The orchestrator then runs P8 again. A `RED` or an `AMBER` row goes to `/plan-update` before E1.
In review, a `RED` row stays a must-fix of the fix round.
