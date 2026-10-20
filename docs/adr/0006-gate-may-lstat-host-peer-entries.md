---
decision_id: ADR-0006
status: accepted
question: Can a gate look at a host entry that has a peer's name, under core:standalone-package?
choice: A gate can lstat and resolve() a host entry with a peer's name to find a peer shadow (scripts/peer_shadows.py only). It never reads, loads, or runs the content of a host entry.
rationale: The defect is not visible from the skill root. A host runs a user command or skill with a peer's name in place of the bundled peer. A check of file metadata for a fixed list of names finds it, and no host content gets to a gate.
rejected:
  - alt: A plugin exemption, or an orchestrator that sends /cadence:<peer>
    why_not: the orchestrator sends the bare slash name. A user entry with that name still runs in place of the peer (spec cd-53 G-B2, option a)
  - alt: Read the host files and compare their content with the bundled peer
    why_not: that reads host procedure, which core:standalone-package forbids
links:
  - rel: implements
    target_type: file
    target_id: scripts/peer_shadows.py
  - rel: implements
    target_type: file
    target_id: references/architecture-core-rules.md
---
# ADR-0006: A gate may lstat host peer entries

Ticket: jtouley/cadence#53 (task cd-53)

## Context

A user-level command or skill with the name of a Cadence peer runs in place of the peer. Run `b7a3447a` (cd-47) ran a user `pr-review-local` command, and its R1 and R2 gates failed. Run cd-58 ran a stale `plan-review` command from a different checkout. That command stopped the run after cycle 3 of 5. `install.sh` keeps each user entry and prints only a `skip` line. No gate told the agent.

## Decision

- `scripts/peer_shadows.py` checks the command entry and the skill entry for each peer name in the host directories of the session's host.
- An entry is a peer shadow when it exists and is not a link of the same kind to the bundled file or directory.
- A2 (`scripts/preflight.py`) fails on a peer shadow. `install.sh` prints the same list as a warning.
- The finder uses only `lexists` and `resolve()`. It does not open a host entry.

## Consequences

- After this change, A2 fails on each machine that has a peer shadow, until the user removes or renames each listed entry.
- Some entries are the user's own skills that other repositories call by name. To rename one changes its slash name in each repository.
- A plugin install does not clear a peer shadow.
