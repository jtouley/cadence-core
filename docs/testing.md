# Testing

## Run the suite

```bash
uv sync --extra dev
uv run pytest
```

CI runs the same command on each pull request (`.github/workflows/ci.yml`).

## Layout

| Folder | Covers |
|---|---|
| `tests/gates/` | Gate scripts: pass and fail on evidence, fail closed on bad input |
| `tests/hooks/` | Hook scripts: stop guard, edit guard, peer attestation, Task prompts |
| `tests/session/` | Activation, the config loader, the registry and its overlays, the headless walk, deactivation |
| `tests/pi/` | The Pi extension. CI runs it with Node |
| `tests/install/` | `install.sh` in a sandbox home, the plugin package, the product tree |
| `tests/contract/` | `SKILL.md`, the docs, the peer output contract, and the check for org names |

Shared factories are in `tests/conftest.py`. `install_config` copies the config files into a test skill root, and `project_config` writes a project `.cadence.json`. Fixture repos and the golden peer outputs are in `tests/fixtures/`.

## Check the plugin on a live host

The suite runs `claude plugin validate --strict` when the `claude` CLI is
installed. To check that the plugin loads and that the Bash tool gets
`CADENCE_HOME`, run a headless session from an empty directory:

```bash
claude -p --plugin-dir /path/to/cadence-core --allowedTools "Bash(printenv:*)" \
  "Run printenv CADENCE_HOME and reply with its output only."
```

The output must be the path of the checkout.

## Evals and benchmarks

This repository holds no evals and no benchmark. Both measure Cadence against
other harnesses and against a baseline, so they live in an external benchmark,
not in core.
