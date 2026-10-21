---
decision_id: ADR-0017
status: accepted
question: What does the orchestrator do itself, and how does Cadence keep its context small?
choice: The orchestrator routes. It runs each gate, sends each peer its rendered command, and reads the peer's one-line return. E1 is one `/spec-driven <plan>` peer call that turns the plan's phases into its own tasks, and each review fix round is one `/spec-driven --fix <review>` peer call. On Claude Code a hook denies an orchestrator read of a run artifact, of the code or of the diff, and an orchestrator write to the code. The context budget only records the size. It never denies a call.
rationale: On an end-to-end run of the strict profile, every peer prompt was already a slash command with a path or a PR number, and the eleven peer returns added about 3,000 tokens. The orchestrator started at about 44,000 tokens and read about 110,000 more itself. It read the plan five times, the reviews and the references, and it implemented E1, because the skill told the main agent to. The context budget in `enforce` then denied the next peer call at 90,000 tokens, the call that would have moved work out of the orchestrator, and the run stopped before R1.
rejected:
  - alt: Keep the `enforce` deny and raise `max_tokens`
    why_not: The deny blocks a peer call, which is the call that keeps the context small. A larger limit moves the stop to a later block and keeps the cause
  - alt: A limit for each orchestrator model
    why_not: The 90,000 default came from a pilot that ran every agent on Haiku 5.5, which costs five times more per token above 100,000 prompt tokens. Fable 5.1, Opus 5.5 and Sonnet 5.5 have a 1,000,000-token window and no price step. A routing orchestrator does not grow, so no limit has to fit it
  - alt: Write the rule in SKILL.md only
    why_not: The skill already said to delegate the planning and review blocks, and the orchestrator still read their files. A rule that a model must remember is not a gate (ADR-0010)
  - alt: One `/spec-driven` peer call for each plan phase
    why_not: The plan is the task list. One peer reads it once, makes a task for each phase and commits each one. The stop hook holds that peer while a phase is open (ADR-0015), and the orchestrator sends the same command again when the E1 gate still fails
  - alt: Deny every orchestrator read under `.context/`
    why_not: The orchestrator runs scripts that read the session, the gate records and the ledger. Only the files a peer reads and writes are out of its scope
links:
  - rel: implements
    target_type: file
    target_id: shared/scripts/orchestrator_scope.py
  - rel: implements
    target_type: file
    target_id: hooks/guard_secret_read.py
  - rel: implements
    target_type: file
    target_id: hooks/guard_skill_gate.py
  - rel: implements
    target_type: file
    target_id: hooks/pipeline_stop_guard.py
  - rel: implements
    target_type: file
    target_id: scripts/context_budget.py
  - rel: implements
    target_type: file
    target_id: commands/spec-driven.md
  - rel: relates
    target_type: file
    target_id: docs/adr/0015-e1-drives-the-next-phase.md
  - rel: relates
    target_type: file
    target_id: docs/adr/0010-the-registry-drives-the-lean-handoff.md
---
# ADR-0017: The orchestrator routes, and the peers do the work

## Context

ADR-0004 and ADR-0010 made each peer prompt a slash command with its pinned inputs, and the lean
return check (gh-96) made each peer reply one line. Those two rules control what goes into a peer
and what comes back. Nothing controlled what the orchestrator read on its own.

An end-to-end run of the strict profile (25 blocks) on a throwaway repository showed where the
orchestrator's context came from:

| Source | Tokens |
|---|---|
| The host's system prompt and the skill, at the first request | about 44,000 |
| Eleven peer returns | about 3,000 |
| The orchestrator's own reads: the plan (five times), the reviews, the peer contract, the architecture rules, the source and the tests | about 110,000 |

The skill told the main agent to run E1 itself, so the orchestrator read the plan and the code
for the implementation. It also read each review and report, which the gates already check. At
90,000 tokens the context budget in `enforce` denied the R1 peer call, and the run stopped with a
hard stop. The default limit came from a pilot that ran the orchestrator on Haiku 5.5, which costs
five times more per token above 100,000 prompt tokens. Fable 5.1, Opus 5.5 and Sonnet 5.5 have a
1,000,000-token window and no such price step.

## Decision

1. The orchestrator runs each block's gate, sends each peer the rendered command, and reads the
   one-line return. A peer's input and its output are markdown files: the plan, a review, the
   implementation artifact. The gates and the peers read those files and the code.
2. E1 is one peer call, `/spec-driven <plan>`. The peer makes a task for each `### Phase N:` of the
   plan, commits each phase, follows `deslop.md` and, after a failed local R3, `simplify-code.md`
   itself, writes `.context/implementation/<task>_stage4.md`, and returns its path.
3. Each review fix round is one peer call, `/spec-driven --fix <review>`, where `<review>` is the
   PR review `.context/prs/reviews/PR_REVIEW_#<N>.md`, pinned by its sha256 (input kind
   `pr-review`): a red test and a fix commit for each open must-fix row, and the row set to
   `fixed` with `fixed_in`. Its row is in `EXTRA_PEER_ROWS` (block `RF`), and its longer command
   wins over the E1 row in the handoff.
4. On Claude Code, during an active run, `shared/scripts/orchestrator_scope.py` gives the deny text
   for an orchestrator call (no `agent_id`, or an agent that ran activation):
   - a `Read`, `Grep` or shell read of a run artifact (the peer verdict files, the plans, the
     proposals and `.context/implementation/`), of a file under `gated_prefixes`, or of a test file;
   - a `git diff` or `git show` that prints the change;
   - a write to a file under `gated_prefixes` or a test file.
   The read guard and the edit guard call it. `orchestrator_scope.mode: off` turns it off.
5. The stop guard holds the E1 peer once while a plan phase has no commit, with the ADR-0015 text.
   A Claude Code orchestrator that stops in E1 is told to send the peer the plan again.
6. The context budget records the size before each orchestrator peer call and writes a notice
   above `warn_tokens`. It has no `enforce` mode and no `max_tokens`, and `context` is no longer a
   hard stop class.

## Consequences

- An end-to-end run with every pack on (25 blocks, a plan of four phases, a local git remote)
  closed in one session. The orchestrator sent 18 peer calls, each with a markdown input, and
  the returns added about 5,000 tokens. E1 was one `/spec-driven <plan>` call that committed each
  phase test first. The fix round got the PR review. A reopen of the closed run for a must-fix
  found after R7 kept the run id, ran one fix round and closed again at R7.
- The orchestrator still writes some markdown itself: the P5 decision record, the P8 deferral
  register, the R6 closure comment and the session summary. To write them it reads skill
  references and script sources, which made about half of its tool output on that run. A peer or
  the block's own script can write each of them; that is a later decision.
- A peer's call is never judged by the scope check. An orchestrator that needs a fact from a run
  artifact gets it from a gate's output or from a peer's return line.
- Pi and the other headless hosts have no peers. Their main agent does every block, and no hook
  judges its reads. Cursor runs record the host `other`, as Pi does, so the scope check and the
  peer form of the E1 stop text apply to Claude Code only.
- The scope check judges the paths that a call names, with the parser of the secret read guard.
  It does not expand a glob, and it does not look into a directory that `grep -r` searches.
- A layer that sets `context_budget.mode: enforce` or `context_budget.max_tokens` now fails closed
  as an unknown value. Remove the key.
- The runtime size budget (`tests/contract/test_source_size_budget.py`) rises from 17,400 to
  17,550 lines for the scope module, the E1 peer hold and the `pr-review` input. This is the one
  record.
