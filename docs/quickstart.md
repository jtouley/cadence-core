# Cadence quickstart

Three questions: what is Cadence, where does it run, and how do you take one change from a plan to a PR?

## 1. What is Cadence?

- **Not a chat prompt pack alone** — it is a **fail-closed harness**: registry (`stages.json`), scripts, hooks, and an append-only findings ledger.
- The agent-facing contract is root [`SKILL.md`](../SKILL.md). Procedure detail loads from `references/` and `stages.json` on demand.
- **4 phases**: Alignment (A1–A5) → Planning (P1–P9) → Execution (E1–E4) → Review (R1–R7).
- **12 blocks by default, 25 with every pack on**: a default run visits the twelve `core` blocks. With `profile: strict`, or with all six stage packs in `packs`, a run visits all 25 blocks. `stages.json` holds the order and the pack of each block.

```text
/cadence  →  A1 activate → … → P9 entry-invariant → E1 implement → E4 draft PR → R5 closure → R7 deactivate
```

## 2. Prerequisites

| Need | Description |
|---|---|
| Host | Cursor (hooks), Claude Code, or Pi/headless (CLI gates) |
| Git + `gh` | Branch work + draft PR (`E4` / `R6`) |
| Python 3 | Stdlib-only core. `uv` runs the tests |
| Ticket | Adapter-bound issue (default: GitHub Issues) |

Install on Claude Code as a plugin:

```text
/plugin marketplace add jtouley/cadence-core
/plugin install cadence@cadence-core
```

Install on Cursor or Pi (home only — never writes into project repos):

```bash
git clone https://github.com/jtouley/cadence-core.git ~/Projects/cadence-core
~/Projects/cadence-core/install.sh
```

The installer does three things:

1. It symlinks `~/.cursor/skills/cadence` (and `~/.claude/skills/cadence`).
2. It publishes the bundled peers from [`references/bundled-peers.json`](../references/bundled-peers.json) into `~/.cursor/skills` and `~/.cursor/commands` (and the `~/.claude` equivalents). It does not replace names that you already have.
3. It merges the hooks by command path: `hooks/hooks-cursor.json` into `~/.cursor/hooks.json`, and the hooks of `hooks/hooks.json` into `~/.claude/settings.json`.

R3 measures `bugs` and `types` run two pinned tools, `ruff==0.17.0` and `mypy==2.4.0`.
A gate run never downloads them. It runs them from the `uv` cache. Fill the cache once, with the network:

```bash
uv tool run ruff==0.17.0 --version
uv tool run mypy==2.4.0 --version
```

`./install.sh --tools` runs the same two commands. They put nothing on `PATH`, so your own `ruff` and `mypy` stay.
Without the tools, R3 reports the two measures as `unsupported`, with a `warn` verdict by default.

Use one install path for Claude Code, not both. To move from `install.sh` to the plugin,
run `./install.sh --remove-claude` first. It removes the `~/.claude` links into this
checkout and the Cadence hooks in `~/.claude/settings.json`. It does not change the
Cursor install. It removes only the links into the checkout that you run it from, but it
removes all Cadence hooks in `settings.json`.

The edit guard only enforces while a workspace has an active Cadence session.

### Config

Cadence reads a stack of JSON layers: the core defaults, a profile, your user file
`~/.config/cadence/config.json`, the project `.cadence.json`, a gitignored `.cadence.local.json`
and `CADENCE_*` env vars. Do not edit `cadence.defaults.json` in the checkout. An update overwrites it.
In the project, `python3 <skill root>/scripts/cadence_config.py resolve` prints each value and the layer that set it.
[configuration.md](configuration.md) is the full reference.

### A2 preflight

A2 (`scripts/preflight.py`, in the `preflight` pack) does these checks. Each failure names the command to run next.

- `gh api user` passes. A2 runs `gh` only when `adapter` in the resolved config is `github-issues`. With `adapter: none`, A2 writes a note and does not run `gh`.
- HEAD is on a branch, and that branch is not the default branch. A2 reads the default branch from `origin/HEAD`. With no `origin/HEAD`, `main` and `master` are the default branches.
- `git status` shows no change outside `.context/` and `.cursor/plans/`. The failure lists each path, and untracked files too.
- Each config layer is valid, and each pack that is on loads.
- No user command or skill is a peer shadow (see the next section).

### Peer shadows

A user-level command or skill with the name of a Cadence peer is a peer shadow.
The host runs it in place of the peer. This applies to each install kind.
A2 (`scripts/preflight.py`) fails on a peer shadow and names each entry. `install.sh` prints the same list as a warning.
On Cursor, A2 also looks for peer shadows in the skills directories that Cursor loads for compatibility:
`.agents/skills`, `.claude/skills` and `.codex/skills`, in your home directory and in the workspace.
Cursor does not document which copy wins, so a copy in any of them is a peer shadow.
To clear a peer shadow, remove or rename each listed entry.
On an `install.sh` install, then run `install.sh` of the same checkout again.
On a plugin install, do not run `install.sh`.
Start a new host session before you continue the run.

### Peer handoff mode

The hook `hooks/guard_subagent_handoff.py` gives each peer one slash command and the inputs that the command needs, pinned by sha256.
The core defaults set `subagent_hygiene.mode` to `enforce`.
For a first install, you can set it to `shadow` in a config layer, for example `~/.config/cadence/config.json`:

```json
{"subagent_hygiene": {"mode": "shadow"}}
```

`"profile": "minimal"` sets the same mode and also turns the context budget off.

In `shadow`, the hook does not deny or rewrite a peer call. It writes what `enforce` would do to `.context/handoff/<run-key>/log.jsonl`.
Read that log after one run, then set `enforce` again. The model check and the lean prompt check deny in each mode.
`references/delegation.md` gives the slash command of each peer block.

## 3. First action: activate

In the project you are shipping (not inside the Cadence repo itself):

```bash
SKILL_ROOT="${CADENCE_HOME:-${CADENCE_SKILL_ROOT:-$HOME/.cursor/skills/cadence}}"
python3 "$SKILL_ROOT/scripts/activate_session.py" --from-branch --fresh --supersede
# resume:
python3 "$SKILL_ROOT/scripts/activate_session.py" --from-branch --resume
```

Read the script output (`task_id`, `resume_block`, artifacts) before planning or editing.

## 4. One conversation: ship with `/cadence`

Give the agent a concrete shippable ask (a feature, bugfix, or refactor that must end in a PR).
On the Claude Code plugin the skill is `/cadence:cadence`; with `install.sh` it is `/cadence`:

```text
/cadence
Add greet(name) under src/toy_pkg — keep it boring. Ticket already exists.
Hold the paceline through R7; do not stop after E1 to ask about the draft PR.
```

Expected spine:

1. **A1–A5** — session, preflight, ops triage, ticket, vibe-test  
2. **P1–P9** — plan, reviews, entry invariant (cleared before gated edits)  
3. **E1–E4** — TDD implement → impl artifact → arch review → draft PR  
4. **R1–R7** — PR review → consolidate → closure → PR comment → deactivate  

## 5. Choose entry by scenario

| Scenario | Start | Notes |
|---|---|---|
| New feature end-to-end | A1 → R7 | Full `/cadence` |
| Resume after interrupt | A1 `--resume` | Same `pipeline_run_id` |
| Ops / catalog-stale only | A3 | Aborts code path |
| Existing PR review only | — | Use `/pr-review-local`, not the full Cadence run |

## 6. What you get when you finish

| Phase | Example outputs |
|---|---|
| Alignment | Bound session, ticket, vibe-test notes |
| Planning | `.cursor/plans/*.plan.md`, review artifacts, entry-invariant pass |
| Execution | Commits + draft PR |
| Review | Findings ledger closed, structured PR comment, session deactivated |

A finding is **verified** only when `ledger_verify.py` records a passing check — the model never self-verifies.

## 7. Headless gates (Pi, `claude -p`, CI)

On `claude -p`, set `CLAUDE_AUTO_BACKGROUND_TASKS=false` and `CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=0`.
The host can start a peer Agent call as a background task. The stop guard allows a stop while a peer runs.
A headless session then ends, and the host stops the peer after ten minutes. The two variables keep the peer in the foreground and make the host wait.

Cursor hooks do not fire on Pi. Before every gated edit:

```bash
python3 ~/.cursor/skills/cadence/shared/skill_gate.py state --skill cadence
python3 ~/.cursor/skills/cadence/shared/skill_gate.py check --skill cadence \
  --action strreplace --file "src/thing.py"
```

To find the open block, use `scripts/headless.py`. It runs each registered `gate_script` from `stages.json` in order and stops at the first failure. That failure is the open block. Later gates can pass with nothing done (P4, R3, R6 `--check`), so "last pass + 1" would skip work.

```bash
SKILL_ROOT="${CADENCE_HOME:-$HOME/.cursor/skills/cadence}"
python3 "$SKILL_ROOT/scripts/headless.py" list              # registry rows, runs nothing
python3 "$SKILL_ROOT/scripts/headless.py" next --json       # first open block
python3 "$SKILL_ROOT/scripts/headless.py" run --through P9  # gates A1..P9
```

Exit codes: `0` every gate in range passes, `1` a block is open, `2` usage or registry error. `next --json` returns `{done, next, results[]}`. `next` carries `block`, `slug`, `kind` (`script` or `peer`), `command`, `gate_script`, `run`, `check`, and, for peers, `peer_doc` (the bundled procedure to follow). `run` is the command to run and `check` is the gate command. Both name absolute script paths under the skill root, so they work from the project. `command` and `gate_script` are the registry text, relative to the skill root. Each result has `ok`, `exit_code`, `timed_out` and the last 2000 characters of gate output. `--timeout` sets the limit per gate (default 120 s).

`next` and `run` execute the real gates, so they have the same side effects as running each gate by hand. A gate that is not given `--check` writes a breadcrumb under `.context/gates/`. A1 (`activate_session.py --check`) can create `.context/` in a workspace that never ran Cadence. Use `list` when you only need the registry.

An outer loop does one block per turn and stops when `done` is true. Stop on exit `2` too: it means the registry or the arguments are broken, and another turn cannot fix that. The loop owns the retry budget:

```bash
for turn in $(seq 1 40); do
  python3 "$SKILL_ROOT/scripts/headless.py" next --json > next.json
  case $? in
    0) break ;;                                   # every gate passes
    1) ;;                                         # a block is open: run it
    *) cat next.json >&2; exit 2 ;;               # registry or usage error
  esac
  claude -p "/cadence — run the open block in next.json, then stop: $(cat next.json)"
done
```

The plugin hooks (`hooks/hooks.json`), or the settings hooks that `install.sh` derives from them, make `claude -p` run the same edit, shell and stop guards as an interactive session.

For gate state, `headless.py next` is authoritative. `pipeline_status.py` only gives an artifact-hint resume suggestion at activation.

## 8. FAQ

| Question | Answer |
|---|---|
| Does Cadence generate domain models? | No — that is DDD-skill territory. Cadence ships the change. |
| Can I skip stages? | A conditional trigger can do nothing. The registry still fixes the order. Ops-only work stops at A3. |
| Where is quality proof? | The tests: [testing.md](testing.md). |
| How do I change a limit, a block or a peer? | In a config layer, not in core: [configuration.md](configuration.md). |

## Next

- [System design](system-design.md) — phases, provenance, backtrack matrix  
- [Pipeline notes](pipeline.md) — Host enforcement matrix  
- [SKILL.md](../SKILL.md) — agent contract  
