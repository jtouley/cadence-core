RUN_BY_SKILL: de-adversarial-reviewer
Mode: plan-gate
Pipeline-Run: 595744b8-a249-432d-8838-2b696d1b412f

**Tracker:** none
**Ticket:** TICKET_SKIPPED (none: no tracker declared)

# Adversarial review: feat-local-4-csv-export (P6, round 3)

Plan: `.context/plans/feat-local-4-csv-export.plan.md`
Plan sha256: f1e63d5cca258772cefb3d368d57fa475e8e3043b8a70096a9e079357b411eca (matches the input line).
Packet sha256: 51604e3a0f8b60c576f8627e6b96dc01a6878fdedf3372b6f02ebd5765712eb7 (not in the workspace, so this review did not check it).

## Verification notes

- Read the plan in full and checked its sha256. The plan changed since round 2 (round 2 saw sha f2aed211...).
- Read the peer contract, the architecture core rules and `references/plan-review-round.md`.
- Read the current source: `src/budgetcli/ledger.py` and `src/budgetcli/cli.py`.
  - `Expense.day` is a `date`. `Ledger.add` rejects `amount_cents <= 0`, so the amount formatting in Phase 2 sees only positive cents.
  - `cli.py` has no `export` branch yet. It ends with `if __name__ == "__main__"`, so Phase 4 `python3 -m budgetcli.cli` works.
- Read the round 2 report `.context/adversarial/adversarial_feat-local-4-csv-export_r2.md`. Its B-1, B-2, C-1 and C-2 are the input to this round. Status below.
- Read the four decision records in `.context/proposals/`. All four are `status: proposed`. The out-file record now says "write the bytes of the CSV in binary mode". It matches Phase 3 step 3.
- Read the vibe-test report and the request snapshot. The examples S1 to S6 map to tests in the plan.
- Scratch check outside the repo: with an existing `ledger.json` and a fresh path `fresh.csv` (not yet created), `os.path.samefile("ledger.json", "fresh.csv")` raises `FileNotFoundError`. It does not return False. This is the basis of B-1.
- Context was not truncated.

## Prior round status (round 2 items)

| Round 2 item | Status in this plan |
|---|---|
| B-1 out-file record says text mode | Resolved. The record says binary mode, status `proposed`. |
| B-2 hard link to the ledger | Resolved in the plan text (`os.path.samefile`). A new gap in the same check is B-1 below. |
| C-1 owner acceptance before Phase 1 | Partly resolved. The precondition is written. Acceptance is not in the workspace (C-1 below). |
| C-2 CLI bad-month test covers `2026-13` only | Resolved. The CLI test is parametrized over `2026-13` and `2026-1`. |
| Round 2 verdict | REQUEST CHANGES |

## Blocking issues

### B-1. The hard-link check raises on a new `--out` path

Phase 3 step 3, second check: "the ledger file exists and `os.path.samefile` on both paths is true." `os.path.samefile` needs both paths to exist. When the `--out` file does not exist yet, it raises `FileNotFoundError`. The scratch check above shows this.

The normal case is a new output file with a ledger that already exists. In that case the plan runs the check, the call raises, and the user gets a traceback. The plan's acceptance says "never a traceback" for a bad month, and a traceback here breaks the same contract for a valid export. The plan does not say that the `--out` path must exist before `samefile` runs. The plan's tests do not catch it either:

- `test_export_out_writes_file_and_prints_count` does not say that a ledger file exists. The second check then does not run, so the defect stays hidden.
- `test_export_out_refuses_a_hard_link_to_the_ledger` and `test_export_out_refuses_the_ledger_file` use an `--out` path that exists.

Fix: in Phase 3 step 3, run the `samefile` check only when both the ledger file and the `--out` path exist. Keep the resolved-path check for a missing file. Add one test with an existing ledger and a fresh `--out` path. It asserts exit 0, the file is written, the stdout count line is printed, and stderr has no `Traceback`.

Phase: Phase 3 (steps 1 and 3).

Rules: `core:implement` (the tests come first and must pin each branch of the guard).

## Conditions

### C-1. The owner must accept G-1, G-2 and decisions 1 to 4 before Phase 1

The plan states this as a precondition in Design decisions. All four records are still `status: proposed`. No owner acceptance is in the workspace. The vibe-test verdict is NOT READY FOR PLANNING, and G-1 and G-2 are blocking in that report. The plan settles both as planner assumptions.

Condition: before Phase 1 starts, the request owner accepts G-1, G-2 and decisions 1 to 4, and the records show `status: accepted`. The planner does not set this. If the owner changes G-1 or G-2, or changes the out-file behavior, the plan needs a new review.

Phase: global.

### C-2. The manual check writes to a shared `/tmp` path

Phase 4 step 2 uses `--file /tmp/x.json`. A fixed name in the shared `/tmp` can clash with another run. Use a fresh directory from `mktemp -d` for the scratch ledger. The plan already says not to write `ledger.json` in the repository.

Phase: Phase 4 (step 2).

## Adversarial scenarios

| # | Scenario | Handled? | Evidence |
|---|---|---|---|
| A-1 | A category or note with a comma, a quote, LF or CR breaks the CSV row. | Yes. The stdlib `csv` writer with QUOTE_MINIMAL and CRLF. The quoting table includes the category case `food, drink`. | Plan Phase 2 steps 1 and 2; decision 1. |
| A-2 | A month with a trailing newline or non-ASCII digits passes the check. | Yes. `re.fullmatch` with `[0-9]`. The unit table has both cases. | Plan Phase 1 steps 1 and 2; decision 2. |
| A-3 | `--out` names the ledger through a hard link, and the write truncates the ledger. | Yes, when the ledger and `--out` both exist. The `samefile` check covers it and has a test. | Plan Phase 3 step 3; test `test_export_out_refuses_a_hard_link_to_the_ledger`. |
| A-4 | `--out` is a new path while the ledger exists. The `samefile` check raises a traceback. | No. See B-1. | Scratch run: `FileNotFoundError` from `os.path.samefile` on a missing path. |
| A-5 | `--out` names the ledger through a symlink or the same path. | Yes. `resolve()` and `samefile` both follow symlinks. | Plan Phase 3 step 3; test `test_export_out_refuses_the_ledger_file`. |
| A-6 | The out-file decision record says text mode, and the owner accepts a rule the code does not follow. | Yes. The record now says binary mode. | `.context/proposals/feat-local-4-csv-export-out-file.md`, `choice:` line. |
| A-7 | A bad month writes a file before it fails. | Yes. The month check runs before any file is opened. Test `test_export_bad_month_with_out_writes_no_file`. | Plan Phase 3 step 3, last bullet. |
| A-8 | `export` with no `--month` calls `parse_month(None)`. | Yes. `parse_month` runs only when `--month` is set. Test `test_export_without_month_prints_all`. | Plan Phase 3 step 3, first bullet. |
| A-9 | The ledger file is missing or empty. | Yes. `Ledger.load` returns `[]` for a missing file. The export prints the header only. | `src/budgetcli/ledger.py` `Ledger.load`, first branch; plan N-1. |
| A-10 | Same-day rows change order between runs. | Yes. `sorted` is stable and `Ledger.add` appends in file order. Test `test_same_day_rows_keep_file_order`. | `src/budgetcli/ledger.py` `Ledger.add`; plan Phase 1 step 3. |
| A-11 | `--out` is an unwritable path, such as a directory. | Yes. The `open` sits in the `OSError` branch, which prints `error:` and returns 2. Test `test_export_out_unwritable_is_an_error_line`. | Plan Phase 3 step 3. |

Adversarial scenarios with evidence: 11. Handled: 10. Not handled: A-4 (B-1).

## Trade-offs

- The exact month check differs from the prefix filter that `totals` uses. The two commands differ on `2026-1`. The plan records this as N-9, and `totals` does not change. This is acceptable.
- CRLF follows RFC 4180. A reader that expects LF can fail. The plan lists this as an open risk. No change needed.
- A category that starts with `=`, `+`, `-` or `@` can run as a formula in a spreadsheet. The plan records this as an open risk. The ledger is local and single-user, so a note in the plan is enough.
- The amount formatting with `divmod` assumes positive cents. `Ledger.add` enforces this, so no change is needed now.

## Verdict

Verdict: REQUEST CHANGES
