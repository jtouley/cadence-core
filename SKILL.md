---
name: cadence
description: Gated pipeline from a plan to a reviewed draft PR: plan, architecture review, adversarial review, TDD implementation, architecture conformance, draft PR, and review gates through closure. Use for "ship this", "build and PR", "implement end-to-end", or other work from a plan to a PR. Not for docs-only, config-only, reviewing existing PRs, or ops-only / CATALOG_STALE handoffs.
---

# Cadence

Cadence takes one change from a spec test to a reviewed draft PR. The order is `validate`, plan, review, implement, draft PR, and review closure.
Stay in the paceline through **R7**. The procedure is in `references/` and `stages.json`.
Load only the section for the block that you run. Do not copy stage details into the transcript.

**Skill root:** use `$CADENCE_HOME` first, then `$CADENCE_SKILL_ROOT`, then
`~/.cursor/skills/cadence` (a symlink into the checkout). `stages.json` is the core block order (stdlib JSON only).

**Config:** a stack of JSON layers. The lowest is `cadence.defaults.json` in the skill root. Above it
are the profile, `~/.config/cadence/config.json`, the project `.cadence.json`, `.cadence.local.json`
and `CADENCE_*` env vars ([docs/configuration.md](docs/configuration.md)). Do not edit
`cadence.defaults.json`. `python3 "$SKILL_ROOT/scripts/cadence_config.py" resolve` prints each value
and the layer that set it. A bad layer fails the gate that reads it.

**Writing:** write each artifact, PR text and commit under the writing rules of
[`references/peer-contract.md`](references/peer-contract.md).

## STOP — first action

```bash
SKILL_ROOT="${CADENCE_HOME:-${CADENCE_SKILL_ROOT:-$HOME/.cursor/skills/cadence}}"
python3 "$SKILL_ROOT/scripts/activate_session.py" --from-branch --fresh --supersede --request - <<'CADENCE_REQUEST'
<the user's request, as the user wrote it>
CADENCE_REQUEST
# resume this worktree's run (keeps its pipeline_run_id; with no run, starts fresh):
python3 "$SKILL_ROOT/scripts/activate_session.py" --from-branch --resume
```

Run activation through the Bash tool: the agent that runs it is the orchestrator and cannot write peer verdicts (`references/activation.md`).
Read the script output (`task_id`, `resume_block`, artifacts) before you do anything else.
Pass the user's request as written. Activation saves it as `.context/proposals/<task_id>.md`, the input of A5. Do not rewrite it.

## Canonical `.context`

Use one gitignored `.context/` at the workspace root. When you vendor the skill, find it with
`python3 "$SKILL_ROOT/shared/scripts/context_root.py"`. WIP plans can stay in
`.context/plans/*.plan.md`. Shareable plans that Cursor shows stay in
`.cursor/plans/`. Do not make a third artifact tree.

## Self-healing

A recoverable gate failure is not a stop condition. Do these steps:

1. Classify the failure.
2. Log it in the ticket or in `.context/issues/`.
3. If the scope or the tests change before P9, run `/plan-update`. After P9 the plan is frozen: stop and tell the user. A plan change is a new run (`--fresh`).
4. Run the gate again.

Stop only for destructive risk, missing credentials with no fallback, a security or data-safety risk, or no retries left.
Record each one with `activate_session.py --hard-stop {destructive,credentials,security,retries,a5-gap,not-needed} --reason "..."`.
`not-needed` is the no-PR end (see "A Cadence run always ends in a PR"). A blocking A5 gap does not stop the run: the plan answers it. `a5-gap` stays for a person who stops the run at A5. An ops-only handoff stops at **A3**.
`--deactivate` refuses an open run that has no hard stop.
A peer can set a hard stop by mistake, for example a user-level copy of a peer with an old rule (a peer shadow).
To clear it: Remove the cause, and run `scripts/preflight.py` until it passes. Start a new host session. Then run `activate_session.py --from-branch --resume`.
Fix the review findings with one `/spec-driven --fix <review>` peer call for each round, or open a ticket under the parent issue. Do not leave a must-fix finding without an owner.

## Hard preflight

Run these checks before gated edits. Cursor runs them as hooks. Pi has no `hooks.json`, so run them as CLI commands.

```bash
SKILL_ROOT="${CADENCE_HOME:-${CADENCE_SKILL_ROOT:-$HOME/.cursor/skills/cadence}}"
python3 "$SKILL_ROOT/shared/skill_gate.py" state --skill cadence
python3 "$SKILL_ROOT/shared/skill_gate.py" check --skill cadence --action strreplace --file "<target-file>"
```

### Execution entry (fail-closed)

Run this command before any edit under the gated prefixes. The prefixes are `src/`, `lib/`, `app/`, `packages/`, or the `gated_prefixes` of the resolved config. A prefix matches from the repo root or from the workspace directory, not below another directory.

```bash
python3 "$SKILL_ROOT/scripts/entry_invariant.py" --phase execution
```

If the exit code is not 0, you cannot start **E1**. To recover, activate again from **A1** with `--fresh`.
If artifacts went missing during the work, stop and ask the user.
**Forbidden:** backfill. After the code edits start, do not write a missing P1–P9 artifact (a plan or a plan review) to clear the entry invariant.

## Pi CLI gate protocol

On Pi, the extension `pi/cadence-gates.ts` runs the gates. `install.sh` links it into `~/.pi/agent/extensions/`.
It runs the hooks of `hooks/hooks.json` (the Claude Code hooks) on each `write`, `edit` and `bash` call, and the stop guard at each end of turn.
During a run it blocks each `powershell` call, because the gate does not read PowerShell. Use `bash`.
Each `bash` call sets `CADENCE_OWNER_ID` to the Pi session id, so activate the run inside Pi. Then the stop guard knows the run.
Each `bash` call also sets `CADENCE_HOME` to the skill root of the extension, so the scripts and the hooks come from one checkout.
Limits: Cadence does not judge a custom tool that writes files, or a user `!` command.
An extension that Pi loads after Cadence can change `event.input` after the check.
Without the extension, use the CLI checks as the fallback. Pi and other headless hosts do **not** run Cursor `hooks.json`. Run the CLI checks above before each
gated Write, StrReplace, or EditNotebook, and before `gh pr comment`. When you can, give `check` the exact `--action` and `--file`.
A non-zero exit means: do not edit. To find the open block, run `scripts/headless.py next --json`.
Run the command in `next.run`, and check the block with `next.check`. Both name absolute script paths.
Run the gates from the run's worktree, or pass `--workspace <worktree>`. E1 and R4 judge only the run of the worktree they run in.
It reports the first registered gate that fails.

## Cadence (anti-stop)

Hold cadence through **R7**. `hooks/pipeline_stop_guard.py` blocks an early exit.
Hard stops are only these: destructive risk, missing credentials, a security or data-safety risk, or no retries left (see Self-healing).
Do not ask permission to continue the paceline. Continue from the next open block.
Do not stop on `READY WITH CHANGES` or `NOT READY`. Run `/plan-update`, then `/plan-review`, in that turn.
Do not wait when a background task finishes during that loop.

## Bundled resources (load on demand)

| Resource | When to load |
|----------|----------------|
| `stages.json` | The core order, and the gate and manifest of each block |
| `references/activation.md` | After the STOP activation (when present) |
| `references/delegation.md` | Before peer-skill Task prompts (Cursor) |
| `scripts/pipeline_status.py` | Activation and resume |
| `scripts/headless.py` | Headless hosts: the first open block (`next --json`) |
| `pi/cadence-gates.ts` | Pi: the extension that runs the hooks (`install.sh` links it) |
| `scripts/entry_invariant.py` | Before E1 edits |
| `scripts/activate_session.py` | A1 and R7 |
| `hooks/pipeline_stop_guard.py` | Stop-hook enforcement |
| `packs/*/pack.json` | Optional domain packs (`packs` in a config layer) |
| `adapters/*.json` | Ticket ID regex and tracker binding |

## Pipeline

`stages.json` sets the order. A conditional trigger (`on_review_changes`) can do nothing when it does not apply.
Record the gate in that case too. Do not ask whether to continue.
**P3 and P4:** repeat `/plan-review` and `/plan-update` until the newest review has `RUN_BY_COMMAND: plan-review` and `Verdict: READY TO EXECUTE`.
That review is from a plan-review run after the last plan update. Plan update does not create or edit `reviews/plan_*.md`.
Each cycle writes the review's required contracts into the plan.
A blocking verdict means the P4 trigger applies. Do not record a no-op for that trigger.
The loop has at most five review cycles (`plan_review_cycles`, default 5; a config layer can only lower it). After the fifth blocking review, do not review again:
- `READY WITH CHANGES`: run `/plan-update` once more. It adds `## Carried review findings` and maps each open `MF-n` to the phase whose test settles it. P9 then clears.
- `NOT READY`: the plan is wrong. Run `activate_session.py --hard-stop retries --reason "..."`.
Do not ask the user to allow another cycle. Do not end the turn on a blocking verdict.
**P6:** the adversarial loop has at most three cycles (`adversarial_cycles`, default 3; a config layer can only lower it). Below the limit, `REQUEST CHANGES` means:
`/plan-update`, then `/plan-review` (when P3 is below its limit), then `/de-adversarial-reviewer plan-gate`.
At the limit, do not run P6 again. Run `/plan-update` once more to carry each `B-n` and `C-n` under the report's heading.
P3 and P6 share one budget: a P3 review that a P6 round causes counts in the P3 limit.
Do not run P3 and P6 on the same plan revision at the same time. Run P6 after the P3 verdict for that revision.
Use a new Agent call for each P3 and P6 review round. Do not resume the peer of the prior round.
When the gate output names a step, do that step before the `NEXT` command.
Post the GitHub comment only after **R5** closure and **R6**.
Skip a row whose pack is off: its pack is not `core` and not in `packs` of the resolved config.
The table is the core order. A pack or `stages.overlays` can insert, replace or remove a block, and `peers.<block>` can bind another peer to a peer block.
`python3 "$SKILL_ROOT/scripts/load_stages.py"` prints the order of this run. Follow that order.

| Block | Slug | Pack | Command | Intent |
|------:|------|------|---------|--------|
| A1 | activate-session | `core` | `python3 scripts/activate_session.py` | Start or bind the active session for the feature branch and worktree |
| A2 | preflight | `preflight` | `python3 scripts/preflight.py` | Check the environment, the branch, and a clean state |
| A3 | ops-gate | `preflight` | `python3 scripts/ops_gate.py` | Sort ops-only work from code changes. Send catalog-stale and overlay-only tasks away |
| A4 | ticket-gate | `tracker` | `python3 scripts/ticket_gate.py` | Check the ticket binding and the acceptance criteria |
| A5 | validate | `spec-review` | `/vibe-test` | Test the spec and its assumptions before planning |
| P1 | plan-create | `core` | `/plan-create` | Write the structured execution plan (`.cursor/plans/*.plan.md`) |
| P2 | domain-analysis | `architecture` | `python3 scripts/domain_analysis.py` | Find the domain boundaries and select the domain packs |
| P3 | plan-review | `core` | `/plan-review` | Staff-level review of plan readiness and blocking items |
| P4 | plan-update | `core` | `/plan-update` | Apply review feedback until the plan is `READY TO EXECUTE` |
| P5 | architecture-review-plan | `architecture` | `python3 scripts/architecture_review.py --phase plan` | Core architecture review: dependency direction, boundaries, decision coverage |
| P6 | adversarial-review | `spec-review` | `/de-adversarial-reviewer` | Adversarial review of edge cases and assumptions |
| P7 | test-benchmark-plan | `test-plan` | `/test-benchmark plan` | Audit the proposed test plan for rigor, layers, and mutation awareness |
| P8 | deferral-register | `deferrals` | `python3 scripts/deferral_adjudication_check.py` | Register and adjudicate each approved deferral with a ticket link |
| P9 | entry-invariant | `core` | `python3 scripts/entry_invariant.py --phase execution` | Check that every alignment and planning artifact exists and passes before code edits |
| E1 | implement | `core` | `/spec-driven` | TDD implementation with a red-green-refactor loop |
| E2 | impl-artifact | `deferrals` | `python3 scripts/impl_artifact.py` | Collect the implementation diff and the commit manifest |
| E3 | architecture-review-impl | `architecture` | `python3 scripts/architecture_review.py --phase impl` | Check plan conformance, layers, and boundaries on the diff. Resolve findings per `references/review-rigor.md` |
| E4 | draft-pr | `core` | `/commit-push-pr --draft` | Open or update the draft PR |
| R1 | pr-review-local | `core` | `/pr-review-local` | Local staff-level review of the PR branch |
| R2 | test-benchmark-pr | `core` | `/test-benchmark` | Score test quality and mutation kill rate on the changed code |
| R3 | cognitive-complexity | `core` | `python3 scripts/cognitive_complexity_gate.py` | Fail a changed function over the cognitive complexity limit that is new or got worse |
| R4 | final-consolidate | `deferrals` | `python3 scripts/final_consolidate.py` | Two-pass review consolidation (close pass, then delta pass) through the findings ledger |
| R5 | closure | `core` | `python3 scripts/closure.py` | Check that each must-fix finding is `verified`, `deferred`, or `spec_gap` |
| R6 | pr-comment | `tracker` | `python3 scripts/guard_gh_pr_comment.py` | Post the structured closure ledger comment on the GitHub PR |
| R7 | session-close | `core` | `python3 scripts/activate_session.py --deactivate` | Close the session after the PR is ready |

**Provenance:** each artifact must carry its `RUN_BY_*` marker. A peer skill runs
the named command. A peer skill does not make up verdict files.

**Deferral:** use a deferral only as the last option. Each one needs ticket IDs and a ripeness value.
**P8** writes the zero register when nobody proposed a deferral. The plan peer writes each approved deferral.

## When to use / not

**Use:** a feature, bugfix, or refactor that must go from plan to PR. Example phrases:
"ship this", "build and PR", "implement end-to-end", `/cadence`.

**Not:**

- A docs-only commit or a config-only change
- A push of existing work without a plan cycle
- A review of an existing PR (use `/pr-review`)
- Exploration only
- **Ops, CATALOG_STALE, or overlay incidents with empty code paths.** Stop at **A3**
  (`ops-gate`) and keep notes in `.context/debug/`.

An explicit `/cadence` does **not** cancel the ops-only stop when the fix type is ops or the code path is empty.

## Activation (checklist)

1. Run `pipeline_status.py` (and the checkpoint list when available).
2. Run **A2** preflight (worktree and environment).
3. Run **A3** ops-gate when a handoff or a context pack exists. Stop the code path if the work is ops-only.
4. Run **A4** ticket gate (adapter regex from `adapters/*.json`).
   With `"adapter": "none"`, each tracker post goes to `.context/issues/<task_id>.md` (see `references/activation.md`).
5. Load the registry row for the resume block. Continue through **R7**.
6. At exit, run `activate_session.py --deactivate`. It writes the run's manifest and asks for
   `/session-summary <manifest>`. Send that peer, then run `--deactivate` again.

## Paceline (delegation)

**The orchestrator routes.** For each block, run its gate, or send its peer the slash command
and its arguments, and read the peer's one-line return (`ARTIFACT: <path>`). A peer gets a
markdown file and gives back a markdown file: the plan, a review, the implementation artifact.
The gates and the peers read those files and the code. The orchestrator does not. On Claude Code a hook
denies an orchestrator read of a run artifact, of the code or of the diff. It also denies an
orchestrator write to the code or to the markdown of the run.
A block whose file needs no judgment writes the file itself: A1 the request, P8 the zero deferral
register, R6 the PR comment, R7 the manifest of the run. A block that needs judgment is a peer with a
skill, and its input is a markdown file. E1 is one peer call, `/spec-driven <plan>`: the peer makes a task
for each plan phase and commits each phase. Each review fix round is one peer call,
`/spec-driven --fix <review>`.

**Cursor:** give **P1** (`plan-create`), **P3** (`plan-review`), and **P6**
(`adversarial-review`) to **separate Task calls**. Each Task `prompt=` is the slash command and its arguments only.
Keep prompts short. While a cadence session is active, `hooks/guard_lean_task_prompt.py` rejects a Task prompt that is not one short line, and it rejects a parent briefing. It stores an allowed peer prompt. A peer gate accepts the artifact only when that record is present and the prompt is still lean.
An optional context pack goes to **P1** only.
Do not write a peer artifact, or accept one, unless the peer ran.
**Claude Code:** when the session has `peer_models`, pass its model on each peer Agent call (`references/delegation.md`, "Peer models").
**Claude Code:** the same lean rule applies to each peer Agent call. The `PreToolUse` hook records the call. Do not run `begin` (`references/delegation.md`, "Claude Code (lean record)").

**Pi / headless:** there is no Cursor Task tool. Run the planning and review blocks
**one after another in the same session**, in registry order. The gates, the artifacts, and the fail-closed CLI checks stay the same.
Only the delegation host changes.

**E1** (`implement`) is one `/spec-driven <plan>` peer call on Claude Code and Cursor. The peer turns
the plan's `### Phase N:` headers into its task list, commits each phase, and returns the path of
`.context/implementation/<task>_stage4.md`. The E1 gate fails until each phase has a commit. When
it fails, send the peer again: the stop hook names the next open phase.
On Pi and headless hosts the main agent runs `commands/spec-driven.md` itself.
After E1 passes, run **E2**. Then continue into **E3** and **E4** in the same turn, unless a hard stop applies (see Self-healing).

## Review phase (short)

Load [`references/review-rigor.md`](references/review-rigor.md) for E3, R1, and
R2. The reviewer reproduces each finding, walks the documented path, runs
adversarial inputs, and tests each design choice three ways with strict TDD.

1. Run **R1** `/pr-review-local`. Check the artifact, the provenance, and the rigor sections.
2. Resolve each must-fix finding: send `/spec-driven --fix <review>`, or open a ticket.
3. Run **R2** `/test-benchmark --code`. Resolve each HIGH item. Record `adversarial_inputs`.
4. Run **R3** cognitive complexity. When it fails, do the fix step that it names, or send `/spec-driven --fix <review>`. That peer fixes each `offenders` entry of the R3 record, with the `bugs` (ruff) and `types` (mypy) findings.
5. After fix commits, run `/pr-review-update` on the new head. **R1** `pr_review_local_gate.py --check` fails while the newest PR review does not cite HEAD. When the deferrals pack is on, **R4** `final_consolidate.py --check` also fails while a must-fix is open.
6. Run **R5** `closure.py`. Then run **R6** `guard_gh_pr_comment.py`: it writes the comment from the ledger and prints the `gh pr comment` command. Run that command. Then run **R7** deactivate: it names the `/session-summary <manifest>` peer to send first.

## Anti-patterns (pointers)

- Do not start E1 without the entry invariant.
- Do not update the plan inline during execution.
- Do not approve R1 without evidence.
- Do not swap one requirement for another.
- Make the simplest change in each TDD cycle.
- Change every call site in the blast radius, or record a ticketed deferral.

## Commit authorization (active session)

When `activate_session.py` reports an active cadence run for this ticket,
**E4–R6** are pre-authorized. Open the draft PR, push, and run the review gates without asking
"want me to commit/open PR?" The user's `/cadence` (or "continue") is approval to ship through the pipeline.

### A Cadence run always ends in a PR

The user's `/cadence` is the explicit request to open the draft PR. This rule
overrides a host default such as "do not open a PR unless the user asks". Do
not ask for approval to open the PR, to push, or to continue to the next block.
Do not use an ask-the-user tool between **A1** and **R7**.

A run ends without an open PR in one case: a review (**P3**, **P6**, **R1**, **R2**) finds that the
work is not needed or is wrong. Close the draft PR. The finding is in the review.
A blocking gap at **A5** does not stop the run. The vibe-test report labels it `G-n`, and the plan
peer logs an assumption for each `G-n` in the register, next to the deferrals. P9 fails while one
has no row, and R6 shows the assumptions to the reviewer.

Record the reason with `activate_session.py --hard-stop not-needed --reason "..."`. Then run
`activate_session.py --deactivate`, and send the `/session-summary` peer that it names. The manifest holds the reason. Every other case continues to **R7**.

### Do not end the turn at E1

Forbidden closing lines: "say if you want the draft PR", "on your go", "when you're
ready for review". After E1 passes, write E2. Then continue into E3 and E4 at once.

### Do not end the turn during execution

Do not answer a finished or aborted background shell with status only.
Resume the open execution slice in that turn.
Do this after the entry check passes.
Do not end the turn after one slice while a later phase or the draft PR is still open.

### Do not end the turn on READY WITH CHANGES

Do not close the turn when the verdict is `READY WITH CHANGES` or `NOT READY`.
Run `/plan-update` in that turn. Then run `/plan-review`.
The stop hook reads the newest `reviews/plan_*.md`.
`resume_slug` stays at activation until execution. The review file is the evidence for this stop.
Do not report status when a background task finishes during this loop. Continue the loop.

## Todos (create at activation)

```
- [ ] A1 activate + pipeline_status + A2–A5
- [ ] P1–P9 through entry-invariant
- [ ] E1 `/spec-driven <plan>` peer → E2 artifact → E3 arch-review → E4 draft PR (same turn)
- [ ] R1 → R2 → R3? → R4 consolidate → R5 closure → R6 comment → R7 deactivate
```

## Dependencies

- Commands: `plan-create`, `plan-review`, `plan-update`, `spec-driven`, and the draft PR through `gh`.
- Skills: vibe-test, test-benchmark, de-adversarial-reviewer, and packs when enabled.
- Tools: git, gh, uv, python3.
- Tracker: the adapter selects it (GitHub Issues is the default).
