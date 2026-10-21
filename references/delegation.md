# Cadence delegation (paceline)

**When to load:** before Cursor Task calls for P1 / P3 / P6 (and review peers).

## Stage-to-prompt table (ADR-0010)

The hook renders this. The orchestrator types the slash command with its arguments and nothing else.

| Block | Rendered slash command | Pinned inputs | Artifact that the peer writes |
|---|---|---|---|
| A5 | `/vibe-test <ticket>` | none | `.context/vibetest/{slug}_{YYYY-MM-DD}.md` |
| P1 | `/plan-create <ticket>` | the request `.context/proposals/{task}.md` and the newest vibe-test report, when they exist | `.cursor/plans/{slug}_{short_id}.plan.md` |
| P3 | `/plan-review <plan>` | the plan and the newest vibe-test report. From cycle two, also the newest plan review | `.context/reviews/plan_{basename}.md` |
| P4 | `/plan-update <plan> --from-review <review>` | the plan, the newest plan review and the newest vibe-test report | the plan, with a dated update section |
| P6 | `/de-adversarial-reviewer plan-gate <plan>` | the plan | `.context/adversarial/adversarial_{slug}.md` (`_r{n}` from round two) |
| P7 | `/test-benchmark plan <plan>` | the plan | `.context/benchmarks/{task_id}_plan.json` |
| E1 | `/spec-driven <plan>` | the plan | the code and the tests, one commit for each phase, and `.context/implementation/{task}_stage4.md` |
| E4 | `/commit-push-pr --draft` | none | the draft PR and `.context/prs/draft-pr-<pipeline_run_id>.md` |
| R1 | `/pr-review-local <N>` | the draft-PR record | `.context/prs/reviews/PR_REVIEW_#{N}.md` |
| R2 | `/test-benchmark <N>` | the draft-PR record | `.context/benchmarks/{task_id}_code.json` |
| R1 to R4 | `/spec-driven --fix <review>` | the PR review `.context/prs/reviews/PR_REVIEW_#{N}.md` | a red test and a fix commit for each open must-fix, its ledger row `fixed`, and a `## Fix round` section in `.context/implementation/{task}_stage4.md` |
| R4 | `/pr-review-update <N>` | the draft-PR record | a `## Update <date>` section in `PR_REVIEW_#{N}.md` |
| R7 | `/session-summary <manifest>` | the manifest `.context/manifests/{task}.md` | `.context/summaries/{task}.md` |

- `<ticket>` is the `task_id` of the session.
- `<plan>` is the newest plan of the run in `.cursor/plans/` or `.context/plans/`. The P3 gate uses the same rule.
- `<review>` is the newest plan review of the run in `.context/reviews/`.
- `<N>` is the PR number in the draft-PR record of the run.
- `<review>` is the PR review of that PR, `.context/prs/reviews/PR_REVIEW_#<N>.md`.
- The `inputs` field of each peer row in `stages.json` gives the input kinds. The R4 peer row and the fix round row (block `RF`) are `EXTRA_PEER_ROWS` in `scripts/prompt_shape.py`.
- The orchestrator reads no input itself. It sends the command and reads the peer's one-line return (ADR-0017).

The hook writes the packet `.context/handoff/<pipeline_run_id>/<block>.json` before the peer call. It writes the packet again when an input changes. The rendered prompt is the slash command, then the packet lines:

```text
/plan-update .cursor/plans/x_ab12.plan.md --from-review .context/reviews/plan_x_ab12.md
Block: P4
Contract: commands/plan-update.md
Input: .cursor/plans/x_ab12.plan.md sha256 <64 hex>
Input: .context/reviews/plan_x_ab12.md sha256 <64 hex>
Packet sha256: <64 hex>
```

- On Claude Code, the hook replaces the orchestrator's prompt with the rendered prompt.
- On a host that cannot change a prompt (`--host cursor`), the hook denies a prompt that is not the rendered slash command. The deny message gives that command.
- The Cursor fragment `hooks/hooks-cursor.json` does not register this hook. On Cursor, `hooks/guard_lean_task_prompt.py` checks the Task prompt, and the peer gets the slash command as the orchestrator typed it.
- A required input that the hook cannot find is a deny. The message names the artifact and the block that writes it. Run that block first.
- On Pi and headless hosts, run `python3 scripts/handoff.py write --block <block>`, then `render`. Each `--input <path>` adds one more pinned input. That is the manual override. It cannot remove an input of the row.

## Cursor

Those slashes resolve to bundled peers under `$CADENCE_HOME/commands/` and
`$CADENCE_HOME/skills/` (see `references/bundled-peers.json`). When a config layer binds
another peer to a block (`peers.<block>`), `scripts/headless.py next` and the handoff packet name
that peer. Send its slash command in the same way.

Delegate **plan-create**, **plan-review**, and **de-adversarial-reviewer** via
**separate Task calls**. Task `prompt=` is slash + args only:

```
/plan-create <ticket-or-path>
/plan-review <plan-path>
/de-adversarial-reviewer plan-gate <plan-path>
```

Optional context pack attaches to plan-create only (`--pack <path>`). Do not
paste skill procedures, hard-rules essays, or "Return to parent" schemas into
the Task string (postmortem: skill_subagent_prompt_poison_overspec).

On Pi and headless hosts, before each peer block:

```bash
python3 "$CADENCE_HOME/scripts/subagent_provenance.py" begin \
  --stage plan-create \
  --command "/plan-create TICKET"
```

After the peer writes its artifact + manifest entry:

```bash
python3 "$CADENCE_HOME/scripts/subagent_provenance.py" complete \
  --stage plan-create \
  --artifact <path> \
  --manifest-id <id>
```

## Claude Code (lean record)

On `claude -p`, set `CLAUDE_AUTO_BACKGROUND_TASKS=false` and `CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=0`, so a peer runs in the foreground and the host waits for it (`docs/quickstart.md`, section 7).
On Claude Code, the `PreToolUse` hook `hooks/guard_subagent_handoff.py` checks each orchestrator Agent call that names a peer command. The command can be at the start of the prompt or inside the text.

- The prompt must be the slash command and its arguments only. Example: `/plan-review .context/plans/x.plan.md`.
- The hook denies a prompt with more text, more lines, or a briefing. The deny message gives the lean command. If you only name a command in text, write it without the leading slash.
- The hook denies a peer call with the subagent type `fork`. A fork carries the whole parent context.
- The hook writes the record `.context/subagent-runs/<pipeline_run_id>/<stage>.json` with the command that the orchestrator sent and `source: PreToolUse`.
- Do not run `begin` on Claude Code. On a run that activation marked (`lean_record_by_hook`), `begin` refuses, and the gates accept only a record with `source: PreToolUse`.
- `complete` is optional. It keeps the hook record and adds the artifact. `check` is not a gate on Claude Code.
- If a lean call does not leave a record, the `PreToolUse` hook did not run. Check that the cadence plugin hooks are on. Do not edit the session or the record.
- `source: PreToolUse` is a field that the writer sets. It is not an attestation. The check stops a careless orchestrator (a prose briefing, a missed `begin`). It does not stop a hostile one.

## Pi / headless

No Cursor Task tool. Run the same blocks **sequentially in-session** in
`stages.json` order. Same artifacts, same provenance markers, same fail-closed
CLI gates (`shared/skill_gate.py check`). Only the delegation host differs.

## Lean prompt gate

Two hooks write the record. On Claude Code, `hooks/guard_subagent_handoff.py` writes it (see "Claude Code (lean record)").
`hooks/guard_lean_task_prompt.py` runs on Cursor `preToolUse` for `Task`
(`failClosed`). While `cadence-session.json` is active it denies multiline
prompts, prompts over 400 characters, parent-briefing phrases, and bundled-peer
prompts that are not slash + path. An allowed peer prompt is stored at
`.context/subagent-runs/<pipeline_run_id>/<stage>.json`.

`run_artifact_gate` and P9 `check_required_before_execution` call
`require_lean_delegation`. The artifact with `RUN_BY_*` is necessary and not
sufficient: the delegation record must exist and pass the lean check again. The command sets
the sidecar file name, not the `stages.json` slug: `/vibe-test` writes
`vibe-test` (slug `validate`). `subagent_provenance.py begin` refuses a
`--stage` that does not match its command. `/test-benchmark plan` writes
`test-benchmark-plan`. `/test-benchmark` writes `test-benchmark-pr`. A record
for one of those commands does not clear the other. `stages.json`
`manifest_type` names the document half. The home `manifest.py` register is an
optional index and is not the gate.

Pi has no Task hook. Record the same lean command with
`subagent_provenance.py begin` before the artifact can clear. The gate reads
that recorded command. It cannot see in-session thoughts.

## Verdict files on Claude Code

Claude Code puts `agent_id` on a tool call only inside a subagent. On a run that
activation marks `host: claude`, the main agent may not write a peer verdict file
(vibe-test, plan reviews, adversarial, benchmarks, PR reviews). A peer writes it
with the Write or Edit tool. The hook records the sha256 of the resulting content
in `.context/attest/`. A gate accepts a verdict file only when its current content
matches a recorded hash. A shell redirect cannot be attested.

## Verdict stage on Claude Code (gh-66)

A hash match proves that a peer wrote the file. It does not prove which stage that peer was for. On Claude Code, two hooks record the stage:

- The `PreToolUse` hook `hooks/guard_subagent_handoff.py` appends one call row to `.context/attest/<run>.calls.jsonl` for each orchestrator peer call: `tool_use_id`, `stage` (the delegation stage of the command), `model_slug`, `model` and `decision` (`allow` or `deny`).
- The `PostToolUse` hook for `Agent|Task` (the same script) appends one join row to `.context/attest/<run>.joins.jsonl`: `tool_use_id`, `agent_id` (the subagent that the call started) and `resolved_model`.

Activation can mark a run `verdict_join`. On that run, a verdict gate finds the writer of the newest attested content of a file. The gate accepts the file only when that writer joins to an allowed call of a stage in `writer_stages`. Each verdict row of `stages.json` has `writer_stages`. A P3 review from a `/plan-update` peer fails P3, and the message names `plan-update`. R1 and R4 accept `pr-review-local` and `pr-review-update`.

- `/pr-review-update` is a lean peer command. On Claude Code the hook denies a fat `/pr-review-update` prompt. Hook change on Cursor: `hooks/guard_lean_task_prompt.py` now records and checks `/pr-review-update` too. Gate results on Cursor do not change.
- A peer that the orchestrator continues with SendMessage keeps the stage of its first call. The join uses the first join row of an agent. For a new review round, start a new peer.
- A gate message names the cause: the writer stage, `no join row`, `no call row for the join`, or `the peer call was denied`. Call the peer again from the orchestrator. Do not edit the verdict file.
- Activation sets `verdict_join` only when the host loads the `PostToolUse` Agent hook: the user settings file, the project settings, or the plugin's own `hooks/hooks.json`. Else the run keeps the old check, and activation writes this warning: `WARNING: the PostToolUse Agent hook is not registered. The verdict stage check is off for this run. ACTION: install.sh install: run install.sh. Plugin or --plugin-dir session: the check is off for this session; do not run install.sh.`
- Known limit: A session that does not load user settings must not run `activate_session.py` on a tree whose join hook is only in user settings. The check is on, but the hook writes no join row, so each verdict fails with `no join row`.
- `.context/attest/<run>.models.jsonl` (cd-58) still exists. To merge it into `calls.jsonl` is #127.

## Peer models (Claude Code)

A config layer can set `peer_models`: a model for each delegated peer stage. Activation copies the map into the session. A config change during a run has no effect on that run.

1. Read `peer_models` from the session: `python3 "$CADENCE_HOME/scripts/activate_session.py" --status`.
2. On each Agent call for a stage in the map, set `model:` to the value for that stage. Example: `model: "opus"` for `plan-review`.
3. Use a subagent type that is not `fork`. A fork ignores `model:` and runs on the model of this session. The hook denies a fork call for a stage in the map.
4. The hook denies a call with no model or with a different model. The deny message names the configured model. Call the peer again with that model.
5. The stage gate passes only when the hook recorded an allowed call with the configured model. A `begin` record is not model evidence.
   On a `verdict_join` run, the allowed call must also have a join row. The writer of the verdict must join to a call with the configured model (#59).
6. To see each call, its requested model and its served model, run `peer_models.py report` (`python3 "$CADENCE_HOME/scripts/peer_models.py" report`).

Other hosts cannot pass a model to a peer. On those hosts, activation stops when the config has `peer_models`.
