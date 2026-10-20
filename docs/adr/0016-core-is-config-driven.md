---
decision_id: ADR-0016
status: accepted
question: How does a user change core Cadence without a fork, so that core, skills and a personal setup live in three repos?
choice: One loader reads layered config. The layers, lowest first, are core `cadence.defaults.json`, `~/.config/cadence/config.json`, the project `.cadence.json`, a gitignored `.cadence.local.json` and `CADENCE_*` env vars. The defaults declare every key, and an unknown key or a bad file fails the gate. A `resolve` command prints each value and its layer. Overlays change `stages.json` by block id. Config binds a peer to a block, and a contract test checks the output of the peer. Packs get a versioned manifest with a pinned source. Profiles name sets of packs and limits. A pack gate can be any executable. The runtime stays standard-library Python, with no Pydantic. Gates stay hook-run and fail closed.
rationale: Today the scripts read `cadence.config.json` from the skill root, so a user who wants other options edits core. About ten scripts load it with their own checks. A project's `.cadence.json` takes three `_extra` keys only. Cycle limits, the score floor and the runner list are constants. Nothing can turn off, replace or move a core block, and `references/bundled-peers.json` fixes the peers. Five open-source agent frameworks (Superpowers, Spec Kit, OpenSpec, GSD Core, BMAD) all let a user change behavior without a fork. None of them has gates that fail closed.
rejected:
  - alt: Keep one `cadence.config.json` in the skill root and document the keys
    why_not: The personal setup then stays inside core, and the repo split cannot happen. A second user must fork core to change a limit
  - alt: Fall back to the defaults when a config file is not valid, with a warning (OpenSpec, GSD)
    why_not: A gate fails closed. A warning that an agent reads and ignores turns a strict project into a default project with no record
  - alt: Drop a bad key and keep the rest of the file (OpenSpec project config)
    why_not: A misspelled strictness key then turns a limit off with no failure. BMAD stops on an undeclared key, and Cadence does the same
  - alt: Let any layer loosen a limit
    why_not: ADR-0008 and ADR-0015 make a limit a floor. A lower layer can make a limit stricter only. A project that needs a looser limit records it in the project layer, and `resolve` shows it
  - alt: Let a project remove any block, core blocks too
    why_not: P9, R1 and R5 make the evidence that closes a run. A project can replace them with a peer that writes the same output. It cannot remove them
  - alt: Change behavior through prompt text only, with override points in the skills (Superpowers)
    why_not: The gates parse peer output. A prompt override that changes the output format breaks a gate with no test to catch it
  - alt: Check config with Pydantic models
    why_not: The runtime is the Python standard library only (AGENTS.md). Hooks run with a plain `python3`. A runtime dependency needs an environment on each host, and a missing package fails each hook on import. The defaults file declares the types, and a small standard-library checker applies them
  - alt: Port the runtime to Go for one static binary
    why_not: A port stops feature work for months and drops the behavior that the tests prove. Each supported host has `python3`. A second implementation can follow `docs/gate-pattern.md` and the peer output contract, and the golden tests of §3 check both
  - alt: Clone a mutable skills repo into the user's config folder at run time (Superpowers v2)
    why_not: Superpowers dropped it within days. Upgrades needed backups, and user branches drifted. The host plugin and skills directories load a second repo, so core stays read-only
  - alt: Many kinds of add-on (Spec Kit extensions, presets, workflows, steps, bundles, catalogs)
    why_not: Each kind is one more concept to learn and one more file to read. Cadence keeps three concepts: config, overlays and packs
  - alt: Let the prompt call the gates (GSD)
    why_not: The hooks call the gates today, and an agent cannot skip a hook. A gate that the prompt calls is a gate that the agent can forget
links:
  - rel: relates
    target_type: file
    target_id: cadence.defaults.json
  - rel: relates
    target_type: file
    target_id: stages.json
  - rel: relates
    target_type: file
    target_id: references/bundled-peers.json
  - rel: relates
    target_type: file
    target_id: scripts/pack_loader.py
  - rel: relates
    target_type: file
    target_id: scripts/load_stages.py
  - rel: relates
    target_type: file
    target_id: shared/skill_gate.py
  - rel: relates
    target_type: file
    target_id: tests/install/test_core_split_runs_the_toy_run.py
  - rel: relates
    target_type: file
    target_id: docs/adr/0007-five-step-core-with-opt-in-blocks.md
  - rel: relates
    target_type: file
    target_id: docs/adr/0008-measured-dry-gates.md
  - rel: relates
    target_type: file
    target_id: docs/adr/0015-e1-drives-the-next-phase.md
---
# ADR-0016: Core is config-driven

## Context

The owner wants to split Cadence into three repos:

1. **Core.** Gates, hooks, `stages.json`, adapters, the pack mechanism and the default peers of the core blocks.
2. **A skills repo.** Personal skills, commands and subagents, and references or pinned forks of favorites from other repos.
3. **A personal setup.** The owner's config, org packs and private scripts.

The split needs a core that a user changes through config, not by an edit of core. Before this decision:

- The scripts read `cadence.config.json` from the skill root (`Path(__file__).parent.parent`). The personal setup and core are one directory.
- About ten scripts load the file, each with its own checks: `context_budget.py`, `cognitive_complexity.py`, `final_consolidate.py`, `activate_session.py`, `domain_analysis.py`, `tracker_header.py`, `handoff.py`, `preflight.py`, `pack_loader.py`, `load_stages.py` and `architecture_review.py`.
- A project's `.cadence.json` takes `gated_prefixes_extra`, `enabled_packs_extra` and `secret_paths_extra` only.
- These limits are constants: `PLAN_REVIEW_CYCLE_LIMIT = 5`, `ADVERSARIAL_CYCLE_LIMIT = 3`, `SCORE_FLOOR = 7` and `RUNNERS` in `findings_io.py`.
- A pack turns on blocks. Nothing turns off, replaces or moves a core block. `references/bundled-peers.json` fixes the peer of each block.
- The gates parse the output of the peers: `Verdict:` lines, `MF-n` ids, `Last reviewed commit` and `Last reviewed plan sha256:`. That output is the API of core.

Five open-source frameworks, each read at a pinned commit, handle this in five ways:

| Framework | Config | Workflow change | Add-on | Hard gates |
|---|---|---|---|---|
| Superpowers `8ca22db` | None. CLAUDE.md wins over the skills | A personal skills directory | Plugin marketplaces | No |
| Spec Kit `b9e08bf` | Defaults → committed → `.local` → env | Overlays by step id, slots, `resolve` | Versioned manifest, SHA-256 catalog | No |
| OpenSpec `9111a76` | Global + project, drops a bad key | Fork a schema. Project → user → package | None | No |
| GSD Core `83aba44` | Global seeds the project, written order | 12 named points, `when:` toggles, `agent_skills` | `capability.json`, semver, sha512, lock file | Few. Most fail open |
| BMAD `bda3c59` | Team and personal layers, merge by shape, an unknown key stops | Options that each skill declares | Module record with migrations | No |

All five let a user change behavior without a fork. None of them runs gates from hooks that fail closed. Cadence keeps its gates and takes the config ideas.

## Decision

### 1. One loader with layers

1. A new `scripts/config.py` is the one reader of config. Each script that reads `cadence.config.json` today calls it.
2. The layers, lowest first:
   1. `cadence.defaults.json` in core. It declares each key, its type and its default. An update overwrites it.
   2. `~/.config/cadence/config.json` (`$XDG_CONFIG_HOME` when it is set). The personal setup.
   3. `<project>/.cadence.json`. The team. Git tracks it.
   4. `<project>/.cadence.local.json`. Personal to one checkout. Git ignores it.
   5. `CADENCE_<SECTION>_<KEY>` env vars.
3. An object merges deep. A list of objects merges by `id`. A list item `{"id": "x", "remove": true}` removes `x`. Another list replaces the lower list.
4. The defaults declare every key. A key that `cadence.defaults.json` does not declare fails the gate, and the fail text names the file and the key. A pack declares its own keys (§4).
5. A file that does not parse fails the gate. The loader never falls back to the defaults.
6. A strictness key keeps the floor rule of ADR-0008. Each layer can make the limit stricter only. The defaults file marks each strictness key.
7. `python3 scripts/config.py resolve` prints each value with the layer that set it. The run record stores the resolved config and the sha256 of each layer file.
8. The constants in the Context move into `cadence.defaults.json`.
9. The checker is standard-library Python in `scripts/config.py`. Pydantic and other runtime packages stay out (AGENTS.md). A Pydantic model can live in the `dev` extra for tools, never in the runtime.
10. `references/cadence.config.schema.json` is a JSON Schema of the user layers. An editor reads it for completion and inline errors on `.cadence.json`. The runtime does not read it. A test fails when the schema and `cadence.defaults.json` declare different keys, types or defaults.

### 2. Overlays change the block order

1. `stages.json` stays the one source of the core order.
2. A config layer can hold `stages.overlays`, a list of operations by block id: `insert_before`, `insert_after`, `replace` and `remove`.
3. `stages.json` names slots, for example `slot:after-R1`. A pack fills a slot. It does not edit `stages.json`.
4. P9, R1 and R5 take `replace` only. A `remove` of them fails the load. A project can remove the other blocks. The run record and the R5 output list each removed block.
5. `python3 scripts/load_stages.py resolve` prints the final order and the layer of each change.

### 3. Config binds the peers

1. `peers.<block>` names the peer of a block: a path in core, or `<plugin>:<name>` for a peer from another plugin, for example the skills repo.
2. The default is the row in `references/bundled-peers.json`.
3. A peer output contract (an extension of `docs/gate-pattern.md`) names the lines that each gate reads.
4. A golden test runs each gate on a sample output of each bound peer. A replacement peer that does not write the contract fails the test.

### 4. Packs have a versioned manifest

`pack.json` version 2 adds:

- `version` (semver) and `requires.cadence` (a semver range).
- `config`, the keys that the pack declares, with type and default. The loader adds them to the closed key set.
- `slots`, the slots that the pack fills.
- `source`, a git URL and a sha.
- `gate_command`, an argv list. The first item is an executable in the pack, or a command on `PATH`. Today `run_pack_gate` starts `gate_script` with `sys.executable`, so a pack gate must be Python. With `gate_command`, a gate can be a Go binary, a Node script or any other program. The contract does not change: JSON on stdin, `{"findings": [...]}` on stdout, and a failure with the pack name for no output or bad JSON. A manifest holds `gate_script` or `gate_command`, not both. `gate_script` stays for Python gates.

`cadence.lock.json` in the project records the sha and the sha256 of the files of each installed pack. A pack whose files do not match the lock fails A2. A version 1 manifest still loads.

### 5. Profiles

`profile` is one of `minimal`, `standard` or `strict`. A profile is a named set of packs and limits in `cadence.defaults.json`. A higher layer changes a value of the profile like any other value. The default profile gives the behavior of today.

### 6. The repo split comes last

1. The full test suite passes from a core-only copy. Test fixtures use stub peers that write the output contract, not copies of `commands/` and `skills/`.
2. `git filter-repo` moves the skills and the private scripts out, with their history.
3. Core cuts a release. The setup repo pins core and the skills repo by version.

## Consequences

- The personal setup moves out of core into `~/.config/cadence/` and the project files. Core holds no owner value.
- One loader replaces about ten. Each gate gets the same checks and the same fail text.
- `resolve` answers "why does this run do this?" for config and for the block order.
- A second user can change limits, blocks and peers with no fork. A change that removes evidence fails closed.
- Overlays make the block order of a run differ from `stages.json`. The run record stores the resolved order, so a gate judges the run against the order that it used.
- The pack manifest and the lock file add a concept for pack authors. A version 1 pack keeps working.
- Cadence stays standard-library Python. R3 measures a target repo in Go, Java or C/C++ as today (#118). A pack gate in another language uses `gate_command`.
- The JSON Schema gives editor help with no runtime cost. The schema test keeps it in step with the defaults.
- A prose style rule for agent output is a candidate for a pack, not a rule of core. Core does not lint prose.
- Each step of the Decision ships as its own PR with its own tests. No step raises `BUDGET_LINES` in the same PR that adds the code: the PR first removes the loaders that it replaces.

## Answers to the open questions

- The default peers of the core blocks stay in core. Their output is the API of the gates, and
  `docs/peer-output-contract.md` states it. Another peer plugs in with `peers.<block>`.
- The layers are JSON, as the other manifests are.

## Implementation notes

The implementation follows the decision, with these differences:

- The loader is `scripts/cadence_config.py`, not `scripts/config.py`: a module named `config` can
  collide with a module of the target repo on `sys.path`.
- The runtime reads `references/cadence.config.schema.json` for the key set, the types and the
  floors (`x-cadence-stricter`, `x-cadence-grow-only`, `x-cadence-merge`). One file is the source of
  each type. `cadence.defaults.json` holds the values, and a test checks that the two agree.
- A profile is a file, `profiles/<name>.json`, and a layer of its own between the defaults and the
  user layer.
- A pack fills no named slot. A version 2 pack has `stages`, a list of the same overlay operations
  that a config layer uses, so one mechanism changes the block order.
- A pack lives in core, in `~/.config/cadence/packs/` or in `<project>/.cadence/packs/`. Each user
  and project origin has its own `cadence.lock.json`. A pack that is not in its lock, or that changed
  after it, fails the load, and so fails A2, P9, E3 and R3.
- An overlay can replace the text and the peer of P9, R1 and R5, but not their gate script.
- A peer bound as `<plugin>:<name>` is external: the handoff packet names `external:<plugin>:<name>`,
  and core never reads its text.
- A1 refuses to activate a run on a bad layer, and stores the resolved config in the run record.
