RUN_BY_SKILL: vibe-test
Pipeline-Run: 3bff9d24-a23a-46fc-983c-61bd8297aa2e

# Vibe test: feat-local-3-recurring-expenses

**Tracker:** none
**Ticket:** TICKET_SKIPPED (none: no tracker declared; cadence.config.json does not exist in this repository)

Ticket in the request text: LOCAL-3. Task id: feat-local-3-recurring-expenses.

## Target

- Class: disk spec (snapshot of the request).
- File: `.context/proposals/feat-local-3-recurring-expenses.md`.
- Code read for the trace: `src/budgetcli/cli.py`, `src/budgetcli/ledger.py`, `tests/test_ledger.py`, `README.md`.

## Verdict

NOT READY FOR P1 until the 5 blocking gaps (B1 to B5) get a decision. The spec is clear on the happy path. It is silent on several behaviors that change the code and the tests. The non-blocking gaps (N1 to N10) can take a default that P1 writes down.

## Scenarios traced

| # | Scenario | Result against the spec |
|---|----------|-------------------------|
| S1 | `recurring add rent 900 --day 1 --from 2026-01`, then `totals --month 2026-10` | Works. Rent appears for 2026-10. The merge rule with one-off rows is unclear (B3). |
| S2 | `totals --month 2025-12` (before `--from`) | Spec implies the expense is not due. Rule "from that month on" makes 2026-01 inclusive. Not stated for the month before. Default: not due. |
| S3 | `totals` with no `--month` | NOT COVERED. Spec only covers `--month`. See B2. |
| S4 | `recurring remove 99` (unknown id) | Covered: error line, exit 2. |
| S5 | `recurring remove abc` (id not a number) | Depends on the id scheme (B1). argparse may exit 2 with a usage message, not an `error:` line. |
| S6 | `recurring add rent 900 --day 29` or `--day 0` | Covered: `error:` and exit 2. Must check that argparse does not print a usage block instead (B5). |
| S7 | `recurring add rent abc --day 1 --from 2026-01` | Existing `_cents` raises ValueError, caught in the `add` path. A new path must catch it too. |
| S8 | `recurring add rent inf --day 1 --from 2026-01` | `round(float("inf") * 100)` raises OverflowError, not ValueError. Traceback. Existing `add` has the same fault. Spec says never a traceback. See B5. |
| S9 | `recurring add rent 0` or `-5` | The ledger rule is "amount must be positive". Spec does not say the same rule applies. Default: apply it. |
| S10 | `recurring add rent 900 --day 1 --from 2026-13` | Covered: error, exit 2. Format rule for `--from` is not stated (N3). |
| S11 | `totals --month 2026-13` or `--month abc` | Spec says a wrong month is an error. Today `total_by_category` uses `startswith`, so a bad month returns an empty result and exit 0. A change is needed for the existing command (B4). |
| S12 | `recurring list` with nothing stored | Not stated. See N5. |
| S13 | Remove one recurring expense, then add another | Id reuse is not stated (B1). |
| S14 | `<ledger>.recurring.json` is missing, empty or corrupt | Missing: treat as empty (expected). Corrupt: a JSON error gives a traceback. Not stated (N7). |
| S15 | `--file` after the subcommand: `recurring add ... --file x` | `--file` is a top-level option. It must come before the subcommand. Same as today. Not a new gap, but the README must show it. |
| S16 | Two recurring expenses with the same category and day | Not stated. Default: allow (ids differ). |

## Gaps

### Blocking

- **B1. Id scheme.** The spec says "prints its id" but not what an id is. Options: next integer, max+1, or a random string. This changes the file format, the `remove` argument type, the reuse after removal (S13), and every test. Decision needed: pick sequential integers that are never reused (store `next_id`) or max+1.
- **B2. `totals` with no `--month`.** One-off totals are all time. A recurring expense has no end, so "all time" has no finite answer. Options: exclude recurring, count each month from `--from` to the current month (depends on today's date, so a test needs a clock), or count to the last month that has a one-off row. Decision needed.
- **B3. "Next to the one-off expenses".** It is not clear if recurring amounts add into the same category row (rent 900 plus a one-off rent 20 prints 920) or print as separate rows. This changes the output and the test assertion. Decision needed.
- **B4. Month validation scope.** The spec says a wrong month is an error. It lists `--from` and `totals --month`. The current `totals --month` accepts any text. Decision needed: confirm that `totals --month` becomes strict (`YYYY-MM`, month 01 to 12, exit 2 on error). This is a behavior change to an existing command, so it needs a test for the old valid case.
- **B5. Error path for argparse and non-ValueError faults.** "An `error:` line and exit 2, never a traceback" is a hard rule. Two faults break it: (a) argparse type errors print `usage: ... budgetcli: error: ...` and exit 2, which is an `error:` text but not a line that starts with `error:`; (b) `inf` and very large amounts raise OverflowError (S8). Decision needed: either accept the argparse format as meeting the rule, or validate all input in code and print `error: <reason>`. The exit criterion must say which one.

### Non-blocking (P1 may set a default and write it down)

- **N1. Ledger path rule.** "`<ledger>.recurring.json`": for `ledger.json` this is `ledger.json.recurring.json` on a literal reading, or `ledger.recurring.json` if the suffix is replaced. Default: literal (append to the full file name). Write one test that names the path.
- **N2. `--from` default.** The example always passes `--from`. Not stated if it is required. Default: required, because "from that month on" needs a start.
- **N3. `--from` format.** Default: `YYYY-MM`, same check as `--month`. Reject `2026-10-05` and `2026-1`.
- **N4. Output formats.** `add` prints the id: bare or with text? `list` columns: separator, amount in dollars with two decimals (like `totals`) or in cents, `from` as `YYYY-MM`. Default: one line per item, fields in the stated order, amount as `900.00`, tab or space separated. Fix one format in the plan.
- **N5. Empty `list`.** Default: print nothing, exit 0.
- **N6. Amount and category rules.** Default: same as `add` (positive amount, same rounding). Empty category: reject, exit 2.
- **N7. Corrupt or unreadable recurring file.** Default: `error:` line, exit 2. The same fault exists for the ledger today (not in scope, note it).
- **N8. Name clash.** `recurring add --day` takes an integer 1 to 28. `add --day` takes a date. Same flag, two meanings. Not a defect, but the help text must be clear.
- **N9. Delete effect on history.** `remove` also removes the expense from past months, since there is no end date and no per-month record. Default: accept and document.
- **N10. Rounding.** `round(float * 100)` can mis-round values such as 12.345. Existing behavior. Default: reuse `_cents` and do not change it.

## Conflicts

- No conflict inside the spec text.
- The spec says "Ticket LOCAL-3". The tracker is `none`, so no ticket is read. The local tracker file is `.context/issues/feat-local-3-recurring-expenses.md`. This is only a naming note.
- Spec statement "A wrong ... month is an `error:` line" conflicts with current `totals --month` behavior (see B4).

## Missing exit criteria

The spec has no acceptance list. P1 should add these checks at least:

1. Add then list shows the item; the printed id works with `remove`.
2. `remove` of an unknown id: stderr starts with `error:`, exit 2, file unchanged.
3. `totals --month` for: the `--from` month, a month before it, a later month, and a month with a one-off row in the same category.
4. Each bad input (amount, day 0 and 29, month 13, text month, `inf`) gives an `error:` line, exit 2, no traceback (assert on stderr, not only on the exit code).
5. The recurring file path and its content after `add` and `remove`.
6. The existing three tests still pass.
7. README shows the new commands.

## Risks for P1

- R1. A clock in `totals` with no month (B2) makes tests unstable. Pass `today` as a parameter if that option is chosen.
- R2. The `Ledger` class owns the one JSON file. A second store should not make `Ledger.total_by_category` do two jobs. Keep a separate small class and merge in the CLI or in one new function.
- R3. Non-atomic file write: a crash during `write_text` can leave a half file. Same as the ledger today. Low risk.
- R4. Strict month validation (B4) is the only change to an existing command. Test it first.
