RUN_BY_COMMAND: pr-review-local
Last reviewed commit: 0bf7e61262fcbdf2217be3048758e473409e013d

# PR review 1: recurring expenses (feat/LOCAL-3-recurring-expenses)

**Tracker:** none
**Ticket:** TICKET_SKIPPED (none: no tracker declared)

Input: .context/prs/draft-pr-f996c4d5-f193-456d-a9a3-34aa60ae5876.md
Worktree: this is the Cadence working branch for PR 1, so no extra worktree was attached.

## Verdict

MERGE-READY. No must-fix item. Two nits go to `## Follow-ups`.

## Must-fix items

None.

## Reproductions

None needed: no must-fix item. I ran the nit cases against the PR head in a throwaway directory (see `## Adversarial inputs`).
Suite on the PR head: `python3 -m pytest -q` gave `173 passed`.

## End-to-end walk

I ran the README path in an empty temporary directory with the real CLI entry (`budgetcli.cli.main`).

1. `recurring add rent 900 --day 1 --from 2026-01` printed `1`, exit 0. Cleared.
2. `recurring remove 1` printed nothing, exit 0. Cleared.
3. `recurring add x 5 --day 2 --from 2026-02` printed `2`. The id was not reused. Cleared.
4. `recurring list` printed `2  x  5.00  2  2026-02`. Cleared.
5. `add food 10`, then `totals --month 2026-10` printed `food 10.00` and `x 5.00`. Cleared.
6. `totals` without `--month` printed `food 10.00` only, as the README says. Cleared.
7. `remove 99` printed `error: unknown id 99`, exit 2. Cleared.

The PR body has no unchecked test-plan item.

## Adversarial inputs

| Input | Result |
|-------|--------|
| amount `1e400`, `nan` | `error: amount must be a finite number`, exit 2 |
| amount `-5`, `0.004` | `error: amount must be positive`, exit 2 |
| `--day 29` | `error: day must be 1 to 28`, exit 2 |
| `--from 2026-13`, `2026-1`, full-width digits | `error: invalid month`, exit 2 |
| blank category | `error: category must not be empty`, exit 2 |
| store file missing | treated as empty store, exit 0 |
| store file corrupt (`{bad`) | `error: cannot read ...`, exit 2, for `totals --month` and `recurring list`; `totals` without `--month` does not read the store and still works |
| store with valid JSON and valid item | listed correctly |
| leftovers of another ledger | store path is derived from `--file`, so another ledger has its own store; no cross-read |
| adjacent id (`remove 1` vs `10`) | match is by exact integer; `_digits` rejects non-digit ids |
| `--resume`, `gh` offline | n/a: the change has no hook, gate, installer or network call |
| amount `9999999999999999999` | accepted, listed as `10000000000000000000.00` (nit N-1) |
| `--file /nonexistent/dir/l.json recurring add ...` | `FileNotFoundError` traceback, exit 1 (nit N-2) |

## Contracts

- No `SKILL.md`, `references/` or `cadence.config.json` contract applies to this product repo. The README is the contract. Every README claim matched the walk above (ids never reused, `remove` silent, exit 2 with `error:`, `--file` before `recurring`, `totals` without `--month` is one-off only).
- R3 `bugs` (ruff 0.17.0): 0 findings on added lines. R3 `types` (mypy 2.4.0): 0 findings on added lines. Both ran. Source: `cognitive_complexity_gate.py` output `OK ... bugs ... 0 findings ... types ... 0 findings`.
- I found no `# noqa` or `# type: ignore` on added lines.

## Design choices

None. No finding has more than one reasonable fix that needs options built.

## Scope

Title type `feat` matches the diff. 5 files: `README.md`, `src/budgetcli/cli.py`, `src/budgetcli/ledger.py`, `src/budgetcli/recurring.py`, `tests/test_recurring.py`. The README change documents the new command, so docs and runtime belong together. No split needed.

## Lookback

- Failure class for N-1: missing upper bound on a numeric input. The positive and finite checks exist, no maximum.
- Failure class for N-2: unhandled `OSError` on write. This class already exists in `Ledger.add` (same traceback with `--file /nonexistent/d/l.json add food 5`), so the PR copies the existing behavior, it does not add a new class.
- Combined tree: suite passes (173). R3 `bugs` and `types` records show 0 findings. I re-read the combined diff adversarially. I found no further issue. Item counts: `RecurringStore._load` validates ids, duplicate ids and `next_id`, and raises `ValueError` for each.

## Reuse and extension

1. Reuse: `parse_month` is added in `ledger.py` and is the one month parser for `cli.py` and `recurring.py`. `Ledger.total_by_category` is reused for the merge. `_cents` is reused by `_recurring_cents`.
2. Duplication: none added. There is no `conftest.py` in the repo, so no conftest helper can be copied.
3. Extension point: `_cmd_recurring` dispatches on `args.action`. A new action needs a new branch (a small if-chain of three; below the R3 limit). No config key or gate is added.
4. Net test lines: +388 in one new file (`tests/test_recurring.py`). No existing test was changed.

Measure records:
- R2 `test_shape.py --change`: `offenders: []`, `unmeasured: []`, `helpers_reused: []`. No reimplemented helper and no thin file.
- R3 `cognitive_complexity_gate.py`: verdict OK. 73 functions in 4 changed files, limit 15; duplication and code shape clear.

## Follow-ups

- N-1 (nit): `recurring add` accepts a huge amount. Add an upper bound. Track with PR 1.
- N-2 (nit): a write error (`OSError`) in `RecurringStore._save` gives a traceback. Catch `OSError` in `_cmd_recurring` and print `error: ...`, exit 2. `Ledger.add` has the same gap. Track with PR 1.
