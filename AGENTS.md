# AGENTS.md — work on Cadence core

This file is for an agent or a person who changes this repository. To run Cadence on another
repository, load [`SKILL.md`](SKILL.md). That file is the runtime contract.
[CONTRIBUTING.md](CONTRIBUTING.md) has the pull request flow.

## Setup

```bash
uv sync --extra dev
uv run pytest
```

The runtime is the Python standard library only. Do not add a runtime dependency. A typed model
for tools (for example Pydantic) may live in the `dev` extra, never in `scripts/`, `hooks/` or
`shared/`. Manifests are JSON (`stages.json`, `cadence.defaults.json`, `profiles/*.json`,
`packs/*/pack.json`, `adapters/*.json`). Do not add YAML manifests.

## Where a change goes

| Change | Files | Tests |
|---|---|---|
| Block order, gate, or artifact | `stages.json`, then the gate in `scripts/` | `tests/gates/`, `tests/session/` |
| A config key | `references/cadence.config.schema.json` and `cadence.defaults.json`, then the reader through `scripts/cadence_config.py` | `tests/session/test_cadence_config.py` and the gate's tests |
| Overlays, peer bindings | `scripts/load_stages.py`, `scripts/handoff.py` | `tests/session/test_load_stages_packs.py`, `tests/hooks/` |
| Packs | `scripts/pack_loader.py`, `references/pack-standard.md` | `tests/gates/test_packs.py`, `tests/gates/test_pack_gates_judge_code.py` |
| What a peer must write | `docs/peer-output-contract.md`, the peer in `commands/` or `skills/` | `tests/contract/test_peer_output_contract.py` |
| Hook behavior | `hooks/*.py` | `tests/hooks/` |
| Hook registration | `hooks/hooks.json` (Claude Code), `hooks/hooks-cursor.json` (Cursor) | `tests/install/` |
| Install or plugin packaging | `install.sh`, `.claude-plugin/` | `tests/install/` |
| Agent instructions | `SKILL.md`, `references/`, `commands/` | `tests/contract/` |

`stages.json` is the only source of the core block order. Do not write a block table in a second
place. Docs point at `stages.json` and `docs/system-design.md`.

## Rules for a change

- Write the failing test first. Shared factories are in `tests/conftest.py`
  (`install_config`, `project_config`, `make_skill_root`).
- A gate fails closed. A gate that cannot read its evidence, or its config, fails. It does not pass.
- Read config only through `scripts/cadence_config.py`. Add each key to the schema with a default.
  A limit that a layer may only tighten gets `x-cadence-stricter`.
- Only `ledger_verify.py` writes `verified` to the findings ledger.
- Keep host paths out of Python. Scripts find the root from their own file path.
- Keep org rules and personal setup out of core. They go in a pack, a config layer or another
  plugin. `tests/contract/test_origin_headers_and_org_terms_clean.py` checks for org names.
- Keep a pull request to one change. Do not raise `BUDGET_LINES` in
  `tests/contract/test_source_size_budget.py` in the same pull request that adds the code.
- Write docs in plain English: short sentences, one meaning for each word, the result first.

## Keep the repository public and ready for contributions

This repository is public. It holds the product, its tests, and the docs that a user or a
contributor reads. Keep it that way:

- Do not add decision records (ADRs), design notes, plans, proposals, review transcripts, run
  evidence, benchmark output or session notes. Design history stays with the maintainers, outside
  this repository. A change gives its reason in the pull request and in one `CHANGELOG.md` line.
- `docs/` holds only the pages that [`docs/index.md`](docs/index.md) lists. A new page goes in that
  index, for a reader of this repository.
- A comment or a doc says why in plain words. Do not cite a decision record id, a private issue or a
  review round as the reason: a reader of this repository cannot open them.
- `tests/contract/test_repo_holds_no_design_records.py` fails on a tracked design record or on a
  decision record id outside the test data of the decision-coverage gate.

The decision-coverage gate reads `docs/adr/` of the repository that Cadence runs on. That is product
behavior, not a design record of this repository.

## Product and lab

Git tracks the product only. Local evidence stays out of the tree: `.gitignore` lists
`.context/`, `mutants/`, and `.cursor/plans/`.
`tests/install/test_product_boundary_paths_untracked.py` fails on a new top-level path that is not
on its list.

## Release

1. Set the same version in `pyproject.toml` and `.claude-plugin/plugin.json`.
2. Add the changes to [`CHANGELOG.md`](CHANGELOG.md).
3. Run `uv run pytest` and `claude plugin validate --strict .claude-plugin/plugin.json`.

## Docs

Start at [`docs/index.md`](docs/index.md).
