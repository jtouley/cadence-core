"""Clean sample: a narrow `except`, and `subprocess.run` with a list of arguments."""

import subprocess


def parse_weight(text):
    try:
        return float(text)
    except ValueError:
        return 0.0


def label(name):
    return subprocess.run(["echo", name], capture_output=True, text=True, check=False).stdout
