# Cadence packs

Packs are optional. Core runs fully with no pack on (`profile: standard`, `"packs": []`).

A pack is a directory with a `pack.json`. [`references/pack-standard.md`](../references/pack-standard.md)
is the manifest and gate contract. `packs` in a config layer turns a pack on, for example in the
project `.cadence.json`:

```json
{"packs": {"append": ["architecture"]}}
```

[`docs/configuration.md`](../docs/configuration.md) has the layers. A pack can live in three places:
here in core, in `~/.config/cadence/packs/` (user), or in `<project>/.cadence/packs/` (project). A
user or project pack loads only when its lock (`cadence.lock.json`) pins it.

Six packs here own stage rows (ADR-0007): `preflight`, `tracker`, `spec-review`,
`architecture`, `test-plan` and `deferrals`. With no pack on, a run visits the twelve `core`
rows of `stages.json`. `profile: strict` turns on all six, so a run visits all 25 rows.

Two sample packs show the gate contract:

- `_sample_fail_gate` always fails the P9 entry invariant when a config layer turns it on.
- `_sample_impl_gate` has the phases `impl` and `review`. It fails E3 and R3 when the
  change has a file with the name `SAMPLE_IMPL_GATE_PLANTED.txt`.
