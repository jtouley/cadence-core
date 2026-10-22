RUN_BY_SKILL: vibe-test
Pipeline-Run: a6acfbd8-95a4-4830-96f3-da1a132ba49a

**Tracker:** none
**Ticket:** TICKET_SKIPPED (none: no tracker declared)

# Vibe test: feat-local-4-csv-export

## Target

Classification: disk spec.

The spec is `.context/proposals/feat-local-4-csv-export.md`. The code under test is the `budgetcli` package in `src/budgetcli/`. The current `export` subcommand does not exist yet.

The spec body names `LOCAL-4` as the ticket. The tracker is `none`, so the ticket line above is `TICKET_SKIPPED`.

## Spec under test

The spec asks for these behaviors:

1. `budgetcli export --month YYYY-MM` prints each expense of that month as CSV.
2. The header is `date,category,amount,note`.
3. Rows are oldest first. Amounts print with two decimals.
4. A note with a comma, a quote or a newline is quoted as RFC 4180 says.
5. `budgetcli export` with no `--month` prints every expense.
6. `--out <file>` writes the CSV to that file and prints the row count.
7. A wrong month prints an `error:` line and exits 2. It never prints a traceback.
8. Out of scope: the upload to the bank portal (LOCAL-9, waits on LOCAL-8).

## Scenarios traced

| # | Scenario | Result against the spec |
|---|---|---|
| S1 | Ledger has rows on 2026-09-03, 2026-10-01 and 2026-10-02. Run `export --month 2026-10`. | Covered. Only the two October rows print, oldest first. |
| S2 | Run `export` with no `--month`. | Covered. All rows print, sorted by date. |
| S3 | A note holds `a, "b"` and a line break. | Covered by rule 4. The field is quoted, and inner quotes double. |
| S4 | A stored amount is 1250 cents. | Covered. It prints as `12.50`. |
| S5 | Run `export --month 2026-13`. | Covered for the `error:` line and exit 2. The spec does not define a valid month. See G-a. |
| S6 | Run `export --month 2026-1` or `export --month October`. | Gap. The spec does not say whether these are wrong months. See G-a. |
| S7 | Run `export --out f.csv`. | Gap. The spec does not say where the row count line goes, or its exact text. See G-b. |
| S8 | `--month` names a month with no expenses. | Gap. The spec does not say whether the header prints alone. See G-c. |
| S9 | A category holds a comma, for example `food,drink`. `add` accepts it. | Conflict. Rule 4 names only the note. Unquoted, the row has five fields. See C-1. |
| S10 | Two expenses share one day. | Gap. The spec does not give a tie order. See G-d. |
| S11 | `--out` names a path in a folder that does not exist. | Gap. The spec does not say what happens. A traceback is possible. See G-e. |
| S12 | `--out` names a file that exists. | Gap. The spec does not say whether the file is overwritten. See G-e. |
| S13 | `ledger.json` is not valid JSON. | Out of the spec. The rule "never a traceback" covers only a wrong month. See G-f. |
| S14 | A note holds a bare carriage return, or CRLF. | Minor gap. Rule 4 names a newline only. RFC 4180 treats CRLF as a line break inside a field. See G-g. |

## Gaps

Each gap is non-blocking unless the blocking section lists it.

- G-a. The spec does not define a valid `--month`. The plan can assume `YYYY-MM` with a month from 01 to 12. The `error:` text is not fixed. Tests can check the prefix `error:`.
- G-b. The row count line under `--out` has no stated stream or text. The plan can assume one line on stdout, such as `wrote N rows`.
- G-c. The header prints when a month has no expenses. The spec reads as if the header always prints. The plan can assume that.
- G-d. Rows with the same day have no stated order. The plan can assume insertion order. A stable sort by date gives that order.
- G-e. Write failures and overwrites under `--out` have no stated behavior. The plan can assume an `error:` line and exit 2 for a write failure. It can assume overwrite.
- G-f. A corrupt ledger file is outside the spec. The plan can leave it as is and log it as out of scope.
- G-g. The spec names a newline. The plan can quote on `\r` and `\n` both.

## Conflicts

- C-1. Rule 4 quotes the note. Rule 4 also says "as RFC 4180 says". RFC 4180 applies to every field. The two readings differ only for categories that contain a comma, a quote or a line break. The safer reading quotes every field. The spec owner should confirm this.

## Risks for P1

- R-1. Output format changes for `export` only. `totals` and `add` keep their output. The plan should keep both unchanged in tests.
- R-2. The amount formatting uses integer cents. Formatting by float division can give odd results for large sums. The plan should format from integer cents.
- R-3. The `export` subcommand name and the `--out` flag must match the spec exactly. Tests call the CLI through `main([...])`, as the existing tests do.

## Blocking gaps

None.

## Verdict

Verdict: READY FOR PLANNING

The spec has no blocking gap. The plan peer logs an assumption for each gap in the register. The gaps G-a to G-g and conflict C-1 need a row each in the assumption register.

## Notes for the orchestrator

- The `.context/issues/feat-local-4-csv-export.md` local ticket file does not exist. The peer contract names it for adapter `none`. The ticket gate file names it as `local_tracker`. The owner of that file should write it. This peer did not write it.
