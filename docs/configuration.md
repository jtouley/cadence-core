# Configuration

Cadence reads its config from a stack of JSON layers. One loader reads them all:
[`scripts/cadence_config.py`](../scripts/cadence_config.py). One schema declares every key:
[`references/cadence.config.schema.json`](../references/cadence.config.schema.json).

## Layers

A higher layer wins over a lower layer, key by key.

| # | Layer | File | Owner |
|---|---|---|---|
| 1 | `default` | `<skill root>/cadence.defaults.json` | Core. An update overwrites it. Do not edit it |
| 2 | `profile:<name>` | `<skill root>/profiles/<name>.json` | Core. The `profile` key of any layer picks it |
| 3 | `user` | `$CADENCE_CONFIG_HOME/config.json`, else `$XDG_CONFIG_HOME/cadence/config.json`, else `~/.config/cadence/config.json` | You. It applies to every project |
| 4 | `project` | `<project>/.cadence.json` | The team. Commit it |
| 5 | `local` | `<project>/.cadence.local.json` | You, in one checkout. Add it to `.gitignore` |
| 6 | `env` | `CADENCE_<KEY>` | One shell or one CI job |

The project is the nearest directory at or above the workspace that holds `.cadence.json`,
`.cadence.local.json` or `.git`.

An env var name is the key path in upper case, with `_` for each `.`:
`CADENCE_ORCHESTRATOR_SCOPE_MODE=off`, `CADENCE_REVIEW_FIX_ROUNDS=1`, `CADENCE_PACKS='["tracker"]'`.
The value is JSON when it parses as JSON, else a string.

## Merge rules

- An object merges key by key.
- A list replaces the list below it.
- A patch changes the list below it: `{"append": ["a"], "remove": ["b"]}`.
- `stages.overlays` lists join in layer order.
- `secret_paths` only grows. A list or an `append` adds patterns. A `remove` fails.
- A limit only gets stricter. The schema marks each limit with `x-cadence-stricter`:
  - `lower` is stricter: each measured limit, `plan_review_cycles`, `adversarial_cycles`, `review_fix_rounds`.
  - `higher` is stricter: `benchmark_score_floor`.

  A looser value is ignored. `resolve` and each measure record name it, for example
  `default (project value 20 ignored)`.

## Fail closed

Each of these stops the gate that reads config, and the message names the file and the key:

- a key that the schema does not declare,
- a value of the wrong type or outside its range,
- a file that is not valid JSON, or not a JSON object,
- a `profile` with no file.

The loader never falls back to the defaults. A1 refuses to activate a run on a bad layer, and A2
reports it.

## Commands

```bash
python3 scripts/cadence_config.py resolve            # each value, and the layer that set it
python3 scripts/cadence_config.py resolve --json     # the run record: each layer file with its sha256
python3 scripts/cadence_config.py check              # exit 1 on a bad layer
python3 scripts/load_stages.py                       # the resolved block order
python3 scripts/pack_loader.py check                 # each pack that is on loads
python3 scripts/pack_loader.py lock                  # pin the project packs in cadence.lock.json
```

A1 stores the resolved config in the run record (`config`: each layer, its path, its sha256, and the
values). A resumed run keeps the config of its first activation.

## Keys

| Key | Default | Meaning |
|---|---|---|
| `profile` | `standard` | `minimal`, `standard` or `strict` (see below) |
| `adapter` | `github-issues` | The tracker: `adapters/<adapter>.json`. `none` runs with no tracker |
| `packs` | `[]` | The packs that are on |
| `pack_config.<pack>` | `{}` | The settings of a pack. The pack's manifest declares the keys |
| `peers.<block>` | `{}` | Another peer for a block: `/<command>` or `<plugin>:<name>` |
| `stages.overlays` | `[]` | Changes to the block order (see below) |
| `gated_prefixes` | `src/ lib/ app/ packages/` | Path prefixes, from the repo root, that the edit gate holds until P9 passes |
| `secret_paths` | `[]` | Secret file patterns, in addition to the built-in ones |
| `layer_map` | `{}` | Architecture layers for the E3 dependency rules |
| `decision_frontmatter_required` | `true` | `true`: decision coverage needs an ADR or a proposal with decision frontmatter |
| `peer_models` | `{}` | Claude Code only: a peer stage slug, or `default`, to a model alias |
| `subagent_hygiene.*` | `mode: enforce` | The handoff packet checks ([subagent-hygiene.md](subagent-hygiene.md)) |
| `orchestrator_scope.mode` | `enforce` | Claude Code only: deny an orchestrator read of a run artifact, the code or the diff, and an orchestrator write to the code. `off` turns it off |
| `context_budget.*` | `mode: shadow` | The orchestrator context size: a row and a notice above `warn_tokens`. It never denies |
| `cognitive_complexity.*`, `duplication.*`, `code_shape.*`, `test_shape.*`, `bugs.*`, `types.*` | see the defaults | The measured R2 and R3 limits ([tool-gates.md](tool-gates.md)). `unsupported` is `warn` or `fail` |
| `measure_exclude` | `[]` | Path prefixes that R2 and R3 do not measure |
| `plan_review_cycles` | `5` | The most P3 review cycles |
| `adversarial_cycles` | `3` | The most P6 review cycles |
| `review_fix_rounds` | `null` | An optional cap on the R1 to R4 fix rounds. `null` is no cap: the loop goes on until R4 passes, and R4 holds when a round changed nothing |
| `ci_commands` | `[]` | The repository's checks that R5 runs at HEAD. Empty: what the tree shows (`make test`, pytest, `npm test`, `go test ./...`, `cargo test`) |
| `benchmark_score_floor` | `7` | The lowest layer, DRY and assertion score of a test-benchmark card that P7 and R2 pass |

### Verdict judge

A gate reads a verdict or a severity in each format that real peers write. When no strict reader takes
the text, `scripts/verdict_judge.py` asks Jev (TypeSafe) for the meaning, if `TYPESAFE_API_KEY` is set in
the environment. `TYPESAFE_API_URL` overrides the endpoint. An answer below the confidence bar is not
taken, and the gate fails closed. Each answer that a run uses goes to `.context/verdict-judgments.jsonl`,
with the model, the confidence and whether the gate took it. Without the key, nothing is sent, and an
unreadable verdict fails closed. The key is not a config key: keep it out of every config file.

## Profiles

| Profile | Changes from the defaults |
|---|---|
| `minimal` | `subagent_hygiene.mode: shadow`, `context_budget.mode: off` |
| `standard` | None. The twelve core blocks |
| `strict` | Every stage pack on (25 blocks), and each measure `unsupported: fail` |

A profile applies over the defaults and under every other layer. A higher layer changes a value of
the profile like any other value.

## Block overlays

An overlay changes the block order of `stages.json` by block id. The operations are
`insert_before`, `insert_after`, `replace` and `remove`. This `.cadence.json` adds a license check
after E1, with its gate in the repository at `.cadence/gates/license_check.py`:

```json
{"stages": {"overlays": [
  {"op": "insert_after", "block": "E1", "row": {
     "block": "E1b", "slug": "license-check", "phase": "execution",
     "command": "python3 .cadence/gates/license_check.py",
     "gate_script": ".cadence/gates/license_check.py --check"}},
  {"op": "replace", "block": "R1", "row": {"description": "Our review, our rubric"}}
]}}
```

- A block gate is a Python script that exits 0 to pass. A relative `gate_script` is looked up next
  to the file that declares the overlay (here, the repository root), then in the skill root.
- `remove` drops a block: `{"op": "remove", "block": "A5"}` drops A5 when its pack is on. A block
  that is not in the order of the run is an error, so remove only blocks that are there.
- `replace` merges the given fields over the row.
- An inserted row needs `block`, `slug`, `phase` and `gate_script`, and its `block` and `slug` must be new.
- P9, R1 and R5 make the evidence that closes a run. An overlay cannot remove them or change their
  `gate_script`. It can change their peer and their text.
- The order of operations: the overlays of each pack that is on, then `stages.overlays` of the
  layers, in layer order. `resolve_stages` returns the blocks that a `remove` dropped.

## Peer bindings

`peers.<block>` runs another peer at a peer block. The gate of the block does not change.

```json
{"peers": {"R1": "my-skills:pr-review", "P1": "/our-plan-create"}}
```

- `/<name>` is a host command or skill. When Cadence ships a peer with that name, the handoff uses
  its doc as the contract.
- `<plugin>:<name>` is a skill of another plugin. Core never reads its text. The handoff packet names
  it as `external:<plugin>:<name>`.
- The peer must write the lines that the gate reads: [peer-output-contract.md](peer-output-contract.md).

## Packs

`packs` turns a pack on. A pack is a directory with a `pack.json`, in one of three places:

| Origin | Directory | Lock |
|---|---|---|
| core | `<skill root>/packs/<name>/` | none |
| user | `~/.config/cadence/packs/<name>/` | `~/.config/cadence/cadence.lock.json` |
| project | `<project>/.cadence/packs/<name>/` | `<project>/cadence.lock.json` |

A user or project pack loads only when its lock names it with the sha256 of its files. Run
`python3 scripts/pack_loader.py lock` (add `--user` for the user dir) after you check a pack. A name
in two places fails. [references/pack-standard.md](../references/pack-standard.md) is the manifest
and gate contract.

## A personal setup

Keep your own skills, commands and org rules out of core:

- Install your skills as a plugin or in the host's skills directory, and bind them with `peers`.
- Put your defaults (profile, `peer_models`, extra `secret_paths`) in `~/.config/cadence/config.json`.
- Put an org rule in a pack under `~/.config/cadence/packs/`, and lock it.
- Put a team rule in the repo's `.cadence.json`, and a pack under `.cadence/packs/` with its lock.
