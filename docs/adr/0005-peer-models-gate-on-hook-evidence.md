---
decision_id: ADR-0005
status: accepted
question: How does a Cadence run prove which model each delegated peer used?
choice: The config key peer_models sets a model alias for each delegated peer stage. A1 copies it into the session. The PreToolUse hook on Agent and Task checks each orchestrator peer call, denies another model, no model, or a fork, and writes one row per call to attest/<run>.models.jsonl. The stage gate passes only on an allowed row with the configured model. Claude Code only.
rationale: Only a hook sees the real tool input, and only hooks may write under attest/, so the row is evidence that the orchestrator cannot forge. A fork ignores model, so a fork call cannot prove a model. A snapshot at A1 keeps one run on one set of rules.
rejected:
  - alt: Record the model in the subagent_provenance begin record
    why_not: the orchestrator writes that record itself, so it is a claim, not evidence
  - alt: Gate on the served model in the subagent transcript
    why_not: the transcript format is private to the host and can change; the report shows the served model, and an opt-in gate is tracked in jtouley/cadence#60
  - alt: Read the config at each gate
    why_not: a config edit during a run would change the run's rules after its peers ran
links:
  - rel: implements
    target_type: file
    target_id: scripts/peer_models.py
  - rel: implements
    target_type: file
    target_id: hooks/guard_subagent_handoff.py
---
# ADR-0005: Peer models gate on hook evidence

Ticket: jtouley/cadence#58 (task cd-58)

## Context

A team wants to keep the work of a run on a cheap model and send only the reviews to a stronger one, or to keep every peer on the cheap model with a guarantee that none falls back to a stronger one. Before this decision, every peer ran on the host session's model, and no gate could show which model a peer used.

## Decision

- `peer_models` in the skill-root config file (now any config layer, ADR-0016) maps the delegated peer slugs (`validate`, `plan-create`, `plan-review`, `adversarial-review`, `test-benchmark-plan`, `pr-review-local`, `test-benchmark-pr`) and `default` to `haiku`, `sonnet`, `opus` or `fable`.
- A1 validates the map and copies it into the session for a fresh run. A resumed run keeps its copy.
- The hook checks the orchestrator's peer calls first, in every hook mode, and fails closed.
- The gate reads only the hook's rows. A denied row does not count, so a corrected retry passes.
- `peer_models.py report` joins each row to its subagent through the `toolUseId` in `agent-<id>.meta.json`, and shows the requested and served models side by side.

## Consequences

- A row proves the requested model and the hook decision. It does not prove that the peer that wrote the verdict is the peer of that call (jtouley/cadence#59).
- Cursor and Pi cannot pass a model to a peer. A1 refuses `peer_models` there.

## Update (cd-66)

Ticket: jtouley/cadence#66 (also closes #59)

- The report joins each row to its subagent through the `PostToolUse` join row (`.context/attest/<run>.joins.jsonl`), not through `agent-<id>.meta.json`. The host does not document that file.
- On a `verdict_join` run, the model check reads the call rows (`.context/attest/<run>.calls.jsonl`). An allowed call counts only with a join row for its `tool_use_id`. So P1 needs a plan-create call with a join row.
- The verdict gate checks the model of the call whose join row names the writer of the verdict. So a verdict now proves the stage and the model of its writer.
