# Toy run

One ticket goes through the real Cadence gates and hooks on a toy repository. No model runs,
and the run needs no network. It takes about 15 seconds.

```bash
python3 examples/toy-run/run.py
```

The output is the same text on each run. [`transcript.txt`](transcript.txt) is that output.
A test compares the two, so the transcript stays current with the gates.

## What the run shows

The ticket asks for `subtract()` in the toy package in
[`tests/fixtures/toy_repo`](../../tests/fixtures/toy_repo). The script copies it into a
temporary directory with its own `HOME`, then does these steps:

1. A1 starts a session on the branch `feature/gh-12-add-subtract`.
2. The edit guard refuses an edit and a shell write to `src/` before the plan passed review.
   It lets the edit to `README.md` through. The secret-read guard refuses a read of `.env`.
3. `scripts/headless.py next` names each open block and the reason that its gate fails.
   The script plays the peer of the block, and the gate passes. A default run visits the
   twelve core blocks, so the walk goes A1, P1, P3, P4, P9, E1.
4. At P3, the orchestrator writes the plan review itself, with no peer call. The gate
   fails: no hook recorded a call to the peer. After the call, the gate takes the same file.
   On Cursor, no hook records which agent wrote a verdict (V4 in
   [`docs/gate-pattern.md`](../../docs/gate-pattern.md)).
5. After P9, the edit guard lets the same edit to `src/` through.
6. The walk stops at E4, because E4 needs a draft PR on GitHub. After P9, the edit guard
   refuses a write to the plan.
7. Then the script runs the findings ledger of R4 and R5 on its own:
   - `findings_io.py` refuses a check of `true`, because a shell no-op proves nothing.
   - `findings_io.py` refuses `verified`, and `fixed` with no `fixed_in` commit.
   - `ledger_verify.py` keeps a `fixed` row with no fix, because its check fails.
   - Closure fails until the agent adds the test and the check passes.
8. The last part runs P3 again with the Claude Code hooks. The write hook refuses the
   orchestrator's review. After the peer call, the gate still refuses a copy of the review
   that no peer wrote. The gate passes when the peer writes it.

## How the script plays a peer

For each peer, the script does what a host and a peer do:

1. It sends the lean slash command through the host hook that records the call:
   `hooks/guard_lean_task_prompt.py` as Cursor runs it, and `hooks/guard_subagent_handoff.py`
   as Claude Code runs it in the last part.
2. It writes the artifact that the peer doc names, with the run id.

Each verdict comes from a gate in [`stages.json`](../../stages.json) or a hook in
[`hooks/`](../../hooks). The script writes no verdict. The artifacts are small, and no model
wrote them. Thus the run shows what Cadence enforces. It does not show how good a real
plan or review is.

## Files

| File | What it is |
|---|---|
| `run.py` | The script. Python standard library only. `--keep DIR` keeps the sandbox for you to examine. |
| `transcript.txt` | The output of `run.py`. Paths and the run id show as `<sandbox>`, `<cadence>` and `<run>`. |
| `render_demo.py` | Builds the terminal animation `assets/toy-run.svg` from `transcript.txt`. It shows transcript lines only. |

After a change to a gate or a hook, write the transcript again and read the diff:

```bash
python3 examples/toy-run/run.py > examples/toy-run/transcript.txt
git diff examples/toy-run/transcript.txt
python3 examples/toy-run/render_demo.py
```

`render_demo.py` fails when a line of a scene is no longer in the transcript. Then change the
scene in `SCENES`. A test fails when the SVG is older than the transcript.
