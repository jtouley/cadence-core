"""Planted fault: a bare `except` under a blanket `# noqa` (PGH004), and `subprocess.run` with `shell=True` (S602)."""

import subprocess


def parse_weight(text):
    try:
        return float(text)
    except:  # noqa
        return 0.0


def label(name):
    return subprocess.run(f"echo {name}", shell=True, capture_output=True, text=True).stdout
