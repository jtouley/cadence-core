---
decision_id: ADR-0014
status: accepted
question: Where does the R2 test-shape measure find the test files, the conftest files and the runtime modules of the repo that it measures?
choice: scripts/test_shape.py reads `test_shape.layout` of the config (`test_dirs`, `conftest`, `runtime_dirs`). A key that is not set is detected from the repo. A measure whose part of the layout is not found is unsupported, and `test_shape.unsupported` gives its verdict (warn by default, fail when set). It is never an empty pass.
rationale: test_shape.py had the paths of Cadence itself (`tests/conftest.py`, `scripts/`, `hooks/`, `shared/`). On a repo with `src/`, `lib/` or `tests/unit/conftest.py`, the three lists stayed empty and R2 passed with nothing measured. The benchmark target repos have such layouts.
rejected:
  - alt: Keep the fixed paths and document them
    why_not: R2 then passes on most repos with nothing measured, and the record does not say so
  - alt: Require `test_shape.layout` in every repo
    why_not: Most Python repos declare their packages in pyproject.toml or setup.cfg, so the layout can be found. A config key stays for the repo where detection is wrong
  - alt: Fail by default when a part of the layout is not found
    why_not: Each other measured section (`cognitive_complexity`, `duplication`, `code_shape`, `bugs`, `types`) has `unsupported` warn by default. A repo with no conftest.py is common. The `warn` verdict and the `unmeasured` entry say that the measure did not run, and `unsupported` fail makes it block
  - alt: Read `[tool.hatch]` and other build backends too
    why_not: Each backend has its own keys. The directory scan finds the package of a backend that this rule does not read
links:
  - rel: implements
    target_type: file
    target_id: scripts/test_shape.py
  - rel: implements
    target_type: file
    target_id: scripts/cognitive_complexity.py
  - rel: implements
    target_type: file
    target_id: scripts/test_benchmark_gate.py
  - rel: relates
    target_type: file
    target_id: tests/contract/test_source_size_budget.py
  - rel: relates
    target_type: file
    target_id: docs/adr/0008-measured-dry-gates.md
---
# ADR-0014: test_shape reads the layout of the target repo

## Context

ADR-0008 made R2 compute three lists with `scripts/test_shape.py`. The script had the
layout of Cadence: one `tests/conftest.py` and the runtime directories `scripts/`,
`hooks/`, `shared/` and `shared/scripts/`. On another layout the lists were empty, and
an empty list is a pass. The R2 gate passed and measured nothing (gh-180).

## Decision

1. **Config first.** `test_shape.layout` in the skill-root config file (now any config layer, ADR-0016) is a map with the keys
   `test_dirs`, `conftest` and `runtime_dirs`. Each value is a list of paths relative
   to the repo root. A path that the repo does not have, a value that is not a list
   and an unknown key fail R2 closed. `load_config` passes the map through
   (`cognitive_complexity.MAPS`). `test_shape.py` checks it.
2. **Detection for each key that is not set.**
   - `test_dirs`: the top-level `tests/` and `test/` directories.
   - `conftest`: each `conftest.py` under the test dirs, outside `measure_exclude`,
     sorted. When two files define a name, the first file wins.
   - `runtime_dirs`: the package directories that `pyproject.toml` names
     (`[tool.setuptools] packages`, `[tool.setuptools.packages.find] where`, the
     `[project] name`) or that `setup.cfg` names (`[options] packages`,
     `[options.packages.find] where`, `[metadata] name`). A name is looked up at the
     root, under `src/` and under each `where`. Only a directory that holds a `.py`
     file counts. When none is found: each directory that holds a `.py` file,
     outside the test dirs, `measure_exclude`, hidden directories and tool directories
     (`__pycache__`, `node_modules`, `venv`, `build`, `dist`, `site-packages`).
     The scan goes below the top level, so `shared/scripts/` and `src/<pkg>/` count.
3. **Module lookup.** A thin file imports a dotted module. The module is a file of a
   runtime dir: flat (`scripts/mod.py` for `mod`) or under its package
   (`src/pkg/mod.py` for `pkg.mod` when the runtime dir is `src/pkg`).
4. **Not found is unsupported.** `helpers_reimplemented` needs test dirs and a conftest
   file. `thin_files` needs test dirs and a runtime dir. When a part is empty, the
   record has an `unmeasured` entry that names the config key to set. The verdict is
   `warn`, or `fail` with `test_shape.unsupported` = `fail`. R2 lists the
   `unmeasured` entries of a failed record, so a fail with no offender still blocks.
5. **The output names the layout.** The result of `test_shape.py` has `layout`, with
   `source` `config` or `detected` for each key.
6. **A golden sample.** `tests/fixtures/golden/{good,bad}/test_shape/src_layout/` is a
   `src/` layout with `tests/unit/conftest.py`. The bad sample copies the conftest helper
   and is a thin file. The good sample imports the helper and tests a new module.

## Consequences

- On Cadence nothing is declared (`[project] name` is `cadence-harness`, which is no
  directory), so the scan finds the runtime dirs. They include all four of the old list
  and also `examples/toy-run`, `packs/_sample_fail_gate`, `scripts/stubs` and the
  `scripts/` of the bundled skills. The conftest file is `tests/conftest.py`.
- A `pyproject.toml` or `setup.cfg` that does not parse fails R2 closed.
