# Security

Cadence runs on a developer machine with the rights of the coding agent. Its hooks deny edits,
secret reads and PR posts that a run has not earned. A way to get past a hook or a gate, or to make
a gate pass on missing evidence, is a security problem.

## Report a problem

Use GitHub's private vulnerability reporting: the **Security** tab of this repository, then
**Report a vulnerability**. Please do not open a public issue or pull request for it.

Include the host (Claude Code, Cursor or Pi), the Cadence version, and the smallest set of steps
that shows the problem. A failing test is the best report.

## Scope

In scope: the hooks in `hooks/`, the edit guard in `shared/`, the gates in `scripts/`, the config
loader and the pack loader. Out of scope: a pack or a peer that you install from someone else. Read
a pack before you lock it in `cadence.lock.json`.
