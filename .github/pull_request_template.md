## Summary

## Test plan
- [ ] `uv run pytest`
- [ ] Walked the affected path end to end (for example `docs/quickstart.md`)

## Hardening checklist (see `docs/review-followup.md`)
- [ ] Title type matches the diff (`docs:` PRs contain docs only)
- [ ] New or changed gates are tested with missing, corrupt, other-ticket, resumed, and adjacent-id input
- [ ] Installer / hook changes were tested in a workspace that never ran Cadence
- [ ] Contract docs (`SKILL.md`, `references/`, `docs/configuration.md`, `docs/peer-output-contract.md`) updated for any behavior change
- [ ] A new config key is in `references/cadence.config.schema.json` and `cadence.defaults.json`
- [ ] A change to a gate, or to what a gate accepts, gives its reason here and in one `CHANGELOG.md` line (no design records in the repo)
