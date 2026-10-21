# Peer output contract

A peer is the slash command or skill that runs at a peer block. The gate of the block does not
read the chat. It reads the artifact that the peer writes. The lines that the gate parses are the
API of core. This page lists each of them, with the code that reads it.

`stages.json` is the block order. This page does not repeat it.

## Bind another peer

`peers.<block>` in a config layer runs another peer at a peer block
([configuration.md](configuration.md)). The value is `/<name>` or `<plugin>:<name>`.
`scripts/load_stages.py:bind_peers` puts the name in the `command` of the row. Only a block whose
`command` starts with `/` takes a binding. The gate of the block does not change.

- When Cadence ships a doc for `/<name>`, `scripts/handoff.py:resolve_contract` gives that doc to
  the peer as its contract.
- A `<plugin>:<name>` peer, or a host command that Cadence does not ship, is `external:<name>`.
  Core gives it no contract file and never reads its text. That peer must follow this page.
- A replacement peer writes the same lines as the bundled peer. The `RUN_BY_*` marker names the
  bundled peer (for example `RUN_BY_SKILL: de-adversarial-reviewer`), not the replacement.

## How to read the tables

- **Artifact path** is a glob under the workspace `.context/`, unless it starts with `.cursor/`
  or names a commit.
- **Line or marker** is the exact form. A regex is in `re` syntax. "Normalized" means that the
  reader removes `**bold**` and `` `backticks` `` first
  (`scripts/artifact_requirements.py:normalize_text_markers`). "Raw" means that it does not, so
  write the line plain.
- **Required?** is `Yes` (the gate fails without it), `If <condition>`, or `No` (the gate reads it
  when it is there).
- **Read by** is `file:function`.

## Shared rules

These rules apply to each peer block. `references/peer-contract.md` tells the bundled peers.

| Artifact path | Line or marker (exact form) | Required? | Read by |
|---|---|---|---|
| Each Markdown artifact | `RUN_BY_COMMAND: <name>` or `RUN_BY_SKILL: <name>`, normalized, anywhere in the text. A JSON artifact has the top-level key `"RUN_BY_SKILL"` or `"RUN_BY_COMMAND"` with the string value `<name>`. | Yes | `scripts/artifact_requirements.py:file_matches_markers` |
| A5, P1, P3, P6 artifacts | A run id line: `Pipeline-Run: <uuid>`, also `Pipeline run:` or `pipeline_run_id:`, with optional list, quote or heading prefix, bold and backticks. The value is a lower-case 36-character UUID. | If the file has no ticket token of the run. A file that names a run id counts only when one of its ids is the id of this run. | `scripts/pipeline_run_binding.py:run_ids_in_text`, `scripts/labeled_lines.py:labeled_values`, `scripts/artifact_requirements.py:run_scope_accept` |
| P1, P3, P6, P7 artifacts | A ticket token of the run in the file name or the text, as a whole token. A number `12` matches `#12`, `gh-12` or `issue-12`. | If the run has a ticket token (P9 counts no other file) | `scripts/artifact_requirements.py:ticket_scope`, `scripts/artifact_requirements.py:check_required_before_execution` |
| `vibetest/*.md`, `reviews/plan_*.md`, `adversarial/*.md`, `benchmarks/*.json`, `prs/reviews/*.md`, `reviews/pr_*.md` | No line. On Claude Code, the peer saves the file with the Write or Edit tool. The gate takes a file only when the hook recorded the sha256 of its content for a peer of the run. | Yes, on Claude Code | `shared/scripts/peer_attest.py:is_peer_artifact`, `scripts/artifact_requirements.py:peer_written_filter` |
| The final message of a delegated peer | One line: `ARTIFACT: <path>`, `ARTIFACT: <path> VERDICT: <words>` or `STOPPED: <reason>`. Regex `^(?:ARTIFACT: \S+(?: VERDICT: [^\r\n]{1,80})?\|STOPPED: [^\r\n]{1,200})$`, at most 300 characters, with no phrase that hands a decision back (for example `decisions needed`, `should i `). | Yes, on Claude Code (the stop hook holds the peer one time) | `scripts/peer_return.py:return_reason`, `hooks/pipeline_stop_guard.py:peer_return_check` |

## A5: /vibe-test

| Artifact path | Line or marker (exact form) | Required? | Read by |
|---|---|---|---|
| `vibetest/*.md` | `RUN_BY_SKILL: vibe-test` (normalized for the artifact check, raw for the header check) | Yes | `scripts/gate_common.py:run_artifact_gate`, `scripts/tracker_header.py:marked_report_header_errors` |
| `vibetest/*.md` | `**Tracker:** <adapter>`, raw, at the start of a line: `(?m)^\*\*Tracker:\*\*[ \t]*(\S+)\s*$`. The value is the name of the resolved `adapter`. | Yes | `scripts/tracker_header.py:tracker_header_problems` |
| `vibetest/*.md` | `**Ticket:** <ticket id>`, raw: `(?m)^\*\*Ticket:\*\*[ \t]*(.+?)\s*$`. The value matches the adapter `ticket_pattern`, or is `TICKET_SKIPPED (<reason>)` and the reason names the adapter. | Yes | `scripts/tracker_header.py:tracker_header_problems`, `scripts/tracker_header.py:_ticket_line_problems` |
| `vibetest/*.md` | No `**Linear:**` line unless the adapter is `linear` | Yes | `scripts/tracker_header.py:tracker_header_problems` |

A5 checks the header of each marked report of the run, not only the newest. A NOT READY report labels its blocking gaps:

| Artifact path | Line or marker (exact form) | Required? | Read by |
|---|---|---|---|
| `vibetest/*.md` | `Verdict: READY FOR PLANNING` or `Verdict: NOT READY FOR PLANNING`, at the start of a line | Yes | `scripts/gate_common.py:_a5_gaps` |
| Same | Under `## Blocking gaps`, one id `G-<n>` for each blocking gap: `G-1: <the gap>` | If the verdict is NOT READY | `scripts/artifact_requirements.py:spec_gap_ids`, `scripts/gate_common.py:_a5_gaps` |
| `deferrals/<task>_deferral-register.md` (the plan peer writes it) | Under `## Assumptions`, a table row whose first cell is `G-<n>`, for each `G-n` of the newest report of the run | If the report names a `G-n` | `scripts/artifact_requirements.py:logged_assumptions`, `scripts/artifact_requirements.py:check_spec_gaps` (P9) |

## P1: /plan-create

| Artifact path | Line or marker (exact form) | Required? | Read by |
|---|---|---|---|
| `plans/*.plan.md` or `<worktree>/.cursor/plans/*.plan.md` | `RUN_BY_COMMAND: plan-create` or `RUN_BY_SKILL: plan-create`. P1 normalizes. E1 reads the raw text, so write it plain. | Yes | `scripts/artifact_requirements.py:check_plan_create_artifacts`, `scripts/gate_common.py:run_plan_create_gate`, `scripts/phase_ledger.py:select_plan` |
| The plan | `### Phase N: <title>`: `^#{2,4}\s+Phase\s+(\d+)\b[ \t]*[:.\-–—]?[ \t]*(.*?)[ \t]*$`. These headings are the E1 task list. | Yes (E1 fails on a plan with none) | `scripts/phase_ledger.py:parse_phases`, `scripts/phase_ledger.py:open_work` |

P3 and P6 name their reports after the plan file (`plan_<basename>.md`, `adversarial_<slug>.md`).
Put the ticket token in the plan file name, so those reports carry it too.

## P3: /plan-review

| Artifact path | Line or marker (exact form) | Required? | Read by |
|---|---|---|---|
| `reviews/plan_*.md` | `RUN_BY_COMMAND: plan-review` | Yes | `scripts/gate_common.py:run_artifact_gate`, `scripts/artifact_requirements.py:check_required_before_execution` |
| `reviews/plan_*.md` | `Verdict: <value>` on its own line (normalized, no list marker): `^\s*Verdict:\s*(READY TO EXECUTE\|READY WITH CHANGES\|NOT READY)\s*$`. A bare value line also counts. The last match wins. | Yes (a review with no verdict does not count in the loop) | `scripts/artifact_requirements.py:verdict_in_text`, `scripts/artifact_requirements.py:select_newest_verdict` |
| `reviews/plan_*.md` | A `Must-fix` heading (`^(#{1,6})\s*must-fix`, any case), for example `## Must-fix items`. Under it, one item for each open id `MF-<n>`. An item line starts with the id, after at most a list, number, heading, table or emphasis marker and two words. With no such heading, each `MF-<n>` in the file is open. | If the review has a must-fix item | `scripts/artifact_requirements.py:review_must_fix_ids` |
| `reviews/plan_*.md` | `Last reviewed plan sha256: <hex>`, normalized: `^\s*Last reviewed plan sha256:\s*([0-9a-fA-F]{12,64})\s*$`. The value is a prefix of the sha256 of the plan. The last line wins. | No (with no line, P9 compares the mtimes) | `scripts/artifact_requirements.py:plan_changed_after_review` |

A `READY WITH CHANGES` or `NOT READY` verdict blocks P9 until the loop ends
(`scripts/artifact_requirements.py:check_plan_review_verdict`).

## P4: /plan-update

| Artifact path | Line or marker (exact form) | Required? | Read by |
|---|---|---|---|
| `plans/*.plan.md` or `<worktree>/.cursor/plans/*.plan.md` (the gate also searches `reviews/plan_*.md`; the peer does not write there) | `RUN_BY_COMMAND: plan-update` or `RUN_BY_SKILL: plan-update` | If a `reviews/plan_*.md` file holds `REQUEST CHANGES`, `READY WITH CHANGES`, `NOT READY` or `on_review_changes` | `scripts/plan_update_gate.py:run`, `scripts/plan_update_gate.py:_reviews_need_update` |
| The plan | `## Revision ledger (review <sha>)`: `^(#{2,3})\s+revision\s+ledger\s*\(\s*review\s+([0-9a-f]{12,64})\s*\)` (any case). `<sha>` is the first 12 or more hex characters of the sha256 of the review file. With two ledgers for one review, the last counts. | If the newest review is blocking, has an open `MF-<n>`, and the run is below `plan_review_cycles` | `scripts/artifact_requirements.py:revision_ledger_ids`, `scripts/artifact_requirements.py:revision_ledger_problems`, `scripts/artifact_requirements.py:plan_review_step` |
| The plan | One table row for each open `MF-<n>` of that review under the ledger heading, up to the next heading of the same or a higher level. The first cell is the id, with an optional `*` or `_` before it: `\| MF-1 \| ... \|`. A `not changed: <reason>` row counts. | Same as the ledger | `scripts/artifact_requirements.py:revision_ledger_ids` |
| The plan | `## Carried review findings` (the line starts with it). Its body names each open `MF-<n>`. | If P3 is at `plan_review_cycles` with `READY WITH CHANGES` | `scripts/artifact_requirements.py:carried_section`, `scripts/artifact_requirements.py:check_plan_review_verdict` |
| The plan | `## Carried adversarial findings (<file> @ <sha12>)`, exactly as `python3 scripts/adversarial_review_gate.py --heading <report>` prints it. Its body names each open `B-<n>` and `C-<n>` of that report. | If P6 is at `adversarial_cycles` with `REQUEST CHANGES`, or before P9 after `APPROVE WITH CONDITIONS` | `scripts/artifact_requirements.py:carried_heading`, `scripts/artifact_requirements.py:carried_body`, `scripts/artifact_requirements.py:check_adversarial_verdict` |

## P6: /de-adversarial-reviewer plan-gate

| Artifact path | Line or marker (exact form) | Required? | Read by |
|---|---|---|---|
| `adversarial/adversarial_<slug>.md` (round 1) or `adversarial/adversarial_<slug>_r<n>.md` (round n). `<slug>` is the plan file name without `.plan.md`. A `_spec-pressure` file never counts. | The file name | Yes (a newer report with another name fails P6) | `scripts/artifact_requirements.py:adversarial_round`, `scripts/artifact_requirements.py:check_adversarial_verdict` |
| Same | `RUN_BY_SKILL: de-adversarial-reviewer` | Yes | `scripts/gate_common.py:run_artifact_gate`, `scripts/artifact_requirements.py:check_required_before_execution` |
| Same | `Mode: plan-gate`, normalized: `^\s*Mode:\s*plan-gate\s*$` | Yes | `scripts/artifact_requirements.py:require_plan_gate_mode` |
| Same | `Verdict: <value>`: `^[ \t]*(?:[#>*-]+[ \t]*)?Verdict[ \t]*:[ \t]*(APPROVE WITH CONDITIONS\|REQUEST[ _]CHANGES\|APPROVE)[ \t]*(?:[—–\-:.(].*)?$`, or the JSON form `"verdict": "<value>"`. The last match wins. | Yes | `scripts/artifact_requirements.py:adversarial_verdict_in_text` |
| Same | `## Blocking issues` with items `### B-<n>. <title>` or `- B-<n>. <title>` | If the report has blocking items | `scripts/artifact_requirements.py:adversarial_ids` |
| Same | `## Conditions` with items `### C-<n>. <title>` or `- C-<n>. <title>` | If the report has conditions | `scripts/artifact_requirements.py:adversarial_ids` |

## P7: /test-benchmark plan

| Artifact path | Line or marker (exact form) | Required? | Read by |
|---|---|---|---|
| `benchmarks/*_plan.json` (`benchmarks/<task_id>_plan.json`) | A JSON object with the top-level key `"RUN_BY_SKILL": "test-benchmark"` | Yes | `scripts/test_benchmark_gate.py:run`, `scripts/artifact_requirements.py:check_required_before_execution` |
| Same | `scores`: an object of numbers. Each layer, DRY and assertion score is 7 or more, and so is the mean | Yes | `scripts/test_benchmark_gate.py:_score_problems` |
| Same | `verdict`: `PASS`. While it is not `PASS`, the P4 packet pins the scorecard (ADR-0020) | Yes | `scripts/test_benchmark_gate.py:_plan_problems`, `scripts/handoff_inputs.py:_plan_benchmark` |

A plan finding gets no findings ledger row. A new plan scorecard that passes closes it.

## E1: /spec-driven

| Artifact path | Line or marker (exact form) | Required? | Read by |
|---|---|---|---|
| A commit since the base | A subject that names a phase: `^(?:\[[^\]]*\]\s*)?(?:(?:fixup\|squash)!\s*)?(?:\w+(?:\([^)]*\))?!?:\s*)?phase[\s_-]*(\d+)\b` (any case), for example `phase N: <title>`, `phase 2: wiring` or `feat(x): phase 2 wiring`. A later revert undoes the phase. | Yes, one for each `### Phase N:` of the plan | `scripts/phase_ledger.py:phase_of`, `scripts/phase_ledger.py:done_phases`, `scripts/implement_gate.py:run` |
| The phase commits | No two phase commits with the same author time (to the second) | If the plan has three or more phases | `scripts/phase_ledger.py:one_shell` |
| `implementation/<task>_stage4.md` | `RUN_BY_COMMAND: spec-driven`, and the final message `ARTIFACT: .context/implementation/<task>_stage4.md` | Yes | `scripts/peer_return.py:return_reason` (the return line) |

The same peer runs each review fix round as `/spec-driven --fix <review>`, with the PR review
`.context/prs/reviews/PR_REVIEW_#<N>.md` as its input. For each open must-fix row of the run, it
commits a red test and a fix, and sets the row to `fixed` with `fixed_in` through
`scripts/findings_io.py`. R4 and R5 read the ledger ([Findings ledger rows](#findings-ledger-rows)).

## E4: /commit-push-pr --draft

| Artifact path | Line or marker (exact form) | Required? | Read by |
|---|---|---|---|
| `prs/draft-pr-<pipeline_run_id>.md` (a legacy `prs/draft-pr.md` counts only when its `Branch:` is the current branch) | The file name | Yes | `scripts/artifact_requirements.py:draft_pr_record`, `scripts/draft_pr_gate.py:run` |
| Same | `RUN_BY_COMMAND: commit-push-pr` | Yes | `scripts/draft_pr_gate.py:run` |
| Same | A PR URL anywhere: `https://github\.com/[\w.-]+/[\w.-]+/pull/(\d+)`. The first match is the PR of the run; R1 and R4 read it. | Yes | `scripts/draft_pr_gate.py:run`, `scripts/artifact_requirements.py:draft_pr_number` |
| Same | `Branch: <branch>`: `^\s*\**Branch:?\**\s*(\S+)\s*$`. The first match equals the current branch. | Yes | `scripts/draft_pr_gate.py:run` |

## R1: /pr-review-local

| Artifact path | Line or marker (exact form) | Required? | Read by |
|---|---|---|---|
| `prs/reviews/*.md` or `reviews/pr_*.md`. When the E4 record names PR `<n>`, only `prs/reviews/PR_REVIEW_#<n>.md` counts. | The file name | Yes | `scripts/final_consolidate.py:run_pr_review`, `scripts/gate_common.py:_r1_review` |
| Same | `RUN_BY_COMMAND: pr-review-local` or `RUN_BY_SKILL: pr-review` | Yes | `scripts/gate_common.py:run_artifact_gate` |
| Same | Each of these headings, normalized: `## Reproductions`, `## End-to-end walk`, `## Adversarial inputs`, `## Contracts`, `## Design choices`, `## Scope`, `## Lookback`, `## Reuse and extension` | Yes | `scripts/artifact_requirements.py:require_sections` |
| Same | `Last reviewed commit: <sha>` in any labeled form (list, quote, bold, backticks), 7 to 40 lower-case hex characters. The last such line is a prefix of HEAD of the run's worktree. | Yes | `scripts/final_consolidate.py:last_reviewed_sha`, `scripts/final_consolidate.py:head_coverage`, `scripts/gate_common.py:_r1_head_problems` |
| Same | A `Must-fix` heading, for example `## Must-fix items`, with one item for each id `MF-<n>` (the P3 item rules). With no such heading, each `MF-<n>` in the file is open. | If the review has a must-fix item | `scripts/artifact_requirements.py:review_must_fix_ids`, `scripts/gate_common.py:_r1_ledger_problems` |
| `findings.jsonl` or `findings/*.jsonl` | A must-fix row `<task prefix>-<run tag>-MF-<n>` for each `MF-<n>` (see [Findings ledger rows](#findings-ledger-rows)) | Yes | `scripts/findings_io.py:ledger_must_fix_ids`, `scripts/findings_io.py:r1_row_id`, `scripts/gate_common.py:_r1_ledger_problems` |
| The review | `Verdict: <value>` in any labeled form. The last one counts. A value that starts with `NO MERGE` or `no-merge` (any case) needs at least one `MF` row of this run. | No | `scripts/labeled_lines.py:labeled_values`, `scripts/gate_common.py:_r1_ledger_problems` |

## R2: /test-benchmark

| Artifact path | Line or marker (exact form) | Required? | Read by |
|---|---|---|---|
| `benchmarks/*_code.json` or `benchmarks/*benchmark*.json` | A JSON object with the top-level key `"RUN_BY_SKILL": "test-benchmark"` | Yes | `scripts/test_benchmark_gate.py:run` |
| Same | `"adversarial_inputs"`: a non-empty list. Each entry is an object with non-empty `"input"`, `"expected"` and `"observed"`. | Yes | `scripts/test_benchmark_gate.py:_adversarial_input_problems` |
| Same | `"scores"`: a non-empty object of numbers. A key that contains `layer`, `dry` or `assert` (any case) is at or above `benchmark_score_floor` (default 7). The mean is at or above the floor. | Yes | `scripts/test_benchmark_gate.py:_score_problems` |
| Same | `"verdict"`: when it is a string, it is `PASS` (any case) | If the key is a string | `scripts/test_benchmark_gate.py:_score_problems` |
| Same | `"findings"`: a list. A finding with `"must_fix": true` (or `1`, `"true"`, `"yes"`) or `"severity"` `must-fix`, `high` or `critical` (any case) has an `"id"` that is the id of a must-fix ledger row. | If a finding is must-fix | `scripts/test_benchmark_gate.py:_is_must_fix`, `scripts/test_benchmark_gate.py:_must_fix_ledger_problems`, `scripts/findings_io.py:ledger_must_fix_ids` |
| Same | `"dry_measurement"`: an object with the lists `helpers_reimplemented`, `helpers_reused` and `thin_files`. A difference from the computed lists is a note only. | No | `scripts/test_benchmark_gate.py:_dry_mismatch` |

## R4: /pr-review-update (supporting command)

R4 is a script block. `peers` does not bind it. Its peer `/pr-review-update` appends to the R1 review, so the R1 lines stay in the file.

| Artifact path | Line or marker (exact form) | Required? | Read by |
|---|---|---|---|
| `prs/reviews/PR_REVIEW_#<n>.md` (the R1 review of the run's PR); with no E4 record, any `prs/reviews/*.md` or `reviews/pr_*.md` | One of `RUN_BY_COMMAND: pr-review-local`, `RUN_BY_COMMAND: pr-review-update`, `RUN_BY_SKILL: pr-review`, raw | Yes (the R1 marker of the file counts) | `scripts/final_consolidate.py:marked_reviews` |
| Same | `Last reviewed commit: <sha>` as the last such line, a prefix of HEAD | Yes | `scripts/final_consolidate.py:head_review_problems`, `scripts/final_consolidate.py:head_coverage`, `hooks/pr_guard.py:_head_reviews` |
| Same | `## Update <date>`: `^##[ \t]+Update\b`. Each section is one fix round. At `review_fix_rounds` (default 2), the gate holds an open loop. | No (counted) | `scripts/final_consolidate.py:fix_rounds`, `scripts/final_consolidate.py:review_rounds` |
| Same | New must-fix items under a `Must-fix` heading, for example `### Must-fix items`, with the next free `MF-<n>` ids | If the update finds a new must-fix | `scripts/artifact_requirements.py:review_must_fix_ids`, `scripts/gate_common.py:_r1_ledger_problems` |
| `findings.jsonl` | A must-fix row for each new `MF-<n>`. R4 fails while a must-fix row of the task is `open`. | If the update has a new `MF-<n>` | `scripts/gate_common.py:_r1_ledger_problems`, `scripts/findings_io.py:is_open_must_fix`, `scripts/final_consolidate.py:consolidate` |

## R7: /session-summary (supporting command)

R7 is a script block. `activate_session.py --deactivate` writes the manifest
`manifests/<task_id>.md` and closes the run only after this peer writes the summary.

| Artifact path | Line or marker (exact form) | Required? | Read by |
|---|---|---|---|
| `summaries/<task_id>.md` | The file, written by a peer with the Write or Edit tool. On Claude Code its current content is the newest content that a peer wrote (attested). | Yes | `scripts/activate_session.py:summary_missing` |
| Same | `RUN_BY_COMMAND: session-summary` | No | None |

## Findings ledger rows

A peer changes the ledger only through `python3 scripts/findings_io.py append` and `set-status`.
The edit hooks protect the file. Only `scripts/ledger_verify.py` writes `verified`.

| Artifact path | Line or marker (exact form) | Required? | Read by |
|---|---|---|---|
| `findings.jsonl` (also `findings/*.jsonl` for R1 and R2) | One JSON object per line with `id`, `stage`, `rule_ref`, `class`, `severity`, `location`, `evidence`, `status` | Yes | `scripts/findings_io.py:validate_finding` |
| Same | `id`: `^[A-Za-z0-9_-]+-(?:F\|MF-)\d+$`. R2 writes `<task prefix>-F<n>`. R1 and R4 write `MF-<n>` with `stage` `pr-review-local` or `pr-review-update`; `append` makes it `<task prefix>-<run tag>-MF-<n>`. | Yes | `scripts/findings_io.py:validate_finding`, `scripts/findings_io.py:_expand_r1_id`, `scripts/findings_io.py:r1_row_id` |
| Same | `class` is `bug`, `spec-gap` or `fast-follow`. `severity` is `must-fix` or `nit`. `status` is `open`, `fixed`, `deferred` or `spec_gap`. `evidence` is not empty. | Yes | `scripts/findings_io.py:validate_finding` |
| Same | `check` on a must-fix row: a command that runs a test runner or a script path, with no shell no-op segment, or `manual` | Yes, for `must-fix` | `scripts/findings_io.py:validate_finding`, `scripts/findings_io.py:check_problem` |
| Same | `fixed_in`: the hex sha (7 to 40 characters) of a commit on the branch | If `status` is `fixed` | `scripts/findings_io.py:validate_write` |
| Same | A must-fix row that is not `verified`, `deferred` or `spec_gap` | Blocks R5 | `scripts/findings_io.py:blocks_closure`, `scripts/closure.py:closure_errors` |

## Lines that no gate reads

The bundled peers also write these lines. They help the next peer or the user. No gate parses them,
so a replacement peer can leave them out:

- `## Reuse and extension` in the plan (P1). P3 reads it as a reviewer.
- `## Status of earlier items` in a delta plan review (P3). Ids in it are not open when the review
  has a `Must-fix` heading.
- `## Plan update <date>` in the plan (P4).
- `.context/implementation/<task>_stage4.md` with `RUN_BY_COMMAND: spec-driven` (E1).
- `PR:` as a label in the draft-PR record (E4). The gate reads the URL, not the label.
- `### Review threads` and `Review threads: unsupported (<reason>).` in a review update (R4).
