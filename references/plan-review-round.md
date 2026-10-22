# Plan review round

**When to load:** a plan peer of a review round and `/plan-update`.

The members of the round are the rows of `stages.json` whose `review_round.round` is `plan`. With all packs on, they are P3 `/plan-review`, P5 (a script that the round runs), P6
`/de-adversarial-reviewer plan-gate` and P7 `/test-benchmark plan`. A pack row that names the round
joins it. The reviewers review one plan sha in parallel. `scripts/plan_wave.py` joins their must-fix rows by
section, and `/plan-update` answers all of them in one pass. In the next round only a reviewer that
had a must-fix reviews again. The pass of each other reviewer stands.

## A reviewer records each finding

Write your report as your contract says. Then record each finding of the report, one call each:

```bash
python3 "$CADENCE_HOME/scripts/plan_wave.py" add --stage <stage> --json '{"id": "MF-1", "phase": "Phase 2", "location": "the test spec of Phase 2", "severity": "must-fix", "evidence": "no test for an empty month"}'
```

- `<stage>` is the slug of your row in `stages.json`, for example `plan-review`.
- `id` is the id of the item in your report: `MF-n`, `B-n`, `C-n` or the scorecard finding id.
- `phase` is the section that the change goes in: `global` or `Phase N` exactly, with `N` a phase
  header of the plan. Use `global` for a change outside the phases.
- `severity` is `must-fix` or `nit`. A `B-n` and a `C-n` are must-fix.

Then record that you reviewed this plan:

```bash
python3 "$CADENCE_HOME/scripts/plan_wave.py" done --stage <stage>
```

The script pins the plan sha and the round. It fails when the round does not name your stage, or
when the plan changed after the round opened. For a stage with `report_ids` in its row (`plan-review`),
it also fails while an `MF-n` item of your review has no row. Your stop is held until you record `done`.

From round two, a must-fix may target only a section that the update changed, or a section where
your stage still had a must-fix in the round before. Record any other finding as a `nit`: a section
that did not change, and that you passed, stays passed. So the open set can only shrink.

## A run has no person to wait on

A run goes from A1 to R7 with no person in the loop. So a plan never waits on a person:

- No phase has an entry condition that a person must meet, such as "the owner accepts the record" or
  "the owner decides G-1".
- A blocking A5 gap `G-n` is settled by its row under `## Assumptions` in the deferral register
  (`references/deferral-framework.md`). A reviewer that thinks an assumption is wrong asks for a
  different assumption, and names it. It does not ask for an owner decision.
- A decision record with `status: proposed` is complete for the run. Do not ask for `accepted`.

A reviewer that finds a phase that waits on a person records a must-fix: remove the wait, and settle
the question with an assumption row.

## The update answers the round

`/plan-update <plan> --from-review .context/plan-findings/round-<n>.md` reads the round file:

- Change only the sections that the round file names. Edit a phase in place. Do not append a
  second version of a phase.
- Keep the plan's header lines as P1 wrote them: `RUN_BY_COMMAND: plan-create`, `Ticket:` and
  `Pipeline-Run:`. Without them the run cannot find its plan.
- When two reviewers name one section, make one change that answers each of their rows.
- Answer each id of the round file with a row of `## Revision ledger (review <sha>)`, where `<sha>`
  is the first 12 characters of the sha256 of the round file.
- The ledger and the carried sections (`## Revision ledger`, `## Carried ...`,
  `## Deferral conditions`) are always yours to change.

`plan_wave.py` fails when the update changed a section that no finding names, or when an id has no
ledger row. It then names `/plan-update` again with the same round file, and shows the lines to undo.
The `RUN_BY_COMMAND: plan-update` marker is in no section, so it can go anywhere.
