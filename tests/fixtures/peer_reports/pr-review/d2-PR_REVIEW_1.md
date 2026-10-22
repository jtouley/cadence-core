RUN_BY_COMMAND: pr-review-local

# PR review 1: Add budgetcli export: CSV output for expenses

**Tracker:** none
**Ticket:** TICKET_SKIPPED (none: no tracker declared)

Last reviewed commit: 01e2dad40afcd99938721839eff6fc612106c4a5

Verdict: NO MERGE

PR: https://github.com/local/budgetcli/pull/1
Branch: feat/LOCAL-4-csv-export (base main)
Reviewed head: 01e2dad40afcd99938721839eff6fc612106c4a5
Diff: .context/prs/diffs/PR_1.diff (956efe6..01e2dad, 4 files, +263)

## Summary

The PR adds `budgetcli export` with `--month` and `--out`. It renders
RFC 4180 CSV with CRLF line ends. `--out` writes through a temp file and
`os.replace`.

The test suite passes (52 tests). The DRY, complexity, ruff and mypy
measures pass. One must-fix blocks merge. It breaks the error contract
that the PR states for itself: bad input exits 2 with one error line and
no traceback. An unreadable ledger file produces a traceback and exit 1.

## Must-fix items

MF-1. `budgetcli export` does not handle an OSError raised while it reads the ledger.
`src/budgetcli/cli.py:21` catches only `(ValueError, KeyError, TypeError)`.
`Ledger.load` calls `Path.read_text`, which raises `PermissionError`, `EIO`
and similar errors. These escape `_export` as a traceback, exit code 1, and
no `error:` line. The PR body says "Bad input exits 2 with one error line".
The test helper `assert_refused` in `tests/test_export.py` forbids a
traceback, but no test feeds an unreadable ledger. Fix: add `OSError` to
the except tuple at `src/budgetcli/cli.py:21`, then add a test row that
makes the ledger unreadable and asserts `assert_refused`.

## Reproductions

Reproduced in a throwaway `git worktree` at 01e2dad (detached). The
worktree was removed after the run. The user tree was not changed.

MF-1, unreadable ledger. Root can read any file, so the permission
denial was simulated by patching `Path.read_text` for `ledger.json` to
raise `PermissionError(13, ...)`, then calling `main(["--file",
"ledger.json", "export"])`:

    Traceback (most recent call last):
      ...
    PermissionError: [Errno 13] Permission denied: 'ledger.json'
    exit=1

Expected under the PR contract: one `error:` line on stderr and exit 2.

Output file mode (see NIT-1). With `PYTHONPATH=<worktree>/src`:

    python3 -m budgetcli.cli --file ledger.json export --out out.csv
    wrote 1 rows to out.csv
    600 out.csv
    # keep.csv created as 644 by hand, then exported over
    wrote 1 rows to keep.csv
    600 keep.csv

Ledger path is a directory (see Adversarial inputs):

    python3 -m budgetcli.cli --file adir export
    date,category,amount,note
    exit=0

`--out` names an existing directory:

    error: [Errno 21] Is a directory: '<dir>/tmpXXXXXX' -> 'odir'
    exit=2

## End-to-end walk

The README documents `budgetcli add` and `budgetcli totals`. The `budgetcli`
console script is not installed in this environment, so each step ran as
`python3 -m budgetcli.cli`.

1. `add food 12.50 --day 2026-10-03 --note lunch`: cleared (exit 0).
2. `export --month 2026-10`: cleared. Header plus one row, CRLF ends.
3. `export` with no month: cleared. Every row, oldest first.
4. `export --month 2026-10 --out out.csv`: cleared. Message `wrote 1 rows to out.csv`, file content matches stdout.
5. `export --month 2026-13`: cleared. Exit 2, one `error:` line, no output file.
6. `export --out <ledger path>`: cleared. Exit 2, ledger unchanged.
7. Pytest suite: 52 passed.

The PR's test plan lists no unchecked items.

## Adversarial inputs

Changed CLI surface: `budgetcli export`. No hooks, gates or installers
changed. Input classes that do not apply to this CLI are `n/a`.

- Missing ledger file: header only, exit 0. Matches `test_empty_or_missing_ledger_prints_header_only`.
- Empty ledger `[]`: header only, exit 0. Covered by test.
- Corrupt JSON: exit 2, one error line. Covered by `test_corrupt_ledger_json_exits_2`.
- Row without `day`: exit 2. Covered.
- Unreadable ledger (OSError): traceback, exit 1. Fails. See MF-1.
- Ledger path is a directory: `Ledger.load` treats it as missing (`is_file()` is False). Export prints the header only and exits 0. The same happens for `totals`. Silent empty result. See NIT-2.
- Bad month `2026-13`, `2026-1`, `October`: exit 2. Covered by parametrized test.
- Adjacent month `2026-10` vs `2026-1`: strict parse. Covered.
- `--out` equal to the ledger path: exit 2, ledger bytes unchanged. Covered.
- `--out` into a missing directory: exit 2, no file. Covered.
- `--out` into an existing directory: exit 2, one line. Message names a temp file. Cosmetic, not tested.
- `--out` over an existing file: content replaced. Mode changes to 0600. See NIT-1.
- Write fails before `os.replace`: temp file removed, target unchanged, listing shows only the target. Covered by `test_out_failure_leaves_existing_file_unchanged`.
- Disk full during `f.write`: caught by the same OSError branch. Not tested directly. The `os.replace` case covers the cleanup path.
- `--out` path is a symlink: `os.replace` replaces the link with a regular file. Not tested. See NIT-3.
- Another ticket's leftovers, `--resume`, adjacent ticket ids, offline host without `gh`: n/a. The CLI does not use these.

## Contracts

- The PR states "Bad input exits 2 with one error line" (draft PR body). MF-1 contradicts this statement. This is a must-fix, not a design decision.
- `README.md` documents `add` and `totals` only. It does not mention `export`. See NIT-4.
- No `SKILL.md`, `references/` or `cadence.config.json` in the repository constrains the export behavior.
- Measures: `bugs` (ruff) record: verdict `pass`, value 0, 6 files. `types` (mypy) record: verdict `pass`, value 0, 6 files. Both read from `.context/gates/cognitive-complexity.json`. No `# noqa` or `# type: ignore` was added in the diff.

## Design choices

None. MF-1 has one fix that fits the existing except pattern in `_export`.

## Scope

- PR title type `feat` matches the diff: a new subcommand with tests.
- The PR does not mix docs and runtime changes.
- 4 files changed: `src/budgetcli/cli.py` (+28), `src/budgetcli/export.py` (+40, new content), `tests/conftest.py` (+12), `tests/test_export.py` (+183). No split is needed.
- README is not updated for `export` (see NIT-4).

## Lookback

Failure class behind MF-1: the error boundary is narrower than the
documented contract. The handler lists the exception types that the
parser raises, but not the I/O errors that the file read raises. The same
class covers the `totals` path, which has no `except` around `ledger.total_by_category`.
Only the export path is in scope for this PR.

Failure class behind NIT-1: a temp-file replace takes the mode of the
temp file, not of the target. `tempfile.mkstemp` creates the file 0600.

Combined tree: the head is the only tree in this review. After the MF-1
fix lands, `/pr-review-update` must re-run the suite and re-read the new
commits.

Re-read of the combined diff (adversarial pass): the findings above
(MF-1, NIT-1, NIT-2, NIT-3, NIT-4) came from this pass. No other issue
was found.

## Reuse and extension

Checked against `core:dry-extension` (`references/architecture-core-rules.md`).

1. Helpers and modules reused: `tests/conftest.py` `ledger_file` and `run_cli`. `budgetcli.ledger.Ledger` and `Expense`. `budgetcli.cli.main` in the fixture.
2. Duplication added or removed: none measured. New test helpers in `tests/test_export.py` are `export_text_ledger` and `assert_refused`. No existing helper with the same logic exists in `tests/test_ledger.py` or `tests/conftest.py`.
3. Extension point: the `export` subcommand is registered through the argparse subparser table in `cli.py`, the same as `add` and `totals`.
4. Net test lines: +183 in `tests/test_export.py`, +12 in `tests/conftest.py`. No test file was removed.

R2 record (`scripts/test_shape.py --change`): `helpers_reimplemented` verdict `pass` (value 0, threshold 0). `thin_files` verdict `pass` (value 0, threshold 0). Read, not judged again.

R3 record (`scripts/cognitive_complexity_gate.py`, file `.context/gates/cognitive-complexity.json`): complexity 5 of 15 `pass`. Statements 26 of 50 `pass`. Branch chain 2 of 6 `pass`. Duplication 0.0 percent of 3 `pass`. `bugs` (ruff 0.17.0) verdict `pass`, value 0. `types` (mypy 2.4.0) verdict `pass`, value 0. Read, not judged again.

Test quality: the table tests (`test_amount_two_decimals`, `test_text_field_quoting`, `test_bad_month_exits_2`) use exact expected strings. No partial-success threshold is used. The one gap is the missing OSError row (MF-1).

## Follow-ups

Nits. None blocks merge. Each goes to the tracker with the PR, or stays here under this heading, once the MF-1 fix is merged. Tracker is none, so the nits stay here.

- NIT-1: `write_atomic` in `src/budgetcli/export.py` (`tempfile.mkstemp`, line 33) creates files 0600. The new file and any overwritten file end up 0600. An existing 0644 target becomes 0600. Choose: keep 0600 as the safer default and document it, or copy the target mode with `os.chmod` before `os.replace`.
- NIT-2: `Ledger.load` (`src/budgetcli/ledger.py:34`) treats a directory as a missing ledger. `export` and `totals` print an empty result with exit 0. Fix in a later PR with a test for `--file <dir>`.
- NIT-3: `--out` through a symlink replaces the link. Document or resolve the link first.
- NIT-4: `README.md` does not document `export`. Add one usage line.
- NIT-5: CSV cells that start with `=`, `+`, `-` or `@` are not escaped. A spreadsheet can read them as formulas. Low risk for a personal ledger. Decide in a later PR.
