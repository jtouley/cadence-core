# Review rigor (E3, R1, R2)

Load this for every review stage after TDD and PR creation: resolving **E3**
architecture findings, the **R1** PR review, and the **R2** test review. The
reviewer does the verification. Never hand the author "please check whether X".
If you can't reproduce a finding, drop it.

Background and worked example: [`docs/review-followup.md`](../docs/review-followup.md).

## R1 review artifact: required sections

`.context/prs/reviews/PR_REVIEW_#{N}.md` must carry every heading below, in
any order. `pr_review_local_gate.py` fails without them. When a section doesn't
apply, write `None` and one line saying why.

### `## Reproductions`

For each must-fix: the command you ran and the output you saw, against the PR
head. Reproduce in a throwaway workspace (the scratch dir, `git worktree`, a
fresh `git init`), never the user's tree.

### `## End-to-end walk`

Run the path the PR's docs describe (`docs/quickstart.md`, the changed command
or skill) for real, with the repo's real branch naming. List each step and
whether it cleared. Unchecked test-plan items in the PR are yours to run.
Run the acceptance checks of the run (`scripts/acceptance.py run`), and compare the delivered
files with each outcome of the request (`.context/proposals/<task_id>.md`).

### `## Adversarial inputs`

For every changed gate, CLI, hook or installer, the result for each input:
missing, corrupt, another ticket's leftovers, `--resume`, an adjacent id
(`cd-1` vs `cd-12`), an offline host without `gh`. For hooks and installers,
also a workspace that never ran Cadence and a user symlink with the same name.

For any change that reads or writes data, also try these inputs. The plan
review does not find this class of defect. The PR round must find it.

- an output path that is also an input, for example `--out` set to the file that the command reads
- a closed output pipe, for example `| head -0`
- a bad row in a stored file: a missing field, or a value of the wrong type
- a trailing newline or a space in a parsed value
- a very large number, NaN or a negative number
- text that does not encode, for example a lone surrogate in a note

When the packet pins `.context/acceptance/<task_id>.repair.jsonl`, an acceptance check changed after
P9. Each `repin` row holds the text of the check before and after the change. Compare them with the
claim. A new check that is weaker than its claim is a must-fix: for example, it no longer reads the
delivered output, or it accepts an output that the claim does not.

### `## Contracts`

Check each finding against `SKILL.md`, `references/`, and the resolved config (`scripts/cadence_config.py resolve`).
A finding that contradicts written design is a decision for the author, not a
must-fix. List it here with the doc line it contradicts.

### `## Design choices`

For each finding with more than one reasonable fix (including E3 findings):

1. Write the scenario matrix first (expected results from the contracts).
2. Build simple, medium and over-engineered options, each in its own worktree
   from the same base, with strict TDD (red for the right reason, then minimal
   green).
3. Record per option: matrix score, full-suite result, diff size, existing
   tests broken.
4. Pick the winner by what its misses cost (a fail-closed false positive that
   blocks every run is worse than a false negative a later stage catches), then
   contract fit, then size. Take cheap ideas from the losers.
5. Label any scenario added after seeing results as post-hoc, and re-score
   every option against it.

### `## Scope`

Does the PR title type match the diff? Does the PR mix docs with runtime
changes? Give the file count, and recommend a split when concerns are mixed.

### `## Lookback`

Name the failure class behind each finding, not just the bug. After fixes
land, re-run the suite and every matrix on the combined tree, then re-read the
combined diff adversarially and record what that pass found.

### `## Reuse and extension`

Check the change against `core:dry-extension` in
[`architecture-core-rules.md`](architecture-core-rules.md). List these items:

1. The helpers and modules that the change reuses.
2. The duplication that the change adds or removes. Name each new test helper
   or test file that duplicates an existing one.
3. The extension point that the change adds or uses: a config key, a
   `stages.json` field or a pack gate.
4. The net change in test lines.

A new helper that duplicates an existing one is a must-fix. Merge it into the
existing one.

### Measures (R2, R3)

Two gates measure DRY and code shape. A peer does not measure them.

- R2: `scripts/test_shape.py` lists each test helper that copies a conftest
  helper, and each thin test file. The R2 gate fails on a list that is longer
  than its `test_shape` limit. The script finds the test layout of the repo, or
  reads it from `test_shape.layout`.
- R3: `scripts/cognitive_complexity_gate.py` measures complexity, statements,
  branch chains and duplicated lines in each changed file. The R3 gate fails on
  a value over its limit that is new or larger than at the merge base.

Each measure writes one record with `value`, `threshold`, `threshold_source`
and `verdict`. Read `verdict`. Do not judge the code again. Cite the records in
`## Reuse and extension`. To get the records before R2 and R3, run these commands:

```bash
python3 "$CADENCE_HOME/scripts/test_shape.py" --change
python3 "$CADENCE_HOME/scripts/cognitive_complexity_gate.py"   # writes .context/gates/cognitive-complexity.json
```

The reviewer still checks item 4 of `core:dry-extension` (open for extension).
A branch chain over the limit is only a sign of it.

### Bugs and types (R3)

R3 also writes a `bugs` record (ruff) and a `types` record (mypy). Each record
counts the tool findings on the added lines of the change. One finding fails R3.
Cite the two records in `## Contracts` and `## Lookback`. Read `verdict`. Do not
judge the findings again.

- `unsupported` in a record names a tool that did not run, and the command that
  installs it. Write that the measure did not run. Do not write that it passed.
- A targeted `# noqa: <code>` or a `# type: ignore` on an added line hides a
  finding from the measure. Name each one in the review, with its reason.
- The tools do not find logic bugs. Do the `## Reproductions` and
  `## Adversarial inputs` work.

## E3: resolving architecture findings

`architecture_review.py --phase impl` emits findings. This reference tells you
how to resolve them. Reproduce each must-fix, check it against the written
contract (`references/architecture-core-rules.md`), and run **Design choices**
above when it has more than one fix. The R1 review then records the outcome.

## R2: test review

The `/test-benchmark --code` artifact carries `adversarial_inputs`: one entry
per input class above, each with the input, the expected result and the
observed result. An input class that doesn't apply to the change gets an entry
with `"observed": "n/a: <reason>"`. `test_benchmark_gate.py --code` fails
without the key.

The R2 gate also computes the DRY lists of the changed test files with
`scripts/test_shape.py`. It fails on each entry of `helpers_reimplemented` and
`thin_files` over the limit. The scorecard key `dry_measurement` is optional.

Prefer a new row in an existing `pytest.mark.parametrize` table to a new test
file. This applies to a mutation finding and to an adversarial input.
