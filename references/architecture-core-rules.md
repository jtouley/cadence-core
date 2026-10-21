# Architecture core rules

Always-on rules for **P5** (`architecture-review-plan`) and **E3**
(`architecture-review-impl`). Findings must cite these ids in `rule_ref`.

| ID | Name | When | Severity default |
|----|------|------|------------------|
| `core:dependency-direction` | Dependency direction | Always. **Skipped** (with a logged notice) when `layer_map` is empty | must-fix |
| `core:boundary-integrity` | Boundary integrity | Always. **Skipped** when `layer_map` is empty | must-fix |
| `core:decision-coverage` | Decision coverage: an ADR or proposal with decision frontmatter and status `proposed\|accepted` | Always (`decision_frontmatter_required` default true) | must-fix → `spec-gap` when none exists |
| `core:decision-coverage-scope` | Coverage came only from decisions that never mention this ticket | Always, when `core:decision-coverage` passes | nit (never blocks closure) |
| `core:plan-conformance` | Plan conformance | **Impl only** (E3) | must-fix |
| `core:dry-extension` | DRY, single responsibility, open for extension | Always | must-fix |
| `core:implement` | TDD rules of E1 | E1, and E3 checks them | must-fix |
| `core:standalone-package` | Closed package | Always | must-fix |

A repo declares `layer_map` in a config layer, for example its `.cadence.json`. Rules 1–2 skip with a
logged notice under `.context/architecture-review-notice.json` when the map is
empty.

### `core:decision-coverage`

Boolean only (no NL scan of plan prose). Must-fix unless an ADR or proposal file has the
required frontmatter keys (`decision_id`, `status`, `question`, `choice`, `rationale`) and
`status` `proposed` or `accepted`. The gate reads `docs/adr/`, `docs/adrs/`, `docs/edrs/` and
`.context/proposals/`. No `pipeline_run_id` / `task_id` filter: when no such decision names the
ticket, the gate adds the nit `core:decision-coverage-scope`.

`decision_frontmatter_required` (default **true**): set `false` in a config layer so that any
architecture document counts (an ADR, EDR or proposals directory, `ARCHITECTURE.md` or
`docs/architecture/`).

### `core:dry-extension`

Apply this rule to source code, gates and tests. A finding names the item that it breaks.

1. **Reuse.** Use a helper that exists in a shared module. Do not write it again.
   In Cadence, the shared modules are `tests/conftest.py`, `scripts/gate_common.py` and `shared/`.
   The R2 gate measures this rule for tests: `scripts/test_shape.py` lists each copy of a conftest helper.
   It reads the test layout of the repo from `test_shape.layout`, or it finds the layout.
2. **Rule of Three.** Add a shared helper when three callers need the same logic.
   Do not copy the logic into a third caller.
   The R3 gate measures copied code: `scripts/duplication.py` finds copied blocks, also with new names.
3. **Single responsibility.** Keep each function under the R3 limit (`cognitive_complexity.max` of the resolved config).
   Split a function that does more than one job.
   R3 also fails a Python function over `code_shape.max_function_statements`.
4. **Open for extension.** When a second case is likely, add an extension point.
   Do not add a branch to a core function for that case.
   R3 fails a Python branch chain over `code_shape.max_branch_chain`. The reviewer checks the rest of this rule.
   An extension point is a config key, a `stages.json` field or a pack gate (`references/pack-standard.md`).
5. **Gates.** A stage whose check is only slug + artifact globs + `RUN_BY_*` markers + `--check` is a `stages.json` row for one runner (`gate_common` + `artifact_requirements`).
   Add a new Python module only when the check is not that shape.
   After three cloned CLI shells, extract the runner. Do not add a fourth.

This is Cadence’s own rule (single responsibility, open/closed). Do not load another skill to
explain it. The R1 review records the result under `## Reuse and extension` (`review-rigor.md`).

### `core:implement`

E1 (`commands/spec-driven.md`) writes the tests and the code. Apply the four rules of
`## Rules` in `commands/spec-driven.md`: the seam-contract test, bug-fix mode, the mock
boundary and the test names. E3 finds a must-fix when the change breaks one of these rules.

### `core:standalone-package`

Cadence is a closed package. Every slash command in
`stages.json` is a peer that **lives in this repo** (`skills/` or
`commands/`, listed in `references/bundled-peers.json`). `install.sh`
publishes those peers. Runtime reads `$CADENCE_HOME` only. A config layer can bind
another peer to a peer block with `peers.<block>` (`docs/configuration.md`). The
host runs that peer, and core never reads its text.

Findings when a procedure, subagent, or gate opens `~/.cursor/commands`,
another skill under `~/.cursor/skills/`, or `~/.cursor/rules`. Exception: a gate can `lstat` and `resolve()` a host
entry that has a peer's name to find a peer shadow (`scripts/peer_shadows.py`). It does not read, load, or run the content of that entry. A missing peer is a **must-fix** (do not skip the
stage, do not fall back to a host global). Authoring tools
(`skill-creator`, `postmortem`) stay off the install surface.

Relation to adversarial: **P5** runs before **P6** (`adversarial-review`).

## Resolving findings (E3)

Reproduce each must-fix before fixing it. Check it against this file first: a
finding that contradicts a rule written here is a decision for the author. A
finding with more than one reasonable fix goes through **Design choices** in
[`review-rigor.md`](review-rigor.md), and R1 records the outcome.
