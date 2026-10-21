---
decision_id: ADR-0018
status: accepted
question: Who writes the markdown of a block that has no peer, so that the orchestrator writes none?
choice: A block whose file needs no judgment writes the file in its own script. A1 saves the user's request as written, P8 writes the zero deferral register, R6 writes the closure comment from the ledger, and R7 writes the run's manifest. A file that needs judgment is the work of a peer with a skill and a markdown input. The plan peer writes the decision records and the approved deferrals, and the `/session-summary` peer writes the summary from the manifest. On Claude Code the scope check denies an orchestrator write to the markdown of the run.
rationale: After ADR-0017, the orchestrator of an end-to-end run still wrote four files itself, because a gate checked each one and no block wrote it. It wrote the P5 decision record, the P8 register, the R6 comment and the session summary, and it rewrote the user's request into a spec snapshot for A5. To get each format right it read skill references and script sources, and that reading made about half of its tool output. Its context reached 109,000 to 119,000 tokens against a start of 44,000. The orchestrator routes. A step that needs judgment goes to a subagent with a skill and the markdown it needs, and a step that needs none belongs to a script.
rejected:
  - alt: Keep the formats in SKILL.md so the orchestrator writes each file without reading references
    why_not: The orchestrator still writes the file, and a rule it must remember is not a gate. Each format grows SKILL.md, which every request carries
  - alt: A peer for each file, also the mechanical ones
    why_not: The register with no deferral, the closure comment and the summary follow from the ledger and the run record. A model call can only add drift, and the R6 table then needs its own check against the ledger
  - alt: A script that writes the decision record
    why_not: A decision record says what the plan chose and why. That is the planner's judgment, so the plan peer writes it with the plan
  - alt: A script that writes the session summary from the run record
    why_not: A summary says what the run decided and why, and what a person does next. That takes reading the documents, which is judgment. The script writes the manifest of the documents, and a peer reads them
  - alt: Let R6 post the comment itself
    why_not: The shell hook holds `gh pr comment` until the R5 breadcrumb names HEAD. The script prints the command, and the post goes through that hook
links:
  - rel: implements
    target_type: file
    target_id: scripts/activate_session.py
  - rel: implements
    target_type: file
    target_id: scripts/deferral_adjudication_check.py
  - rel: implements
    target_type: file
    target_id: scripts/guard_gh_pr_comment.py
  - rel: implements
    target_type: file
    target_id: shared/scripts/orchestrator_scope.py
  - rel: implements
    target_type: file
    target_id: commands/plan-create.md
  - rel: implements
    target_type: file
    target_id: commands/plan-update.md
  - rel: implements
    target_type: file
    target_id: commands/session-summary.md
  - rel: relates
    target_type: file
    target_id: docs/adr/0017-the-orchestrator-routes.md
---
# ADR-0018: A block writes its own markdown

## Context

ADR-0017 made the orchestrator route: it runs each gate, sends each peer a markdown file, and reads
a one-line return. An end-to-end run with every pack on then showed what was left. Five files had
a gate that checked them and no block that wrote them, so the orchestrator wrote each one:

| Block | File | What the orchestrator did |
|---|---|---|
| A5 | `.context/proposals/<task_id>.md` | Rewrote the user's request as a spec snapshot |
| P5 | a decision record in `.context/proposals/` | Wrote a decision stub to pass decision coverage |
| P8 | `.context/deferrals/<task>_deferral-register.md` | Wrote the register with no deferral |
| R6 | `.context/prs/comments/pr-comment-<run id>.md` | Wrote the closure comment, and edited it by hand on a reopen |
| R7 | `.context/summaries/<task_id>.md` | Wrote the session summary |

To write them it read `deferral-framework.md`, `pr-review-github-comment.md`,
`final-consolidate.md`, `architecture-core-rules.md` and `ledger_verify.py`.

## Decision

1. `activate_session.py --request <file>` or `--request -` saves the user's request as written. A
   fresh run writes `.context/proposals/<task_id>.md` with a `Ticket:` line. A resume keeps the
   file. An empty request fails activation.
2. P8 without `--check` writes the zero register when no register of the ticket exists. It never
   changes a register that exists.
3. R6 without `--check`, with no open must-fix, writes the closure comment from the ledger: the
   summary, the `## Fixes` and `## Deferrals` tables, and the open findings for the tracker. It
   checks the body and prints the `gh pr comment` command. `--check` writes nothing.
4. R7 `--deactivate` writes the manifest `.context/manifests/<task_id>.md`: the run facts, each
   document of the run with its sha256, and the commits since the base. It closes the run only
   after the `/session-summary <manifest>` peer writes `.context/summaries/<task_id>.md`. On
   Claude Code the hook attests that write, and the orchestrator cannot write it.
5. `/plan-create` writes a decision record with frontmatter for each design decision of the plan.
   `/plan-update` writes a decision record or an approved deferral row when a review asks for one.
6. On Claude Code, the scope check of ADR-0017 also denies an orchestrator write to the run's
   markdown: the run artifacts, `.context/deferrals/`, `.context/prs/comments/` and
   `.context/summaries/`. A tracker post in `.context/issues/` is not run markdown.

## Consequences

- The orchestrator writes no file of the run. It passes the user's request to activation as
  written, and then it only runs gates, sends commands and reads returns.
- A hand-written R6 body is replaced by the rendered one. `--check` still checks any body, and
  `--body-file` names another file.
- The runtime size budget (`tests/contract/test_source_size_budget.py`) rises from 17,550 to
  17,800 lines for the four writers, the manifest, and the A5 gap checks of ADR-0019. The tree
  had 17,779 lines after these changes. This is the one record.
