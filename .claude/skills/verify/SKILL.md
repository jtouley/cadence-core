---
name: verify
description: Test Cadence at its real surfaces. Run install.sh into a sandbox HOME, drive the installed Cursor hooks, and run every stages.json gate A1→R7 on a toy project.
---

# Verify Cadence

Surfaces are CLIs: `install.sh`, the hook commands merged into `~/.cursor/hooks.json`,
and each `stages.json` `command` / `gate_script`. Never run against the real `$HOME`.

## Sandbox

```bash
SB=$(mktemp -d); H=$SB/home; mkdir -p $H/.cursor/skills $H/.claude/skills
git worktree add --detach $SB/cadence <ref>          # the exact commit under test
(cd $SB/cadence && HOME=$H ./install.sh)            # run twice: must be idempotent
CH=$H/.cursor/skills/cadence
```

## Drive the hooks as Cursor does

Run each `preToolUse` command string from `$H/.cursor/hooks.json` via `sh -c`,
with `HOME=$H`, stdin `{"tool_name":"Write","tool_input":{"path":"src/x.py"}}`.
Expected results:

- A repo that never ran Cadence: allow, and no `.context/` appears.
- An active session before P9: deny on `src/`, allow on `README.md`.
- After P9: allow.

## Drive the pipeline

Toy repo on branch `feature/gh-N-slug` (the canonical shape), then:
`activate_session.py --from-branch --fresh --supersede`, and for each block run the
registered `gate_script` from `stages.json` exactly as written. Play each peer by
writing its artifact exactly where its doc says (`skills/*/SKILL.md`,
`commands/*.md`, `references/deferral-framework.md`) with a `Ticket: #N` line.

## Probes worth repeating

- second ticket on a new branch: P9 must ignore the first ticket's artifacts
- `--resume` on the same ticket. A corrupt `findings.jsonl` makes R6 fail closed.
- an unknown key in the toy repo's `.cadence.json` → A1 and A2 fail and name the file and the key
- a `stages.overlays` entry that removes P9 → the stage order does not load

## Gotchas

- `tests/session/test_load_stages.py` guards the registered command
  lines. If a stage fails with an argparse `usage:` error, that test must fail too.
- P8/P9 read `.context/deferrals/<task>_deferral-register.md` (markdown, per
  `references/deferral-framework.md`). They read no JSON.
- `git push` to a local bare remote prints a harmless proxy "acknowledgments" warning.
