# Cadence system design

The design of Cadence: a gated pipeline from a plan to a reviewed draft PR. Companion to [`stages.json`](../stages.json) (the core order), [`SKILL.md`](../SKILL.md) (agent contract) and [configuration.md](configuration.md) (config layers).

## Design motives

**Problems to solve**

- Agents stop mid-pipeline to ask permission (“say the word for the draft PR”).
- “Fixed it” claims with no machine-checkable proof.
- Stage numbering drift (`3.5`, `6.65`…) and org rules bolted into core.
- Single-point skills that do not chain into a closable delivery loop.

**Goals**

- Fixed drivetrain: Alignment → Planning → Execution → Review through **R7**.
- Stable slugs in one registry. A pack or a config overlay changes the order of a run by block id only. It cannot remove P9, R1 or R5.
- Append-only findings ledger. Only `ledger_verify.py` writes `verified`, after a pass.
- Fail-closed edits (hooks + CLI) and fail-closed early stop while paceline is open.
- Non-sequential *entry* with fixed *order* once activated (ops abort, resume, full ship).

**Non-goals**

- Not a session-memory wiki.
- Not a DDD modeling suite (no aggregates/context maps as product surface).
- Not org-specific policy in core (use a pack or a config layer).
- Not a runtime dependency outside the Python standard library.

## Phase model (4 phases / 25 blocks)

```text
┌─────────────┐   ┌─────────────┐   ┌─────────────┐   ┌─────────────┐
│  Alignment  │ → │  Planning   │ → │  Execution  │ → │   Review    │
│   A1–A5     │   │   P1–P9     │   │   E1–E4     │   │   R1–R7     │
└─────────────┘   └─────────────┘   └─────────────┘   └─────────────┘
```

| Phase | Blocks | Exit criterion |
|---|---|---|
| I Alignment | A1–A5 | Session bound, ops triaged, ticket attached, assumptions tested |
| II Planning | P1–P9 | Plan reviewed, deferrals registered, **entry invariant passes** |
| III Execution | E1–E4 | TDD impl + artifact + arch conformance + draft PR |
| IV Review | R1–R7 | Must-fix closed, PR comment posted, session deactivated |

Full slug list: see [`stages.json`](../stages.json). Host enforcement and the findings ledger are in [pipeline.md](pipeline.md).

## Artifact continuity

Each block expects a `manifest_type`. Peer skills must run the named command so artifacts carry `RUN_BY_*` provenance. Rubber-stamping verdict files without a peer run is forbidden.

Artifacts live in three places:

- WIP plans under `.context/plans/`
- Shareable plans under `.cursor/plans/`
- Run state under the workspace `.context/`

## Backtrack / recovery matrix

Cadence backtracks on **process** failures (not invariant-expression scores).
[`references/error-recovery.md`](../references/error-recovery.md) is the recovery table. It has one row for each failure that a gate or a hook reports, with the command to run next. It also has the cap of each loop: P3, P6, the R1 to R4 fix rounds and the held stops.

## Packs and adapters

- Core runs with **zero packs** by default (`profile: standard`). `packs` in a config layer turns a pack on.
- A pack (`pack.json`) adds a gate at P9, E3 or R3, and a version 2 pack can change the order with stage operations ([pack-standard.md](../references/pack-standard.md)). A pack from the user or the project dir loads only when its lock pins it.
- `adapters/*.json` bind ticket ID regex / tracker (default GitHub Issues). The `adapter` key of the resolved config picks one.
- `adapters/none.json` is the no-tracker mode (#65). Set `"adapter": "none"` in a config layer. In the project `.cadence.json` it applies to one repository. In `~/.config/cadence/config.json` it applies to each repository that this install runs on. A4 then records `TICKET_SKIPPED (none: ...)`, and each tracker post goes to `.context/issues/<task_id>.md`. Only the config turns on the mode.
- A run records its resolved config: each layer, its path and its sha256 (`python3 scripts/cadence_config.py resolve --json`). A benchmark sets its config in a layer, and the record shows it.

## Host matrix

| Host | Enforcement | Delegation |
|---|---|---|
| Cursor | `hooks.json` preToolUse / beforeReadFile / beforeShellExecution / stop | Parallel Task for P1/P3/P6 |
| Claude Code | `settings.json` PreToolUse / Stop / SubagentStop (`--host claude`) | Subagents |
| Pi | Extension `pi/cadence-gates.ts`: `tool_call` (write, edit, bash) and `agent_before_settle` run the `hooks/hooks.json` hooks (`--host claude`). During a run the extension blocks `powershell`. Fallback: `skill_gate.py` CLI | Sequential in-session |
| Headless (no extension) | Explicit `skill_gate.py` and `headless.py next` | Sequential in-session |

Cursor starts a user-level hook (`~/.cursor/hooks.json`) in a directory that is not the project. The hooks find the project from what the host sends: payload `cwd`, then `CURSOR_PROJECT_DIR`, then payload `workspace_roots[0]`, then `CLAUDE_PROJECT_DIR` (`hooks/hook_protocol.py`, `hook_workspace`). A guard judges each source that is a directory (payload `cwd`, `CURSOR_PROJECT_DIR`, each `workspace_roots` entry, `CLAUDE_PROJECT_DIR`) and the process cwd (`hook_candidates`). It denies when one of them denies. Thus a payload `cwd` outside the project does not hide the project, and each root of a multi-root workspace is judged.

The `stop` hook does not fire in `cursor-agent -p` (headless). In that mode the stop hold is not available. Use `headless.py next` in an outer loop.

## Related

- [Quickstart](quickstart.md)
- [Testing](testing.md)
- [Configuration](configuration.md)
- [references/](../references/) — load-on-demand protocols
