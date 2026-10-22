RUN_BY_COMMAND: pr-review-local

# PR review 1: Add CSV export of expenses

Last reviewed commit: c78ee12cd98955a311f61de9fd57a0e64c382d22

**Tracker:** none
**Ticket:** TICKET_SKIPPED (none: no tracker declared)

PR: https://github.com/local/budgetcli/pull/1 (draft, base main)
Branch: feat/LOCAL-4-csv-export
Worktree: the user tree is already on the Cadence working branch for this PR. HEAD equals the PR head. No worktree was attached.

## Verdict

NO MERGE. One open must-fix (MF-1): the export writes a wrong amount for every negative cent value.

## Must-fix items

### MF-1: negative amounts export with the wrong value

- Location: `src/budgetcli/export.py:_amount`, the `divmod` on a negative `cents`.
- Rule: `core:dry-extension` does not apply. The rule is correctness of the output contract: the CSV `amount` column must carry the ledger value.
- Evidence: `divmod` floors toward minus infinity. The result is `-2.50` for `-150` cents and `-1.95` for `-5` cents. Both are wrong.
- Reach: `Ledger.add` refuses amounts of zero or less, so the CLI cannot write a negative row. `Ledger.load` does not check the sign. A hand-edited `ledger.json` with `amount_cents` below zero exports wrong numbers without an error.
- Test gap: `test_amounts_have_two_decimals` covers only positive values.
- Fix direction (one reasonable fix, no design choice): format the absolute value and prefix `-` when `cents < 0`. Add a test row with a negative cent value to the `test_amounts_have_two_decimals` table.

## Reproductions

Run in a scratch directory with `PYTHONPATH=src`. Against the PR head c78ee12.

```
$ python3 -c "from budgetcli.export import _amount; print(_amount(-5), _amount(-150), _amount(-100))"
-1.95 -2.50 -1.00
```

Expected: `-0.05 -1.50 -1.00`. Observed: `-1.95 -2.50 -1.00`. MF-1 reproduces.

Ledger file `[{"day":"2026-10-01","category":"food","amount_cents":-150,"note":""}]`:

```
$ python3 -m budgetcli.cli --file neg.json export
date,category,amount,note
2026-10-01,food,-2.50,
```

Expected `-1.50`. Observed `-2.50`. MF-1 reproduces through the CLI.

Suite on the PR head: `python3 -m pytest -q` gives 39 passed. The suite does not cover the negative case, so it does not catch MF-1.

## End-to-end walk

The PR body and the README document `add` and `totals`. Export is new and has no README entry. Walk run with a fresh ledger:

1. `add food 12.50 --day 2026-10-01 --note "lunch, friends"`: cleared (rc 0).
2. `add rent 900 --day 2026-10-02`: cleared (rc 0).
3. `add food 5 --day 2026-09-30`: cleared (rc 0).
4. `totals --month 2026-10`: prints `food 12.50` and `rent 900.00`. Cleared.
5. `export --month 2026-10`: prints a header and two rows. The note with a comma is quoted. Cleared.
6. `export --out all.csv`: writes 3 rows, CRLF line ends. Cleared.
7. `export --month 2026-13`: exit 2 and one `error:` line, no traceback. Cleared.

Open: the README has no export section. This is a nit (see Follow-ups).

## Adversarial inputs

Each input is run against `budgetcli export` at the PR head.

| Input | Observed | Result |
|---|---|---|
| Ledger file missing | header only, rc 0 | pass |
| Ledger file corrupt JSON | Python traceback, rc 1 | nit (see Follow-ups) |
| Ledger row with a bad date (`2026-02-30`) | traceback, rc 1 | nit (see Follow-ups) |
| Ledger row with negative cents | wrong amount | MF-1 |
| `--month 0000` | `error: month must be YYYY-MM`, rc 2 | pass |
| `--month 2026-13`, `2026-1` | error line, rc 2, no file written with `--out` | pass (tests cover it) |
| `--out` to a missing parent directory | `error: [Errno 2]...`, rc 2 | pass |
| `--out` to the ledger file | refused, rc 2, ledger unchanged | pass |
| `--out` to a symlink that points at the ledger | refused, rc 2 | pass |
| `--out` to a relative path that names the ledger | refused, rc 2 | pass |
| `--out` to a directory | error line, rc 2, no stdout | pass (tests cover it) |
| Note that starts with `=` | written raw, quoted by CSV | nit (see Follow-ups) |
| Adjacent id (`cd-1` vs `cd-12`) | not applicable: the CLI has no ticket ids | n/a: no ticket id in this CLI |
| `--resume` | not applicable: export has no resume mode | n/a: no resume mode in this CLI |
| Offline host without `gh` | not applicable: export does not call any host | n/a: no network path |
| Workspace that never ran Cadence, user symlink with the same name | not applicable: no hook or installer changed | n/a: no hook or installer in this PR |

## Contracts

- The repo has no contract document. There is no `docs/`, no `SKILL.md`, and no quickstart. The contracts checked are the PR body and the code's own docstrings and tests.
- The PR body says: `python3 -m pytest -q` passed with 39 tests. Confirmed: 39 passed.
- `Ledger.add` rejects amounts of zero or less (`ledger.py`). The export does not apply the same rule to a loaded row. MF-1 comes from that gap.
- `export.py` docstring says RFC 4180 with CRLF line ends. Confirmed: header and rows end with `\r\n`. The quoting tests match the RFC rules for comma, quote, and newline.
- `ledger.expenses` docstring says oldest first and same-day rows keep file order. Confirmed by `test_expenses_are_oldest_first` and `test_same_day_rows_keep_file_order`.
- R3 records: `cognitive-complexity` pass (38 functions, limit 15, highest complexity 6). `bugs` (ruff 0.17.0) pass, 0 findings on added lines. `types` (mypy 2.4.0) pass, 0 findings on added lines. No `# noqa` or `# type: ignore` on added lines.

## Design choices

None. MF-1 has one reasonable fix (sign-aware formatting). The nits do not have a choice that changes behavior, so they stay in Follow-ups.

## Scope

- Files changed: 5 (`src/budgetcli/cli.py`, `src/budgetcli/export.py`, `src/budgetcli/ledger.py`, `tests/test_export.py`, `tests/test_ledger.py`). Net: 258 insertions, 2 deletions.
- Title type `Add` matches a feature. The diff holds only runtime and test changes. No docs are mixed in.
- Recommendation: no split. The change is one feature.

## Lookback

- Failure class of MF-1: sign-unaware integer arithmetic on money. Floor division and modulus behave differently for negative inputs. The same class can appear in any new formatter. The test table for the formatter covers only positive inputs. Add a negative case to every amount-format test table.
- Failure class of the nits: error handling at the file-load boundary. `Ledger.load` lets a `JSONDecodeError`, a `KeyError`, or a `ValueError` escape to the top level. The existing `totals` command has the same behavior, so the export inherits it. This is not new in the PR.
- After the MF-1 fix, the author re-runs the suite and the repro above. Then the review update checks the new commit.

## Reuse and extension

- Helpers reused: `Ledger.load`, `Ledger.add` (test setup), `main` (CLI tests), and `parse_month`.
- Duplication added: none. The test file has one fixture (`ledger_file`) and one assertion helper (`_assert_out_refuses_ledger`). Neither copies an existing helper.
- Extension point: the export is a new subcommand in `main`. It follows the same shape as `add` and `totals`. No new config key, no `stages.json` change.
- Net test lines: 179 added (143 in `test_export.py`, 36 in `test_ledger.py`), 1 removed (an import line).
- R2 record `test_shape` (`scripts/test_shape.py --change`): `thin_files` pass (0). `helpers_reimplemented` warn with `unmeasured`: the layout has no `conftest.py`, so the copied-helper check did not run. This is not a pass for that measure. No helper in the change copies a conftest helper by inspection, but the check is unmeasured.
- R3 record `cognitive-complexity`: pass. R3 `bugs` and `types`: pass (see Contracts).

## Follow-ups

These are nits. They do not block merge. They are not for a fix commit. The repo has no tracker, so they stay here.

1. Corrupt ledger input (bad JSON, or a bad date such as `2026-02-30`) gives a traceback with exit 1. `totals` does the same. A shared fix belongs in `Ledger.load`, not in the export. Scope: pre-existing behavior.
2. CSV formula injection: a note that starts with `=`, `+`, `-`, or `@` is written raw. Spreadsheet apps can run it. Decide whether export should prefix such values. No contract in this repo says either way.
3. README has no export section. Add two lines to the usage block when the PR is marked ready.
