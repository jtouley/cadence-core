RUN_BY_COMMAND: plan-review
**Tracker:** none
**Ticket:** TICKET_SKIPPED (none: no tracker declared)
Task: feat-local-4-csv-export
Pipeline-Run: ac8a9620-5ee0-4f36-b0a7-3edee8beda01
Reviewed plan: .context/plans/feat-local-4-csv-export.plan.md (sha256 ccc5013a9e73599548ecacc1a811021d26ae4b1d263d164efc381017c96f6b2b)
Prior review: .context/reviews/plan_feat-local-4-csv-export.md (sha256 7c17b618cb0ef742b6830e654c42d7466db91a39ddd7dce8e8911dfff47bcf3d), last reviewed plan sha256 acc724f1...
Packet sha256: bb7ae62691a422b96d08cffe7624691cf24a1060d2db05a4445c364bed324f08
Last reviewed plan sha256: ccc5013a9e73599548ecacc1a811021d26ae4b1d263d164efc381017c96f6b2b
Verdict: REQUEST CHANGES

# Plan review (round 2): feat-local-4-csv-export

## Summary

The plan update fixed MF-2 and half of MF-1. One must-fix is still open, so the plan is not ready for E1. The other review notes (N-1 to N-5) were not applied and the plan's revision ledger does not mention them.

## Evidence

- The four packet inputs match their recorded sha256 values.
- Repo facts hold: `ledger.load`, `ledger.save`, `ledger.parse_month`, `ledger.in_month` and `cli.main` exist with the signatures the plan uses. `acceptance/check_csv_export.py` exists as an untracked file and runs the CLI in a subprocess.
- `acceptance/check_csv_export.py` `check_ac1` compares parsed rows only (`csv.reader`). It has no byte check for `\r\n`.
- The plan's Phase 1 test list now includes the CRLF test (line 92). Phase 2 now includes the overwrite and no-expenses cases (MF-2, revision ledger line 160).
- `commands/plan-review.md`, the contract named in the packet, is not in this repo. The only copies found are in pytest temp fixtures. This review follows the packet inputs and the prior review's checklist.

## Must-fix items

- MF-1 (partly open): The review asked for two things. (a) A Phase 1 test that each `to_csv` row ends with `\r\n`. Done, at plan line 92. (b) A byte check for `\r\n` in AC-1. Not done. The revision ledger (line 159) says the AC-1 wording "is in a section the review does not name, so it is unchanged." The review did name AC-1, in the acceptance checks table and in the acceptance file. Fix: add to the AC-1 claim in `.context/acceptance/feat-local-4-csv-export.json` and the AC-1 check in `acceptance/check_csv_export.py` a byte assertion that the output contains `\r\n` on every line. Record the change in the revision ledger with the command that proves it.
- MF-2 (closed): Phase 2 now tests overwrite and the no-expenses case. The revision ledger line 160 names the tests.

## Notes (not blocking)

- N-1 (Phase 2, unchanged): `sys.stdout.write` on Windows turns `\n` into `\r\n`, so the CRLF rows become `\r\r\n`. State that the target is POSIX, or write bytes to `sys.stdout.buffer`.
- N-2 (Blast radius, unchanged): No rollback line. Add: rollback is a revert of the commit; the change only adds code.
- N-3 (Blast radius and Phase 3, unchanged): The blast radius table still lists `acceptance/check_csv_export.py` as "New". Phase 3 says it "already exists" and "was written during this plan run". The plan also says the checks are not committed and that the orchestrator commits them. Use one statement.
- N-4 (Deferrals, unchanged): The plan still says "request LOCAL-9" and names LOCAL-8 as the blocker. The register (`.context/deferrals/feat-local-4-csv-export_deferral-register.md`, row 1) has request trace `none`. The issue file (`.context/issues/feat-local-4-csv-export.md`, LOCAL-1) says "request LOCAL-9". Align the trace across the plan, register and issue.
- N-5 (Phase 3, unchanged): No step runs AC-1 to AC-5 before implementation. Add a step that shows each check fails before Phase 1 starts.

## Decision

Verdict: REQUEST CHANGES. The blocking item is MF-1(b): add the `\r\n` byte check to AC-1 in the acceptance file and the script, and record it in the revision ledger. Notes N-1 to N-5 are advisory, but N-3 and N-4 are inconsistencies a reader will hit, so fix them in the same update. The plan was not edited in this run.
