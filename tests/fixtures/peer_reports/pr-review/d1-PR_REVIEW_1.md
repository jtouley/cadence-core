RUN_BY_COMMAND: pr-review-local

# Add CSV export of monthly expenses

**Tracker:** none
**Ticket:** TICKET_SKIPPED (none: no tracker declared)
**PR:** https://github.com/local/budgetcli/pull/1 (draft)
**Branch:** feat/LOCAL-4-csv-export, base main
Last reviewed commit: 4a6accc720aadc842dcccbffb8b6b5ebb668723f

Review worktree: the user tree at `<e2e>/d1-budgetcli`. It is already on the PR head branch, so no second worktree was attached. The untracked `.context/` and `.cursor/` directories were not touched by the change under review.

## Verdict

MERGE-READY

No must-fix item. Five follow-ups and one open human check are listed below. None blocks merge.

## Summary

The change adds `Ledger.expenses(month)`, a CSV renderer (`budgetcli.export`), and an `export` subcommand with `--month` and `--out`. The behavior the PR text claims holds on the head commit. The 43 tests pass. The `--out` guard refuses the ledger by path, by symlink and by hard link, and it does so before any write.

## Must-fix items

None.

## Reproductions

No must-fix item, so no must-fix reproduction. Commands run against head `4a6accc` in the user tree:

- `python3 -m pytest` gives `43 passed`.
- README walk: `budgetcli add food 12.50` exits 0. `budgetcli totals --month 2026-10` prints `food              12.50` and exits 0.
- `budgetcli export --month 2026-10` on a fresh ledger prints the header and the October row, and exits 0.
- `export --out nodir/x.csv` prints `error: cannot write nodir/x.csv: No such file or directory`, exits 2, and prints no traceback.
- `export --out .` (an existing directory) prints `error: cannot write .: Is a directory`, exits 2.
- `export --out ledger.json` (same file as `--file`) prints `error: --out would overwrite the ledger`, exits 2, and the ledger bytes are unchanged.
- `export --out sym.json`, where `sym.json` is a symlink to the ledger, is refused the same way.

Non-blocking finding, see Follow-ups F1: a corrupt ledger makes `export` exit 1 with a Python traceback. The same happens with `totals` today, because `Ledger.load` is unchanged.

## End-to-end walk

The PR body and README describe these steps. Each step was run for real in a scratch directory.

1. `budgetcli add food 12.50` (run as `python3 -m budgetcli.cli`): cleared.
2. `totals --month 2026-10`: cleared. Output matches the expected text in `tests/test_ledger.py::test_cli_totals_output_is_unchanged`.
3. `export` with no month: cleared. It prints all rows in date order, header first.
4. `export --month 2026-10 --out <file>`: cleared. It prints `wrote N rows` and writes the file with LF line ends.
5. PR test-plan item "check the CSV output in a spreadsheet tool": NOT RUN. This is a human check. It stays open. See Follow-ups F4.

## Adversarial inputs

Changed CLI surface: `export`, `--month`, `--out`, and `Ledger.expenses`.

| Input | Result on head |
|---|---|
| `--month` bad values (`2026-1`, `2026-13`, `2026-00`, `October`, `2026-10-01`, empty, non-ASCII digits) | exit 2, `error: --month must be YYYY-MM...`, no traceback. Covered by `BAD_MONTHS` parametrized test. |
| `--month` valid, no rows | exit 0, header only. Covered. |
| `--out` is the ledger by path, `..` path, hard link | exit 2, refused before write, ledger bytes unchanged. Covered. |
| `--out` is a symlink to the ledger | exit 2, refused. Checked by hand. |
| `--out` is a missing folder | exit 2, `error: cannot write`, no traceback. Covered. |
| `--out` is an existing directory | exit 2, `error: cannot write .: Is a directory`. Checked by hand. |
| `--out` name too long (300 chars) | exit 2, no traceback. Covered. |
| `--out` existing file | overwritten. Covered. |
| Ledger file missing | exit 0, header only. `Ledger.load` returns `[]`. Covered by `test_expenses_on_a_missing_file_is_empty`. |
| Ledger file corrupt JSON | exit 1, JSONDecodeError traceback. Not covered. See F1. |
| Ledger row with invalid date (`2026-13-01`) | exit 1, ValueError traceback. Not covered. See F1. |
| Ledger file contains `x` and `--out` names the same file | exit 1 traceback, because the ledger loads before the `--out` guard runs. See F1. |
| Another ticket's leftovers, `--resume`, adjacent ids (`cd-1` vs `cd-12`), offline host without `gh`, a workspace that never ran Cadence | Not applicable. The change touches no gate, hook, installer or `gh` call. |
| Spreadsheet formula text in a note (`=HYPERLINK(...)`) | Written unescaped. `render_csv` uses `csv.writer` with no formula prefix. See F2. |

## Contracts

Checked against the PR body (the stated contract) and against `cadence.config.json`, which declares `adapter: none` and `profile: strict`. No `SKILL.md` or `references/` file applies to this CLI repository.

- PR claim "`Ledger.expenses(month)` returns the expenses of one month, oldest first, file order kept on the same day": holds. Covered by `test_expenses_keeps_file_order_for_the_same_day`.
- PR claim "`total_by_category` output does not change": holds. Covered by `test_cli_totals_output_is_unchanged`.
- PR claim "`budgetcli.export` formats amounts as integer cents with two decimals": holds for negative amounts and for amounts under one unit.
- PR claim "`--out` refuses the ledger file, by path or by hard link, before any write": holds. The refusal sits before `open(args.out, "w")`.
- PR claim "A write failure exits 2 with an error line": holds for missing folder, directory and long name.
- PR claim "A wrong month exits 2 with an error line and no traceback": holds.
- The README does not describe `export`. This is not a contract breach because the README never listed the feature. See F5.

Tool records (R3, read only, not re-judged):
- `bugs` (ruff 0.17.0): `verdict: pass`, 0 findings on added lines of 5 Python files.
- `types` (mypy 2.4.0): `verdict: pass`, 0 findings on added lines of 5 Python files.
- No `# noqa` or `# type: ignore` is added on any changed line.

## Design choices

None. No finding has more than one reasonable fix.

## Scope

- Title type `feat:`-style description ("Add CSV export..."): matches the diff. The diff adds a feature and tests. It does not change unrelated code.
- File count: 5 files, 280 insertions, 3 deletions. The 3 deletions are the `total_by_category` loop, which now calls `expenses(month)`.
- Mixed concerns: none. No docs change is mixed with runtime change.
- Recommended split: none.

## Lookback

Failure classes behind the examined areas:

- Overwrite of user data by the output path. Guarded at three points: path resolve, samefile, and the ordering before `open`. The three cases are tested.
- Boundary of user input. The month regex is anchored with `fullmatch`. The bad-month table covers the boundary inputs.
- Unhandled input error on the new command path. Write errors are handled. Read errors on the ledger are not. This is the single gap found. It is the same gap that `totals` already has, so it is classed as a pre-existing failure class, carried into the new command. See F1.

Re-run on the combined tree: the head `4a6accc` was the only tree examined. No fix commit was made by this review, so no re-run is needed beyond the test suite, which passed.

Re-read the combined diff adversarially: the `_same_file` helper returns `False` on `OSError` from `resolve()` or `samefile`. The worst case is that a path that cannot be resolved is treated as not the ledger. The open step then fails with a normal OSError, which is handled. No data-loss path was found.

## Reuse and extension

Checked against `core:dry-extension`.

1. Helpers reused: `Ledger.load`, `Ledger.expenses`, `Expense`, `main` from `budgetcli.cli` in the tests. `is_month` is reused from `budgetcli.export` in the CLI.
2. Duplication added: the CLI `add` calls in `test_export.py::seeded_ledger` repeat the setup style of `test_ledger.py`. The duplication is small (3 calls) and sits in different test files with different fixtures. Not a conftest copy. No new helper duplicates an existing one.
3. Extension point: `export` is a new subcommand in the existing argparse table. No core function gained a branch.
4. Net change in test lines: 208 added (`tests/test_export.py` 173, `tests/test_ledger.py` 35). 0 removed.

Measure records:

- R2 `test_shape --change` (`helpers_reimplemented`): `verdict: warn`, `unmeasured: no conftest.py under the test directories`. This is an unmeasured result, not a pass. The repo has no conftest, so no conftest helper exists to copy. The reviewer read the test files and found no copied helper. `thin_files`: `verdict: pass`, 0 offenders.
- R3 `cognitive_complexity_gate.py`: `OK cognitive-complexity 38 functions in 5 changed files, limit 15`. Record findings `[]`, highest complexity 14 (limit 15), highest statements 43 (limit 50), highest branch chain 2 (limit 6), duplication 0.0%. Read `verdict`; not re-judged.

## Follow-ups

Open nits. Not fix commits. Recorded here per .

- F1 (nit, pre-existing class): a corrupt ledger or an invalid date in the ledger makes `export` exit 1 with a traceback. `Ledger.load` raises `JSONDecodeError` and `ValueError`. `totals` has the same behavior today. Suggested follow-up: catch `ValueError` (JSON errors are a subclass) in `Ledger.load` callers and print `error: cannot read <ledger>` with exit 2. Not required by the PR contract.
- F2 (nit, design): CSV formula injection. `render_csv` does not prefix a category or note that starts with `=`, `+`, `-` or `@`. A spreadsheet can evaluate such a cell. Suggested follow-up: decide whether the export neutralizes these cells, and document the choice.
- F3 (nit, text): `wrote 1 rows` is not grammatical. The test `test_export_out_one_row_prints_wrote_1_rows` asserts the wrong grammar. Suggested follow-up: `wrote 1 row` for a count of 1.
- F4 (open human check): the PR test-plan item "check the CSV output in a spreadsheet tool" is not done. Owner: the PR reviewer. This review did not run a spreadsheet.
- F5 (nit, docs): `README.md` does not mention `export`. Suggested follow-up: add one example line.

## Notes on evidence

- The PR body reports 43 tests. This review re-ran them on head `4a6accc`: 43 passed in 0.17 s.
- The PR body's claim "git diff of `cli.py` removes no line" matches the diff: `cli.py` has 0 deletions.
- The PR's smoke-run claim was re-run in a scratch directory and matches.
