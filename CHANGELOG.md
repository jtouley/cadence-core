# Changelog

## 0.1.0

The first public release of Cadence core.

- The twelve-block core order in `stages.json`, and six stage packs (`preflight`, `tracker`,
  `spec-review`, `architecture`, `test-plan`, `deferrals`) that add the other thirteen blocks.
- Gate scripts that fail closed, and the findings ledger. Only `ledger_verify.py` writes `verified`.
- Hooks for Claude Code (`hooks/hooks.json`), Cursor (`hooks/hooks-cursor.json`) and Pi
  (`pi/cadence-gates.ts`): the edit guard, the secret read guard, the peer handoff and the stop guard.
- Layered config: the core defaults, a profile, the user file, the project `.cadence.json`, a local
  file and `CADENCE_*` env vars. A closed schema, `cadence_config.py resolve`, limits that a layer
  can only make stricter, and the profiles `minimal`, `standard` and `strict`.
- Block overlays (`stages.overlays`) and peer bindings (`peers.<block>`), with the
  [peer output contract](docs/peer-output-contract.md).
- Pack manifest version 2: `gate_command`, `requires`, `config` and `source`, and locks for the user
  and project packs.
- The toy run (`examples/toy-run/`): the real gates and hooks on a toy repository, with no model and
  no network.

Cadence core grew out of an earlier repository (`jtouley/cadence`). The decision records in
`docs/adr/` keep their history, so an issue number such as `#53` or `cd-53` there names an issue of
that repository.
