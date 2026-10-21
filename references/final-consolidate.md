# Final consolidate (R4)

Two passes over the findings ledger:

1. **Close pass** — action-resolve every open `must-fix` (fix → `fixed`, or
   `deferred` / `spec_gap` with ticket).
2. **Delta pass** — re-scan for regressions introduced by close-pass fixes.

## Fix-loop budget

The config key `review_fix_rounds` sets the most fix rounds (default 2). A config
layer can only lower the budget. The gate ignores a larger value, and a value that
is not an integer of at least 1 fails the gate.

A fix round is one `## Update` section of the PR review. The R4 gate counts the
sections of the run's PR review. At the budget, while a must-fix is open or the
review does not cite HEAD, the gate holds with the ADR-0009 text: a `MERGE-READY`
verdict with no open must-fix ends the loop, and a nit goes to the tracker. Do
not start another fix round. Record each open must-fix as `deferred` with a
ticket or as `spec_gap`, or stop and tell the user (ADR-0015).

Promotion to `verified` happens only via `ledger_verify.py` after the finding's
`check` command passes.

## The check of a finding

`findings_io.py` refuses a `check` that cannot prove a fix:

- An empty command, or a command that ends with `--`.
- A shell no-op in any part of the command: `true`, `:`, `echo ...`, `printf ...` or `exit ...`.
  This includes `pytest || true` and `pytest; echo done`.
- A command that names no test runner and no script path. Name one of `pytest`, `python`,
  `make`, `tox`, `nox`, `uv run`, `npm`, `pnpm`, `yarn` or `bun` with `test` or `run`, `npx`,
  `cargo`, `go test` or `go vet`, `ruff`, `mypy`, `pyright`, `tsc`, `eslint`, `jest`, `vitest`,
  a `./` path or a `scripts/` path.
- A command that the shell cannot parse (`sh -n`), such as a command with a note after it. Put the
  reason in `evidence`, and write only the command in `check`.
- A relative path to a Cadence script, such as `scripts/test_shape.py`, in a repository that has no
  such file. Write `$CADENCE_HOME/scripts/test_shape.py`. `ledger_verify.py` sets `CADENCE_HOME`.

The rule reads the text of the command. It does not prove that the command tests the fix.
The reviewer names the test that proves the fix. A doc change, a kept line or a follow-up ticket
does not close a must-fix.

## A fix names its commit

`set-status --status fixed` requires `--fixed-in <sha>`. The sha is HEAD or an ancestor of HEAD.
`ledger_verify.py` runs the check at HEAD. It refuses a row whose `fixed_in` is not HEAD or an
ancestor of HEAD. It writes the HEAD sha to `verified_at_sha`.

## A manual check

A `manual` check closes only on the word of a person who read the fix. The person runs
`set-status --human --by <name>`. The command writes `human_confirmed`, `confirmed_by` and
`confirmed_at`. `append` refuses a row with these fields. `closure.py` writes one `MANUAL` line
for each row with `human_confirmed`, so the user sees each one. The agent does not run `--human`.

## Changing the ledger

The edit hooks block direct writes to `findings.jsonl` and `findings/*.jsonl`.
Record and resolve findings through the CLI.
The id prefix is the task id of the run (`gh-1` in the examples).
In a worktree with a run, `append` to the canonical ledger rejects an id with a different prefix.
With no run for the cwd, it rejects an id that matches no known task. Use `--ledger` for a file outside the run ledger:

```bash
python3 "$CADENCE_HOME/scripts/findings_io.py" append --json '{"id": "gh-1-F01", ...}'
python3 "$CADENCE_HOME/scripts/findings_io.py" set-status --id gh-1-F01 --status fixed --fixed-in <sha>
python3 "$CADENCE_HOME/scripts/findings_io.py" set-status --id gh-1-F02 --status deferred --ticket "#42"
python3 "$CADENCE_HOME/scripts/ledger_verify.py" --ledger .context/findings.jsonl
```
