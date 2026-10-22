RUN_BY_SKILL: vibe-test
Pipeline-Run: b8f0a8f8-dd36-4460-957c-e1bf30384354

**Tracker:** none
**Ticket:** TICKET_SKIPPED (none: no tracker declared)

# Vibe test: feat-local-3-recurring-expenses (A5)

Target: disk spec `.context/proposals/feat-local-3-recurring-expenses.md` (snapshot of the user's spec, verbatim).
Code read for tracing: `src/budgetcli/cli.py`, `src/budgetcli/ledger.py`, `tests/test_ledger.py`.
The spec text names "Ticket LOCAL-3". The adapter is `none`, so the header above records TICKET_SKIPPED.

## Verdict

The spec is implementable, but 7 gaps block a plan. The spec fixes the commands, the day range, the storage file and the error rule. It leaves open the semantics that decide the tests: ids, `totals` without `--month`, how recurring rows appear in `totals`, output formats, and validation of existing inputs.

## Scenarios traced

| # | Scenario | Trace result |
|---|----------|--------------|
| S1 | `recurring add rent 900 --day 1 --from 2026-01`, then `totals --month 2026-10` | Works if "due" means month >= from. Output shape for the recurring row is undefined (G3). |
| S2 | `totals --month 2025-12` with rent from 2026-01 | Rent excluded (before from). Implied by "from that month on", not tested by an example. |
| S3 | `totals --month 2026-01` (the from month) | Rent included. Boundary is implied only. |
| S4 | `totals` with no `--month` | Undefined. One-offs sum all time. A recurring expense has no end, so "all time" has no value (G1). |
| S5 | Add rent, add food one-off, a one-off "rent" in the same month | Same category in both sources. Merge or separate row is undefined (G3). |
| S6 | `recurring add rent 900 --day 29` / `--day 0` / `--day abc` / `--day 1.5` | Range rule exists. Exit 2 and `error:` line are specified. Message text and stream are not (G9). |
| S7 | `recurring add rent abc` / `0` / `-5` / `inf` / `nan` / `1e3` / `12.505` | Reusing `_cents` gives: `abc` and `nan` raise ValueError (caught). `inf` raises OverflowError, which is not caught, so a traceback (verified by running `_cents`). `1e3` is accepted as 1000.00. `12.505` becomes 1250 by float rounding. The spec says "wrong amount" without defining it (G6). |
| S8 | `--from 2026-13`, `2026-1`, `2026`, `abc`, `2026-10-01` | "Wrong month" is an error, but no format is stated. `totals --month` today uses `startswith` and accepts any string (G7). |
| S9 | Add two recurring, remove the one with the highest id, add a third | If id = max+1, the third reuses the removed id. If a counter is stored, the file schema needs it. Id scheme is undefined (G2). |
| S10 | `recurring remove 99` / `remove abc` / `remove` with no id | Unknown id: exit 2 and error are specified. Non-numeric id depends on the id scheme. Missing id is an argparse error (G9). |
| S11 | `recurring list` with nothing stored | Output undefined (empty, header only, or a message). Exit code undefined. |
| S12 | `--file other.json recurring add ...` | The sidecar path is `<ledger>.recurring.json`. For `ledger.json` this is `ledger.json.recurring.json` or `ledger.recurring.json` (G4). |
| S13 | Remove a recurring expense, then `totals --month 2026-01` | Past months change retroactively, because no end date or history is stored. Consequence of the design, not stated in the spec (G8). |
| S14 | `<ledger>.recurring.json` holds invalid JSON or a wrong shape | `json.loads` raises JSONDecodeError. "Never a traceback" is unmet unless handled. The existing `Ledger.load` has the same flaw (G10). |
| S15 | `recurring add` without `--day` or without `--from` | Required or defaulted is undefined (G5). |
| S16 | Add the same category/amount/day twice | Allowed or rejected is undefined. Non-blocking. |
| S17 | Empty or whitespace category | Existing `add` does not validate. Spec silent. Non-blocking. |

## Gaps

Blocking gaps stop P1 from writing testable steps. Non-blocking gaps need a default in the plan.

| ID | Gap | Blocking |
|----|-----|----------|
| G1 | `totals` without `--month`: include recurring or not, and for which months. A rule such as "recurring is only counted when `--month` is given" or "months from `from` to today" is needed. | Blocking |
| G2 | Id scheme: integer or string, sequence or max+1, reuse after remove, how the next id is stored in the JSON. | Blocking |
| G3 | How recurring rows appear in `totals`: summed into the category line, or a separate line, and the label. "Next to" is ambiguous. | Blocking |
| G4 | Sidecar name: append (`ledger.json.recurring.json`) or replace suffix (`ledger.recurring.json`). A test asserts the path. | Blocking |
| G5 | Are `--day` and `--from` required? Defaults (day 1, current month) are not given. | Blocking |
| G6 | Definition of a wrong amount: non-positive, non-finite, more than 2 decimals, exponent notation. The `inf` OverflowError path is a known traceback. | Blocking |
| G7 | Month format (`YYYY-MM`, month 01 to 12) and whether `totals --month` is tightened. Tightening changes existing behavior: `totals --month garbage` returns nothing today. | Blocking |
| G8 | Retroactive effect of remove and no end date. State it as accepted, or add an end/stop field. | Non-blocking |
| G9 | Error contract: stream (existing code uses stderr), message wording, and whether argparse usage errors (`budgetcli: error: ...`, exit 2) count as the `error:` line. A non-numeric `--day` with `type=int` or a missing argument takes the argparse path. | Non-blocking |
| G10 | Corrupt or wrong-shape sidecar file, unreadable path: handling. Write atomicity. Spec says "never a traceback" for input only. | Non-blocking |
| G11 | Exact stdout of `recurring add` (id alone), `recurring list` (column order, separators, amount format, `from` as YYYY-MM, sort order, empty case). The spec lists the fields but not the layout. | Non-blocking, but needed for exact-output tests |
| G12 | `--file` is a top-level option and must precede the subcommand. Examples omit it. Confirm this is kept. | Non-blocking |
| G13 | Duplicate rows and category validation. | Non-blocking |

## Conflicts

- C1: "never a traceback" against reuse of `_cents`. The existing amount parser leaks OverflowError for `inf`. Fixing it touches `add` as well as `recurring add`.
- C2: "A wrong month is an `error:` line" against the current `totals --month`, which accepts any string. The requirement forces a change to an existing command.
- C3: "prints its id" against argparse: `recurring add` is a nested subparser, and `add --day` is a date while `recurring add --day` is an int. The same flag name has two meanings across subcommands. Help text must separate them.
- C4: The spec names ticket LOCAL-3 while the config adapter is `none`. No conflict in behavior. The report records TICKET_SKIPPED.

## Missing exit criteria

- No test list or coverage statement. Needed: each command, the boundary month (from month and the month before), day 1 and 28 pass, 0 and 29 fail, unknown id, bad amount, bad month.
- No statement that existing tests (`tests/test_ledger.py`) stay green.
- No README update requirement, although README lists commands.
- No definition of done for exit codes of `list` and `remove` on success (assume 0).

## Risks for P1

1. Money: use integer cents and one shared parser for `add` and `recurring add`. Fix `inf` in the shared parser.
2. Put the due-in-month rule in one function (`due_in(month)`), tested on its own, so `totals` and any later command share it.
3. Keep the sidecar load/save beside `Ledger` so corrupt-file handling is in one place.
4. Decide G1 to G7 before the plan. P1 should list each decision as an assumption if the user cannot be asked.

## Suggested defaults if no answer comes

G1: `totals` without `--month` ignores recurring and says so in `--help`. G2: integer ids from a stored `next_id`, never reused. G3: recurring amounts add into the category line. G4: append `.recurring.json` to the full ledger file name. G5: both flags required. G6: positive, finite, at most 2 decimals, plain decimal notation only. G7: `YYYY-MM` with month 01 to 12, applied to `--from` and to `totals --month`.

## C.O.R.E.

SUMMARY: The spec traces cleanly for the main flow. 7 blocking gaps (G1 to G7) and 4 conflicts remain.
DECISIONS NEEDED: (1) G1 and G3, the meaning of totals with recurring rows. (2) G2 and G4, id scheme and sidecar name. (3) G6 and G7, validation rules, including the change to `totals --month`.
ACTIONS REQUIRED: Answer or accept the suggested defaults above, then run P1.
EVIDENCE: This file; `_cents('inf')` raised OverflowError when run; `totals --month` uses `startswith` in `src/budgetcli/ledger.py`.
