# Cadence peer contract

Load this before executing any bundled skill or command.

## Home

```bash
SKILL_ROOT="${CADENCE_HOME:-${CADENCE_SKILL_ROOT:-$HOME/.cursor/skills/cadence}}"
```

Procedure lives under `$SKILL_ROOT` only. Do not open a host command tree or the
directory of another skill. Missing peer file is a must-fix (`core:standalone-package`).

## Provenance

Every artifact must include one marker:

| Kind | Marker |
|------|--------|
| Skill | `RUN_BY_SKILL: <name>` |
| Command | `RUN_BY_COMMAND: <name>` |

Write under the workspace `.context/` (or `.cursor/plans/` for plans). Register
the path the matching `scripts/*_gate.py` already expects.
[`docs/peer-output-contract.md`](../docs/peer-output-contract.md) lists each line that a gate reads.

Name the ticket in the file name (`plan_cd-12.md`) or the body (`Ticket: #12`).
P9 only counts planning artifacts that carry a session ticket token as a whole
token, so another ticket's leftovers never clear entry, and `cd-1` never matches
`cd-12`.

## Chat

Human-facing close uses C.O.R.E.: SUMMARY, DECISIONS NEEDED (max 3), ACTIONS
REQUIRED, EVIDENCE. Weigh each verdict by its evidence. Do not approve without evidence.

## Return to the orchestrator

This rule replaces the C.O.R.E. close when a Cadence run delegates the peer to a subagent.
The final message goes into the orchestrator's context. The gate reads the artifact, not the message.
End with one line only:

```text
ARTIFACT: <path>
ARTIFACT: <path> VERDICT: <words>
STOPPED: <reason>
```

Put the summary, the evidence and the open decisions in the artifact.
Do not ask the orchestrator for a decision.
On Claude Code, the stop hook holds a peer once when its final message has a different shape.

## Writing

Agents read Cadence text with no human to ask. Write so that each sentence has one meaning:

- Put the result first. Use short sentences and lists. Do not add a preamble or a closing offer.
- Name the actor: the agent, the gate, the user or a script.
- Use one word for one meaning. A block is a position in `stages.json`, a gate is a script that exits 0 or not, a finding is a row in the findings ledger.
- Keep each hedge that the evidence needs. Do not turn "may have failed" into "failed".
- A peer that a Cadence run delegates replaces the C.O.R.E. close with the one-line return above.

A project that wants a stricter style turns on a writing pack. Core does not lint prose.

## Tickets

Read the `adapter` key of the resolved config (`python3 "$SKILL_ROOT/scripts/cadence_config.py" resolve`).
Use `adapters/<adapter>.json` for the ID regex.
Write `**Tracker:**` with that adapter name.
Write `**Ticket:**` with the ticket id.
When no ticket matches, write `TICKET_SKIPPED (reason)` in the artifact.
The reason names that adapter. Otherwise the ticket id matches the adapter regex.
Do not write `**Linear:**` unless the adapter name is `linear`.
With adapter `none`, write `**Tracker:** none` and `**Ticket:** TICKET_SKIPPED (none: no tracker declared)`.
With adapter `none`, write a new local ticket in `.context/issues/<task_id>.md`.
The `references/activation.md` section "No tracker" gives the file name and the format.
Do not call a host Linear gate.

## Verdict files on Claude Code

Save a verdict file (vibe-test, plan review, adversarial review, benchmark, PR review) with the Write or Edit tool.
The hook records the hash of the content a peer's tool write leaves. A shell write (`>`, `tee`, `mv` of a temp file) has no record, and the gate rejects that file.
If a gate reports an unattested verdict after an Edit, rewrite the whole file with Write.
