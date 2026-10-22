RUN_BY_COMMAND: pr-review-local
Last reviewed commit: 3ddcd505b72a2d3b0ad036eb5a5938e852cef5d2

# PR review 1: feat(recurring): add recurring expenses

Tracker: none
Ticket: TICKET_SKIPPED (none: no tracker declared)

Verdict: MERGE-READY. There is no must-fix item. Four nits are in `## Follow-ups`.

## Must-fix items

None.

## Reproductions

None. There is no must-fix item to reproduce.
I ran the full suite on the PR head (3ddcd50): `python3 -m pytest -q` gave 158 passed.
I ran the probes in `## Adversarial inputs` in a throwaway `git worktree` (detached at the head, removed after use). I did not use the user tree.

## End-to-end walk

Branch: `feat/LOCAL-3-recurring-expenses`. Ticket: none. The PR test plan has three unchecked items. I ran all three.

1. Run the full suite with pytest: cleared (158 passed).
2. `recurring add rent 900 --day 1 --from 2026-01` printed `1`. After `add food 10 --day 2026-10-03`, `totals --month 2026-10` printed food 10.00 and rent 900.00: cleared.
3. The CLI rejects bad input: cleared. `totals --month 2026-1` gave `error: month must be YYYY-MM` and exit 2. `recurring remove 99` gave `error: no recurring expense with id 99` and exit 2. `recurring add c 0.004` gave `error: amount must be positive` and exit 2.

README steps:
- `recurring list` printed one line per item and nothing when empty: cleared.
- `recurring remove 1`, then `list`: item 1 gone and id 1 not reused: cleared.
- `totals` without `--month` counted one-off expenses only (food 10.00): cleared.
- Ledger with a missing field (`[{"day":"2026-10-01"}]`) gave a `KeyError` traceback and exit 1. The README states this limit: cleared as documented.

## Adversarial inputs

| Input | Result |
|-------|--------|
| Missing sidecar | Treated as empty. `add` creates it. |
| Corrupt sidecar (bad JSON, wrong types, low `next_id`) | `error: recurring file is damaged` and exit 2. The tests assert the file is unchanged. `totals` without `--month` ignores the sidecar. |
| Another ticket's leftovers: a sidecar with `next_id` 2 and no items, next to a different ledger | `add` printed `2`. The counter is kept. |
| `--resume` | n/a: the CLI has no resume option. |
| Adjacent id (`1` and `10`, `99`) | `parse_id` takes whole digits only. `remove 99` gave an error. Remove acts on an exact id match. |
| Adjacent month (`2026-1` vs `2026-10`) | `2026-1` is rejected by `parse_month`. Month prefix matching is gone from `totals --month`. The README states this. |
| Offline host without `gh` | n/a: the CLI does not use a network or `gh`. |
| `--file` in a directory that does not exist | `FileNotFoundError` traceback, exit 1. The ledger has the same behavior before this PR. See nit N-3. |
| Empty category (`recurring add "" 5`) | Accepted, and `totals` prints a line with a blank name. See nit N-1. |
| Amount `1e300` | Accepted. `list` prints a 300-digit number. See nit N-2. |
| Amount `nan`, `inf`, `0.004` | Rejected with `error:` and exit 2 (tests cover `nan` and `inf`). |
| Hooks and installers | n/a: the PR changes neither. |

## Contracts

I checked the diff against the plan `.context/plans/feat-local-3-recurring-expenses.plan.md`.
- D-3 (`totals` without a month ignores recurring items): met.
- D-4 (same category line, and a line for a recurring-only category): met.
- Plan line 163 (list format string): matches the code exactly.
- Plan line 36 (no atomic writes): the code follows it. The ledger does not use them either.
- Plan line 198 (hardened `_cents` for `inf`, `nan`, huge values): met. Message is `amount must be a finite number`.
- Behavior change that the plan does not list as a decision: `totals --month` now needs `YYYY-MM`. Before, any prefix such as `2026` worked. The README records the change and a test covers it. It is not a must-fix.

R3 `bugs` record (ruff 0.17.0): 0 findings on added lines. Verdict pass.
R3 `types` record (mypy 2.4.0): 0 findings on added lines. Verdict pass.
I found no `# noqa` and no `# type: ignore` on added lines.
Both tools ran. Neither is `unsupported`.

`core:standalone-package`: the change adds no host path and no outside file. Met.

## Design choices

None. No finding has more than one reasonable fix.

## Scope

Title type is `feat`. The diff has 8 files: 2 runtime files (`cli.py`, `ledger.py`), 4 test files, `README.md` and `.gitignore`. The README and `.gitignore` lines document and support the new feature. The concerns are not mixed, so no split is needed. The `_cents` hardening is a small change to existing behavior inside the same feature. The PR body names it.

## Lookback

No must-fix item, so there is no failure class to name for a defect.
Classes that the author already handled: non-finite numbers, partial writes on bad input (validate before the sidecar is read), id reuse after remove, string compare of month keys.
Classes that remain, all pre-existing and all nits: input validation of free text (category), no upper bound for amounts, no handling of I/O faults for the file path.
I re-read the combined diff adversarially (cli.py, ledger.py) after the probes. I found nothing new beyond N-1 to N-4.
R3 `bugs` and `types` records: both pass (see `## Contracts`).

## Reuse and extension

1. Reused: `Ledger`, `Ledger.total_by_category` (the one-off sum is not rewritten), `_cents`, `Expense`. The CLI split into `_build_parser`, `_run` and `_run_recurring` keeps `main` thin.
2. Duplication: none added to the source. Parsing is in three small helpers (`parse_month`, `parse_id`, `parse_day`) and each has callers. Tests: `tests/test_recurring_cli.py` has its own `ledger_file` and `run` fixtures, and `tests/test_recurring_totals.py` has a `parts` fixture. The repo has no `tests/conftest.py`, so there is no shared helper that these copy.
3. Extension point: a new `rcmd` branch in `_run_recurring` and a new function `totals_with_recurring`. No config key or `stages.json` field was added. The `if/elif/else` on `rcmd` is a small branch chain (3 arms) and is below the R3 limit.
4. Net test lines: +463 (four new test files: 77, 203, 126, 57; the existing `test_ledger.py` is unchanged).

R2 record `scripts/test_shape.py --change`:
- `helpers_reimplemented`: value 0, verdict `warn`. The warning says that no `conftest.py` exists under the test directories, so the measure had nothing to compare. This is not a copy that the measure found.
- `thin_files`: value 0, verdict `pass`.

R3 record `scripts/cognitive_complexity_gate.py`: `OK cognitive-complexity`, 82 functions in 6 changed files, limit 15, duplication and code shape clear. Verdict pass.

## Follow-ups

Nits. None blocks the merge. Track them with PR 1.

- N-1: `recurring add` accepts an empty category. `totals` then prints a line with a blank name. `add` has the same gap. Suggest one check in both commands.
- N-2: There is no upper bound for an amount. `1e300` is stored and makes `list` print a 300-digit number. Suggest a cap in `_cents`.
- N-3: A `--file` path in a missing directory gives a traceback (exit 1) for the recurring commands. The ledger had this gap before the PR. Suggest catching `OSError` in `main`.
- N-4: A ledger row with a missing field gives a traceback. The README states this limit, so it is a known gap.
