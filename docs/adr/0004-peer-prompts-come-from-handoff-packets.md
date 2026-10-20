---
decision_id: ADR-0004
status: proposed
question: How does Cadence stop old context or planted text from reaching a peer through the router's prompt?
choice: The router writes a handoff packet with scripts/handoff.py. A hook on Agent and Task renders the peer prompt from the packet. It puts each input inside nonce data markers. It denies a peer call that has no packet. The default mode is shadow.
rationale: The hook builds the prompt from hashed inputs, so the router cannot add to it and no judgment about text is needed. Shadow mode comes first. Exact-match tracing flags harmless paraphrase, and the first release must not block normal work.
rejected:
  - alt: Classify every router prompt with a model
    why_not: a classifier reads the attacker's text, adds a model call to each start, and has a false-positive rate that nobody measured for Cadence
  - alt: Check only the artifact scope from #13
    why_not: scope decides which files a gate accepts, not what a peer reads before it starts
  - alt: Enforce from the first release
    why_not: the false-positive reports on similar guards show that enforcement without a measured shadow period blocks legitimate runs
links:
  - rel: implements
    target_type: file
    target_id: scripts/handoff.py
  - rel: implements
    target_type: file
    target_id: hooks/guard_subagent_handoff.py
---
# ADR-0004: Peer prompts come from handoff packets

Ticket: jtouley/cadence#16

## Context

A Cadence peer gets its prompt from the router. The prompt can carry text from a stacked run or text that an attacker planted in a file, a web page, or a tool result. The peer then acts on an instruction that no user gave.

## Decision

- The router writes `.context/handoff/<run-key>/<block>.json` with `scripts/handoff.py`. The skill gate denies any other writer.
- The hook `hooks/guard_subagent_handoff.py` builds the peer prompt from the packet.
- Each input goes inside `<<DATA-nonce>>` markers. The prompt states that the text inside is data.
- The classifier is optional and is a command. Cadence stays stdlib Python.
- The default mode is `shadow`.

## Consequences

- A sha256 proves that an input did not change. It does not prove that the input is safe.
- Cursor parity is not checked. Pi runs `handoff.py check` before each peer call.
- Measured rates are in `bench/results/handoff.json`. They come from small cases.

## Consequences (cd-64)

- Two checks in the same hook deny in every mode: the cd-58 model check and the cd-64 lean check. `shadow` applies to the handoff only.
- The lean check has no `shadow` period. The measured basis: 0 false positives in 452 Agent calls (cd-64 vibe-test r3, G-X12). Cursor already enforces the same lean shape.
- An orchestrator peer call on Claude Code is lean or denied. Thus the trace path (`rewrite_prompt: false`) sees only nested peer calls. jtouley/cadence#73 examines its retirement.
