---
decision_id: ADR-0010
status: accepted
question: Where does a peer handoff get its inputs, and does the handoff enforce by default?
choice: Each peer row of stages.json has an `inputs` list of input kinds. Before the peer call, the hook finds each kind in the run's .context and worktree and writes the packet. The rendered prompt is the slash command with its arguments, then the packet lines. The shipped config sets `subagent_hygiene.mode` to `enforce`. A required input that the hook cannot find is a deny.
rationale: With `shadow` as the default, the orchestrator's prompt went to the peer as typed. The packet had inputs only from a manual `handoff.py write --input` call. No orchestrator made that call, because no row said what the inputs of a peer are. The predecessor harness recorded that a fat prompt poisons a create, review or adversarial peer. The registry already holds the block order, so it is the one place that can hold the inputs too.
rejected:
  - alt: Keep `shadow` as the default and tell the orchestrator to run `handoff.py write`
    why_not: The orchestrator did not run it. A rule that a model must remember is not a gate
  - alt: Write a second table of peer inputs in a script
    why_not: stages.json is the only source of the block order. A second table can go out of step with it
  - alt: Embed the text of each input in the prompt between data markers
    why_not: A plan or a review is larger than the prompt cap. The peer reads the file with its own tools and can check the sha256
links:
  - rel: implements
    target_type: file
    target_id: scripts/handoff_inputs.py
  - rel: implements
    target_type: file
    target_id: hooks/guard_subagent_handoff.py
  - rel: implements
    target_type: file
    target_id: stages.json
  - rel: relates
    target_type: file
    target_id: docs/adr/0004-peer-prompts-come-from-handoff-packets.md
---
# ADR-0010: The registry drives the lean handoff

## Context

ADR-0004 added handoff packets and a hook that renders the peer prompt from a packet. Three facts kept the packet out of use:

1. `subagent_hygiene.mode` was `shadow` by default. In `shadow`, the hook logs and does not rewrite, so the orchestrator's prompt went to the peer as typed.
2. The packet got its inputs only from a manual `python3 scripts/handoff.py write --input <path>` call. The orchestrator did not make that call.
3. No row of `stages.json` said what the inputs of a peer are, so the hook could not write the packet itself.

The predecessor harness recorded a postmortem on prompt poisoning. An orchestrator that adds its own summary, its rules or a return schema to a peer prompt changes the judgment of the peer. A create, review or adversarial peer then follows the orchestrator and not its own command file. The rule is: a peer receives one slash command and the documents that it needs, pinned by sha256, and nothing else.

## Decision

1. Each peer row of `stages.json` (a row whose command starts with `/`) has an `inputs` list. The R4 peer row is `EXTRA_PEER_ROWS` in `scripts/prompt_shape.py`, because the R4 row of `stages.json` is a script.

   | Kind | Source |
   |---|---|
   | `ticket` | the `task_id` of the session |
   | `plan` | the newest plan of the run in `.cursor/plans/` or `.context/plans/`, with the rule of the P3 gate |
   | `plan-review` | the newest plan review of the run in `.context/reviews/` |
   | `plan-review-previous` | the same review, optional: it is present only when a review exists |
   | `adversarial-report` | the newest P6 report of the run |
   | `pr-number` | the PR number in the draft-PR record of the run |

   The rows: A5 and P1 `[ticket]`, P3 `[plan, plan-review-previous]`, P4 `[plan, plan-review]`, P6, P7 and E1 `[plan]`, E4 `[]`, R1, R2 and R4 `[pr-number]`. The P6 row also has `command_args: ["plan-gate"]`, because its peer needs the mode before the plan.
2. Before each peer call, the hook finds the inputs (`scripts/handoff_inputs.py`) and writes the packet. A packet on disk stays only when it has the same command and pins each input at its current sha256. Else the hook writes it again. `handoff.py write` stays as the manual path for Pi and headless hosts. Each `--input` adds an input. It cannot remove an input of the row.
3. The shipped default config (now `cadence.defaults.json`, ADR-0016) sets `subagent_hygiene.mode` to `enforce` and keeps `rewrite_prompt: true`. On Claude Code the hook replaces the prompt with the rendered prompt. With `--host cursor` it denies a prompt that is not the rendered slash command, and the message gives that command. `hooks/hooks-cursor.json` does not register this hook, because `guard_lean_task_prompt.py` owns the Cursor `Task` matcher. On a Cursor install the peer thus gets the slash command as the orchestrator typed it. `shadow` stays available for a first install (`docs/quickstart.md`).
4. The rendered prompt is the slash command with its arguments, then the lines `Block`, `Contract`, one `Input` line with the sha256 of each input, and `Packet sha256`. It holds no input text. Its length is not a fat-prompt sign: the hook renders it. The lean cap applies to the orchestrator's own prompt (PR #186 R1 MF-1).
5. A required kind that the hook cannot find is a deny in `enforce`. The message names the artifact and the block that writes it. A bad row (no `inputs`, or an unknown kind) is a deny too.
6. `references/delegation.md` has the stage-to-prompt table.

## Consequences

- The orchestrator types the slash command. The registry decides the arguments and the inputs. When the orchestrator types another path, the peer still gets the path of the registry.
- On Claude Code the cd-64 lean check runs first and denies a fat orchestrator prompt in each mode. The rewrite then applies to a lean prompt, and to a nested call that the lean check does not see.
- The packet has a new key, `command`. A packet with the old keys is not valid and the hook denies the call. Only a manual `handoff.py write` wrote such a packet.
- The rendered prompt holds no input text, so the data markers are no longer in the prompt. The `SubagentStart` data rule stays.
- The hook now covers `/pr-review-update` (R4). The stop guard treats an R4 peer as a peer.
- The runtime size budget goes from 16,550 to 16,900 (merged with ADR-0011) lines. The tree has 16,766 lines after this change.
