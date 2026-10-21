---
name: test-benchmark
description: >
  Score test strategy or test code for DRY, layer, assertions, and mutation
  resilience. Use for Cadence P7 (plan) and R2 (code), or when asking if
  tests would catch real bugs.
---

# Test benchmark (Cadence P7 / R2)

Load `$CADENCE_HOME/references/peer-contract.md` (code mode also loads
`$CADENCE_HOME/references/review-rigor.md`).

## Mode

- `/test-benchmark plan <plan>` — score the plan's test spec (no scripts).
- `/test-benchmark` or `--code` — score tests that exist.
- `/test-benchmark <PR-number>` — R2 on a pull request: score the tests of its change.

## Deliverable

`.context/benchmarks/{task_id}_plan.json` (plan mode) or
`.context/benchmarks/{task_id}_code.json` (code mode). The gates and P9 read
only these paths. Top-level keys:

```json
{"RUN_BY_SKILL": "test-benchmark", "task_id": "<task_id>", "scores": {}, "findings": []}
```

Code mode adds `adversarial_inputs`: one entry per input class in
`review-rigor.md` (missing, corrupt, other ticket, resume, adjacent id,
offline), each `{"input", "expected", "observed"}`, run for real. An input
class that doesn't apply gets `"observed": "n/a: <reason>"`. The R2 gate fails
without them.

Scorecard dimensions (1–10): coverage of behavior, assertion strength, DRY /
no clone setups, layer correctness, failure paths, mutation resilience.

The floor is `benchmark_score_floor` of the resolved config (default 7). A config
layer can only raise it. A score below the floor on layer, DRY, or assertions is a
must-fix finding. So is a mean score below the floor. Set `verdict` to `FAIL` in
those cases, and to `PASS` otherwise. The R2 gate fails on any `verdict` other than `PASS`,
and on a score below that floor.

## DRY measurement

The R2 gate computes the DRY lists (ADR-0008). Do not make the lists with grep.
In code mode, run this command before you score:

```bash
python3 "$CADENCE_HOME/scripts/test_shape.py" --change   # the change against the base that R3 uses
```

The script reads the changed test files and writes three lists:

- `helpers_reimplemented`: each function in a changed test file whose name or
  normalized body is a function of a conftest file. A copy with new names counts.
- `helpers_reused`: each conftest name that a changed test file imports.
- `thin_files`: each changed test file with one or two tests that imports a
  module of a runtime directory that another test file also imports.

The script reads the layout of the repo from `test_shape.layout` of the
resolved config: `test_dirs`, `conftest` and `runtime_dirs`. For a key
that is not set, the script finds the value in the repo (ADR-0014). The output
names the layout under `layout`. When the script finds no part of the layout that a measure needs, the
record of that measure has an `unmeasured` entry. Its verdict is `warn`, or
`fail` when `test_shape.unsupported` is `fail`. Write that the measure did not
run. Do not write that it passed.

Each entry names the file and the helper or the module, for example
`tests/gates/test_x.py: _session (tests/conftest.py: make_session)`.

The R2 gate fails in code mode when `helpers_reimplemented` or `thin_files`
has more entries than its `test_shape` limit in the resolved config. The default
limit is 0. A config layer can make a limit stricter, but not looser. The gate names
each entry in its fail text and writes the lists to
`.context/gates/test-benchmark-gate-code.json` for the R1 review.

Each measure has one record with `value`, `threshold`, `threshold_source` and
`verdict` (`pass`, `warn` or `fail`). Read `verdict`. Do not judge the code again.
Base the DRY score on the records.

The `dry_measurement` key in the scorecard is optional. When you write it, copy
the three lists. The gate compares the key with the computed lists. It writes a
note for each difference. A note does not change the verdict.

In plan mode, measure the test spec and the `## Reuse and extension` section
of the plan.

Prefer a new row in an existing `pytest.mark.parametrize` table to a new test
file. This applies to a mutation finding and to an adversarial input.

## Findings

Write each scorecard finding as
`{"id": "cd-52-F01", "severity": "must-fix", "evidence": "..."}`. The R2 gate
treats `severity` `must-fix`, `HIGH` or `critical`, or `"must_fix": true`, as a
must-fix finding. In code mode, append a must-fix ledger row with the same `id` with
`findings_io.py` before the turn ends. A row that later becomes `fixed`,
`verified` or `deferred` still counts. The index reads the findings ledger.
A finding that exists only in the scorecard JSON is not registered, and the
R2 gate rejects it. Use severity `must-fix` and include `check`. The scorecard
word `HIGH` is not a ledger severity.

In plan mode, write no ledger row. A ledger row closes with a fix commit, and the plan is not
code. The P7 gate fails while the plan scorecard does not pass. The plan peer applies the
findings, and a new plan scorecard closes them.

If mutants were not run, say so. Do not invent a kill rate.
Run each mutant in a copy outside the worktree, made with `mktemp -d`, and delete the copy after the
run. Leave no file in the worktree: a mutant copy there fails R3 on duplication.

Chat: C.O.R.E. with the scorecard path. When a Cadence run delegates this peer, replace the C.O.R.E. close with the one-line return in `peer-contract.md`.
