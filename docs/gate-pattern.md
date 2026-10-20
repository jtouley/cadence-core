# The gate pattern: a host-neutral spec

This page tells you what to build to enforce a fixed workflow on a coding agent, on any
host. It names no host paths and no personal skills. Cadence is one implementation. The
last table maps each rule to the Cadence file that implements it.

## The idea

An instruction tells the agent what to do. The agent can ignore it. A gate is a script
that decides from files on disk, and a hook runs the gate before the agent acts. So the
order of the work lives in code that the agent cannot skip:

1. A registry lists the blocks of the workflow in order.
2. Each block has a gate. The gate passes only on evidence that the right writer made.
3. Hooks run before each file write, shell command, peer call and stop. They refuse an
   action that the open block does not allow.
4. A findings ledger holds each review finding. Only a check that passes can close a
   finding.

## Terms

| Term | Meaning |
|---|---|
| Block | One position in the registry, for example "plan review" |
| Gate | A script for one block. It exits 0 (pass) or non-zero (fail) |
| Run | One pass of the workflow for one ticket. It has a unique run id |
| Orchestrator | The agent that drives the run |
| Peer | A separate agent call that does the work of one block, for example a reviewer |
| Artifact | A file that a peer or a script writes for a block |
| Evidence | An artifact that the gate accepts |
| Hook | A host command that runs before or after a tool call or a stop |

## 1. Registry

- R1. One file lists the blocks in order. No other file holds the order.
- R2. Each block has a stable slug, a command (what the agent runs) and a gate (the check).
- R3. A plug-in can add rules to a gate. Config or a plug-in can change the order only through the
  registry loader, by block id. It cannot remove a block whose evidence closes a run, or change that
  block's gate.

## 2. Gate contract

- G1. A gate exits 0 when the work of the block is complete. Any other exit code is a fail.
- G2. A gate fails closed. If it cannot read its evidence, it fails.
- G3. On a fail, the gate prints one line for each reason. Each line names the missing
  evidence or the next action.
- G4. A gate reads files only. It does not ask the agent, and it does not ask a model.
- G5. A gate can run in read-only mode, with no side effects. The stop hook uses this mode.
- G6. The open block is the first block whose gate fails, in registry order. A later gate
  can pass with no work done, so "the last pass plus one" is not the open block.

## 3. Evidence

- V1. Each artifact names its writer (for example `RUN_BY_SKILL: plan-review`) and the run id.
- V2. An artifact that names a different run id is not evidence for this run.
- V3. Some files carry the trust of the run. Each one has one sanctioned writer, and the
  hooks deny a write by anyone else:

  | File | Sanctioned writer |
  |---|---|
  | The session (run id, ticket, phase) | The activation script |
  | Gate markers (for example "the plan gate passed") | The gate scripts |
  | Peer call records | The hook on peer calls |
  | Peer write records (which agent wrote a verdict) | The hook on file writes |
  | The findings ledger | The ledger scripts |

- V4. A verdict counts only when the peer of its block wrote it. The orchestrator cannot
  write its own approval.
- V5. A record that the orchestrator writes about itself is a claim, not evidence. Only a
  hook sees the real tool input, so evidence about a peer call comes from a hook.

## 4. The entry gate

- N1. One gate opens code edits (the entry gate). It passes only when each planning gate passes.
- N2. On a pass, it writes a marker that names the run id.
- N3. The edit hook denies a write under a gated path (for example `src/`) until the marker
  of this run exists. Paths outside the gated prefixes stay open.
- N4. With no active run, the hooks allow everything. The pattern does not change a
  repository that never started a run.

## 5. Findings ledger

- L1. The ledger is an append-only JSON Lines file. Each row is one finding with an id,
  block, rule, class, severity (`must-fix` or `nit`), location, evidence and status.
- L2. The agent changes a status only through the ledger script. The script refuses `verified`.
- L3. Only one script, the ledger checker, writes `verified`. It runs the finding's check command at HEAD, and it
  writes `verified` only when the command exits 0. The ledger script refuses a check that is a shell no-op or
  names no test runner. A `fixed` row names its fix commit, and that commit is HEAD or an ancestor of HEAD.
- L4. A finding with a manual check needs a human confirmation before it becomes `verified`. The confirmation
  names the person. Closure lists each such row, because a hook cannot tell the user from the agent.
- L5. Closure fails while a `must-fix` row of this run has a status other than `verified`,
  `deferred` (with a ticket and a reason it can wait) or `spec_gap`. A row marked `fixed` is not closed.

## 6. Hook points

| Hook point | The hook must |
|---|---|
| Before a file write or edit | Deny a gated path before the entry marker (N3). Deny a write to a file from V3. |
| Before a shell command | Do the same checks for a command that writes a file. Shell parsing finds most writes, not all. |
| Before a peer call | Check that the prompt is the peer's command and its arguments only. Write the peer call record (V3). |
| After a peer call | Record which peer wrote which verdict (V4). |
| Before a stop | Run the gates read-only (G5). Refuse the stop while a block is open, unless the run recorded a hard stop. Refuse a limited number of times, then let the stop through and say why. |
| Session start | Optional. Give the agent the path of the gate scripts. |

- H1. A hook that crashes blocks the action. It does not allow it.
- H2. A hook finds the project from the payload of the host, not from its own working directory.
- H3. A host with no hooks can still run the gates from a loop (G6). The edit and stop
  checks then depend on the agent, so they are conventions, not enforcement.

## 7. How to test an implementation

Each rule has a probe: a replayed agent action with an expected result. A probe sends the
same input that the host sends to the installed hook, then reads the decision.

| Probe | Expected |
|---|---|
| Edit a gated file before the entry gate passes | Deny (N3) |
| Edit the same file after it passes | Allow (N3) |
| Write a gated file through the shell before the entry gate passes | Deny (N3, shell) |
| Write the entry marker directly | Deny (V3) |
| Write a passing verdict for your own work | Deny, or the gate does not count it (V4) |
| Stop while a block is open | Refuse (stop hook) |
| Stop after the run completed | Allow |
| Set a finding to `verified` with the ledger script | Refuse (L2) |
| Set a finding to `fixed` with no fix, then close | Closure fails (L3, L5) |

[`examples/toy-run`](../examples/toy-run/README.md) runs the edit, shell, verdict, ledger,
secret-read and plan probes on Cadence. An external benchmark can run the same hook probes on
several harnesses.

## Cadence implementation

| Rule | Cadence file |
|---|---|
| R1 to R3 | `stages.json`, `packs/`, `scripts/load_stages.py` (`stages.overlays`, `peers`) |
| G1 to G6 | `scripts/*_gate.py`, `scripts/gate_common.py`, `scripts/headless.py` |
| V1, V2 | `scripts/pipeline_run_binding.py`, `scripts/artifact_requirements.py` |
| V3 | `shared/skill_gate.py` (`protected_writer`) |
| V4, V5 | `hooks/guard_subagent_handoff.py`, `hooks/guard_lean_task_prompt.py`, `shared/scripts/peer_attest.py` |
| N1 to N4 | `scripts/entry_invariant.py`, `hooks/guard_skill_gate.py`, `gated_prefixes` of the resolved config (`scripts/cadence_config.py`) |
| L1 to L5 | `scripts/findings_io.py`, `scripts/ledger_verify.py`, `scripts/closure.py` |
| Hook points | `hooks/hooks.json` (Claude Code), `hooks/hooks-cursor.json` (Cursor), `pi/cadence-gates.ts` (Pi) |

Cadence does not meet each rule on each host:

- V4 and V5 hold on Claude Code only. On Cursor, no hook records which agent wrote a verdict.
- The Cursor `stop` hook does not fire in headless mode (`cursor-agent -p`). Then the stop rule
  depends on the outer loop.

Cadence also has two rules that are outside this spec. During a run, `hooks/guard_secret_read.py`
denies a read of a secret file such as `.env`. While the P9 marker exists, the edit guard denies a
write to the plan.
