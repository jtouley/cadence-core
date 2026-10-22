# Cadence deferral framework

**When to load:** P8 (`deferral-register`) after adversarial review.

Tracker-agnostic.

## Default

Ship in-PR (car-apart). The work that the user asked for is built in this PR. A deferral is the
last option, and each one gets a ruling of the `/deferral-review` peer.

## Criteria

A row defers work only when all of these are true:

1. **The request does not ask for it.** The request does not ask for the item to be built in this
   change. The item serves no line of the request, no acceptance criterion, no `G-n` assumption and
   not the goal of the plan. The `Request trace` cell is `none`. A request that names the item only to
   put it out of this change ("it waits on LOCAL-8", "a later ticket does it") does not ask for it.
   A request that asks for the item in this change and also asks to defer it does ask for it. A row that traces to the request fails P8
   (`deferral-core-of-request`): build the item. The run does not stop to ask the user.
2. **A code change in this PR cannot do it.** Size, effort, time, convenience, nits, YAGNI and
   "out of AC" are not reasons.
3. **The ripeness is one of these, with its evidence in the `Evidence` cell:**

   | Ripeness | Evidence |
   |---|---|
   | `blocked-by` | The open ticket or PR that the item waits on. With adapter `none`, the request's own words that name it |
   | `prod-mutation` | The production change that an operator makes |
   | `fleet-ops` | The fleet operation that an operator runs |
   | `different-epic` | The other epic that owns the item |
   | `different-layer` | The other service, repository or layer that owns the item |

4. **It has a follow-up ticket** that matches the configured adapter pattern. With adapter `none`,
   the follow-up ticket is a `LOCAL-<n>` section in `.context/issues/<task_id>.md`. Use the
   numbering rule in `references/activation.md`, section "No tracker".
5. **The `/deferral-review` peer ruled `GREEN` or `AMBER`.** P8 writes the manifest
   `.context/manifests/<task_id>_deferrals.md`, which lists the run's documents, and the peer rules
   from it. The ruling names the sha256 of the register as it is now, so a change to the register
   needs a new ruling. `GREEN` defers the item. `AMBER` defers it with a condition that this PR
   builds: the plan maps the row under `## Deferral conditions`, and P9 fails until it does. `RED`
   builds the item in this PR. A review row is `GREEN` or `RED`.

In review (R1 to R5), a must-fix finding is fixed in this PR. Only `blocked-by`, `prod-mutation` and
`fleet-ops` defer it, because a code change in this PR cannot clear them. A defect of the change
is never deferred: code that does not work, a failing test, or a broken check of the repository.
A `spec_gap` status follows the same rules as `deferred`.

## Register

The register is `.context/deferrals/<task>_deferral-register.md`. The plan peer (`/plan-create`,
`/plan-update`) writes each deferral row into it, and P8 and `/deferral-review` rule on the rows. When nobody proposed a deferral, P8
(`deferral_adjudication_check.py` without `--check`) writes the zero register itself.
The orchestrator writes no register. The register has:

- `## Approved deferrals` table (or explicit zero-approved sentence) with these columns:
  `| # | Item | Why deferred | Request trace | Ripeness | Evidence | Follow-up ticket |`
- `## Deferrals rejected` for items pushed back in-PR
- Optional `## Review deferrals` for review-phase adds, with the same columns. The Item cell starts
  with the finding id
- `## Assumptions` when A5 found blocking gaps: one row (`| G-n | assumption | settled by |`) for each
  `G-n` of the vibe-test report. P9 checks the rows, and R6 lists them in the PR comment

Check:

```bash
python3 "$CADENCE_HOME/scripts/deferral_adjudication_check.py" <path> --json
```

## Deferrals in review (R1 to R5)

After E4 writes the draft-PR record of the run, `findings_io.py` refuses a `deferred` or
`spec_gap` row on the run ledger, from `append` and from `set-status`, unless all are true:

1. The ticket matches the adapter pattern in full.
2. A register of the run passes adjudication and has a row that names the ticket.
3. The `/deferral-review` ruling of that register is `GREEN` for each row.

The `/pr-review-update` peer adds the row under `## Review deferrals`. The orchestrator then runs
P8, which writes the manifest, and sends `/deferral-review <manifest>`. That peer sets each `GREEN`
finding to `deferred`.

Zero approved deferrals still requires a register for P8. P8 writes that zero register.
With no `<path>`, the script finds this ticket's register itself (the P8 gate).
P9 reads the same file: it must name the ticket and pass adjudication.
