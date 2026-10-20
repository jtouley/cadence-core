# Tool gates: measured scores in place of peer judgement

This doc records a proof of concept (POC) and the plan that follows from it.
The POC asks one question. Which open-source tools can replace a score that a peer gives by judgement today?
Each tool must give a measurement that a gate script computes on the diff.

The external bar is the tool gate of an external benchmark.
It already pins `ruff==0.17.0`, `radon==6.0.1`, `complexipy==8.0.1`, `mypy==2.4.0`, `coverage==7.16.2` and `jscpd@5.4.1`.
This plan uses the same pins where they exist, so a Cadence gate and the benchmark give the same number.

The gates run on the repo that a Cadence run targets, not on this repo. The POC used this repo
because it was at hand. A result that depends on this repo's layout, such as the `sys.path` imports
of its tests, does not decide a choice. The phase two rule at the end of section 6 says how a
choice is made.

The POC ran each tool on two targets:

- This repo: the whole tree, and a diff of 6 changed Python files with 107 added lines.
- A toy repo with nine plants. A `base` tag marks the clean commit. The next commit adds the plants.

| Plant | What the change adds |
|---|---|
| DUP | `shipment_value` is a 10-line copy of `order_total` |
| CHAIN | `zone_for` is a 17-way `if`/`elif` chain (cognitive 18, cyclomatic 18) |
| UNTESTED | The `weight_kg > 30` branch of `surcharge` has no test |
| WEAK | The `surcharge` test asserts `> 0` only |
| UNUSED | `normalize_label` drops the result of `label.replace(...)` |
| EXCEPT | `parse_weight` has a bare `except: pass` with a blanket `# noqa` |
| SEC | `subprocess.run(f"...", shell=True)` and `pickle.loads` |
| TYPE | `box_count` returns `str` for `-> int` |
| DEAD | `_legacy_rate` has no caller |

The appendix gives the record for each tool.

## 1. Peer scores and what a tool can compute

"Yes" means that a tool gives the full verdict. "Partly" means that a tool gives a count, and the peer keeps the rest. "No" means that the item needs judgement.

### P3 plan review

| Item | Tool | What stays with the peer |
|---|---|---|
| Phases missing or out of order against `stages.json` | Partly: a stdlib parse of the plan headings | Whether a phase is real work or a heading only |
| Spec gaps and implicit assumptions | No | All. No code exists at P3 |
| `core:dry-extension`: a helper that the plan writes again | Partly: a stdlib `ast` index of the function names in `tests/conftest.py`, `scripts/` and `shared/` | A helper with a new name and the same job |
| The `## Reuse and extension` section | Yes: a stdlib heading check | The quality of the section |
| `core:standalone-package` (host path leaks) | Partly: a stdlib scan for `~/.cursor/...` paths in the plan | A leak that the plan implies but does not write |
| TDD, commit checkpoints, real quality gates | No | All |
| Weakened assertions or partial-success tests | No at P3. At R2, the mutation score measures it | All at P3 |
| Rollback and blast radius | No | All |

### P5 and E3 architecture rules

| Rule | Code today | Tool | What stays with the peer |
|---|---|---|---|
| `core:dependency-direction` | Skipped when `layer_map` is empty. A nit only when `layer_map` has entries | Yes for Python: `import-linter` `layers` contracts from `layer_map` | Other languages: `unsupported` |
| `core:boundary-integrity` | Same as above | Partly: `import-linter` `forbidden` contracts | What a boundary means |
| `core:decision-coverage` | Code | Not needed | None |
| `core:plan-conformance` | Code checks that a plan exists | Partly: a stdlib match of the plan's file list to the diff | Whether the code does what the plan says |
| `core:dry-extension` 1, reuse | Text | Partly: `jscpd` finds a copied block | A reimplementation with different text |
| `core:dry-extension` 2, Rule of Three | Text | Yes: `jscpd` clone groups with three or more places | None |
| `core:dry-extension` 3, single responsibility | R3 | Yes: R3 (cognitive complexity) | None |
| `core:dry-extension` 4, open for extension | Text | No | All |
| `core:dry-extension` 5, cloned gate shells | Text | Yes: `jscpd` finds each cloned shell (see §2) | None |
| `core:standalone-package` | Text | Yes: a stdlib scan of the changed files for host paths | The `peer_shadows.py` exception (ADR-0006) |

### P7 and R2 scorecard dimensions (`scores`, floor 7)

| Dimension | Tool | What stays with the peer |
|---|---|---|
| Coverage of behavior | Partly: `coverage.py` new-line coverage, 80 percent or more | Whether the tests cover the behavior that the ticket names |
| Assertion strength | Yes: the mutation score on the changed lines | None |
| DRY, no clone setups | Yes: `jscpd` duplication on new code | None |
| `dry_measurement.helpers_reimplemented` | Yes: a stdlib `ast` match of the local helper names to `tests/conftest.py` | A helper with a new name |
| `dry_measurement.helpers_reused` | Yes: a stdlib import scan | None |
| `dry_measurement.thin_files` | Yes: a stdlib count of `test_` functions in each changed test file | Whether another file tests the same module |
| Layer correctness | No | All |
| Failure paths | Partly: branch coverage, and the surviving mutants on `except` lines | Which failure paths the ticket needs |
| Mutation resilience | Yes: `cosmic-ray` on the changed lines | None |
| `adversarial_inputs` | No | All. The peer runs each input |

### R1 review-rigor sections

| Section | Tool | What stays with the peer |
|---|---|---|
| Reproductions | No | All |
| End-to-end walk | No | All |
| Adversarial inputs | No | All |
| Contracts | No | All |
| Design choices | No | All |
| Scope | Partly: a stdlib `git diff --numstat` gives the file count and the mix of docs and code | Whether the title type matches the diff |
| Lookback | No | All |
| Reuse and extension | Partly: `jscpd --baseline-from-ref` lists added clones. `git diff --numstat` gives the net test lines | The helpers that the change reuses, and the extension point |
| Bugs in the changed code | Partly: `ruff` bug and security rules, and `mypy` new errors | Logic bugs. No static tool found the UNUSED plant |

### A5 vibe-test

| Item | Tool | What stays with the peer |
|---|---|---|
| Scenarios, gaps, conflicts, exit criteria | No | All. No code exists at A5 |

## 2. Recommended set

Each measure gives one verdict: `pass`, `warn` or `fail`. The verdict comes from one count and one threshold.
A measure that cannot give a verdict for a language reports that language as `unsupported`.
There is no "approximate" result.

| Measure | Block | Tool and pin | Gate rule | Ratchet | `unsupported` |
|---|---|---|---|---|---|
| Duplication on new code | R2 (DRY), evidence for E3 | `jscpd@5.4.1` on `nodejs-wheel==24.19.0` | Fail when the duplicated share of the added code lines is above 3 percent | `--baseline-from-ref <merge base>`. Only a clone with `isNew: true` counts | Fail for a changed code file in a format that `jscpd` does not read |
| New-code coverage | R2 (coverage, failure paths) | `coverage==7.16.2` | Fail when the covered share of the added executable lines is below 80 percent | Added lines only | Warn for a changed file in a language with no coverage tool |
| Mutation score on changed lines | R2 (assertions, mutation) | `cosmic-ray==8.7.0` with `cr-filter-git` | Fail when killed / (killed + survived) is below 80 percent | Changed lines only | Warn for a changed file that is not Python |
| Bug and security findings | R1, E3 | `ruff==0.17.0` | Fail on one finding on an added line | Added lines only | Warn for a changed file that is not Python |
| Type errors | R1 | `mypy==2.4.0`, `--check-untyped-defs` | Fail on one error on an added line | Added lines only | Warn for a changed file that is not Python |
| Cognitive complexity | R3 (no change of tool) | `scripts/cognitive_complexity.py` for Python | No change: over 15 and new or worse | Merge base, as today | C-family: see the note below |
| Dependency direction | P5, E3 | `import-linter==2.15` | Fail on one broken contract | Contracts come from `layer_map` | Warn when `layer_map` is empty, as today |

Notes on the set:

- A timeout in the mutation measure counts as `survived`. The verdict record says how many mutants timed out. Each mutant gets a fixed budget (`timeout_seconds`).
- `cosmic-ray` makes the same mutants in the same order on each run. It uses no random seed.
- The duplication rule enforces `core:dry-extension` rule 5. A new 16-line gate shell is 75 percent duplicated. Such a shell must be a `stages.json` row instead.
- Every Cadence script must find its root from its own path (`AGENTS.md`). `--ignore-pattern import,_SCRIPTS,sys.path` removes 31 of the 41 clones of that preamble. The toy plant still fails.
- The ruff rule set is `E9,F63,F7,F82,F811,B,PLE,E722,PGH004,S`. The measure ignores `S101,S603,S607,S404,S105,S106`. `PGH004` fails a blanket `# noqa`, so a suppression must name its rule.
- The rules of `ruff` and `mypy` are part of the pinned binary. A version pin is the rule-set pin. Nothing comes from the network at run time.
- R3 keeps its own analyzer for Python. It gave the same score as `complexipy` on each toy function. On the repo, 2284 of 2813 functions match. The rest differ by definition: `complexipy` counts each `if` in a comprehension.
- R3 must report TypeScript as `unsupported`, or use an exact tool. On `pi/cadence-gates.ts`, `eslint-plugin-sonarjs@4.2.2` gives `decide` 12. The token scanner gives 17, which is over the cap.

### Proposed config keys

These keys are a proposal. The config schema does not declare them, so a config layer that sets them fails today.

```json
"measures": {
  "duplication": {
    "tool": "jscpd", "version": "5.4.1", "runtime": "nodejs-wheel==24.19.0",
    "min_lines": 5, "ignore_pattern": "import,_SCRIPTS,sys.path",
    "max_new_pct": 3.0, "unsupported": "fail"
  },
  "new_code_coverage": {
    "tool": "coverage", "version": "7.16.2",
    "test_command": ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider"],
    "patch_subprocess": true, "min_pct": 80.0, "unsupported": "warn"
  },
  "mutation": {
    "tool": "cosmic-ray", "version": "8.7.0", "scope": "changed-lines",
    "timeout_seconds": 30, "timeout_counts_as": "survived",
    "max_mutants": 300, "min_score": 80.0, "unsupported": "warn"
  },
  "bug_findings": {
    "tool": "ruff", "version": "0.17.0",
    "select": "E9,F63,F7,F82,F811,B,PLE,E722,PGH004,S",
    "ignore": "S101,S603,S607,S404,S105,S106", "max_new": 0, "unsupported": "warn"
  },
  "type_errors": {
    "tool": "mypy", "version": "2.4.0", "args": ["--check-untyped-defs", "--ignore-missing-imports"],
    "max_new": 0, "unsupported": "warn"
  },
  "dependency_direction": {
    "tool": "import-linter", "version": "2.15", "contracts_from": "layer_map", "unsupported": "warn"
  }
}
```

A change with more than `max_mutants` changed-line mutants gets `fail`, with the count in the record. The gate does not sample, because a sample is not the same on each run.

### Tools that the POC does not recommend for a gate

| Tool | Reason |
|---|---|
| Semgrep 1.178.0 | It found the two SEC plants only, and `ruff` finds them too. The install is 334 MB. The registry rules use the Semgrep Rules License v1.0, which is not an OSI license. Do not vendor those rules into this MIT repo |
| bandit 1.9.4 | Its rules are the source of the `ruff` `S` rules. It adds one tool and gives the same verdicts |
| pylint 4.1.2 `duplicate-code` | It writes the clone ranges inside the message text. Two runs print a different side of the same clone |
| pyright 1.1.414 | It exits 0 with `filesAnalyzed: 0` under a dot directory, such as `.context/`. A gate must read `filesAnalyzed`. `mypy` gives the same toy verdict without Node |
| radon 6.0.1 `cc`, lizard 1.24.0 | Cyclomatic complexity repeats R3. `radon` adds 1 for each `assert`, so test functions score high. Radon and lizard agree on 1462 of 2820 functions |
| radon `mi` | It did not flag the toy (`pkg/shipping.py` gets 56, rank A) |
| vulture 2.16 | It found DEAD. At 60 percent confidence it also flags `visit_TryStar`, which `ast.NodeVisitor` calls by name. Use it as R1 evidence only |
| mutmut 3.8.0 | It is six times faster than `cosmic-ray`. It cannot map a mutant when a test imports a module through `sys.path`, as all Cadence tests do. Its per-mutant limit scales with the measured test time, and its score counts a timeout as killed |

## 3. How a gate runs a tool

The runtime stays stdlib only. A gate never imports a tool. It starts the tool as an external process with `subprocess.run`, with a list of arguments, a timeout and a working directory.

1. The gate reads the measure from the resolved config (`scripts/cadence_config.py`). An unknown key or a bad value fails the gate.
2. The gate gets the added lines from `git diff -U0 --no-color <merge base>` (stdlib parse of the `@@` headers).
3. The gate starts the tool with the pinned version. The tool writes its report to a file in a temporary directory.
4. The gate reads the report with `json`. A missing report, a non-JSON report or an exit code that is not on the tool's list fails the gate.
5. The gate keeps the findings on added lines, counts them, and compares the count to the threshold.
6. The gate writes one verdict record to `.context/gates/<slug>.json`.

| Measure | Command | Report | Field that the verdict reads |
|---|---|---|---|
| Duplication | `uvx --from nodejs-wheel==24.19.0 npx --yes jscpd@5.4.1 --config <empty.json> --no-gitignore --min-lines 5 --ignore-pattern <p> --reporters json --output <tmp> --baseline-from-ref <base> .` | `<tmp>/jscpd-report.json` | Each `duplicates[]` entry with `isNew: true`: `firstFile`/`secondFile` `name`, `start`, `end`, counted on added lines |
| Coverage | `uv run --with coverage==7.16.2 coverage run -m <test_command>`, then `coverage combine` and `coverage json -o <tmp>/cov.json` | `cov.json` | `files[path].executed_lines` and `missing_lines`, intersected with the added lines |
| Mutation | `uvx --with pytest==9.1.1 --from cosmic-ray==8.7.0 cosmic-ray init`, then `cr-filter-git --config`, then `cosmic-ray exec`, then `cosmic-ray dump` | JSON lines | `test_outcome` of each job that is not `SKIPPED`: `killed`, `survived`, or a timeout |
| Bugs | `uvx ruff==0.17.0 check --no-cache --isolated --output-format json --select <s> --ignore <i> <files>` | stdout | `filename`, `location.row`, `code` |
| Types | `uvx mypy==2.4.0 --check-untyped-defs --ignore-missing-imports --no-incremental --cache-dir <tmp> --output json <files>` | stdout, one JSON object per line | `file`, `line`, `severity == "error"` |
| Layers | `uvx --from import-linter==2.15 lint-imports --no-cache --config <tmp>/.importlinter` | stdout and the exit code | Exit code 1 and the line `Contracts: N kept, M broken.` |

Rules for each run:

- Give `jscpd` one root (`.`). With two or more roots it names each file relative to its root, so `scripts/x.py` and `hooks/x.py` look alike.
- Give `jscpd` an empty config and `--no-gitignore`. A repo file must not change the scan.
- Point every tool cache at the temporary directory. `complexipy` writes `.complexipy_cache/` and `mypy` writes `.mypy_cache/` to the working directory otherwise.
- Run `cosmic-ray exec` in a clone of the worktree. It edits the source file in place while it runs.
- Write `[cosmic-ray.filters.git-filter] branch = "<merge base sha>"` in the config. The `--branch` flag does not exist.

Each verdict record holds these keys:

```json
{"measure": "duplication", "verdict": "fail", "count": 10, "denominator": 125, "value": 8.0,
 "threshold": 3.0, "rule": "new duplicated lines / added code lines <= 3.0 percent",
 "tool": "jscpd", "version": "5.4.1", "runtime": "nodejs-wheel==24.19.0",
 "args_sha256": "<hash of the argument list>", "rules_sha256": "<hash of the select set or contract file>",
 "base": "<merge base sha>", "head": "<sha>", "unsupported": [], "timed_out": 0}
```

A golden corpus can pin each verdict. Each sample is a small git repo with a `base` tag, like the toy. Every recommended tool also runs on a plain directory, so a whole-tree sample works too.

## 4. Cost

Wall times are for this repo on a 4-core container. The uv and npx caches were warm unless the table says "cold".

| Measure | Wall time | First run (cold install) |
|---|---|---|
| Duplication (`jscpd`, whole tree, 260 files) | 1.0 s | 38 s for `nodejs-wheel` (205 MB). With host Node: 1.4 s (45 MB) |
| Coverage (full suite under coverage, subprocess patch) | 636 s, against 318 s for the plain suite | 0.3 s |
| Mutation (`cosmic-ray`, 38 changed-line mutants of `scripts/test_benchmark_gate.py`) | 48 s | 3.4 s (50 MB) |
| Mutation (`cosmic-ray`, all 349 mutants of `scripts/findings_io.py`) | 295 s and 339 s | Not applicable |
| Bugs (`ruff`, whole tree) | 0.1 s | 0.4 s (24 MB) |
| Types (`mypy`, `scripts`, `hooks`, `shared`) | 2.0 s | 19 s (58 MB) |
| Layers (`import-linter`, toy) | 1.0 s | Not measured |

What a run adds:

- Coverage adds one more full test run. Today no gate runs the suite. That costs about 5 to 10 minutes on this repo.
- Mutation adds about 1.3 s for each changed-line mutant here. The `max_mutants` cap of 300 limits it to about 6.5 minutes.
- The other measures add less than 5 s together.
- A first run on a new machine adds about 60 s of installs. Semgrep would add another 124 s.

The mutation measure can reuse the coverage data. A test that runs no changed line cannot kill a mutant of that line. Then the measure can run fewer tests for each mutant.

## 5. What stays with the peer

A tool cannot judge intent, design or the ticket. These items stay with the peer, and the review contract shrinks to them:

- A5: every item. No code exists yet.
- P3: spec gaps, implicit assumptions, TDD and commit checkpoints, rollback and blast radius.
- P5 and E3: open for extension (`core:dry-extension` rule 4), boundary meaning, and plan conformance of the content.
- P7 and R2: layer correctness, the behavior that the ticket names, and `adversarial_inputs`. The peer runs each input and records what it saw.
- R1: Reproductions, End-to-end walk, Adversarial inputs, Contracts, Design choices and Lookback. Also the parts of Scope and Reuse and extension that need a reason.
- R1: logic bugs. Every static tool missed the UNUSED plant. Only the surviving mutants of `normalize_label` pointed at it.

The peer stops writing these numbers in `scores`: DRY, mutation resilience, and the coverage part of "coverage of behavior". The R2 gate reads them from the measure records.

## 6. Order to implement

Start with the smallest item. Each item has its own tests. Write the failing test first.

**Phase one: items 2 and 3.** R3 holds the `bugs` (`ruff`) and `types` (`mypy`) measures. They are not a `stages.json` row.
`scripts/tool_measures.py` holds the added-line parse, the tool call and the two records. It does not hold the config load, so it is not the shared runner of item 1.
The two measures are different from items 2 and 3 below in these points:

- The config sections are `bugs` and `types`, with `max` (0), `timeout_seconds` (60) and `unsupported` (`warn`). The schema marks `max` and `timeout_seconds` with `x-cadence-stricter`, so a config layer can only make them stricter. `cadence.defaults.json` sets each of the two sections to its default.
- The version pins are in code (`TOOLS` in `scripts/tool_measures.py`), not in the config.
- The `bugs` rules are `RUFF_RULES` in `scripts/tool_measures.py`. The select set is `E9,F63,F7,F82,F811,B,PLE,E722,PGH004,S`. The ignore set is `S101,S603,S607,S404,S105,S106,B008,B904,S108`. A config value cannot change the two sets. The reasons for each ignored group:
  - `S101` (`assert`): tests and invariant checks use `assert`. It is not a bug.
  - `S404`, `S603`, `S607` (a `subprocess` import, a call without a shell, a program name without a path): a gate starts tools in this way. `S602` (`shell=True`) stays in the set.
  - `S105`, `S106` (a password in a string or an argument): the rule reads the variable name, so a constant such as `TOKEN_KEY = "token"` fails. The secret read guard and the R1 review look for real secrets.
  - `B008` (a function call in a default argument): frameworks use it, for example FastAPI `Depends()`.
  - `B904` (`raise` in `except` without `from`): it is a style of exception chains. The traceback keeps the first error.
  - `S108` (a `/tmp` path in a string): a path string in a test or an example is not a bug.
  - `S110` (`try`, `except`, `pass`) stays in the set: it hides an error.
- The gate starts each tool with `uv tool run --offline`. A tool that is not in the `uv` cache gives `unsupported`, and the record names the cache warm command `uv tool run <tool>==<pin> --version`. `./install.sh --tools` runs it for both tools. It puts nothing on `PATH`, so the user's own `ruff` and `mypy` stay.
- A timeout gives `unsupported` with `timed_out: true`. It never gives `pass`.
- mypy runs once for each group of files with different names, because two `conftest.py` files stop mypy. The gate also adds `--config-file=`, so a repo config cannot change the rules.
- Under `CADENCE_GATES_READONLY` (the stop guard walk) the tools do not run. The record says so, and the verdict is `warn`. The stop guard gives R3 4 s, and the two tools add 2 to 4 s to R3. On a change of 10 Python files, R3 takes 2.6 s with the tools and 0.5 s without them. On a change of 219 Python files, R3 takes 5.6 s with the tools and 2.1 s without them.
- A targeted `# noqa: <code>` and a `# type: ignore` on an added line hide a finding. The `gamed` samples `gamed/bugs/targeted_noqa` and `gamed/types/type_ignore` show this limit. The R1 review names each one.

1. Add the shared runner `scripts/measure_gate.py`. It holds the added-line parse, the config load, the tool call and the verdict record.
   Tests in `tests/gates/`: a `parametrize` table of diffs (new file, removed file, rename, no newline at the end). Also a bad config, a missing tool and a non-JSON report. Each of the last three must fail.
2. Add the `bug_findings` measure (`ruff`). It is the fastest tool and needs one binary.
   Tests: a corpus sample with a bare `except` under `# noqa` and `shell=True` fails. A clean sample passes. A finding on an unchanged line does not count.
3. Add the `type_errors` measure (`mypy`). Tests: the TYPE sample fails. An old error on an unchanged line passes.
4. Add the `duplication` measure (`jscpd`). Tests: the DUP sample fails. A copy of the `sys.path` preamble alone passes. A clone that exists at the base passes.
5. Add the `new_code_coverage` measure. Tests: the UNTESTED sample fails at 67.6 percent. A test that runs a gate through `subprocess` counts the lines that it runs.
6. Add the `mutation` measure. Tests: the WEAK sample fails. A timeout counts as survived. Two runs give the same record. A change over `max_mutants` fails.
7. Add the `dependency_direction` measure at P5 and E3. It writes the contracts from `layer_map`. Tests: a layer violation fails. An empty `layer_map` keeps the current notice.
8. In R3, report each C-family file as `unsupported` until a corpus shows that the token scanner matches `eslint-plugin-sonarjs`. Tests: the `pi/cadence-gates.ts` sample gets `unsupported`.
9. Shrink the peer contracts. Remove the measured dimensions from `skills/test-benchmark/SKILL.md` and `references/review-rigor.md`. Make the R2 gate read the measure records.
   Tests in `tests/contract/` and `tests/gates/`.
10. Add a CI job that runs the measures on each pull request, like the `complexity` job in `.github/workflows/ci.yml`.

Each tool test that starts a real tool needs the network for the first install. Mark it like the Node tests: CI sets an environment variable that makes a missing tool fail the test. A local run without the tool skips it.

### Phase two acceptance rule

Phase one is the `bugs` and `types` measures. Phase two is duplication, coverage, mutation and
dependency direction. The tool for each phase two measure is open. The choice is made on target
repos, not on this repo.

1. Take three target repos from the task set of an external benchmark, with a package layout and a test
   suite that runs under five minutes.
2. Run each candidate tool twice on the same change of each repo. The two verdict records must be
   equal in every field but the wall time.
3. The tool must run on a plain directory and on a diff. Its output must reduce to one count that
   the verdict reads. A result that a peer must read to judge fails this rule.
4. For mutation, the per-mutant limit must be a fixed number of seconds, and a timeout must count
   as survived. A mutant with no test that covers it must stay out of the kill rate, or the record
   must report it as its own count.
5. The run on the change must end inside the gate's time budget. The budget is in the measure's
   config section.
6. Record each run in the appendix of this doc, with the tool version and the repo.

The faster tool wins when two tools pass every rule. For mutation, `mutmut` is about six times
faster than `cosmic-ray`. It fails rule 4 today on the timeout and on the mutants with no test.
It fails on this repo because the tests import through `sys.path`, and that failure does not
count. If a `mutmut` setting fixes rule 4, `mutmut` is the default. If not, `cosmic-ray` is.

## Appendix: tool records

Hit and miss refer to the toy plants. "Same twice" compares a sha256 of the normalized report from two runs. The POC removes run-specific keys first (timestamps, timings).
"Dir" says whether the tool runs on any directory without a git diff.

| Tool and pin | Install | Repo wall | Measures | Report | Toy | Repo false positive (example) | License | Last release | Same twice | Dir |
|---|---|---|---|---|---|---|---|---|---|---|
| `jscpd@5.4.1` | 45 MB, plus 205 MB `nodejs-wheel` | 1.0 s | Token clones, 5 lines or more | JSON, SARIF | DUP hit | `sys.path` preamble in 41 of 68 clones | MIT | 2026-10-09 | Yes | Yes |
| `pylint==4.1.2` `duplicate-code` | 13 MB | 5.5 s | Line clones | JSON, ranges in message text | DUP hit | None seen. The 7 clones are real copies | GPL-2.0-or-later | 2026-10-11 (4.1.3) | Ranges yes, text no | Yes |
| stdlib normalized blocks (POC) | 0 | 0.4 s | 5-line windows without imports or docstrings | jscpd-shaped JSON | DUP hit | Repeated test setup in one file | Not applicable | Not applicable | Yes | Yes |
| `scripts/cognitive_complexity.py` | 0 | 0.6 s | Cognitive per function | Text | CHAIN hit (18) | TS `register` 53: nested arrows add to it | MIT | Not applicable | Yes | Yes |
| `complexipy==8.0.1` | 9.8 MB | 0.2 s | Cognitive per function | JSON, no line numbers | CHAIN hit (18) | `selftest` 35, own 9: comprehension `if` counted | MIT | 2026-09-07 | Yes | Yes |
| `radon==6.0.1` `cc` | 1.1 MB | 0.7 s | Cyclomatic per function | JSON | CHAIN hit (18) | A test with 9 asserts scores 10 | MIT | 2023-03-26 | Yes | Yes |
| `radon==6.0.1` `mi` | 1.1 MB | 2.0 s | Maintainability index per file | JSON | CHAIN miss | `scripts/artifact_requirements.py` gets 0.0, rank C | MIT | 2023-03-26 | Yes | Yes |
| `lizard==1.24.0` | 6.5 MB | 1.0 s | Cyclomatic, many languages | CSV, XML | CHAIN hit (18) | A script of a former skill: 56, radon 68 | MIT | 2026-10-05 (1.24.1) | Yes | Yes |
| `mutmut==3.8.0` | 32 MB | 46 s, 537 mutants | Mutation score | `mutmut-cicd-stats.json`, text | WEAK hit (16 survived), UNUSED 2 survived | 126 "no tests" mutants count against the score | BSD-3-Clause | 2026-09-12 | Yes | Package layout only |
| `cosmic-ray==8.7.0` | 50 MB | 295 s, 349 mutants. 48 s for the diff | Mutation score | SQLite, `dump` JSON lines | WEAK hit (72 survived) | Equivalent mutants, not measured | MIT | 2026-08-09 | Yes | Yes |
| `coverage==7.16.2` | 1.8 MB | 636 s, full suite | Line and branch coverage | JSON | UNTESTED hit (line 64 missed) | None. Diff lines 67 to 68 have no test | Apache-2.0 | 2026-09-27 | Not run twice | Yes |
| `semgrep==1.178.0`, `p/python` and `p/security-audit` | 334 MB | 14.7 s | Rule matches | JSON, SARIF | SEC hit (2), EXCEPT and UNUSED miss | `hashlib.sha1` for a branch slug | Engine LGPL-2.1, rules Semgrep Rules License v1.0 | 2026-10-14 (1.181.0) | Yes | Yes |
| `bandit==1.9.4` | 13 MB | 1.6 s, 2.9 s for tests | Security rules | JSON | SEC hit, EXCEPT hit (B110, LOW) | B613 on the regex that removes bidi characters in `handoff.py` | Apache-2.0 | 2026-02-25 | Yes | Yes |
| `ruff==0.17.0` | 24 MB | 0.1 s | Lint, bugbear, pylint, bandit port | JSON, SARIF | SEC hit, EXCEPT hit (`PGH004`), CHAIN hit (C901), UNUSED miss | `PLE2515` on a zero-width character pattern | MIT | 2026-10-09 (0.17.1) | Yes | Yes |
| `pylint==4.1.2` (W, E) | 13 MB | 11.1 s | Errors and warnings | JSON | EXCEPT hit, SEC and UNUSED miss | 41 `import-error` from the `sys.path` layout | GPL-2.0-or-later | 2026-10-11 (4.1.3) | Yes | Yes |
| `mypy==2.4.0` | 58 MB | 2.0 s | Type errors | JSON lines | TYPE hit | 63 errors in the tree, 0 new | MIT | 2026-10-01 | Yes | Yes |
| `pyright@1.1.414` | 38 MB | 6.0 s | Type errors | JSON | TYPE hit only with `exclude: []` | 61 `reportMissingImports` from the `sys.path` layout | MIT | 2026-09-10 | Yes | Yes, not under a dot directory |
| `vulture==2.16` | 0.3 MB | 1.3 s | Unused code | Text only | DEAD hit | `visit_TryStar` (called by `ast.NodeVisitor`) | MIT | 2026-03-25 | Yes | Yes |
| `import-linter==2.15` | Not measured | 1.0 s (toy) | Import contracts | Text and exit code | SEC caught by a `forbidden` contract | Not run on the repo: no packages | BSD-2-Clause | 2026-09-04 | Not run twice | Package layout only |
| `eslint-plugin-sonarjs@4.2.2` | 65 MB with `eslint@10.12.0`, `typescript-eslint@8.71.0`, `typescript@5.9.3` | 2.2 s (2 TS files) | Cognitive for JS and TS | JSON | Not applicable (TS) | Not applicable | LGPL-3.0-only | 2026-09-28 | Not run twice | Yes |

Each tool installed through the proxy with `uvx` or `npx`. No install failed.
These items did not work as the docs say, or needed a workaround:

- `mutmut` stopped with "tests import the source under a different module path". It ran only after the POC moved `scripts/` to `src/` in a copy and linked it back.
- `pyright` analyzed 0 files under `.context/` and exited 0.
- `jscpd --ignore-pattern` with an anchored regex changed nothing. It matches tokens, not lines.
- `cr-filter-git --branch` is not a flag. The branch goes in the config file.
- `cosmic-ray` took 174 s for 349 toy mutants. `mutmut` took 4.8 s for 214 toy mutants.
