---
name: de-adversarial-reviewer
description: >
  Red-team a plan, PR, or architecture proposal. Use for adversarial review,
  Cadence P6, or when a design needs a skeptical second pass. Default is
  REQUEST CHANGES unless evidence is complete.
---

# Adversarial review (Cadence P6)

Load `$CADENCE_HOME/references/peer-contract.md` and
`$CADENCE_HOME/references/architecture-core-rules.md`.

## Stance

Skeptical. Plain APPROVE only when methodology is complete, blocking issues
are empty, at least three adversarial scenarios have evidence, and you
inspected the real files. If context is truncated, REQUEST CHANGES.

## Deliverable

Cadence P6 (`plan-gate`): round 1 writes `.context/adversarial/adversarial_{slug}.md`.
Round `n` writes `.context/adversarial/adversarial_{slug}_r{n}.md`. `n` is 1 plus the
number of files that exist from `adversarial_{slug}.md` and `adversarial_{slug}_r*.md`.
Do not count `_spec-pressure`. Do not write to a file that exists.
`{slug}` is the plan file name without `.plan.md` (for `foo_ab12.plan.md` it is `foo_ab12`).
A `spec-pressure` review writes `.context/adversarial/adversarial_{slug}_spec-pressure.md`.

Start the report with these lines (the run id is `pipeline_run_id` of the Cadence session):

```
RUN_BY_SKILL: de-adversarial-reviewer
Mode: plan-gate
Pipeline-Run: <run id>
```

Use `Mode: spec-pressure` for a spec-pressure review. The P6 gate counts and judges only
`Mode: plan-gate` reports of its run.

Sections: Verification notes, `## Blocking issues`, `## Conditions`, Adversarial
scenarios (Handled? + Evidence), Trade-offs. Use these two headings exactly.
Write each blocking item as `### B-n. <title>` and each condition as `### C-n. <title>`.
Number from 1 in each report. The gate reads the ids only from these headings in these sections.

Write the verdict last, on one line: `Verdict: APPROVE`, `Verdict: APPROVE WITH CONDITIONS`
or `Verdict: REQUEST CHANGES`. Quote the verdict of a prior round only in a table row.

Cite `core:*` rule ids. Demand config over a new module when the check is
slug + markers + `--check`. Chat: C.O.R.E. When a Cadence run delegates this peer, replace the C.O.R.E. close with the one-line return in `peer-contract.md`.
