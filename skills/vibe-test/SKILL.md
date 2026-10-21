---
name: vibe-test
description: >
  Pressure-test a persisted spec before planning. Use for vibe-test, gap
  reports, spec coherence, or Cadence block A5. Write a report first. Do not
  keep the result only in chat.
---

# Vibe test (Cadence A5)

Load `$CADENCE_HOME/references/peer-contract.md`.

## Deliverable

Write `.context/vibetest/{slug}_{YYYY-MM-DD}.md` on the first Write. Use the Write tool,
not a shell command. The hook attests a Write or an Edit. It denies a shell write to a
report (#78). Do not write over a report that exists. When the file exists, write
`{slug}_{YYYY-MM-DD}-rev{N}.md` with the first free `N` from 2. Include:

```
RUN_BY_SKILL: vibe-test
Pipeline-Run: <pipeline_run_id from the active cadence session, when one exists>
```

Refuse free-floating chat as a target: snapshot it to `.context/proposals/` or
stop. Trace realistic scenarios through the spec. Flag gaps, conflicts, and
missing exit criteria. Close with C.O.R.E. pointing at the file. When a Cadence run delegates this peer, replace the C.O.R.E. close with the one-line return in `peer-contract.md`.

## Ticket line

Read the `adapter` key of the resolved config (`python3 "$CADENCE_HOME/scripts/cadence_config.py" resolve`).
That name is the tracker.
Write this header. Use the adapter name on the first line.

```
**Tracker:** <adapter name>
**Ticket:** <ticket id>
```

When the tracker has no ticket, write this ticket line.

```
**Ticket:** TICKET_SKIPPED (reason)
```

The reason names that adapter.
Do not write `**Linear:**` unless the adapter name is `linear`.
Do not write `LINEAR_MCP_UNAVAILABLE`.
Do not call a Linear MCP server unless the adapter name is `linear`.
When the adapter name is `github-issues`, read the ticket with `gh`.

## Phases

1. Classify the target (disk spec, PR+SHA, or snapshot).
2. Stub the report, then fill: scenarios, gaps, conflicts, risks for P1.
3. Mark each gap blocking or non-blocking.
4. List each blocking gap under `## Blocking gaps`, one line each, with an id: `G-1: <the gap>`.
   End the report with `Verdict: READY FOR PLANNING` when it has no blocking gap, else
   `Verdict: NOT READY FOR PLANNING`. A run does not stop on a gap: the plan peer logs an
   assumption for each `G-n` in the register, and P9 fails while a `G-n` has no row.

Cadence gate `scripts/validate_gate.py` looks for this marker under
`vibetest/*.md` or `.context/vibetest/*.md`.
