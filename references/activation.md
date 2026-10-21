# Cadence activation

**When to load:** immediately after SKILL.md STOP activate.

## First commands

```bash
SKILL_ROOT="${CADENCE_HOME:-${CADENCE_SKILL_ROOT:-$HOME/.cursor/skills/cadence}}"
python3 "$SKILL_ROOT/scripts/activate_session.py" --from-branch --fresh --supersede
# or resume:
python3 "$SKILL_ROOT/scripts/activate_session.py" --from-branch --resume
python3 "$SKILL_ROOT/scripts/pipeline_status.py"
```

Read script output: `task_id`, `pipeline_run_id`, `resume_block` (a
`stages.json` slug), artifact paths.

## Checklist

1. Pipeline status + checkpoint list
2. A2 preflight (worktree / environment)
3. A3 ops-gate when handoff/context-pack exists — abort code path if ops-only
   (reads `.context/debug/`, `.context/diagnostics/`, and `*handoff*` /
   `*context-pack*` files only. It stops on `Fix type: ops`, an empty
   `Code paths:` line, `CATALOG_STALE`, `OPS_ONLY`, or `OPS HANDOFF`)
4. A4 ticket gate (adapter regex from `adapters/*.json`)
5. Load the registry row for the resume block. Run through R7
6. On exit: `activate_session.py --deactivate`. It writes the manifest `.context/manifests/<task_id>.md` and closes the run only after the `/session-summary <manifest>` peer writes `.context/summaries/<task_id>.md`.

## Session file

`.context/cadence-session.json` (may be a `{"ref": ...}` redirect to a
per-worktree session under `.context/sessions/`).

During a run the hooks deny direct edits to the session files, `.context/gates/`
and the findings ledger. Only their scripts write them.

If the session file is unreadable, or its `ref` points at a missing file, the
hooks fail closed: they deny gated and protected writes, and the stop guard
blocks once, then escalates. Recover with
`python3 "$SKILL_ROOT/scripts/activate_session.py" --from-branch --fresh`, or end
the run with `--deactivate`.

## Orchestrator identity (Claude Code)

The agent that runs activation is the orchestrator of the run. It does not write peer verdict files.

- Run activation through the Bash tool. The shell hook then writes a note with the agent id of the caller.
- The note goes to `sessions/pending-orchestrator/` in the canonical `.context` (the `.context` of the primary checkout).
- The hook writes the note only when a shell segment runs `activate_session.py`. A command that only names the script (`grep`, `cat`, `echo`) writes no note.
- On the first activation in a repo, the hook creates `.context/sessions/pending-orchestrator/`. Activation makes `.context/` too, so the hook adds nothing new.
- Activation takes the newest note of 120 s or less and deletes it. It deletes each note older than 120 s.
- The session records `orchestrator_agent_ids` and `orchestrator_source` (`note`, `toplevel` or `missing`). `--status` shows them.
- The hook denies a verdict write by an agent in `orchestrator_agent_ids` of any run in that `.context`. It also denies a verdict write with no agent id (the top-level agent).
- The gates do not count an attest row by an orchestrator.

**Limits:**

- One note pool serves each `.context`. Two activations in 120 s can take the note of the other. Each run then has the wrong orchestrator id.
- The note goes to the repo that the command activates. That is the `--workspace` directory. Without it, the hook follows the `cd` commands before activation, else it uses the hook cwd. A subagent of Claude Code starts each Bash call in the project of its parent, so use `cd <repo> && python3 .../activate_session.py` or `--workspace <repo>`. The hook cannot read a variable that the same command sets, or `cd -`. Then it uses the hook cwd. The Cursor owner note uses the same target.
- When the top-level agent activates and a subagent orchestrates, the hook does not know the subagent. That subagent can still write verdicts (G-C7).

**Missing note:** when activation finds no fresh note, `orchestrator_source` is `missing`. On Claude Code, activation prints `WARNING: orchestrator unknown: activate through the Bash tool`. The attestation gates then fail closed with the same text. A command that you type with `!` does not go through the hook. To recover, run `--resume` again through the Bash tool.

## Request

`--request <file>` or `--request -` (stdin) gives activation the user's request as the user wrote
it. A fresh run saves it as `.context/proposals/<task_id>.md`, the input of A5, with a `Ticket:`
line. A resumed run keeps the file that exists. An empty request fails activation.

## Resume

`--resume` keeps the run of this worktree when the run has the same `task_id` and is active or hard-stopped:

- It keeps `pipeline_run_id` and `activated_at`. The plan, the reviews and the delegation records of the run stay valid.
- It adds the new agent id to `orchestrator_agent_ids` and sets `resumed_at`.
- It deletes `hard_stop`.

`--resume` reopens a completed run of this worktree with the same `task_id` when the run opened its
draft PR (`.context/prs/draft-pr-<pipeline_run_id>.md`). It keeps `pipeline_run_id`, sets
`reopened_at`, and resumes at P9: deactivation deletes the P9 marker, and P9 writes it again. The
gates then walk to the first open block. A commit after R7 holds R1 until the PR review covers
HEAD, so the next block is `/pr-review-update`.

When no run can resume (no session, a completed run with no draft PR, or another task), `--resume` starts a fresh run. It prints `--resume: no resumable run for <task_id> in this worktree; started a fresh run` and sets `resume_fallback: "fresh"`. `--fresh` always starts a new run id and a new orchestrator list.

## PR guard

The hooks guard `gh pr create` and `gh pr ready` in a repository with a Cadence footprint: `.cadence.json`, a run record, or `.context/attest/`. A `.context/` that another skill made is not enough. The command passes after E3 of this run, after a peer review of HEAD, or with `CADENCE_ALLOW_PR=1` (logged). See `references/execution-protocol.md`.

## Tracker

Ticket binding uses the configured adapter (the `adapter` key of the resolved config →
`adapters/<name>.json`). If the tracker CLI/MCP is unavailable, note
`TICKET_SKIPPED (reason)` in the session summary and continue only when the
user explicitly authorizes untracked work.

### No tracker

Set `"adapter": "none"` in a config layer when the run has no tracker.
This declaration is the explicit approval for untracked work.
A4 then records `TICKET_SKIPPED (none: ...)` in `gates/ticket-gate.json`.
A4 fails when the session has no `task_id`.
Cadence does not find a missing tracker CLI itself. Only the declaration turns on the mode.

Pick the layer by scope:

- The project `.cadence.json`: each run in this repository.
- The project `.cadence.local.json`: each run in this checkout only. Add the file to `.gitignore`.
- `~/.config/cadence/config.json`: each repository that this install runs on.
- `CADENCE_ADAPTER=none`: one shell.

Do not edit `cadence.defaults.json` in the skill root. An update overwrites it.

Each post that would go to the tracker goes to the local file `.context/issues/<task_id>.md`.
The file name uses the normalized `task_id`: A4 changes each `/`, `\` and `_` to `-`.
The normalized `task_id` keeps the case.
A4 writes the path in the `local_tracker` key of its record.
The path is relative to the primary checkout, which holds the primary context root.
It is not relative to the worktree.
The guard hook blocks a write to the worktree copy of `.context/issues/`.

These posts go to the local file:

- a log entry for a gate failure (`SKILL.md`, "Self-healing")
- a new ticket for a finding
- a follow-up ticket for a deferral (P8)
- a comment on the ticket

Use this format:

```markdown
# Local tracker: <task_id>

## LOCAL-1: <title>
Date: <UTC timestamp>. Source: <block, for example P6>.
<body>

## Comment: <block> <UTC timestamp>
<body>
```

Write a new local ticket as a `## LOCAL-<n>: <title>` section.
The number `n` is 1 plus the highest `LOCAL-<n>` in the file. In a new file, `n` is 1.
Write a comment on the ticket as a `## Comment:` section.
Two runs with the same normalized `task_id` use one file.
Add each new section at the end of the file. Do not change a section that exists.

In a report header, write `**Tracker:** none` and `**Ticket:** TICKET_SKIPPED (none: no tracker declared)`.

With a real adapter and a missing tracker CLI, the rule above applies: get explicit user approval first.
