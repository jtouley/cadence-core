RUN_BY_COMMAND: plan-create
Pipeline-Run: 1214127c-68d0-42aa-8597-e6a8316737ba
Ticket: feat-local-4-csv-export

**Tracker:** none
**Ticket:** TICKET_SKIPPED (none: no tracker declared)

# Deferral register: feat-local-4-csv-export

## Approved deferrals

| # | Item | Why deferred | Request trace | Ripeness | Evidence | Follow-up ticket |
|---|---|---|---|---|---|---|
| 1 | Upload the export to the bank portal (request LOCAL-9) | The upload needs the portal API credentials, which are not issued. A code change in this PR cannot supply them. | LOCAL-9 (the request names it by id) | blocked-by | LOCAL-8 (portal API credentials, not issued) | LOCAL-1 in `.context/issues/feat-local-4-csv-export.md` |

## Deferrals rejected

None.

## Assumptions

| Gap | Assumption | Settled by |
|---|---|---|
| G-1 | "Wrong month" means any `--month` value that `re.fullmatch` on `[0-9]{4}-(0[1-9]|1[0-2])` does not match (no `$` anchor). The accepted form is exact `YYYY-MM` with month 01 to 12. A day, a bare year and a month name are wrong. `export` does not accept a day. `totals` is unchanged. | Phase 1 tests for `2026-13`, `2026-1`, `2026-00`, `2026`, `2026-10-01` and `October` (D-1) |
| G-2 | With zero matching expenses, `export` prints the header only and returns 0. | Phase 5 test |
| G-3 | Expenses on the same day keep their ledger insertion order. The sort by day is stable. | Phase 2 test |
| G-4 | `export` reads the ledger named by `--file`, which defaults to `ledger.json`. | Phase 2 test |
| G-5 | The exit criteria are the pytest suite and the acceptance tests for S1 to S9 in `tests/test_export.py`. | Phase 6 full run |
