# Cadence deferral framework

**When to load:** P8 (`deferral-register`) after adversarial review.

Tracker-agnostic.

## Default

Ship in-PR (car-apart). Defer only with:

1. Follow-up ticket matching the configured adapter pattern. With adapter `none`, the follow-up ticket is a `LOCAL-<n>` section in `.context/issues/<task_id>.md`. Use the numbering rule in `references/activation.md`, section "No tracker".
2. Ripeness in `{blocked-by, prod-mutation, different-epic, different-layer, fleet-ops}`

Convenience, nits, YAGNI, and "out of AC" without ripeness are **rejected**.

## Register

Write `.context/deferrals/<task>_deferral-register.md` with:

- `## Approved deferrals` table (or explicit zero-approved sentence)
- `## Deferrals rejected` for items pushed back in-PR
- Optional `## Review deferrals` for review-phase adds

Validate:

```bash
python3 "$CADENCE_HOME/scripts/deferral_adjudication_check.py" <path> --json
```

## Deferrals in review (R1 to R5)

After E4 writes the draft-PR record of the run, `findings_io.py` refuses a `deferred` row
on the run ledger, from `append` and from `set-status`, unless both are true:

1. The ticket matches the adapter pattern in full.
2. A register of the run passes adjudication and has a row that names the ticket.

Add the row under `## Review deferrals` first, with its ripeness. Then run
`set-status --status deferred --ticket <ticket>`.

Zero approved deferrals still requires a minimal register artifact for P8.
With no `<path>`, the script finds this ticket's register itself (the P8 gate).
P9 reads the same file: it must name the ticket and pass adjudication.
