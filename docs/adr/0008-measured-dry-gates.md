---
decision_id: ADR-0008
status: accepted
question: Which DRY and SOLID measures does a gate compute, and when does a measure fail the gate?
choice: Two gates compute the measures. R2 runs scripts/test_shape.py on the changed test files and fails when helpers_reimplemented or thin_files has more entries than its test_shape limit (0). Next to complexity, R3 measures statements per function, the longest branch chain and the duplicated share of each changed Python file (scripts/duplication.py). R3 fails a value over its limit that is new or larger than at the merge base. For duplication, larger means more duplicated lines and a larger share, so a file that loses other lines does not fail. Each measure writes one record with value, threshold, threshold_source and a pass, warn or fail verdict, and the exit code follows the verdicts. The defaults are the loosest limits. A config value can only make a limit stricter. A golden corpus pins the verdict of each measure.
rationale: The project rule is that DRY is not a judgement. Under gh-99 the R2 peer ran grep, wrote three lists, and the R1 review judged them. Nothing failed on a list, and the gate could not check a list that it did not compute. R3 already shows a shape that works for complexity. A script measures each changed function and fails only new or worse debt. Old code does not block a change, and the debt cannot grow. The same shape now covers duplication and code shape.
rejected:
  - alt: Let the R1 review judge the lists (gh-99)
    why_not: A judgement can pass a copy. No gate fails on an entry, so the measure has no effect
  - alt: The peer measures and reports the lists
    why_not: The peer can leave out an entry or make the count with a different command. The gate cannot check a count that it did not make
  - alt: A percentage pass threshold for the R2 lists
    why_not: A large change can add copies and stay under a share. R2 counts entries with a limit of 0. R3 uses its percentage only with the merge-base ratchet
  - alt: A third-party clone detector (jscpd, PMD CPD, pylint duplicate-code)
    why_not: The runtime is the Python standard library only (AGENTS.md), and install.sh is a copy and a symlink
  - alt: Compare text without normalization
    why_not: A copy with new names and new values passes. duplication.py maps each name to N and each literal to L
  - alt: Compare each changed file with every file of the repo
    why_not: The stop guard runs R3 with a 4 s limit for each gate. R3 reads the merge-base text of each file with git show, so the measure stays on the changed files
links:
  - rel: implements
    target_type: file
    target_id: scripts/duplication.py
  - rel: implements
    target_type: file
    target_id: scripts/test_shape.py
  - rel: implements
    target_type: file
    target_id: scripts/cognitive_complexity_gate.py
  - rel: implements
    target_type: file
    target_id: scripts/test_benchmark_gate.py
  - rel: relates
    target_type: file
    target_id: references/architecture-core-rules.md
  - rel: relates
    target_type: file
    target_id: tests/contract/test_source_size_budget.py
---
# ADR-0008: Gates measure DRY and code shape

## Context

gh-99 widened `core:dry-extension` to tests and code. The R2 scorecard got a
`dry_measurement` key with three lists, and the peer made the lists with `grep`.
The R2 gate failed only when the key was missing. The skill said "Do not set a
percentage threshold. The R1 review judges each entry." Thus a list of copies
passed R2. R3 measured only cognitive complexity.

## Decision

1. **R2 computes the test lists.** `scripts/test_shape.py` reads the changed test
   files against the base that R3 uses (`change_under_review`). It writes:
   - `helpers_reimplemented`: a function whose name or normalized body is a
     function of `tests/conftest.py`. A body of one line does not count as a copy.
   - `helpers_reused`: the `tests/conftest.py` names that a changed file imports.
   - `thin_files`: a file with one or two `def test_` functions that imports a
     module of `scripts/`, `hooks/` or `shared/` that another test file imports.

   R2 fails in code mode on each list that is longer than its limit. The peer's
   `dry_measurement` key is optional. A difference from the computed lists is a
   note, not a verdict. `test_shape.py --change` prints the records for the peer.
2. **R3 measures duplication and code shape.** Next to complexity, R3 measures each
   changed Python file:
   - `statements`: the statements of a function body, nested ones too
     (`code_shape.max_function_statements`, 50).
   - `branch_chain`: the longest `if`/`elif` chain, where an `if` after an `if` that
     ends in `return` or `raise` continues the chain, or the cases of a `match`
     (`code_shape.max_branch_chain`, 6).
   - `duplication`: the code lines of the file in a copied block of six or more
     normalized units, in the file or in another changed Python file
     (`duplication.max_percent`, 3).

   A value fails when it is over its limit and it is new or larger than at the
   merge base. Old debt that did not grow is a `warn`. Another language follows
   `duplication.unsupported` and `code_shape.unsupported`.
3. **One record for each measure.** The record holds `measure`, `value`,
   `threshold`, `threshold_source`, `verdict` and `offenders`. A peer reads
   `verdict`. It does not judge the code again.
4. **Limits are floors.** The defaults in `cognitive_complexity.SECTIONS` are the
   loosest limits. The gates ignore a looser config value, and
   `threshold_source` says `default (config value N ignored)`. This also applies to
   `cognitive_complexity.max`.
5. **Data is not code.** Comments, blank lines, imports, `parametrize` tables,
   field declarations and a name bound to a table or a literal are not units.
6. **A golden corpus.** `tests/fixtures/golden/{good,bad,gamed}/<measure>/` holds
   small samples, and `verdicts.json` holds the verdict of each. A `gamed` sample
   meets the metric and is still wrong. It passes, and its docstring names the
   limit. A contract test pins each sample by sha256. `measure_exclude` keeps the
   corpus out of R2 and R3. `--path DIR` (R3) and `test_shape.py DIR` measure a
   folder without git.

## SOLID: what a gate measures

| Letter | Measure |
|---|---|
| S, single responsibility | Cognitive complexity and statements per function (R3) |
| O, open for extension | The branch chain (R3). It is a sign only: the reviewer checks the extension point |
| D, dependency inversion | `core:dependency-direction` and `core:boundary-integrity` in `architecture_review.py` (E3), when the repo sets `layer_map` |
| L, substitution | Not measured. The runtime has 38 classes, and none of them is a subclass of another runtime class |
| I, interface segregation | Not measured. The runtime has no interface type (`Protocol` or `ABC`) to split |

## Consequences

- The runtime line budget (`tests/contract/test_source_size_budget.py`) is 16,550 after the measures, the `bugs` and `types` tools, the secret-read guard, the plan freeze, the PR #168 review rounds and the P9 must-fix section reader. This decision records the raise. The next decision lowers it.

- An R2 scorecard with a copied helper or a thin file fails, also when the
  peer lists it. A peer that writes the old `dry_measurement` key still passes.
- R3 can fail on duplication or code shape. A pre-existing copy or long
  function that did not grow passes with a `warn` verdict.
- The limits of each measure are in the `gamed` samples. Examples: a copy with
  one changed line in the middle, a long job split into a chain of small
  functions, and a thin file with two empty tests.
- The duplication measure sees only the changed Python files. A new copy of an
  unchanged file is not found.
- A golden sample or verdict that changes is a product change and needs a
  CHANGELOG line.
- The runtime size budget goes from 15,200 to 15,850 lines. This change adds
  about 620 lines (`duplication.py`, `test_shape.py`, the R2 and R3 gate code and
  the config floors). The tree has 15,744 tracked runtime lines after the change.
