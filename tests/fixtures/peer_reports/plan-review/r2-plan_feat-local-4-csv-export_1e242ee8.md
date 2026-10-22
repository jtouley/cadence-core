RUN_BY_COMMAND: plan-review

**Tracker:** none
**Ticket:** TICKET_SKIPPED (none: no tracker declared)
Pipeline-Run: 1e242ee8-6c60-4125-aa6a-038093ac14e2

Verdict: READY WITH CHANGES

Last reviewed plan sha256: 1d400f937c6d89236738510e33157dc028e2f6aa3162342452ec1a6ce567a65d

# Plan review: feat-local-4-csv-export

Plan: `.cursor/plans/feat-local-4-csv-export_1e242ee8.plan.md`
Vibe test: `.context/vibetest/feat-local-4-csv-export_2026-10-21.md`
Previous review: `.context/reviews/plan_feat-local-4-csv-export_1e242ee8.md` (cycle 4, READY WITH CHANGES)
Review cycle: 5 of 5

## Summary

The update of revision 47fcb8118578 answers MF-4 in Phase 3 only. Phase 1 still declares the first parameter of `export_expenses` as `ledger`, and its tests still take the `sample_ledger` fixture, which returns a path. MF-4 stays open. No new must-fix item was found. The plan needs one more update before it can run.

## Checks made

- The sha256 of the plan, the previous review and the vibe-test report match the packet.
- Read `src/budgetcli/ledger.py`. `Ledger.load` returns `[]` when `is_file()` is false. The zero-byte, malformed-row and `[]` cases behave as the plan states.
- Read `src/budgetcli/cli.py`. The only `error:` line is the inline print in the `add` branch (line 34). `_error`, `_write_out`, `_run_export` and `export.py` do not exist yet.
- `tests/` holds only `test_ledger.py`. There is no `tests/conftest.py`. `sample_ledger` is new.
- Traced the four fixture rows against the Phase 1 and Phase 3 expected outputs (rows 4, 2, 3, 1 with a stable sort; October rows 2, 3, 1). The expected strings match, including the CRLF and the quoted note.
- Each of the 20 scenarios in `## Acceptance` names a test.
- The Assumptions table answers G-1 to G-9. G-1 and G-7, the two blocking gaps of the vibe test, are each settled by a named test.

## Status of earlier items

| Id | Status | Evidence |
|---|---|---|
| MF-4 | open | Phase 3 now reads `export_expenses(rows, args.month)` with `rows: list[Expense]` (revision 47fcb8118578). Phase 1 still reads "Implement `export_expenses(ledger, month)`", and its tests "take the `sample_ledger` fixture". The shared fixture section says that fixture returns the file path. The Phase 1 signature and its test input are not yet the same type as the Phase 3 call. |
| N-3 | open (note) | Phase 5 still says "in the scratchpad directory". Use `tmp_path`. |
| N-4 | open (note) | `## Reuse and extension` still says `cents / 100:.2f`. The `totals` code uses `{cents / 100:>10.2f}`. The number format matches. Only the wording differs. |
| N-5 | open (note) | Blast radius has no rollback line. |
| N-8 | open (note) | The Constraints now name the 0600 mode of every `--out` file. Phase 4 does not repeat it. |
| N-10 | open (note) | No phase has a commit checkpoint. |
| N-11 | open (note) | `_run_export` still checks the month with `export_expenses([], args.month)`. It works. A named month-check function would be clearer. |
| N-12 | open (note) | Phase 4 `_write_out` still does not say how the descriptor from `mkstemp` closes. Use a `with` block. |

## Must-fix items

- MF-4 (Phase 3; the Phase 1 edit is named by N-13, nit): The first parameter of `export_expenses` has two different types in the plan. Phase 1 writes `export_expenses(ledger, month)`. Phase 3 calls `export_expenses(rows, args.month)` with a list of `Expense` rows. The `sample_ledger` fixture returns a path, so the Phase 1 tests cannot pass it to either form as written. Fix: name one type in the Phase 1 signature, for example `export_expenses(rows: list[Expense], month: str | None)`, and say how the Phase 1 tests get the rows, for example `Ledger(sample_ledger).load()`. The Phase 3 call already matches this type. Keep `test_zero_byte_ledger_still_raises` as written.

Section scope for MF-4: the round rule admits a carried must-fix only in the section it names. The fix edits the Phase 1 signature and its test input. The update must change Phase 1 for this item. Phase 1 is not named in the newest revision row, so the update must cite MF-4 in its ledger for Phase 1.

## Notes

- N-3 (still open, Phase 5): Name the temporary directory as pytest `tmp_path` does, not "the scratchpad directory".
- N-4 (still open, global): Make the `## Reuse and extension` wording match the `totals` format string.
- N-5 (still open, global): Add one line to Blast radius. A revert removes the new module, the new test file and the subcommand.
- N-8 (still open, Phase 4): The Constraints state the 0600 mode. Phase 4 may repeat it in one line. This is optional.
- N-10 (still open, global): Each phase ends at its quality gate only. A commit checkpoint per phase is optional.
- N-11 (still open, Phase 3): Optional. A named month-check function would make the check in `_run_export` clear and reusable.
- N-12 (still open, Phase 4): Say that the descriptor from `tempfile.mkstemp` closes in a `with` block, and that the cleanup runs on an interrupt.

## Next step

The orchestrator runs `/plan-update` on the plan with this round file. The update answers MF-4 with a revision ledger row that names Phase 1. It changes the Phase 1 signature and the Phase 1 test input as the Section scope note says. The plan must reach `READY TO EXECUTE` before E1 starts. Under the cycle limit, the plan maps MF-4 to a phase.
