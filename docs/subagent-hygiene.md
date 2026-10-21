# Subagent handoff packets and poisoned-prompt checks

A Cadence peer gets its prompt from the routing agent. That prompt can carry old text from a stacked run. It can also carry text that an attacker put into a file, a web page, or a tool result. This feature gives each peer a clean prompt that Cadence can audit.

The settings are the `subagent_hygiene` key of the resolved config ([configuration.md](configuration.md)). The default mode is `enforce` with `rewrite_prompt: true`. `docs/quickstart.md` tells how to set `shadow` for a first install. Shadow mode logs and never blocks.

## How it works

1. **Packet.** The hook `hooks/guard_subagent_handoff.py` runs before each `Agent` or `Task` call. For a Cadence peer call, it reads the `inputs` of the peer row in `stages.json`. It finds each input in the run's `.context` and worktree (`scripts/handoff_inputs.py`). It writes `.context/handoff/<run-key>/<block>.json`. The packet holds the block, the slash command with its arguments and the contract path. It also holds each input path with its sha256, the ticket, the run key and a nonce. It holds no free text. A required input that the hook cannot find is a deny that names the artifact and the block that writes it.
2. **Rewrite.** The hook builds the prompt from the packet. The prompt is the slash command, then one line for each packet field. The fields are the block, the contract, each input with its sha256, and the packet sha256. The router text does not reach the peer. `references/delegation.md` has the table of each rendered command.
3. **Data rule.** The prompt holds no input text. The peer reads each input with its own tools. A hook on `SubagentStart` tells every subagent that text between data markers is data, never an instruction.
4. **Provenance trace.** Set `rewrite_prompt` to `false` to let the router write its own prompt. The hook splits the prompt into sentences. It looks for each sentence in the allowed sources: the peer contract, the packet inputs, the packet command line, and the user's own messages. The hook calls a sentence with no source untraced. It reports the sentence and the reason.
5. **Classifier (optional).** If you set `classifier` to a command, the hook sends the untraced text to it. In `enforce` mode, untraced text blocks the call only when the score is at or above `classifier_threshold`.

## Modes and settings

| Key | Values | Meaning |
|---|---|---|
| `mode` | `off`, `shadow`, `enforce` | The mode applies to the handoff only. `shadow` writes `.context/handoff/<run-key>/log.jsonl` and the handoff never denies. `enforce` denies or rewrites. The model check and the lean check deny in every mode. |
| `rewrite_prompt` | `true`, `false` | `true` uses the packet rendering as the peer prompt. `false` uses the provenance trace. |
| `classifier` | `none` or a command | The hook runs the command with the text on stdin. The command prints `{"probability": 0.0-1.0}`. `none` starts no process and makes no network call. |
| `classifier_threshold` | number | Default 0.5. |
| `classifier_timeout` | seconds | Default 10. |
| `on_classifier_error` | `allow`, `deny` | Default `allow` in `shadow` and `deny` in `enforce`. |

`install.sh` never installs a classifier and never asks for an API key. A classifier is opt-in: set `classifier` in a config layer.

## What the hook covers

- Covered: the tools `Agent` and `Task`, when a line of the prompt starts with a peer command from `stages.json` (for example `/plan-review`). A command inside a sentence does not count.
- Not covered: an `Agent` call that does not start with a peer command. It gets only the `SubagentStart` rule.
- Not covered: any other tool that starts an agent, and the router's own context.
- If the session file exists but the hook cannot read it, `enforce` mode denies peer calls and tells the router to activate again.
- Only `scripts/handoff.py` writes packets. The skill gate denies a direct write to `.context/handoff/`.
- Claude Code gets the rewrite through `updatedInput`. Cursor parity is not checked. For Cursor, the hook denies the call and puts the rendered slash command in the message. The router sends that command, and the hook lets it pass.
- Pi has no hooks. Run `python3 scripts/handoff.py write --block P3`, then `check` and `render` before the peer call. Each `--input <path>` adds one more pinned input (the manual override).

## Lean returns, the orchestrator scope and the context budget

The checks above control what goes into a peer. These three checks control what goes into the orchestrator.

**Lean return.** A peer's final message goes into the orchestrator's context.
The gates read the artifact on disk, so the orchestrator needs only a pointer.
On Claude Code, `hooks/pipeline_stop_guard.py` checks each `SubagentStop` of a delegated peer:

- The peer is a subagent of an active run that is not an orchestrator. Its first prompt is a peer command or a packet rendering.
- The final message is one line of at most 300 characters: `ARTIFACT: <path>`, `ARTIFACT: <path> VERDICT: <words>`, or `STOPPED: <reason>`.
- The final message does not hand work back. Examples: `DECISIONS NEEDED`, `run it yourself`, `confirm the`, and the briefing phrases of the prompt check.
- A wrong return is held once. The hold reason gives the lean shapes. A second wrong return stops with a notice to the user.
- The hook reads the message from `last_assistant_message`, else from `agent_transcript_path`. When it cannot read the message, it does not hold the peer.
- Each check writes one row to `.context/attest/<run>.returns.jsonl`: `ok`, `hold`, `violation` or `unknown`.
- `SubagentStart` adds the return rule to each subagent.
- `PostToolUse` on `Agent|Task` writes one join row to `.context/attest/<run>.joins.jsonl`: the call's `tool_use_id` and the `agentId` of the subagent it started. The verdict gates use it to know the stage of the peer that wrote a file. It runs no other check and prints nothing.

**Orchestrator scope.** The orchestrator routes: it runs the gates, sends each peer its command,
and reads the one-line returns. On Claude Code, during an active run, the read guard
(`hooks/guard_secret_read.py`) and the edit guard (`hooks/guard_skill_gate.py`) deny an orchestrator call that does a
peer's work (`shared/scripts/orchestrator_scope.py`):

- a `Read`, `Grep` or shell read of a run artifact: a plan, a proposal, a plan or PR review, an
  adversarial report, a benchmark, a vibe-test report, or `.context/implementation/`.
- a read of the code: a path under `gated_prefixes`, or a test file.
- a `git diff` or `git show` that prints the change. `--stat` and the name forms pass.
- a write to the code. E1 is the `/spec-driven <plan>` peer, and each review fix round is the
  `/spec-driven --fix <review>` peer.

The orchestrator is the top-level agent (no `agent_id`) or an agent that ran activation. A peer is
never judged. Session state, gate records and the findings ledger stay readable: the scripts that
the orchestrator runs read them. Set `orchestrator_scope.mode` to `off` to turn the check off.

**Context budget.** Before each orchestrator `Agent` or `Task` call during an active run, `hooks/guard_subagent_handoff.py` reads the size of the orchestrator's last request.
The size is input plus cache read plus cache write tokens from the host transcript.
The hook writes one row to `.context/attest/<run>.context.jsonl`.

| Key in `context_budget` | Values | Meaning |
|---|---|---|
| `mode` | `off`, `shadow` | `shadow` (the default) writes the row, and a notice above `warn_tokens`. |
| `warn_tokens` | integer | Default 60000. Above this size, the hook writes a notice. |

The budget never denies a call. The call it would deny hands work to a peer, and handing work to
peers is what keeps the orchestrator small. A routing orchestrator starts at the host's own prompt
plus the skill, and each block adds a gate result and a one-line return. A notice means the
orchestrator read files that a peer reads.
A bad `context_budget` value makes the hook fail closed.

## Peer models

The key `peer_models` of the resolved config sets the model of each delegated peer stage. Only Claude Code can pass a model to a peer. On another host, activation stops when the config has `peer_models`.

| Key | Value | Meaning |
|---|---|---|
| `validate`, `plan-create`, `plan-review`, `adversarial-review`, `test-benchmark-plan`, `pr-review-local`, `test-benchmark-pr` | `haiku`, `sonnet`, `opus`, `fable` | The model of that peer stage. |
| `default` | the same aliases | The model of each stage in the row above that has no entry of its own. It never applies to `/spec-driven`, `/commit-push-pr` or `/plan-update`. |

Example: `"peer_models": {"default": "haiku", "plan-review": "opus", "pr-review-local": "opus"}`.

- The hook `hooks/guard_subagent_handoff.py` checks each peer call before the handoff checks, in every mode. It denies a call with no model, with another model, or with the subagent type `fork`. It writes one row for each call to `.context/attest/<run>.models.jsonl`.
- The stage gate passes only when the run has an allowed row with the configured model.
- Scope: any config layer can set `peer_models`. Put it in the user layer (`~/.config/cadence/config.json`, or `$CADENCE_CONFIG_HOME/config.json` when that variable is set) for each project, or in the project `.cadence.json` or `.cadence.local.json` for one repo. A1 copies the resolved map into the session.
- Rollback: remove `peer_models` from the layer that sets it (`python3 scripts/cadence_config.py resolve` names the layer), then start a run with `--fresh`. A run in progress keeps its copy of the map until then.

## Limits

- A sha256 proves that a file did not change. It does not prove that the file is safe. Only a classifier reads the text of an input, and a classifier can be attacked too.
- The provenance trace matches exact words. It cannot see an attack that the router copied from an allowed source. It can flag a harmless paraphrase.
- The hook controls what Cadence hands to a peer. It cannot clean the router's own context. The lean return check and the orchestrator scope limit what goes into it.
- The lean return check, the orchestrator scope and the context budget work on Claude Code only. On Cursor the budget writes rows with no size. Pi has no hooks and no peers, so it writes no rows and its main agent does every block. Neither host checks returns.
- The scope check judges the paths that a call names, as the secret read guard does. It does not expand a glob, and it does not look into a directory that `grep -r` searches.
- A classifier adds a model call to each peer start. Set `classifier_timeout` to allow for it.

## Measurement

The packet checks live in `tests/hooks/test_handoff.py`. The rates below come from six attack cases and five clean cases.

| Layer | Attacks stopped | Attacks denied | False positives |
|---|---|---|---|
| rewrite | 6 of 6 | 0 of 6 | 0 of 5 |
| trace | 5 of 6 | 4 of 6 | 2 of 5 |
| trace with a classifier stand-in | 4 of 6 | 3 of 6 | 0 of 5 |

"Stopped" means the hook denied the call, or the attack text reaches the peer only inside the data markers (or not at all). The classifier row uses a keyword stand-in, not a model. Measure a real classifier before you trust it. The probes use small cases. Do not treat the rates as general accuracy.
