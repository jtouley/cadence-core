---
decision_id: ADR-0001
status: proposed
question: How should Cadence hooks detect shell commands that write gated sources or pipeline state?
choice: A stdlib tokenizer that extracts write targets (redirects, tee, sed/perl -i, cp/mv/install/ln, rm/unlink/shred/truncate/touch, dd of=), plus a backstop that denies when the raw command names a protected path and contains a write construct
rationale: Agents can bypass a Write/Edit-only gate with one shell command, and the R1 review of PR #10 (ticket cd-10) showed a tokenizer alone misses redirect fd numbers, mv sources, cd and unexpanded variables; a name-plus-write backstop closes those cheaply while keeping read-only commands and the sanctioned scripts allowed
rejected:
  - alt: Full shell AST (bashlex)
    why_not: third-party dependency breaks the stdlib-only core rule
  - alt: Deny any command that mentions .context
    why_not: false positives on reads and on the sanctioned findings_io and ledger_verify commands would block every review stage
  - alt: Leave shell commands ungated
    why_not: one sed -i or echo > bypasses the plan gate and the protected ledger
links:
  - rel: implements
    target_type: file
    target_id: shared/skill_gate.py
  - rel: implements
    target_type: file
    target_id: hooks/guard_skill_gate.py
---
# ADR-0001: Shell writes are detected best-effort, with a protected-path backstop

Ticket: cd-10 (jtouley/cadence#10)

## Context

Cadence's edit gate and its protected pipeline state (session, gate markers,
findings ledger) were enforced only on file-edit tools. Any shell command could
write the same files. PR #10 added shell gating; its R1 review reproduced four
bypasses of the first version (`2>/dev/null`, `mv` sources, `cd .context`,
`"$PWD/..."`).

## Decision

Parse commands with `shlex` into simple commands and collect the paths each
writes. Then apply a backstop: if the raw command text names a protected path
(`cadence-session.json`, `.context/sessions/`, `.context/gates/`,
`findings*.jsonl`) and contains any write construct, deny it. The backstop also
treats `cd` into `.context` as a write construct.

## Consequences

- Known false positive: copying a protected file elsewhere with a redirect
  (`cat .context/findings.jsonl > /tmp/x`) is denied. Reads without a write
  construct are allowed.
- Known false negative: writes made inside an interpreter (`python -c`, a script
  file) or through aliases and shell functions are not visible. The edit-tool
  gate, P9, and closure still apply.
