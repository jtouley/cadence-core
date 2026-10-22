# Changelog

## Unreleased

- Acceptance checks. P1 writes `.context/acceptance/<task_id>.json`: one check for each outcome that the
  request asks for, each with its own committed script. P9 pins the checks, E1 runs them when the last
  phase is done (E1.5), and R5 runs them again at HEAD. P3, P6, E1 and R1 get the checks in their
  packets, and P6 and R1 also get the request. When no code can pass a check as it is written, the
  implementer records the defect with a repro, the plan peer fixes that one check
  (`/plan-update <plan> --acceptance AC-n`), and `acceptance.py repin` logs the old and the new text for R1.
  This is the one plan change after P9.
- On Cursor, the Task hook writes one history line for each peer call it allows, so the P3 and P6 cycle
  limits stop the loop there too. Before, only a Claude Code run counted its cycles.
- The repo map. A5, P1, P3, P6, E1 and R1 get `.context/repo-map/<tree>.md` in their packets: the
  tracked files, the build targets and the top-level Python names of the tree at HEAD, written once
  per tree (`scripts/repo_map.py`). A peer reads it before it searches the repository.
- Each phase is committed red, then green. `phase_evidence.py record` stores the test command of a phase,
  and the E1 gate runs it in a clean checkout of each commit: it must fail at the `phase N: red` commit and
  pass at the green one. This replaces the check of commit times, which commits one second apart passed.
- The plan is reviewed once. `plan_review_cycles` and `adversarial_cycles` default to 1, and a config layer
  may raise them (`x-cadence-stricter: higher`): a raise only adds review. Live runs of one request compared
  no plan review, one plan round and up to four. The code after no plan review and after one round passed
  and failed the same probes. The PR round found each real code bug, and each run that did not finish
  stopped in a loop or at a gate.
- The plan review round. After P1, `plan_wave.py` sends P3, P6 and P7 in one message, runs P5 itself,
  and joins their findings by plan section in `.context/plan-findings/round-<n>.md`. One `/plan-update`
  answers every reviewer and edits the plan in place, in the sections that the round names. In a later
  round only a reviewer that had a must-fix reviews again, and only the sections that the update changed.
  `REQUEST CHANGES` is a P3 verdict. A reviewer's stop is held until it records `done`.
- Deferrals are judged. A register row needs a `Request trace` of `none`, a ripeness with its evidence
  and a follow-up ticket. Work that the request asks for is never deferred. P8 writes a manifest of the
  run's documents and names the new `/deferral-review` peer, which rules `GREEN`, `AMBER` or `RED` on
  each row. A `RED` row is built in this PR, and an `AMBER` condition is planned before P9.
- The review fix loop runs until R4 and R5 pass. `review_fix_rounds` defaults to no cap, and R4 holds
  only when a fix round changed nothing or at a cap that a layer sets. `--hard-stop retries` is refused
  while the loop still moves.
- R5 runs the repository's own checks: `ci_commands` of the resolved config, else `make test`, pytest,
  `npm test`, `go test ./...` or `cargo test`. A failing check is a must-fix row for the fix round.
- A verdict in the peer's own words. A gate reads each format that real peers wrote (the corpus in
  `tests/fixtures/peer_reports`). When `TYPESAFE_API_KEY` is set, Jev judges a verdict that no strict
  reader takes, and each answer that a run uses goes to `.context/verdict-judgments.jsonl`. Without the
  key, an unreadable verdict fails closed, as before.
- A run asks no person. A plan change after P9 is logged in `.context/issues/` and the run goes on
  with the frozen plan. The last message of a run asks no question.
- `findings_io.py set-check` replaces a check that cannot run, and keeps the old one in `check_was`.
  On Claude Code only the peer whose row it is may run it. A check of inline `-c` code is refused.
- R2 gates on must-fix dimensions and findings with evidence, not on the mean score.
- The source line budget test is removed.

- A plan finding closes in the plan. P7 fails while the plan scorecard does not pass, as
  R2 does, and names `/plan-update` and then `/test-benchmark plan`. The P4 packet pins the failing
  plan scorecard. The plan benchmark writes no findings ledger row, because the plan freezes at P9
  and no commit can close the row.
- A5 gaps are carried into the plan. The vibe-test report labels each blocking gap `G-n`.
  The plan peer logs an assumption for each one under `## Assumptions` in the register, next to the
  deferrals. P9 fails while one has no row, and the R6 comment lists the assumptions.
- A block writes its own markdown. `activate_session.py --request -` saves the user's
  request as written for A5. P8 writes the zero deferral register, R6 writes the closure comment
  from the ledger, and R7 writes the run's manifest for the new `/session-summary` peer. The plan
  peer writes the decision records and the approved deferrals. On Claude Code the orchestrator may
  not write the markdown of the run.
- The orchestrator routes. A peer gets a markdown file and gives back a markdown file. E1 is
  one `/spec-driven <plan>` peer call, and each review fix round is one `/spec-driven --fix <review>`
  peer call on the PR review. On Claude Code a hook denies an orchestrator read
  of a run artifact, the code or the diff, and an orchestrator write to the code
  (`orchestrator_scope.mode`).
- The context budget only records the size and writes a notice. `context_budget.mode: enforce`,
  `context_budget.max_tokens` and the `context` hard stop are gone; a layer that sets them fails.
- `activate_session.py --from-branch --resume` reopens a completed run that opened its draft PR, at
  P9, instead of starting a fresh run. A fix after R7 goes through the review blocks of the same run.

## 0.1.0

The first public release of Cadence core.

- The twelve-block core order in `stages.json`, and six stage packs (`preflight`, `tracker`,
  `spec-review`, `architecture`, `test-plan`, `deferrals`) that add the other thirteen blocks.
- Gate scripts that fail closed, and the findings ledger. Only `ledger_verify.py` writes `verified`.
- Hooks for Claude Code (`hooks/hooks.json`), Cursor (`hooks/hooks-cursor.json`) and Pi
  (`pi/cadence-gates.ts`): the edit guard, the secret read guard, the peer handoff and the stop guard.
- Layered config: the core defaults, a profile, the user file, the project `.cadence.json`, a local
  file and `CADENCE_*` env vars. A closed schema, `cadence_config.py resolve`, limits that a layer
  can only make stricter, and the profiles `minimal`, `standard` and `strict`.
- Block overlays (`stages.overlays`) and peer bindings (`peers.<block>`), with the
  [peer output contract](docs/peer-output-contract.md).
- Pack manifest version 2: `gate_command`, `requires`, `config` and `source`, and locks for the user
  and project packs.
- The toy run (`examples/toy-run/`): the real gates and hooks on a toy repository, with no model and
  no network.
