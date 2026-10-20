# Cadence pack standard

A pack adds org or domain rules to a run without a change to core. A pack can:

- judge the plan (P9), the code (E3) or the review (R3) with a gate in any language,
- add, replace or remove blocks with overlay operations,
- declare its own config keys, with types and defaults,
- own rows of `stages.json` (the six stage packs of core do this, ADR-0007).

`packs` in a config layer turns a pack on ([docs/configuration.md](../docs/configuration.md)).

## Where a pack lives

| Origin | Directory | Lock |
|---|---|---|
| core | `<skill root>/packs/<name>/` | none: it ships with core |
| user | `~/.config/cadence/packs/<name>/` | `~/.config/cadence/cadence.lock.json` |
| project | `<project>/.cadence/packs/<name>/` | `<project>/cadence.lock.json` |

- A pack name is a plain directory name: lower case letters, digits, `_` and `-`.
- A name in two places fails the load.
- A user or project pack loads only when its lock has a row for it with the sha256 of its files.
  A file that changed after the lock fails the load. Check the pack, then run
  `python3 scripts/pack_loader.py lock` (`--user` for the user dir).

```
<name>/
├── pack.json      # required manifest
└── gate.py        # optional: the gate (or any program that gate_command names)
```

## `pack.json`

```json
{
  "name": "license-rules",
  "manifest_version": 2,
  "version": "1.2.0",
  "description": "Every new source file has a license header",
  "requires": {"cadence": ">=0.1.0, <1.0.0"},
  "phases": ["impl", "review"],
  "gate_command": ["./gate", "--strict"],
  "config": {
    "header": {"type": "string", "default": "SPDX-License-Identifier"}
  },
  "stages": [],
  "source": {"git": "https://example.org/acme/cadence-packs.git", "sha": "4f2a9c1"},
  "triggers": [],
  "rules": [{"id": "license-rules:header", "name": "A source file has a license header", "severity": "must-fix"}]
}
```

| Field | Version | Meaning |
|---|---|---|
| `name` | 1, 2 | The directory name |
| `version` | 1, 2 | The pack version. Version 2 needs semver (`1.2.0`) |
| `description`, `triggers`, `rules`, `reviewer_command`, `adr_scope` | 1, 2 | `triggers` are globs that domain analysis (P2) matches |
| `phases` | 1, 2 | The phases that run the gate: `planning` (P9), `impl` (E3), `review` (R3). With no `phases` key, a gate runs in P9 |
| `gate_script` | 1, 2 | A Python script in the pack. Core runs it with the Python of the run |
| `manifest_version` | 2 | `2`. With no key, the manifest is version 1 |
| `gate_command` | 2 | An argv list. The first item is a file in the pack, or a program on `PATH`. Use it for a gate in Go, Node, shell or any other language |
| `requires.cadence` | 2 | A version range of Cadence core: comparators `>=`, `>`, `<=`, `<`, `==`, with commas between them |
| `config` | 2 | The config keys of the pack: a key to a JSON Schema with a `default`. A layer sets them in `pack_config.<name>` |
| `stages` | 2 | Overlay operations on the block order (see below) |
| `source` | 2 | Where the pack comes from: `git`, `sha`, `url`. The lock copies it |

The load fails on each of these:

- a key that the manifest version does not have,
- `gate_script` and `gate_command` both,
- a gate with an empty `phases` list, or `phases` with no gate,
- a phase outside `planning`, `impl` and `review`,
- a `config` default that its own schema does not allow,
- a `requires.cadence` range that the core version is not in.

## The gate contract

The gate reads one JSON object on stdin:

```json
{"phase": "impl", "worktree": "/path/to/worktree", "files": ["src/a.py", "docs/b.md"], "config": {"header": "SPDX-License-Identifier"}}
```

- `files` are the changed files of the run: committed, uncommitted and untracked, from the merge
  base with the base branch. In P9, `files` is `[]`.
- `config` is the pack's settings: its declared defaults, then `pack_config.<name>` of the layers.
- The cwd is the pack directory. A `gate_script` also gets `--phase <phase> --worktree <path>`.

The gate writes one JSON object on stdout:

```json
{"findings": [{"severity": "must-fix", "rule_ref": "license-rules:header", "evidence": "src/a.py: no header"}]}
```

- A finding fails the gate of its phase unless its severity is exactly `nit`. No severity, or
  another spelling, is a must-fix.
- The gate fails closed. No JSON, or JSON with no `findings` list of objects, is a failure that
  names the pack. A program that cannot start is a failure that names the pack.
- An exit code that is not 0, with no must-fix finding, adds the must-fix finding `pack:<name>:exit`.
- E3 does not write a pack finding to the findings ledger. The finding fails E3 and stops the E3
  breadcrumb.

## Stage operations

A version 2 pack can change the block order with the operations of `stages.overlays`
([docs/configuration.md](../docs/configuration.md#block-overlays)):

```json
"stages": [{"op": "insert_after", "block": "E3", "row": {
  "block": "E3b", "slug": "license-check", "phase": "execution",
  "command": "python3 gate.py", "gate_script": "gate.py --check"}}]
```

- A `gate_script` path in a pack row is relative to the pack. The loader makes it absolute.
- The row gets `"pack": "<name>"`.
- The operations of each pack that is on apply first, in the order of `packs`. Then the
  `stages.overlays` of the config layers apply, so a project can remove a pack's row.
- No operation can remove P9, R1 or R5, or change their gate.

## Samples

- `packs/_sample_fail_gate/` always fails P9 when a layer turns it on.
- `packs/_sample_impl_gate/` fails E3 and R3 when the change has a file named
  `SAMPLE_IMPL_GATE_PLANTED.txt`.

## Rules

- The stage packs of core are `preflight`, `tracker`, `spec-review`, `architecture`, `test-plan` and
  `deferrals`. They own rows of `stages.json` and have no gate. A gate that reads the artifact of a
  row whose pack is off passes on that artifact.
- Zero packs is the default (`profile: standard`). `profile: strict` turns on every stage pack.
- A pack that cannot load fails P9, E3, R3 and A2. It is never skipped.
