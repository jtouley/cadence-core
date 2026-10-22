RUN_BY_COMMAND: plan-review
Ticket: LOCAL-3
Last reviewed plan sha256: ab9db922c41bf6398e8e5abba994852607609c14eddb05151e4922eceb335ed3

# Plan review: LOCAL-3 recurring expenses (cycle 5)

Plan: `.cursor/plans/feat-local-3-recurring-expenses_local-3.plan.md`
Previous review: sha256 96fff9e5ca271e6744bd960704e38e7e61234a9d33ac1e3a35748df8f6562c89 (cycle 4, READY TO EXECUTE)
Verdict: READY TO EXECUTE

## Status of earlier items

| Id | Status | Evidence |
|---|---|---|
| (none) | n/a | The previous review had `## Must-fix items` = `None`. No `MF-n` was open. |

## Must-fix items

None.

## Scope of this cycle

The plan changed after cycle 4 by one section: the "Plan update 2026-10-21" that answers the P6 round 2 report (`APPROVE WITH CONDITIONS`, no blocking items). It adds no phase and no decision. It carries three conditions under `## Carried adversarial findings`:

- C-1 `format_cents` with negative cents: parametrized rows `-150`, `0`, `900`, `505` in `tests/test_parsing.py`, plus the Phase 3 byte-identical `totals` test. The behavior matches today's `cents / 100` output. Sound.
- C-2 precedence of the update sections over the superseded base lines. It restates the cycle 4 precedence rule and names the tests that fail if E1 follows the old text (acceptance 4 exact stdout, `recurring_lines` unit test). Sound.
- C-3 read order in `totals --month` (validate month, load sidecar, then main ledger), with two tests (corrupt sidecar with valid ledger; both corrupt). It is consistent with D1 (no sidecar read without `--month`), with the MF-1 decision (main-ledger errors stay uncaught when the sidecar is valid) and with acceptance 8.

Repo check: `tests/` holds only `test_ledger.py`. `src/budgetcli/` has only `cli.py` and `ledger.py`. There is no `format_cents`, `parse_id`, `InputError`, `parsing.py` or `recurring.py`, so the plan writes no helper that already exists. The `## Reuse and extension` section exists. Phase order, per-phase full-suite and commit checkpoints, rollback and blast radius are unchanged and sound. No host path leaks. The one real gate (`python3 -m pytest`) is named; no linter is invented.

## Notes (not blocking)

- Older base lines (D3 summing, `merge_totals`, `except ValueError`) remain in the plan body. The precedence rule in the update sections settles them; a later `/plan-update` may fold them in.
- The empty Revision ledger table is correct, since no `MF-n` was open.
