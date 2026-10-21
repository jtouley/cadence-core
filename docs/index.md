# Cadence documentation

Start with the part that matches what you want to do.

## Get started

| Doc | Read it to |
|---|---|
| [Toy run](../examples/toy-run/README.md) | See the gates and hooks on a toy repository, with no model and no network |
| [Quickstart](quickstart.md) | Install on your host and do a first `/cadence` run |

## Configure and extend

| Doc | Read it to |
|---|---|
| [Configuration](configuration.md) | Set the config layers, a profile, packs, block overlays and peer bindings |
| [Pack standard](../references/pack-standard.md) | Write a pack: the manifest, the gate contract, the lock |
| [Peer output contract](peer-output-contract.md) | Write a peer that the gates accept |
| [Subagent hygiene](subagent-hygiene.md) | Tune the handoff packets and the poisoned-prompt checks for peers |

## How it works

| Doc | Read it to |
|---|---|
| [System design](system-design.md) | Learn the phases, provenance, and backtrack rules. This is the one architecture doc |
| [Pipeline](pipeline.md) | See the Host enforcement matrix and the findings ledger |
| [Gate pattern](gate-pattern.md) | Build the same enforcement on another host: the gate contract, the ledger rules and the hook points |
| [Tool gates](tool-gates.md) | See which tool computes each R3 measure |

## Work on Cadence

| Doc | Read it to |
|---|---|
| [Contributing](../CONTRIBUTING.md) | Open a pull request |
| [AGENTS.md](../AGENTS.md) | Find where a change goes, and the rules for a change |
| [Testing](testing.md) | Run the tests and a live plugin check |
| [Review follow-up](review-followup.md) | Reproduce, fix, and test a design choice after a PR review |

The agent loads the procedures in [`../references/`](../references/) one block at a time.

## Product tree

Git tracks these top-level paths and no others.
`tests/install/test_product_boundary_paths_untracked.py` holds the list.

| Path | Contents |
|---|---|
| `SKILL.md` | The agent contract. The repo root is the skill root |
| `stages.json` | The core block order: gates, artifacts and the pack of each block |
| `cadence.defaults.json` | The default config layer. It sets each key. An update overwrites it |
| `profiles/` | The `minimal`, `standard` and `strict` profiles |
| `scripts/` | Gate scripts, session tools and the config loader `cadence_config.py` |
| `hooks/` | Hook scripts, `hooks.json` (Claude Code), `hooks-cursor.json` (Cursor) |
| `commands/` | Peer commands |
| `skills/` | Bundled peer skills |
| `references/` | Procedures for one block at a time, and the config schema `cadence.config.schema.json` |
| `subagents/` | Delegation templates for the R3 simplify step and the E1 cleanup |
| `shared/` | The portable edit gate and the session store |
| `packs/` | The stage packs of core and two sample packs |
| `pi/` | Pi extension `cadence-gates.ts`: runs the hooks of `hooks/hooks.json` on Pi |
| `adapters/` | Tracker adapters |
| `.cadence.json` | The project config layer of this repo. It turns on every stage pack, so a run on Cadence visits all 25 blocks. It adds the gated prefixes `scripts/`, `hooks/`, `shared/` and `install.sh` with `gated_prefixes: {"append": [...]}`. A prefix matches from the repo root or from the workspace directory, not below another directory. Its `measure_exclude` keeps the golden fixtures (`tests/fixtures/golden/`) out of the R2 and R3 measures |
| `install.sh` | Home-only installer for Cursor, Pi, and linked Claude Code |
| `.claude-plugin/` | Claude Code plugin and marketplace manifests |
| `.claude/` | The repo's own `verify` skill for contributors |
| `.github/` | PR template and CI |
| `docs/` | These docs |
| `tests/` | The test suite |
| `examples/` | `toy-run/`: the gates and hooks on a toy repository, with no model and no network |
| `assets/` | Product images |
| `README.md` | The product page |
| `AGENTS.md` | The guide for an agent or a person who changes this repo |
| `CONTRIBUTING.md` | The pull request flow |
| `SECURITY.md` | How to report a security problem |
| `CHANGELOG.md` | Changes for each version |
| `LICENSE` | MIT |
| `pyproject.toml` | Dev tools and test settings |
| `uv.lock` | Locked dev tools |
| `.gitignore` | The lab paths below |

## Local lab

Git ignores these paths. They hold local evidence, not product.

- `.context/`
- `mutants/`
- `.cursor/plans/`

This repository holds no evals and no benchmarks.
