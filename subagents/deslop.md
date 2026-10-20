Remove AI-generated slop from this branch vs the default base.

Check `git diff` against main (or master). Remove unused imports, filler
comments, duplicated helpers, and decorative prose. Keep behavior.

In a document that a person reads (README, `docs/`, `references/`, `commands/`,
`skills/`, `SKILL.md`, `CHANGELOG.md`), state what is. Remove the story of the
change: what the code did before, why a rule changed, review ids, finding ids,
dates, branch names and commit hashes. Move a reason a reader needs into the
ADR under `docs/adr/`, or drop it. A `CHANGELOG.md` entry is one line per change
that a user sees, with its issue number. Do not edit `docs/adr/` or `.context/`.

Run the tests under `tests/contract/`. They must pass.

Reply with the list of paths cleaned.

<!-- cadence paceline subagent template -->
