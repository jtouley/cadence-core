---
decision_id: ADR-0012
status: accepted
question: What evidence closes a review finding, and when can the run post to the PR?
choice: Each R1 must-fix item is a ledger row `<task prefix>-<run tag>-MF-<n>`, where the run tag is the first 8 letters and digits of the run id. The R1 gate fails on an item with no row, and on a NO MERGE verdict with no row. A check closes a finding only when it runs a test runner or a script path and is not a shell no-op. A fixed row names its fix commit in `fixed_in`, a commit on the branch. ledger_verify.py runs the check at HEAD and writes `verified_at_sha`. Only `findings_io.py set-status --human --by <name>` stamps a manual check. closure.py lists each stamped row. The shell hook denies `gh pr comment` and `gh pr review` until the R5 breadcrumb of the run names HEAD. The R6 comment holds a Fixes table and a Deferrals table. The R6 gate checks each row against the ledger. In review, a deferral needs a ticket of the adapter pattern and a row of an adjudicated deferral register.
rationale: A comparison with the predecessor harness found three ways to close a run with no evidence. An R1 review that ended `NO MERGE (3 must-fix)` did not block closure. The R1 gate read only the reviewed commit, and R4 to R6 read only the ledger. A row with `check` `true` became `verified`, and the model could write `human_confirmed` itself. `gh pr comment` was open while a session was active, and a must-fix could be deferred to the ticket `whatever`.
rejected:
  - alt: Let the R1 gate parse the must-fix items of the review and let R5 read the review too
    why_not: R4, R5 and R6 read the ledger alone. A second store of open findings can disagree with the ledger. R2 already puts each must-fix in the ledger, so R1 does the same
  - alt: Use the plain review id `MF-n` as the ledger id
    why_not: A ledger id names its task, so the gates can scope rows to the run. `<task prefix>-MF-<n>` keeps the review id and the task prefix, and it cannot collide with a P5, E3 or R2 row `<task prefix>-F<n>`
  - alt: Write `MF-n` in a second field of a `<task prefix>-F<n>` row
    why_not: The review id and the row id then differ, and the reader must join two fields. The id rule is the one join
  - alt: Run each check in a sandbox and judge whether it tests the fix
    why_not: The gate cannot read the intent of a test. The text rule stops the no-op forms that the comparison found. The reviewer still names the test that proves the fix
  - alt: Block `set-status --human` when the orchestrator runs it
    why_not: The hook cannot tell the hand of the user from the orchestrator on the same host. Both run the same command in the same shell. A block that the agent can pass with a different spelling is a false promise, so the manual path is visible, not blocked
  - alt: Apply the check and fixed_in rules in load_findings
    why_not: An older ledger with a `check` of `true` then stops every gate that reads it, and its rows cannot be deferred. findings_io.py applies the rules when it writes a row, and ledger_verify.py applies them again, so an old row cannot become `verified`
  - alt: Open `gh pr comment` with the escapes of `gh pr create` (an E3 pass or a review of HEAD)
    why_not: The comment reports the closure. Before R5 the comment can only report a state that is not final
links:
  - rel: implements
    target_type: file
    target_id: scripts/gate_common.py
  - rel: implements
    target_type: file
    target_id: scripts/findings_io.py
  - rel: implements
    target_type: file
    target_id: scripts/ledger_verify.py
  - rel: implements
    target_type: file
    target_id: scripts/closure.py
  - rel: implements
    target_type: file
    target_id: scripts/guard_gh_pr_comment.py
  - rel: implements
    target_type: file
    target_id: hooks/pr_guard.py
  - rel: implements
    target_type: file
    target_id: shared/skill_gate.py
  - rel: implements
    target_type: file
    target_id: scripts/deferral_adjudication_check.py
  - rel: implements
    target_type: file
    target_id: commands/pr-review-local.md
  - rel: implements
    target_type: file
    target_id: commands/pr-review-update.md
  - rel: implements
    target_type: file
    target_id: references/pr-review-github-comment.md
  - rel: relates
    target_type: file
    target_id: references/final-consolidate.md
  - rel: relates
    target_type: file
    target_id: references/deferral-framework.md
---
# ADR-0012: A finding closes on evidence

## Context

Issues #174, #175 and #178 come from a comparison of Cadence with its predecessor harness.
Each issue is a way to close a run with no evidence.

1. An R1 review that ended `NO MERGE (3 must-fix)` did not block closure. The two pr-review
   commands did not tell the peer to write ledger rows. The R1 gate
   (`gate_common._VERDICT_CHECKS["pr-review-local-gate"]`) read only the reviewed commit. R4,
   R5 and R6 read only `findings.jsonl`. R2 already required a ledger row for each must-fix of
   its scorecard (`skills/test-benchmark/SKILL.md`, `_must_fix_ledger_problems`).
2. `ledger_verify.py` ran the `check` of a row and wrote `verified` on exit 0. A check of
   `true`, `:` or `echo ok` passed. A `manual` check passed when `human_confirmed` was true,
   and the model wrote that field in the row it appended.
3. `skill_gate.py --action gh_pr_comment` allowed the comment while a session was active.
   The shell hook did not match `gh pr comment` or `gh pr review`. The comment had no fixed
   form. During review, a must-fix could be deferred to the ticket `whatever`, with no adapter
   pattern and no register row.

## Decision

### R1 must-fix items are ledger rows (#174)

1. The R1 peer puts each must-fix item under `## Must-fix items` with an id `MF-n`. Before it
   returns, it appends one must-fix row with the id `<task prefix>-<run tag>-MF-<n>` through
   `findings_io.py append`. `/pr-review-update` does the same for each new item.
2. The ledger id pattern takes `<task prefix>-<run tag>-MF-<n>` next to `<task prefix>-F<n>`. The
   run tag is the first 8 letters and digits of `pipeline_run_id` (`findings_io.r1_row_id`). The
   peer writes the bare `MF-n`, and `findings_io.py append` builds the row id from the run of
   the worktree, so the peer reads no session file. A
   second run of the task writes its own rows and never finds the rows of the first. The task
   scope (`task_row_pattern`) holds both forms, so R4, R5 and R6 judge the R1 rows.
3. The R1 gate reads the `MF-n` ids of the review it judges with `review_must_fix_ids`, the
   reader that P4 and P9 use. It fails for each id with no must-fix row in any status. It also
   fails when the last `Verdict:` line is `NO MERGE` and the ledger has no `MF` row of this run. The R1 and R2 checks read the ledger with one helper, `findings_io.ledger_must_fix_ids`.
   An unreadable ledger fails R1.

### A real check, a named commit and a visible human stamp (#175)

1. `findings_io.py` refuses a `check` that cannot prove a fix:
   - an empty command, or a command that ends with `--`,
   - a shell no-op in any segment: `true`, `:`, `echo`, `printf` or `exit`. This includes
     `pytest || true` and `pytest; echo done`,
   - a command that names no test runner and no script path: `pytest`, `python`, `make`, `tox`,
     `nox`, `uv run`, `npm`, `pnpm`, `yarn` or `bun` with `test` or `run`, `npx`, `cargo`, `go test`
     or `go vet`, `ruff`, `mypy`, `pyright`, `tsc`, `eslint`, `jest`, `vitest`, a `./` path or a
     `scripts/` path.
   `manual` stays the human path.
2. A `fixed` row carries `fixed_in`. `set-status --status fixed --fixed-in <sha>` resolves the
   sha and refuses it unless it is HEAD or an ancestor of HEAD.
3. `ledger_verify.py` applies the check rule again, and requires `fixed_in` on the branch.
   It runs the check at HEAD and writes `verified_at_sha`.
4. Only `set-status --human --by <name>` writes `human_confirmed`, `confirmed_by` and
   `confirmed_at`, and only on a row with a `manual` check. `append` refuses a row with these
   fields. `ledger_verify.py` refuses a manual row with no full stamp. `closure.py` prints a
   `MANUAL` line and a `manually_confirmed` list for each stamped row.

### The PR comment waits for R5 and maps each closure (#178)

1. A pass of `closure.py` without `--check` writes `gates/closure-<run id>.json` with the run
   id and HEAD. The edit hooks protect `gates/`.
2. `hooks/pr_guard.py` also matches `gh pr comment` and `gh pr review`. It denies them until
   the breadcrumb of the worktree's run names HEAD. An E3 pass or a review of HEAD does not
   open them. `CADENCE_ALLOW_PR=1` lets one segment through and writes the override log, as for
   `gh pr create`. With no run and no commits ahead of the base, the hook allows the comment: it
   is not on the branch of a run.
3. `skill_gate.py check --action gh_pr_comment` (and `gh_pr_review`) applies the same rule for
   Pi and Cursor.
4. The comment body (`.context/prs/comments/pr-comment-<run id>.md`, or `--body-file`) holds a
   `## Fixes` table (finding, commit) and a `## Deferrals` table (finding, ticket).
   `guard_gh_pr_comment.py` requires a Fixes row for each `verified` must-fix with `fixed_in`,
   with that sha or a prefix of 7 or more characters. It requires a Deferrals row for each
   `deferred` or `spec_gap` must-fix, with its ticket. A row that the ledger does not hold, a
   missing table and a missing body fail.
5. After E4 writes the draft-PR record of the run, `findings_io.py` refuses a `deferred` row on
   the run ledger unless its ticket matches the adapter pattern in full and a deferral register
   of the run that passes adjudication has a row that names the ticket. Before E4, P8 owns the
   register.

## Consequences

- A NO MERGE review cannot pass R1 with an empty ledger, and R5 holds on each open R1 row.
- An R1 peer that forgets the rows fails R1 with the id of each missing row.
- The check rule reads the command text. `python3 -c pass` passes it. The reviewer still names
  the test that proves the fix. The rule stops only the no-op forms that the comparison found.
- A hook cannot tell the hand of the user from the orchestrator on the same host. The agent can
  run `set-status --human --by <name>` itself. The manual path is visible, not blocked: closure
  prints each stamped row, and the person reads the R5 output.
- A row that a ledger holds from before this change still loads. A `fixed` row with no
  `fixed_in`, or with a no-op check, cannot become `verified`.
- A row that a later pass of its own gate closes (`supersede_finding`) has no `fixed_in`. The
  Fixes table does not require it.
- A commit after R5 needs R5 again before the comment.
- R2 ids `<task prefix>-F<n>` carry no run tag. A second run of the task shares them.
- The hold text of R4 and the recovery rows name the register step of §5 before `set-status
  --status deferred` (ADR-0015 review).
