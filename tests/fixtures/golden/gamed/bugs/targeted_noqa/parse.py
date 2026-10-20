"""Gamed sample. Limit not caught: PGH004 fails only a blanket `# noqa`. A `# noqa: S602` names its
rule, so it hides the shell injection on its line, and the measure counts no finding."""

import subprocess


def label(name):
    return subprocess.run(f"echo {name}", shell=True, capture_output=True, text=True).stdout  # noqa: S602
