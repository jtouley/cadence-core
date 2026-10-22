RUN_BY_COMMAND: pr-review-local
Last reviewed commit: 46fcb72774e904b3bc7edd5efd522fb9f5cdd806

**Tracker:** none
**Ticket:** TICKET_SKIPPED (none: no tracker declared)

PR: https://github.com/local/budgetcli/pull/1
Branch: feat/LOCAL-4-csv-export (LOCAL-4 is the branch and PR label)
Base: main (583eb69). Head: 46fcb72. Diff saved at `.context/prs/diffs/PR_1.diff`.
Review scope: `budgetcli export` (src/budgetcli/cli.py, src/budgetcli/export.py, tests/test_export.py).

Verdict: NO MERGE. One must-fix (MF-1): `export --out` changes the file mode to 0600. The change also silently resets the mode of an existing file. The test suite pins this behavior.

## Must-fix items

MF-1. `--out` writes the file with mode 0600, whatever the umask is. `_write_out` in `src/budgetcli/cli.py:25-33` uses `tempfile.mkstemp`, which creates the temp file with mode 0600. `os.replace` then moves that mode onto the target. A report written to a shared path is unreadable by the group and by other users. An existing 0644 file also becomes 0600 after an export. The ledger itself is written with `Path.write_text` (mode 0644 under umask 022), so the two outputs disagree. `tests/test_export.py:197` asserts the 0600 mode, so the suite locks in the defect. Fix: create the temp file with the default mode, or `chmod` it to `0o666 & ~umask` before `os.replace`. Then change the assertion at line 197 to match. Evidence in `## Reproductions`.

## Reproductions

Each reproduction ran against a copy of HEAD (46fcb72) made with `git archive`, outside the user's tree. Commands ran with `umask 022`.

1. MF-1, new file mode:
   ```
   $ python3 -m budgetcli.cli --file l.json export --month 2026-10 --out a.csv
   wrote 2 rows to a.csv
   $ ls -l a.csv
   -rw------- 1 root root 130 ... a.csv
   $ python3 -c "import pathlib; pathlib.Path('b.txt').write_text('x')"  # then stat
   write_text perms: 0o644
   ```
2. MF-1, existing file mode: a 0644 file `c.csv` was overwritten with `--out c.csv`. `ls -l c.csv` shows `-rw-------`.
3. Full test suite: `python3 -m pytest -q` gives `34 passed`. The 29 new tests are in `tests/test_export.py`, as the PR body says.
4. Ruff: `ruff check .` gives one finding, `DTZ011` at `src/budgetcli/cli.py:64`. The same finding exists on `main` (583eb69). It is not from this PR. The PR body is correct about it.

## End-to-end walk

Steps from README (the repo has no `docs/` directory, so the README is the quickstart):

1. `budgetcli add food 12.50 --day 2026-10-20 --note "lunch, with friends"`: cleared.
2. `budgetcli add rent 900 --day 2026-10-05 --note rent`: cleared.
3. `budgetcli add food 0.05 --day 2026-09-30`: cleared.
4. `budgetcli export` prints the header and three rows, oldest first, with the quoted note. Cleared.
5. `budgetcli export --month 2026-10 --out oct.csv` writes two rows and prints `wrote 2 rows to oct.csv`. The file content matches stdout. Cleared (mode issue: MF-1).
6. `budgetcli export --month 2026-1` prints `error: --month must be YYYY-MM, for example 2026-10` and exits 2. Cleared.

The PR test plan items are all met: pytest passes, and the ruff item is the known pre-existing one.

## Adversarial inputs

Each row gives the input, the result I observed, and the test that covers it, if one exists.

| Input | Observed | Covered by test |
|---|---|---|
| Ledger file missing | header only, exit 0 | yes (`test_empty_ledger_prints_header_only`) |
| Ledger path is a directory | header only, exit 0 | yes |
| Ledger is zero bytes | `JSONDecodeError` traceback, exit 1 | yes, pins the traceback (see N-3) |
| Malformed row (`2026-13-01`) | `ValueError` traceback | yes, pins the traceback (see N-3) |
| Month `2026-1`, `October`, `2026-13`, `2026-00`, `2026-10-01` | `error:`, exit 2, no stdout | yes |
| Month `2026-10` with trailing newline | `error:`, exit 2 (fullmatch holds) | reproduced, not in suite |
| Wrong month with broken ledger | `error:`, exit 2 (month checked first) | yes |
| `--out` names the ledger | refused, exit 2, ledger unchanged | yes |
| `--out` in a missing directory | `error:`, exit 2, no traceback | yes |
| `--out` replace fails (`os.replace` raises) | exit 2, old file kept, temp file removed | yes |
| `--out` onto an existing file | content replaced, mode becomes 0600 | yes, but the test pins the defect (MF-1) |
| `--out -` | writes a file named `-` in the working directory. Does not write stdout | no (see N-2) |
| Note starting with `=`, `+`, `-`, `@` | written as is, so a spreadsheet can run it | no (see N-1) |
| Cadence resume, adjacent ticket ids (`cd-1` vs `cd-12`), another ticket's leftovers | n/a: the CLI reads no pipeline state and no ticket ids | n/a |
| Offline host without `gh` | n/a: no network or `gh` use in the change | n/a |
| Hooks or installers | n/a: none changed in this PR | n/a |

## Contracts

- The header is `date,category,amount,note`. The code and `HEADER` in the tests agree. Matches the PR body.
- Amounts have two decimals. The code uses `f"{cents / 100:.2f}"`. Float division is exact enough for any realistic ledger, and negative hand-edited amounts format correctly. Matches the PR body.
- Rows are sorted by date, with ties in file order (stable sort). Matches the PR body and `test_same_day_keeps_file_order`.
- `--month` is strict `YYYY-MM`. Matches the PR body.
- Line endings are CRLF, which is the `csv` module default and RFC 4180. The tests pin CRLF in `test_output_bytes_use_crlf`. The PR body does not mention CRLF. This is a documentation gap, not a code defect (N-4).
- Cadence contract: `core:standalone-package` does not apply (no Cadence peers changed). `core:implement` does not apply (not a Cadence run).

## Design choices

MF-1 has more than one reasonable fix:

- Option A (simple): create the temp file with default mode, or `os.chmod(tmp, 0o666 & ~umask)` before `os.replace`. Change one assertion. Smallest diff.
- Option B (medium): read the existing target mode, if any, and copy it to the temp file. Otherwise use the umask mode. Keeps existing permissions on overwrite. Larger diff, more test cases.
- Option C (over-engineered): a permission flag on `export`. Not justified by the contract.

I did not build option worktrees in this review. My recommendation is option A. Option B is the better fit if the author wants overwrites to keep the target's mode. The choice belongs to the author. Both options keep the existing atomic write and the temp-file cleanup, which the tests check.

## Scope

- 3 files changed: 2 source files (77 lines added, 2 removed), 1 test file (237 lines added).
- One concern: the export feature. The `_error` helper replaces the existing `print` and `return 2` pattern in `add`. That is a small refactor, and it is in scope.
- The PR title is not in the packet, so I could not check its type. The commit subjects use `phase N:` and do not follow a conventional type. Nit only (N-5).

## Lookback

Failure classes behind the findings:

- MF-1: side effects of an atomic-write helper. `mkstemp` picks a mode for the caller. The fix is to set the mode on purpose.
- N-1: output escaping. CSV is correct for commas, quotes and newlines. Spreadsheet formula injection is a separate class, and the CSV round-trip tests do not cover it.
- N-2: the `-` convention for stdout. The flag is accepted and not handled.
- N-3: error reporting. The export path raises on a corrupt ledger, and the `error:`/exit 2 path is used only for bad input. The `totals` command has the same traceback behavior, so this is pre-existing.

Post-fix re-run: not applicable yet (no fix commit).

Measures (read from the gates, not judged again):

- `test_shape.py --change`: `helpers_reimplemented` value 0, verdict `warn`, unmeasured because the repo has no `conftest.py`. `thin_files` value 0, verdict `pass`. The `warn` means the check did not run. It does not mean the check passed. There is no shared conftest to copy from.
- `cognitive_complexity_gate.py`: `OK cognitive-complexity 41 functions in 3 changed files, limit 15; duplication and code shape in 3 Python files; bugs (ruff 0.17.0): 0 findings on added lines of 3 Python files; types (mypy 2.4.0): 0 findings on added lines of 3 Python files`. Verdict `OK`.
- No `# noqa` or `# type: ignore` was added in the changed files. I checked the diff text.

## Reuse and extension

1. Reused helpers: `render_csv` and `export_expenses` reuse the `Expense` dataclass and `Ledger.load` from `ledger.py`. `_error` is reused in `add` and `export`.
2. Duplication added or removed: none in source. In tests, `_assert_out_written` and `_assert_month_error` are file-local. No test helper copies a shared helper, because the repo has no shared conftest.
3. Extension point: none added or used. The change does not add a config key or a branch to a core function beyond the new subcommand.
4. Net test lines: +237 in `tests/test_export.py`. No test file was removed.
5. Measure records cited: `test_shape` (warn, unmeasured conftest, see Lookback) and `cognitive_complexity` (OK). Verdicts read, not judged again.

## Follow-ups

Nits. Each goes to the tracker, or stays here until a tracker exists. None blocks merge.

- N-1. CSV formula injection. A note or category that starts with `=`, `+`, `-` or `@` is written as is. Reproduced: note `=HYPERLINK(...)` is written unchanged. Opening the file in a spreadsheet can run it. Option: prefix a single quote in the cell for those characters, and add a test. Decide if the export is for spreadsheets.
- N-2. `--out -` writes a file named `-`. The usual convention is stdout. Either handle `-` as stdout, or reject it with `error:`.
- N-3. A corrupt ledger causes a traceback in `export`. `totals` does the same. Consider a single `error:` path for both. This is pre-existing, so it can be a separate change.
- N-4. The PR body does not mention CRLF line endings or the output file mode (see MF-1). Add one line each.
- N-5. The commit subjects `phase N:` do not match the repo's commit style, and the PR title is not in the packet. Check the title type when the PR is updated.
