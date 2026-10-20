---
decision_id: ADR-0007
status: accepted
question: Which blocks does a default Cadence run visit?
choice: The default run visits twelve blocks in five steps: activate, plan and review, implement, draft PR and review, close. The other thirteen blocks stay in stages.json with a pack name. load_stages.py drops a row whose pack is not core and not in enabled_packs. The order does not change. A block that is dropped is never open, so the stop guard, headless.py and the gates skip it.
rationale: A run visits 25 blocks and calls four review peers before a human sees a PR. That cost is the same for a one-line fix and a new subsystem. Each block brought edge cases, each edge case became an issue, and the issues are about the machine, not about users (#98, #99). The four ideas that are the product (fail-closed gates, the stop guard, the edit guard, the findings ledger) need ten blocks. Those are A1, P1, P3, P4, P9, E1, E4, R1, R5 and R7. R2 and R3 stay because the benchmark's tool gate scores the two signals they produce, mutation score and complexity 15. The rest is judgment that a team can opt into.
rejected:
  - alt: Delete the thirteen blocks
    why_not: The gates, peers and tests behind them took the most work, and the benchmark needs the full pipeline as one arm
  - alt: Profiles in the skill-root config file that list the blocks of a run
    why_not: A second place that holds a block list. stages.json must stay the only order source
  - alt: Keep 25 blocks and document the cost
    why_not: It does not change the cost, and the open issues show that 25 blocks are more than the project can hold
links:
  - rel: implements
    target_type: file
    target_id: stages.json
  - rel: implements
    target_type: file
    target_id: scripts/load_stages.py
  - rel: relates
    target_type: file
    target_id: references/pack-standard.md
---
# ADR-0007: A five-step core with opt-in blocks

## Context

`stages.json` holds 25 blocks. Every row has `"pack": "core"`, so a run visits every row.
A `/cadence` run calls four review peers (`/vibe-test`, `/plan-review`,
`/de-adversarial-reviewer`, `/test-benchmark` twice) and `/pr-review-local`, and no human
reads a result before R7.

The tracker shows the cost. On 2026-10-18 the repo had 41 open issues. Most of them are
about one block's edge cases: review cycle counts (#86, #137, #142, #144), `--supersede`
matching (#135, #136), write gate path forms (#109, #110, #119, #141), run ownership
(#33, #67, #80, #108, #113). #98 counts 2,251 tests, 1.5 times the source. #99 asks why
the stages did not stop that debt.

`packs/` already exists for opt-in rules, and every stage row already has a `pack` field.
`references/pack-standard.md` says a pack must not change the order. This decision keeps
that rule.

## Decision

1. **Core.** These rows keep `"pack": "core"`:

   | Step | Blocks | Slugs |
   |---|---|---|
   | Activate | A1 | `activate-session` |
   | Plan and review | P1, P3, P4, P9 | `plan-create`, `plan-review`, `plan-update`, `entry-invariant` |
   | Implement | E1 | `implement` |
   | Draft PR and review | E4, R1, R2, R3 | `draft-pr`, `pr-review-local`, `test-benchmark-pr`, `cognitive-complexity` |
   | Close | R5, R7 | `closure`, `session-close` |

   P9 stays in core because the edit guard and the stop guard read its marker (ADR-0003).
   P4 stays because P3 can ask for changes. R2 and R3 stay because of the benchmark fit
   below.

2. **Opt-in rows.** The other thirteen rows get a pack name. The rows do not move. Their
   gates and peers do not change.

   | Pack | Blocks | Slugs |
   |---|---|---|
   | `preflight` | A2, A3 | `preflight`, `ops-gate` |
   | `tracker` | A4, R6 | `ticket-gate`, `pr-comment` |
   | `spec-review` | A5, P6 | `validate`, `adversarial-review` |
   | `architecture` | P2, P5, E3 | `domain-analysis`, `architecture-review-plan`, `architecture-review-impl` |
   | `test-plan` | P7 | `test-benchmark-plan` |
   | `deferrals` | P8, E2, R4 | `deferral-register`, `impl-artifact`, `final-consolidate` |

   A run with every pack enabled is today's run. The benchmark keeps that arm.

3. **One filter.** `load_stages.py` reads `enabled_packs` (now `packs`, ADR-0016) from the skill-root
   config file (now `cadence.defaults.json` and the config layers, ADR-0016).
   `load_stages()` returns the rows whose `pack` is `core` or enabled, in file order.
   `load_registry()` still returns the full file. Nothing else learns about packs.
   `headless.py`, the stop guard walk, `pipeline_status.py` and `entry_invariant.py` read the
   filtered list, so a dropped block is never the open block.

4. **A gate for a dropped block does not run.** A gate that reads an artifact of a dropped
   block (for example R5 reads the ledger that R4 consolidates) must pass when that block's
   pack is off. Each such gate gets a test for the off case.

5. **The default config enables no pack.** The skill-root config file keeps `"enabled_packs": []`.
   This repo's own `.cadence.json` enables every pack, so Cadence keeps the full run on
   itself.

## Benchmark fit

An external benchmark plans to score a
harness on three layers and report each one on its own. The core must not drop a block
that a layer scores.

| Layer | What it scores | Cadence block |
|---|---|---|
| 1. Correctness | Pass rate on test-graded tasks (SWE-bench Pro, FeatureBench, Terminal-Bench 2.0) | No single block. The run as a whole helps or costs here |
| 2. Code that holds up under change | SlopCodeBench checkpoints as the spec changes | P3 in core. P5 and E3 in the `architecture` pack |
| 3. Tool gate on each change | No function over complexity 15. Mutation score of the change's own tests. Also Semgrep, duplication and coverage | R3 (`cognitive-complexity`, max 15) and R2 (`test-benchmark-pr`, mutation) in core. Cadence has no Semgrep, duplication or coverage gate |

Layer 3 is why R2 and R3 are core. A harness that produces those two signals only when a
pack is on does not make the claim. Layer 2 is the first place to check the cut: if the
default arm degrades more than the full arm on SlopCodeBench, P5 and E3 move to core.

No layer matches a maintainer's merge decision (METR). Cadence's gates are proxies too.

## Consequences

- A default run calls three review peers, not five. It still cannot mark its own work
  `verified`, stop before R7, or edit code before the plan passed review.
- `SKILL.md`'s pipeline table must match the full `stages.json`
  (`test_skill_md_pipeline_slugs_match_stages_json`). The table gets a pack column. The
  agent reads the pack column and skips the rows that are off.
- `references/pack-standard.md` gains one rule: a pack may own stage rows, and it still may
  not change their order or their gate scripts.
- A new block goes into a pack, not into core. Core grows only by a decision record.
- Tests that walk every stage (`tests/session/`, `tests/gates/test_entry_invariant.py`)
  need a fixture for the default config and one for the full config.
- The open issues of a dropped block move to that pack's epic. An issue that only serves
  a block nobody enables can close.
- `tests/contract/test_source_size_budget.py` holds the line count of `scripts/`, `hooks/`
  and `shared/`. This decision does not lower it. The next one should.
- Landed: the 13 rows have their pack names, `load_stages()` filters on `enabled_packs` and
  the extra pack list of the project's `.cadence.json`, and the walks, P9, E4 and the PR guard pass on a row
  whose pack is off.
