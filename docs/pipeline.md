# Cadence pipeline design

## Intent

Cadence is a **fail-closed drivetrain** for work that goes from a plan to a PR. The agent stays in the paceline through **R7**. Stages are mechanical. Judgment lives in the peer review skills and in how the agent resolves findings.

Phase exits are in [system design](system-design.md). Block order is in [`stages.json`](../stages.json).

## Registry SSOT

`stages.json` is the only source of the core order. Each block has:

- `slug` — stable id (never renumber mid-flight)
- `block` — human label (`A1`, `P3`, …)
- `phase` — alignment | planning | execution | review
- `command` / `gate_script` — how to run and how to verify
- `manifest_type` — artifact kind expected
- `trigger` — `always` or conditional (`on_review_changes`)

Conditional triggers may no-op when not applicable — still record the gate.

A config layer changes the order of a run by block id, never by an edit of `stages.json`.
`stages.overlays` and the `stages` operations of a pack insert, replace or remove a block.
`peers.<block>` runs another peer at a peer block. No overlay can remove P9, R1 or R5, or change
their gate. [configuration.md](configuration.md) has the rules.

## Provenance

Every artifact must carry its `RUN_BY_*` marker. The default peers live in this
repo (`skills/`, `commands/`, listed in `references/bundled-peers.json`).
`install.sh` publishes them. `peers.<block>` in a config layer can bind another peer; it must
write the [peer output contract](peer-output-contract.md). Peers do not synthesize verdict files.
Rubber-stamping without a peer run is forbidden.

## Enforcement matrix (Cursor vs Pi)

| Gate | Cursor | Claude Code | Pi / headless |
|------|--------|-------------|---------------|
| `guard_skill_gate` (`CadenceGate.check_action`) | `preToolUse` (before shadow-context) and `beforeShellExecution` | `PreToolUse` on `Write\|Edit\|MultiEdit\|NotebookEdit\|Bash` | Explicit `shared/skill_gate.py check` |
| `guard_secret_read` | `preToolUse` on `Read`, `beforeReadFile` and `beforeShellExecution` | `PreToolUse` on `Read\|Bash\|Grep` | The extension runs it on `bash`. It does not judge `read` |
| Shadow-context artifact placement | `preToolUse` | `PreToolUse` on edit tools | Same CLI discipline for `.context` writes |
| Stop / subagentStop | Auto hooks (`loop_limit: 3`) | `Stop` / `SubagentStop` | The agent must not exit early. There is no auto-hook |
| Open block | `pipeline_status.py` hint at activation | Same | `scripts/headless.py next --json` (first failing gate) |
| P9 `entry-invariant-<run_id>.ok` | Written by `entry_invariant.py` on success, for the run of its worktree | Same | Same |

Skill-gate runs **before** shadow-context on write tools (`Write`, `StrReplace`, `EditNotebook`, `ApplyPatch`, and on Claude Code `Write`, `Edit`, `MultiEdit`, `NotebookEdit`). Cursor entries are `failClosed: true`. On both hosts, any hook error exits 2, which blocks.

The stop guard finds the open block from evidence. It reads the P9 marker. Then it walks the registered gates from E1 in read-only mode (`CADENCE_GATES_READONLY=1`). The first failing gate is the open block. An orchestrator's subagent stops are pushed back once and never spend the run's three-refusal stop budget. A peer's stop is recorded and allowed (cd-76). Shell commands are gated best-effort.

## Per-run files

Several runs can be active at one time in linked worktrees of one checkout. They share one `.context`. A file that one run writes and a gate reads back has the run id in its name, in the form `<name>-<pipeline_run_id>.<ext>`:

- `gates/entry-invariant-<run_id>.ok`: the P9 marker.
- `prs/draft-pr-<run_id>.md`: the E4 draft-PR record. The agent writes it. A run that started before this rule can use the legacy `prs/draft-pr.md` when its `Branch:` line names the run's branch.
- `gates/draft-pr-<run_id>.json`: the E4 breadcrumb.
- `gates/architecture-review-impl-<run_id>.json`: the E3 breadcrumb.

Each of these readers finds its run from its own worktree (`pipeline_run_binding.run_for`), never from the root redirect. Run P9 and the E2 and E4 gates from the run's worktree.

## Findings ledger

- Append-only findings ledger
- Scripts make each status change. Only `ledger_verify.py` writes `verified`, and only after the finding's command passes
- Closure (`R5`) requires every must-fix finding to be `verified`, `deferred` (with ticket + ripeness), or `spec_gap`

## Backtrack posture

Unlike modeling suites that score invariant expression rates, Cadence backtracks on **process** failures.
[`references/error-recovery.md`](../references/error-recovery.md) has one row for each failure that a gate or a hook reports. Each row names the command to run next. The file also gives the cap of each loop.

## Packs

Core Cadence runs with **zero packs** by default (`profile: standard`). `packs` in a config layer turns a pack on. A pack lives in core (`packs/<name>/`), in the user dir (`~/.config/cadence/packs/<name>/`) or in the project (`.cadence/packs/<name>/`). A user or project pack loads only when its lock pins it. A version 2 pack can add a gate in any language (`gate_command`) and change the order with stage operations. [`references/pack-standard.md`](../references/pack-standard.md) is the contract.

Thirteen blocks belong to six stage packs. With zero packs, a run visits the twelve `core` blocks. `profile: strict` turns on every stage pack, so a run visits all 25 blocks. The `pack` column of `SKILL.md`'s pipeline table names the pack of each block.

## Related

- [System design](system-design.md) — full matrix
- [Quickstart](quickstart.md)
- [SKILL.md](../SKILL.md) — agent contract
- [stages.json](../stages.json) — registry
