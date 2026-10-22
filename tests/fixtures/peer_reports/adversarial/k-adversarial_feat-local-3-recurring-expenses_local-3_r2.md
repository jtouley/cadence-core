RUN_BY_SKILL: de-adversarial-reviewer
Mode: plan-gate
Pipeline-Run: b8f0a8f8-dd36-4460-957c-e1bf30384354

# Adversarial review: LOCAL-3 recurring expenses plan, round 2

Input: `.cursor/plans/feat-local-3-recurring-expenses_local-3.plan.md` (sha256 714b44c07db24f7b7b813cf3460a11859f64a0d493847c2caa2b83cf61f5a6ba), including both "Plan update 2026-10-21" sections. Round 2.

## Verification notes

- Recomputed the plan sha256: it matches the input line.
- Read the plan, the round 1 report, the spec, `D-LOCAL-3.md`, `src/budgetcli/cli.py` and `src/budgetcli/ledger.py`.
- Round 1 items against the second update:

  | Item | Status | Where |
  |------|--------|-------|
  | B-1 | Closed | The update adopts D-LOCAL-3 (own line per due item, sorted by id), names it in Step 0, and rewrites D3, acceptance 4 and the helper `recurring_lines`. |
  | C-1 | Closed | Item-value validation and duplicate ids, with test rows. |
  | C-2 | Closed | `JSONDecodeError`, `UnicodeDecodeError`, `OSError` become `InputError` on read and write. |
  | C-3 | Closed | `parse_id` with `fullmatch`. |
  | C-4 | Closed | Full reject set listed, `.5` and `12.` rejected on purpose. |
  | C-5 | Closed | Wrapper and `add food inf` test moved to Phase 1. |
  | C-6 | Closed | `conftest.py`, `run_cli`, parametrize tables, one table-driven validator. |
  | C-7 | Closed with a defect, see C-1 below | `format_cents` added. |
  | C-8 | Closed | Step 0 states the granularity. |

- Ran `divmod(-150, 100)` in Python: it gives `(-2, 50)`. A naive `f"{q}.{r:02d}"` prints `-2.50` for -150 cents. The current code prints `-1.50` through `cents / 100`.
- Spec checked: totals "next to the one-off expenses" matches the new D3.
- The `by-worktree` session file carries the run id `b8f0a8f8-dd36-4460-957c-e1bf30384354`.

## Blocking issues

None.

## Conditions

### C-1. `format_cents` must handle negative cents (`core:plan-conformance`, regression guard)

The plan claims `totals` output stays byte-identical to `{cents / 100:>10.2f}`. `Ledger.load` does not validate sign, so a hand-edited ledger with a negative amount prints `-1.50` today. A `divmod(cents, 100)` helper without sign handling prints `-2.50`. Require `format_cents` to format the absolute value and prefix `-`, and add a parametrized row for `-150` (`-1.50`) and `0` in Phase 1.

### C-2. The plan text has superseded statements that still read as instructions

The base plan keeps the old D3 ("no separate line"), acceptance 4 (same category line), Phase 2 step 3 (`merge_totals`), the Risks line "challenge D1, D3", and `except ValueError` in Phase 3 step 3. The updates say they win, but E1 reads one document. Require E1 to implement from the update sections for D3, acceptance 4, the helper name and the wrapper. Name that list in the E1 handoff, or strike the old lines. No code change.

### C-3. Order of reads in `totals --month` is not fixed

Acceptance 8 expects exit 2 for a corrupt sidecar, and the update expects a traceback for a corrupt main ledger. If both are corrupt, the order decides the result. State it: validate month, then load the sidecar (`InputError`), then the main ledger. Add one Phase 3 test for a corrupt sidecar with a valid ledger and one for the both-corrupt case, so the order is pinned.

## Adversarial scenarios

| # | Scenario | Handled? | Evidence |
|---|----------|----------|----------|
| 1 | Implementation sums recurring into the one-off category line and passes tests | Yes | The update's acceptance 4 requires the exact stdout `rent` 50.00 then `rent (recurring 1)` 900.00; `recurring_lines` is tested without the CLI. |
| 2 | Negative cents in a hand-edited ledger print wrongly after the `format_cents` change | No | `divmod(-150, 100)` is `(-2, 50)`. Raised as C-1. |
| 3 | Hand-edited sidecar with bad values, `1_0` id, unreadable bytes | Yes | The second update gives test rows for each. |
| 4 | Both ledger and sidecar corrupt on `totals --month` | Partly | MF-1 tests cover the main ledger alone; no ordering statement. Raised as C-3. |
| 5 | Crash during sidecar write | Yes | Temp file plus `os.replace`; failing `os.replace` test; no stray temp file. |
| 6 | Two concurrent `recurring add` calls reuse an id | No, accepted | Read-modify-write has no lock. This is a single-user local CLI; see Trade-offs. |
| 7 | E1 follows a superseded line of the base plan | Partly | The updates state precedence; the text is not struck. Raised as C-2. |
| 8 | A phase commit is red | Yes | Per-phase full pytest and commit; Phase 1 exit criterion now has its test. |

## Trade-offs

- No lock on the sidecar: acceptable for a local single-user tool. A lock module would exceed the spec.
- New `parsing.py` and `recurring.py` stay justified: three parsers plus a store with several callers. No config substitute applies (`core:dry-extension`).
- Tightening `add` and `totals --month` is deliberate, listed in full, and backed by the spec line on wrong amounts and months.
- The plan stacks updates on the base text. This keeps history but costs a reconciliation step (C-2).

Verdict: APPROVE WITH CONDITIONS
