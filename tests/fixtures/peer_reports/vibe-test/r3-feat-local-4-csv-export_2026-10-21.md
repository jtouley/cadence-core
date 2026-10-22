# Vibe test: feat-local-4-csv-export

RUN_BY_SKILL: vibe-test
Pipeline-Run: 04a18835-a28a-4122-b5e2-7a90486056aa

**Tracker:** none
**Ticket:** TICKET_SKIPPED (none: no tracker declared)

The spec names LOCAL-4 as its label. No tracker holds that label.

## Target

Type: snapshot. The target is `.context/proposals/feat-local-4-csv-export.md`. It holds the request as the user wrote it.

The spec adds a CSV export to the budgetcli CLI on branch `feat/LOCAL-4-csv-export`. The upload to the bank portal (LOCAL-9) is out of scope. That upload waits on LOCAL-8, which is not issued.

Reviewed against the current code: `src/budgetcli/cli.py`, `src/budgetcli/ledger.py` and `tests/test_ledger.py`. The export command does not exist yet.

## Scenarios traced

| # | Input | Spec result | Status |
|---|---|---|---|
| S-1 | `export --month 2026-10`. October has 3 expenses. September has 1. | Header, then 3 rows, oldest first. The September row is absent. | Clear |
| S-2 | Expense `12.50` and expense `5` (stored as 500 cents). | `12.50` and `5.00`. | Clear |
| S-3 | Note `lunch, with friend`, note `say "hi"`, note with a newline. | Each note is quoted per RFC 4180, quotes doubled. | Clear for notes. See N-2 and N-8 |
| S-4 | `export` with no `--month`. | Every expense. | Clear |
| S-5 | `export --out report.csv`. | CSV goes to the file. The row count prints. Stdout carries no CSV. | Count format open. See N-3 |
| S-6 | `export --month 2026-13`. | `error:` line, exit 2, no traceback. | Error path clear. "Wrong" is undefined. See N-1 |
| S-7 | `export --month October`. | Same as S-6. | Same gap as N-1 |
| S-8 | `export` when no ledger file exists. | `Ledger.load` returns an empty list. Spec is silent. | Open. See N-4 |
| S-9 | Two expenses on the same day, added in reverse order. | Spec is silent on the order of the two rows. | Open. See N-5 |
| S-10 | `export --out` into a missing directory, or onto an existing file. | Spec is silent. A traceback is possible. | Open. See N-6 and R-1 |
| S-11 | `budgetcli --file other.json export`. | Spec is silent. The global option should apply. | Open. See N-7 |

## Gaps

Blocking gaps: none.

Non-blocking gaps. The plan peer logs an assumption for each one.

- N-1. The spec does not define "wrong month". Assumption candidate: a month is wrong unless it matches `YYYY-MM` with `MM` from 01 to 12.
- N-2. The quoting rule names the note only. The category is free text from `add`. A category with a comma breaks its row. Assumption candidate: apply RFC 4180 quoting to every field.
- N-3. The row count line has no format. The spec does not say whether the header counts. Assumption candidate: print the number of data rows, header excluded, on stdout.
- N-4. The spec does not say what an empty export prints. Assumption candidate: print the header only, exit 0.
- N-5. The spec does not order rows on the same day. `add --day` allows back-dating, so the file order is not date order. Assumption candidate: a stable sort by date, keeping file order for ties.
- N-6. The "never a traceback" rule covers the wrong month only. Assumption candidate: extend it to write errors, with an `error:` line and exit 2.
- N-7. The spec does not name the ledger path for export. Assumption candidate: export reads the path that `--file` sets, as `add` and `totals` do.
- N-8. RFC 4180 quoting applies to a carriage return too. The spec lists comma, quote and newline only. Assumption candidate: quote a field that holds a carriage return.

## Conflicts

No conflict inside the spec. The spec names LOCAL-4, and the session has no tracker. The header follows the no-tracker contract. The label stays in the body.

## Risks

- R-1. `--out` can overwrite an existing file. The spec asks for no check. The plan must choose between overwrite and refuse. A refusal is the safer default for a user file.
- R-2. The existing `totals --month` matches by prefix and does not check the month. The spec does not ask to change it. Keep it out of this change, and log it as a follow-up.

## Exit criteria

The main path has exit criteria: the header, the row order, the two decimals, the quoting, the no-month case, the `--out` case and the wrong-month error.

Missing exit criteria:

- the exit code of a successful run (assume 0)
- the format of the row count line (N-3)
- the output for an empty export (N-4)
- the behavior on a write error (N-6)

## Blocking gaps

None.

Verdict: READY FOR PLANNING
