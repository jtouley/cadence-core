# Cadence error recovery

**When to load:** after a gate fails, a hook denies a call, or the stop guard holds a stop.

## Classify

| Class | Action |
|-------|--------|
| Recoverable (missing artifact, lean prompt, stale session) | Log → fix → retry same block |
| Scope/test change before P9 | `/plan-update`, then `/plan-review` |
| Scope/test change after P9 | Stop and tell the user. The plan is frozen while the P9 marker exists. A plan change is a new run: `activate_session.py --from-branch --fresh`, then P1 to P9 again |
| Hard stop (destructive risk, missing credentials, security) | Stop. Set the session hard-stop flag |

## Failure table

This table has one row for each failure that a gate or a hook reports.
Find the row by the start of the failure text: the `<slug>:` prefix of a gate line, or the `BLOCKED:` line of a hook.
Run the command in the last column. Then run the gate of the block again.

In the commands, `$C` is `$CADENCE_HOME/scripts`.
`<plan>`, `<review>`, `<PR>`, `<ticket>`, `<id>` and `<base>` are the values that the failure text names.
A slash command goes to the block's peer through the Agent tool, as the lean prompt only (`references/delegation.md`).

### Session and activation (A1, any block)

| Block | Failure text starts with | Cause | Next command |
|---|---|---|---|
| A1 | `activate-session: missing cadence-session.json` | No run exists for this worktree | `python3 "$C/activate_session.py" --from-branch --fresh` |
| Any | `<label>: no active cadence session for this worktree` | The run is in another worktree | `cd` to the worktree that the text names, then run the gate again |
| Any | `<label>: active_skill != 'cadence'` | The run ended, or another skill owns the session | `python3 "$C/activate_session.py" --from-branch --fresh` |
| A1 | `ERROR: Cannot determine task ID` | The branch name has no ticket | `python3 "$C/activate_session.py" --task-id <ticket> --fresh` |
| A1 | `FAIL  config: <error>` | A config layer is not valid: an unknown key, a wrong type, or a file that does not parse. The text names the file and the key | Fix that file, then `python3 "$C/cadence_config.py" check` and `python3 "$C/activate_session.py" --from-branch --fresh` |
| A1 | `FAIL  peer_models: <error>` | The `peer_models` map of the resolved config has an error | Fix the map in the layer that sets it (`python3 "$C/cadence_config.py" resolve` names the layer), then `python3 "$C/activate_session.py" --from-branch --fresh` |
| A1 | `WARNING: orchestrator unknown` or `<slug>: orchestrator unknown` | Activation did not run through the Bash tool | `python3 "$C/activate_session.py" --from-branch --resume`, through the Bash tool |
| A1 | `WARNING: the PostToolUse Agent hook is not registered` | The install has no verdict join hook | `"$CADENCE_HOME/install.sh"`, then start a new host session and `--resume` |
| R7 | `FAIL  the run is open: R6 (pr-comment) has not passed` | `--deactivate` before R6 | `python3 "$C/guard_gh_pr_comment.py" --check`, or `python3 "$C/activate_session.py" --hard-stop <class> --reason "..."` |

### Alignment (A2 to A5)

| Block | Failure text starts with | Cause | Next command |
|---|---|---|---|
| A2 | `preflight: <error>. Fix it (python3 scripts/cadence_config.py resolve shows each layer)` | A config layer is not valid, or a pack that is on does not load: an unknown name, a name in two places, no lock row, or a file that changed after the lock | Fix the layer or the pack. After you check a user or project pack, run `python3 "$C/pack_loader.py" lock` (`--user` for the user dir). Then `python3 "$C/preflight.py" --check` |
| A2 | `preflight: not inside a git repository` | The cwd is not a git checkout | `cd` to the repository checkout, then `python3 "$C/preflight.py" --check` |
| A2 | `peer shadow: /<name> resolves to <path>` | A user entry hides a bundled peer | Remove or rename the entry. On an `install.sh` install, run `"$CADENCE_HOME/install.sh"`. Then start a new host session and `--resume` |
| A2 | `preflight: cannot read the peer registry` | `references/bundled-peers.json` is missing or not valid | `git -C "$CADENCE_HOME" status`, then restore the file and run `"$CADENCE_HOME/install.sh"` |
| A3 | `<file>: empty Code paths` or `<file>: contains '<marker>'` (exit 2) | The handoff is ops-only | Stop the code path. Write notes in `.context/debug/`. `python3 "$C/activate_session.py" --hard-stop not-needed --reason "ops-only"` |
| A4 | `ticket-gate: task_id=<id> and branch=<branch>` | No adapter pattern matches the ticket | `python3 "$C/activate_session.py" --task-id <ticket> --fresh` |
| A4 | `ticket-gate: invalid adapter ticket_pattern`, `configured adapter <name> has no file`, `no adapters configured` or `bad config: <error>` | The adapter config has an error | Fix `adapter` in the config layer that sets it, or `adapters/<name>.json`, then `python3 "$C/ticket_gate.py" --check` |
| A4 | `ticket-gate: adapter none needs a non-empty task_id` | No-tracker mode has no ticket id | `python3 "$C/activate_session.py" --task-id <id> --fresh` |
| A4 | `ticket-gate: the config names the adapter none, but adapters/none.json` | The none adapter file has an error | Restore `adapters/none.json` with `git -C "$CADENCE_HOME" checkout -- adapters/none.json` |
| A5 | `vibe-test: no Cadence run for this worktree` | A5 ran outside the run's worktree | `cd` to the run's worktree, then `python3 "$C/validate_gate.py" --check` |
| A5 | `vibe-test: <report>: **Tracker:** ...` or `**Ticket:** ...` | The report header does not name the adapter or the ticket | `/vibe-test <ticket>`. The peer writes a `-rev{N}` report |

### Peer artifacts (A5, P1, P3, P6, P7, R1, R2)

| Block | Failure text starts with | Cause | Next command |
|---|---|---|---|
| Peer | `<label>: no artifact matching <glob>` | The peer did not write its artifact | The block's slash command. `python3 "$C/headless.py" next --json` gives it in `next.run` |
| Peer | `<label>: no file matched markers` | The artifact has no `RUN_BY_*` marker | The block's slash command again. Do not add the marker by hand |
| Peer | `<label>: no verdict file whose content matches what a delegated peer last wrote` | The orchestrator wrote the file, or a shell wrote it | The block's slash command again. The peer saves with the Write or Edit tool |
| Peer | `<slug>: <writer-stage reason>` | A peer of another stage wrote the artifact | The slash command of the stage that the text names |
| Peer | `<stage>: missing lean delegation record` | The peer call did not go through the hook | The block's slash command as the lean prompt, through the Agent tool |
| Peer | `<stage>: lean prompt rejected` or `delegation record is for <other>` | The peer prompt is not the lean slash command | The block's slash command only, with no other text |
| Peer | `<stage>: on this Claude Code run only the hook writes the lean delegation record` | A script or the orchestrator wrote the record | The block's slash command, through the Agent tool |
| Peer | `<slug>: peer_models sets <model>, but no allowed peer call` | The peer call had no model, or another model | The Agent call with `model: "<model>"` |
| Peer | No artifact and no failure text (the peer returned and wrote nothing) | The peer stopped early | The block's slash command once more. On a second empty return, `python3 "$C/activate_session.py" --hard-stop retries --reason "..."` |

### Planning (P1 to P9)

| Block | Failure text starts with | Cause | Next command |
|---|---|---|---|
| P1 | `plan-create: no artifact matching plans/*.plan.md` | No plan for the run | `/plan-create <ticket>` |
| P3 | `plan-review: <review> verdict is READY WITH CHANGES (cycle N of M)` | The review asks for changes | `/plan-update <plan> --from-review <review>`, then `/plan-review <plan>` |
| P3 | `plan-review: <plan> is newer than <review>` | The plan changed after the review | `/plan-review <plan>` |
| P3 | `plan-review: <review> is NOT READY after N cycles` | The plan is wrong | `python3 "$C/activate_session.py" --hard-stop retries --reason "..."` |
| P3 | `plan-review: review limit reached (N cycles)` | `plan_review_cycles` reviews ran (default 5) | `/plan-update <plan> --from-review <review>` with `## Carried review findings`. Do not review again |
| P3 | `plan-review: no review from a plan-review peer` | The review has no plan-review writer | `/plan-review <plan>` |
| P3, P4 | `plan-review: cannot read <file>` or `plan-update: cannot read <file>` | The plan or the review is not readable | `ls -l .context/plans .context/reviews`, then the block's slash command |
| P4 | `plan-update: <where> for <ids> of <review>` | The Revision ledger has no row for an `MF-n` | `/plan-update <plan> --from-review <review>` |
| P4 | `plan-update: review requests changes but no RUN_BY_COMMAND: plan-update` | No plan update since the review | `/plan-update <plan> --from-review <review>` |
| P4 | `plan-update: <review> verdict is <verdict> (cycle N of M) and the plan` | The plan did not change after the review | `/plan-update <plan> --from-review <review>` |
| P5 | `  must-fix <rule>: <evidence>` (architecture review) | The plan breaks a layer rule | Fix the plan, then `python3 "$C/architecture_review.py" --phase plan --check` |
| E3 | `  must-fix <rule>: <evidence>` (architecture review) | The change breaks a layer rule | Fix the code, then `python3 "$C/architecture_review.py" --phase impl --check` |
| P6 | `adversarial-review: no plan-gate report of run <id>` | No P6 report for this run | `/de-adversarial-reviewer plan-gate <plan>` |
| P6 | `adversarial-review: <report> verdict is REQUEST CHANGES (cycle N of M)` | The report asks for changes | `/plan-update <plan> --from-review <report>`, then `/de-adversarial-reviewer plan-gate <plan>` |
| P6 | `adversarial-review: cycle limit reached` | `adversarial_cycles` P6 cycles ran (default 3) | `/plan-update <plan> --from-review <report>` with each open `B-n` carried. Do not run P6 again |
| P6 | `adversarial-review: <report> does not name the plan` or `has no readable verdict` | The report is not complete | `/de-adversarial-reviewer plan-gate <plan>` |
| P7 | `test-benchmark-plan: no artifact matching benchmarks/*_plan.json` | No plan scorecard | `/test-benchmark plan <plan>` |
| P8 | `deferral-register: <file>: <code>: <message>` | A register row breaks `references/deferral-framework.md` | Fix the row, then `python3 "$C/deferral_adjudication_check.py" --check` |
| P9 | `entry-invariant: ...` with the lines of earlier blocks | An earlier block did not pass | Do the row of each line, then `python3 "$C/entry_invariant.py" --phase execution` |
| P9 | `entry-invariant: no Cadence run in this worktree` | P9 ran outside the run's worktree | `cd` to the run's worktree, then `python3 "$C/entry_invariant.py" --phase execution` |
| P9 | `packs: <error>` or `pack <name> gate failed` | A pack that is on does not load, or a pack gate failed | Fix `packs` in the config layer that sets it, or lock the pack (`python3 "$C/pack_loader.py" lock`). For a pack gate, do what its text says |
| P9 | `registry missing required slug` or `registry missing execution phase` | `stages.json` is not complete | `git -C "$CADENCE_HOME" checkout -- stages.json` |

### Execution (E1 to E4)

| Block | Failure text starts with | Cause | Next command |
|---|---|---|---|
| E1 | `implement-gate: phase N of M (<title>) is not done` | A plan phase has no commit | Send `/spec-driven <plan>` again. On a host with no peers, implement phase N with TDD, then `git commit -m "phase N: <subject>"` |
| E1 | Stop hold `Continue E1: send the /spec-driven peer the plan again` | The E1 peer returned with a plan phase open | Send `/spec-driven <plan>` again |
| E1 | Stop hold `Continue E1: phase N (<label>)` (the E1 peer, or a host with no peers) | A plan phase has no commit | Implement phase N with TDD, then `git commit -m "phase N: <subject>"` |
| E1 | `implement-gate: the phase commits '<a>' and '<b>' have the same author time` | One shell wrote the phase commits, not phase by phase. A rebase keeps the author time, so it does not cause this | `git reset <base>`, then implement, test and commit each phase in its own step. Or stop and tell the user |
| E1 | `implement-gate: the plan <plan> has no ### Phase N: headers` | The plan has no task list, and P9 froze the plan | Stop and tell the user. A plan change is a new run: `python3 "$C/activate_session.py" --from-branch --fresh` |
| E1 | `implement-gate: <marker> missing` | P9 did not pass for this run | `python3 "$C/entry_invariant.py" --phase execution` |
| E1 | `implement-gate: no Cadence run in this worktree` | E1 ran outside the run's worktree | `cd` to the run's worktree, then `python3 "$C/implement_gate.py" --check` |
| E1 | `implement-gate: no base ref resolves` or `no commits since <base>` | No base branch, or no work committed | `git fetch origin`, or commit the work |
| E2 | `impl-artifact: <file> does not exist (run without --check first)` | E2 did not write its record | `python3 "$C/impl_artifact.py"` |
| E2 | `impl-artifact: no base ref found` | No base branch in the clone | `git fetch origin`, then `python3 "$C/impl_artifact.py"` |
| E2 | `impl-artifact: no change since the merge base` or `the recorded diff since the merge base is empty` | No committed work | Commit the E1 work, then `python3 "$C/impl_artifact.py"` |
| E2 | `impl-artifact: missing cadence-session.json` or `missing task_id` | No run for this worktree | `python3 "$C/activate_session.py" --from-branch --resume` |
| E4 | `draft-pr: impl artifact is missing`, `unreadable`, `empty` or `is for run <id>` | The E2 record is old or absent | `python3 "$C/impl_artifact.py"`, then `python3 "$C/draft_pr_gate.py" --check` |
| E4 | `draft-pr: no draft-PR record for this run` | The PR exists but the run has no record | `/commit-push-pr --draft` |
| E4 | `draft-pr: record is for branch <a>, current branch is <b>` | The record is for another branch | `git switch <b>` to the run's branch, then `/commit-push-pr --draft` |
| E4 | `draft-pr: <record> has no PR URL` | The record does not name the PR | `gh pr view --json url`, then `/commit-push-pr --draft` |
| E4 | `draft-pr: <breadcrumb> not found` | The gate ran with `--check` before its first run | `python3 "$C/draft_pr_gate.py"` |
| E4 | `draft-pr: no change since the merge base` or `the session has no task_id` | No committed work, or no ticket in the session | Commit the work, or `python3 "$C/activate_session.py" --from-branch --resume` |
| E4 | `draft-pr: no Cadence run in this worktree` | E4 ran outside the run's worktree | `cd` to the run's worktree, then `python3 "$C/draft_pr_gate.py" --check` |

### Review (R1 to R7)

| Block | Failure text starts with | Cause | Next command |
|---|---|---|---|
| R1 | `pr-review-local: the newest review <file> cites <sha>, HEAD is <head>` | A commit came after the review | `/pr-review-update <PR>` |
| R1 | `pr-review-local: no peer-written review of this run's PR` | No review of the run's PR | `/pr-review-local <PR>` |
| R1 | `pr-review-local: <file>: missing sections [...]` | The review has no `## Reuse and extension` or another section | `/pr-review-local <PR>` |
| R1 | `pr-review-local: no git HEAD in the run's worktree` | The worktree has no commit | `git -C <worktree> log -1`, then commit the work |
| R2 | `test-benchmark-pr: ... must-fix dimensions below <floor>` or `mean score ... is below <floor>` | The tests are weak. The floor is `benchmark_score_floor` (default 7) | Fix the tests, then `/test-benchmark <PR>` |
| R2 | `test-benchmark-pr: ... helpers_reimplemented` or `thin_files` | A test copies a conftest helper, or a test file is thin | Import the helper from `tests/conftest.py`, or merge the file. Then `/test-benchmark <PR>` |
| R2 | `test-benchmark-pr: ... must-fix finding <id> has no must-fix row` | The scorecard names a finding that the ledger does not hold | `python3 "$C/findings_io.py" append --json '{"id": "<id>", ...}'` |
| R2 | `test-benchmark-pr: test shape cannot be measured` | No base ref, or a test file does not parse | `git fetch origin`, then `python3 "$C/test_shape.py" --change` |
| R3 | `cognitive-complexity: ... limit is <max>` | A function is over the complexity limit | Split the function, then `python3 "$C/cognitive_complexity_gate.py" --check` |
| R3 | `cognitive-complexity: ... duplication.max_percent` or `code_shape.max_*` | New duplication or a long function | Reuse the code that exists, then `python3 "$C/cognitive_complexity_gate.py" --check` |
| R3 | `cognitive-complexity: bugs: ...` or `types: ...` | A ruff or mypy finding on an added line | Fix the line, then `python3 "$C/cognitive_complexity_gate.py" --check` |
| R3 | `cognitive-complexity: no base ref found` or `base ref <ref> does not resolve` | No base branch | `git fetch origin`, or set `CADENCE_BASE_REF=<base>` |
| R3 | `cognitive-complexity: bad config: <error>` | A config layer is not valid, for example a measured section | Fix the file and the key that the text names, then `python3 "$C/cognitive_complexity_gate.py" --check` |
| R4 | `final-consolidate: open must-fix <id>` | A must-fix finding is open | Fix it, push, then `/pr-review-update <PR>` |
| R4 | `final-consolidate: no PR review covers HEAD <head>` | A commit came after the review | `/pr-review-update <PR>` |
| R4 | `final-consolidate: the review fix loop is at its budget` | `review_fix_rounds` fix rounds ran and the loop is open | Do not start a fix round. Add the row under `## Review deferrals` in the deferral register with the ticket, run `python3 "$C/deferral_adjudication_check.py"`, then `python3 "$C/findings_io.py" set-status --id <id> --status deferred --ticket <n>` (ADR-0012). Or stop and tell the user |
| R4 | `final-consolidate: bad config: <error>` | A config layer is not valid, for example `review_fix_rounds` is not an integer of at least 1 | Fix the file and the key that the text names, then `python3 "$C/final_consolidate.py" --check` |
| R4 | `final-consolidate: no review of this run's PR` or `has no RUN_BY marker` | No R1 review of the PR | `/pr-review-local <PR>` |
| R4 | `final-consolidate: not peer-written (ignored)` | The orchestrator wrote the review | `/pr-review-update <PR>`. The peer saves with the Write or Edit tool |
| R5 | `closure: <id>: must-fix status='fixed' not terminal` | The fix has no `verified` status | `python3 "$C/ledger_verify.py"` |
| R5 | `closure: <id>: must-fix status='open' not terminal` | The finding is open | Fix it, or defer it: a register row with the ticket, `deferral_adjudication_check.py`, then `python3 "$C/findings_io.py" set-status --id <id> --status deferred --ticket <n>` |
| R6 | `guard-gh-pr-comment: N open must-fix finding(s) block PR comment` | A must-fix is open | Close each finding (R4, R5), then `python3 "$C/guard_gh_pr_comment.py" --check` |
| R6 | `guard-gh-pr-comment: unreadable findings ledger` | A ledger row is not valid | Do not edit the ledger by hand. Tell the user the ledger line that the text names |
| R6 | `gh pr comment` exits non-zero (no gate text) | `gh` is offline or the post failed | `gh auth status`, then `gh pr comment <PR> --body-file <file>` once more. If it fails again, tell the user |

### Hooks

| Hook | Failure text starts with | Cause | Next command |
|---|---|---|---|
| Edit gate | `BLOCKED: the cadence session file is unreadable or points to a missing file` | The session file is not readable | `python3 "$C/activate_session.py" --from-branch --fresh`, or `--deactivate` to end the run |
| Edit gate | `BLOCKED: tool payload is missing` | The host sent no JSON object | Do the same tool call again |
| Edit gate | `BLOCKED: this shell command writes and names protected pipeline state` | A shell write to the session, a gate or the ledger | Use the script: `activate_session.py`, the gate script, `findings_io.py` or `ledger_verify.py` |
| Edit gate | `BLOCKED: only a delegated peer writes <file>` or `this agent ran activation` | The orchestrator wrote a peer artifact | The block's slash command through the Agent tool |
| Edit gate | `BLOCKED: a shell write to <file> carries no attestation` | A peer wrote its report with a shell | Write the report with the Write or Edit tool |
| Edit gate | `BLOCKED: no active cadence session` | A gated write with no run | `python3 "$C/activate_session.py" --from-branch --fresh` |
| Edit gate | `BLOCKED: <file> is pipeline state written only by <writer>` | A direct write to pipeline state | Run `<writer>` |
| Edit gate | `BLOCKED: gated path <file> requires entry invariant` | A code edit before P9 | `python3 "$C/entry_invariant.py" --phase execution` |
| Edit gate | `BLOCKED: the plan is frozen while the P9 marker exists` | A plan edit after P9 | Stop and tell the user. A plan change is a new run: `python3 "$C/activate_session.py" --from-branch --fresh` |
| Secret read | `BLOCKED: <path> matches a secret path` | A read of a secret file | Do not read the file. Ask the user for the value that the task needs |
| Secret read | `BLOCKED: this read or shell call names no path or command` | The call names no target | Do the call again with its path or command |
| Secret read | `BLOCKED: <error>; the secret paths are not known` | A config layer is not valid, for example `secret_paths` is not a list | Fix the file that the text names. `secret_paths` is a list of path patterns |
| Handoff | `BLOCKED: a peer prompt is the lean slash command only` or `unsafe peer handoff` | The peer prompt has other text | The block's slash command only |
| Handoff | `BLOCKED: lean peer prompt rejected: <reason>` | The prompt is not a peer command | The command that the text names |
| Handoff | `BLOCKED: peer_models sets <slug> to <model>` | The Agent call has the wrong model | The Agent call with `model: "<model>"`, not a fork |
| Handoff | `BLOCKED: a peer call may not be a fork` | A fork carries the parent context | A new Agent call that is not a fork |
| Scope | `BLOCKED: the orchestrator does not read or write <path>` | The orchestrator read a run artifact, the code or the diff, or wrote the code (Claude Code) | Run the block's gate, or send its peer the command. E1 is `/spec-driven <plan>`, a fix round is `/spec-driven --fix <review>` |
| Handoff | `BLOCKED: the cadence session file is unreadable, so no handoff packet can be found` | The session file is not readable | `python3 "$C/activate_session.py" --from-branch --fresh` |
| PR | `BLOCKED: gh pr <verb> needs a Cadence run past E1` (or E3) | A PR before the run reached E1 or E3 | Do the next block (`python3 "$C/headless.py" next --json`), or get a peer review of HEAD |
| Stop | `BLOCKED early stop in <phase>. Continue paceline: next <block> (<slug>) via <command>` | The run has an open block | Run `<command>` |
| Stop | `Progress unknown: a gate timed out or could not run` | A gate did not end in time | `python3 "$C/headless.py" next --json` |
| Stop | `stop refused N times — escalate to user` | Three held stops at one block | Tell the user what blocks the run. Do not stop in silence |
| Stop | `FAIL-CLOSED: corrupt session during execution/review` | The session file is not readable | `python3 "$C/activate_session.py" --from-branch --resume` |
| Stop | `BLOCKED: lean peer return: <reason>` | The peer's last message is not one line | End with `ARTIFACT: <path> VERDICT: <verdict>` (`references/peer-contract.md`) |

### Host and tool failures (no gate text)

| Failure | Next command |
|---|---|
| Rebase conflict | `git status`, resolve each file, `git add <file>`, then `GIT_EDITOR=true git rebase --continue`. If you cannot resolve it, `git rebase --abort` and tell the user. After the rebase, `/pr-review-update <PR>` |
| Editor hang (git waits for an editor) | Stop the command. Run it again with `GIT_EDITOR=true`, or with `--no-edit` |
| `/pr-review-update` cannot list the review threads | `python3 "$C/pr_threads.py" <PR>` exits 2 with `unsupported` and the reason. Write `Review threads: unsupported (<reason>).` in the review |
| `gh` is offline or not authenticated | `gh auth status`. If it fails, tell the user. On `adapter: none`, a tracker post goes to `.context/issues/<task_id>.md` |

## Retry budget

Do not infinite-loop. Cap consecutive identical gate failures. Escalate to the
user when the run has no retries (see Self-healing in SKILL.md).

| Loop | Cap | At the cap |
|---|---|---|
| P3 plan review | `plan_review_cycles` (default 5) | `## Carried review findings`, or a hard stop on `NOT READY` |
| P6 adversarial review | `adversarial_cycles` (default 3) | `/plan-update` with each open `B-n` carried |
| R1 to R4 fix rounds | `review_fix_rounds` `## Update` sections (default 2) | R4 holds. Defer each open must-fix with a ticket, or tell the user (ADR-0015) |
| Held stops at one block | 3 refusals | The stop goes through with a note to the user |

## Provenance

Never invent verdict files. Re-run the peer skill/command so artifacts carry
`RUN_BY_*` markers.
