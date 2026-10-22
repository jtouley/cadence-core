RUN_BY_SKILL: vibe-test
Pipeline-Run: d4ba0f2c-7d61-43ff-95e3-c0d4df64a622

**Tracker:** none
**Ticket:** TICKET_SKIPPED (none: no tracker declared)

# Vibe test rev 3: LOCAL-1 monthly budgets

Block: A5. Packet sha256: e8f129c90af734b1a1d23b6d19f115980499724876288a6a6d0f9a3b13e23e7f
Target class: disk spec, `.context/proposals/LOCAL-1-monthly-budgets.md` (rev 2, file sha256 2cdb03e4dc9573ad985e6f9bb1ab1d3acf0ccf8531cabc37a7efbfc9e7c09d2a).
Supersedes: `feat-local-1-monthly-budgets_2026-10-20-rev2.md` (same spec text, run key 84838627). Neither earlier report is modified.
Run key: d4ba0f2c-7d61-43ff-95e3-c0d4df64a622 (the active session). The first free rev name was rev3.

## Verdict

READY FOR PLANNING. 0 blocking gaps, 0 conflicts, 3 non-blocking gaps (N1 to N3 below). All non-blocking gaps of rev 2 (N1 to N7) stay as decided in the plan.

## What changed since rev 2

The spec text is the same as the rev 2 target. The tree now holds an implementation (`src/budgetcli/validate.py`, `budgets.py`, `cli.py`) and 89 passing tests (`python3 -m pytest -q`, 89 passed). This pass re-traces the spec scenarios against the code as a reality check. It does not review the code (R1 does that).

## Scenarios traced (spec against built behavior)

Probe: a fresh temp directory, calls through `budgetcli.cli.main`.

| # | Scenario | Observed | Result |
|---|----------|----------|--------|
| S1 | food 320 and travel 45.50 spent, budgets food 300 and rent 900, `totals --month 2026-10` | Three rows identical to the spec example (food `OVER`, rent `0.00`, travel `-` `-`), exit 0 | Pass (AC2, AC3, AC5) |
| S2 | `totals --month ""` | `error: invalid month ''`, exit 2 | Pass (plan N2) |
| S3 | `budget set food 12.345 --month 2026-10` | `error: invalid amount ...`, exit 2 | Pass (AC4) |
| S4 | `add --day 2026-13-01` | argparse error, exit 2 | Pass (outside the spec, still exit 2) |
| S5 | Budgets file holds `[]` | `error: budgets file ledger.budgets.json is not a map ...`, exit 2 | Pass, matches the F4 fix |
| S6 | Budgets file holds `{"2026-10":{"food":"x"}}` | Same shape error, exit 2 | Pass |
| S7 | Budgets file holds `{` | `error: Expecting property name enclosed in double quotes: line 2 column 1 (char 2)`, exit 2 | Behavior exists by side effect (see N2) |

## Blocking gaps

None.

## Conflicts

None. The accepted `add` strictness change (rev 2 N1) still stands.

## Non-blocking gaps

- **N1 D9 is partly overtaken.** The spec says a corrupt file is out of scope (D9). The code now exits 2 with `error:` for a wrong-shape budgets file and, by the shared `except ValueError`, for malformed JSON. D9 and the deferral register should say "corrupt JSON is reported by side effect, not by contract", so the register does not claim a crash. Update the register wording at P8 or R-stage. No spec change.
- **N2 Error text for malformed JSON names no file.** S7 prints a bare parser message, while S5 names the file. A user with two JSON files cannot tell which one is bad. Cosmetic. Accept or record in the deferral register.
- **N3 Wrong-shape ledger file.** Only the budgets file has a shape check (F4). No AC covers a ledger file of the wrong shape. Not traced here, and D9 keeps it out of scope. Record it with N1.

## Risks for P1

None new. The plan already carries the strict-gate dry run, the handler split, the shared validators and the pure `build_rows`.

## Required before P1

None.

## C.O.R.E.

SUMMARY: spec rev 2 is coherent. The built behavior matches the spec example byte for byte. 0 blocking gaps.
DECISIONS NEEDED: none.
ACTIONS REQUIRED: reword the D9 deferral entry (N1); optional N2 and N3.
EVIDENCE: this file, `.context/vibetest/feat-local-1-monthly-budgets_2026-10-20-rev3.md`; `python3 -m pytest -q` gives 89 passed.
