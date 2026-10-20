# Contributing to Cadence core

Thank you for your interest. Cadence core is the gates, the hooks, the block order, the default
peers and the config loader. Changes that make core easier to configure, safer, or smaller are
welcome.

## Before you start

- Read [AGENTS.md](AGENTS.md): where a change goes and the rules for a change.
- A new org rule, a house style or a personal skill does not go in core. Ship it as a pack
  ([references/pack-standard.md](references/pack-standard.md)), a config layer
  ([docs/configuration.md](docs/configuration.md)) or another plugin that you bind with `peers`.
- A change to a gate, or to what a gate accepts, needs a short decision record in `docs/adr/`.

## Pull requests

1. Fork the repository and create a branch.
2. Write the failing test first, then the change.
3. Run the checks:

   ```bash
   uv sync --extra dev
   uv run pytest
   ```

4. Keep one change in each pull request. A pull request that changes many gates at once is hard
   to review, and it will be asked to split.
5. Fill in the pull request template.

A gate fails closed. A test that proves a gate passes on missing, corrupt or old evidence is a
bug report, and it is the most useful kind.

## License

By contributing, you agree that your contribution is under the MIT license of this repository.
