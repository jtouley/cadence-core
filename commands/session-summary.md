---
description: Cadence R7: write the session summary of a run from its manifest.
argument-hint: '<manifest-path>'
---

# Session summary (Cadence R7)

Load `$CADENCE_HOME/references/peer-contract.md`.

## Input

One file: the manifest `.context/manifests/<task_id>.md`. `activate_session.py --deactivate`
writes it. It lists the run facts and each document of the run with its sha256: the request, the
vibe-test report, the plan, the reviews, the adversarial reports, the benchmarks, the deferral
register, the implementation artifact, the PR review, the PR comment and the findings ledger.

Read each document that the summary needs. Read the code only through these documents and
`git show --stat <commit>`. Do not run a gate, and do not change a file of the run.

## Output

Write the path that the last line of the manifest names, `.context/summaries/<task_id>.md`, with the
Write tool. Include these sections, in this order:

```markdown
# Session summary: <task_id>

RUN_BY_COMMAND: session-summary
Pipeline-Run: <pipeline_run_id>

## Request
## What changed
## Decisions
## Reviews
## Findings
## Deferrals and open items
## Next action
```

- **Request:** what the user asked for, in two to four lines, from the request document.
- **What changed:** each plan phase and its commit, and the user-visible change.
- **Decisions:** each decision record and each spec gap that the plan answered, with its choice.
- **Reviews:** the verdict of each plan review, adversarial review, benchmark and PR review.
- **Findings:** each must-fix finding with how it closed (the fix commit, or the deferral ticket).
- **Deferrals and open items:** each deferral, each open nit, and each hard stop or reopen.
- **Next action:** what a person does next: review the PR, answer a question, or merge.

State facts from the documents. Do not add a claim that no document supports. Name the document
of each fact when a reader needs to check it.

## Return

End with one line: `ARTIFACT: .context/summaries/<task_id>.md`. The orchestrator then runs
`activate_session.py --deactivate` again.
