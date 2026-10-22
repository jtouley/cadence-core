RUN_BY_SKILL: de-adversarial-reviewer
Mode: plan-gate
Pipeline-Run: dbc23a1d-55eb-4783-80a5-76662a83373e

**Tracker:** none
**Ticket:** TICKET_SKIPPED (none: no tracker declared)
Ticket: feat-local-3-recurring-expenses (LOCAL-3)

# Adversarial review: feat-local-3-recurring-expenses (round 3)

Input: `.cursor/plans/feat-local-3-recurring-expenses_dbc23a1d.plan.md`, sha256 `c37a4dd2d2d9fd7e83bbf3daae33a075a810030e39e6cbc7587b18bb946ed121` (verified equal to the packet). Round 2 reviewed `050fe0d6...`.

## Verification notes

Files inspected: the plan, `src/budgetcli/cli.py`, `src/budgetcli/ledger.py`, `pyproject.toml`, `.gitignore`, the proposals directory listing, and the round 1 and round 2 reports.

The plan gained a "Plan update (cycle 2)" section (N-6 to N-8) and a second empty "Revision ledger" table. No phase text changed. A grep of the plan for `dest=`, `required=True`, `fullmatch`, `1e307` and `0.004` has no hit. None of round 2 B-1 to B-3 or C-1 to C-6 is answered. The plan review of cycle 2 (READY TO EXECUTE) did not take them in.

Re-run probes (`python3 -I`):

| Probe | Result |
|---|---|
| `float("1e307")` finite, then `round(v*100)` | `OverflowError` (not `ValueError`). The Phase 1 `math.isfinite` guard passes it. |
| `round(float("0.004")*100)` | `0`. |
| `+5`, `1_0` through `float()` | 500, 1000. |
| `re.match(r'^\d{4}-(0[1-9]\|1[0-2])$', "2026-10\n")` | Matches. |

## Blocking issues

### B-1. `1e307` still gives a traceback (open since round 1)
Phase 1 adds `math.isfinite` on `float(text)` only. `1e307` is finite, and `round(v*100)` raises `OverflowError`. The single `except ValueError` in `main` does not catch it. This breaks the constraint "never a traceback" and acceptance item 5 (`core:implement`).
Fix: check `math.isfinite(v * 100)` or a stated maximum, and add `1e307` to the Phase 1 cases.

### B-2. A zero-cent recurring item is stored (open since round 1)
`0.004` gives 0 cents. `Ledger.add` refuses it, but `recurring add` does not call `Ledger.add` and the plan states no check in `RecurringStore.add`. Fix: test `cents > 0` inside `parse_amount` after rounding, and add `0.004` to the Phase 1 cases. N-8 asks E1 to check `Ledger.add` behaviour, which does not address the recurring path.

### B-3. Month and day parsers accept input the plan rejects (open since round 1)
`$` matches before a trailing newline, and `\d` matches non-ASCII digits. A stored `from_month` such as `"2026-10\n"` breaks the string comparison `from_month <= month` in `due_in`. `int()` accepts `+5`, `1_0`. Fix: `re.fullmatch(r"[0-9]{4}-(0[1-9]|1[0-2])", text)`, a `[0-9]{1,2}` fullmatch for the day, and add `"2026-10\n"`, `"+5"`, `"1_0"` to the invalid cases.

## Conditions

### C-1. `--from` needs `dest="from_month"`
`args.from` is a syntax error. Phase 3 must name the `dest`.

### C-2. Nested subparser dispatch is unspecified
Give the dispatch key for the two-level `recurring` command and set `required=True` on the nested subparsers, or `budgetcli recurring` has no handler.

### C-3. `recurring remove <id>` parsing
`type=int` prints usage with no `error:` line. Parse the id in `inputs.py` and add `remove abc` to the Phase 3 cases.

### C-4. Corrupt-file handling
`load` must catch `KeyError`, `TypeError`, `AttributeError`, `OSError` and re-raise `ValueError`, and cover a wrong root shape. Validate unique ids and `next_id` above every id (G-2).

### C-5. Category rule
Reject an empty, whitespace-only or newline-bearing category in `inputs.py`, or list it as a non-goal.

### C-6. Fill both revision ledgers
Both "Revision ledger" tables are empty. Each accepted item needs a row with a verification (a test name or check). Two rounds of empty ledgers show the update step is not mapping findings to changes.

## Adversarial scenarios

| # | Scenario | Handled? | Evidence |
|---|---|---|---|
| 1 | `recurring add rent 1e307` | No | Reproduced `OverflowError`; guard passes it. B-1. |
| 2 | `recurring add rent 0.004` | No | Reproduced 0 cents; no check on the recurring path. B-2. |
| 3 | `--from "2026-10\n"` | No | Regex reproduced as matching. B-3. |
| 4 | `budgetcli recurring` with no sub-subcommand | No | No `required=True` or dispatch key in the plan (grep). C-2. |
| 5 | Sidecar with a list root | Partly | N-6 adds one wrong-type case; no exception list. C-4. |
| 6 | `totals --month` with ledger missing, sidecar present | Yes | Phase 4 `test_totals_with_recurring_and_no_ledger_file`; `Ledger.load` returns `[]` for a missing file. |
| 7 | Remove then add: id reuse | Yes | `test_removed_id_is_not_reused` with stored `next_id`. |
| 8 | Existing tests after the `main` refactor | Yes | `Ledger` untouched; `test_ledger.py` unchanged. |

## Trade-offs

- A sidecar and a new `recurring.py` add a second storage path; the `Ledger` format and its pinned test stay untouched (`core:dry-extension`). Accepted.
- Validating `totals --month` changes old behaviour (`--month foo` was exit 0); named and tested. Accepted.
- Money stays on `float`. Accepted only if B-1 and B-2 are fixed.
- No slug-and-markers check is involved, so a new module is justified over config.
- The six decision records exist in `.context/proposals/` (`core:decision-coverage` met).

Verdict: REQUEST CHANGES
