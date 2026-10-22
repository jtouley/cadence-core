"""Deny the shell routes past the evidence of a run.

Three gate checks read evidence that the agent can write itself:

1. A `manual` check closes on `findings_io.py set-status --human --by <name>`. A hook sees only
   the tool calls of the agent. The person runs the stamp in their own terminal, so this guard
   denies the stamp on every shell call of the agent, with or without a run.
2. E1 reads the author time of each phase commit. `git commit --date`, `GIT_AUTHOR_DATE` or
   faketime forge it. Denied while a run is active.
3. Two or more commits in one shell call (a list, a loop or xargs) are phases written at once:
   `sleep 1` between them also passes the E1 time check. Denied while a run is active.

`evidence_guard_decision` does no file-system or git work on a command that does not match.
The guard reads the command text, so a script file that the agent writes and then runs, an alias
or a shell function stays out of its reach.
"""

from __future__ import annotations

import re
import shlex
from typing import Callable

# A command word starts at the line start, after `;`, `&`, `|`, `(`, `{`, a quote or a new line (so
# `bash -c "..."` and `eval "..."` count), or after `do`, `then` or `else`; then env assignments and
# wrappers (`env`, `sudo -u me`, ...).
_START = (
    r"(?:^|[;&|(\n{'\"]|\b(?:do|then|else|eval)\s)\s*(?:[A-Za-z_][A-Za-z0-9_]*=\S*\s+)*"
    r"(?:(?:env|command|exec|nohup|time|sudo|doas)\s+(?:-\S+\s+(?:(?<=-u\s)\S+\s+)?)*"
    r"(?:[A-Za-z_][A-Za-z0-9_]*=\S*\s+)*)*"
)
# `git`, its global options (`-C <dir>`, `-c <k=v>`, `--no-pager`), then the subcommand.
_GIT = r"(?:\S*/)?git(?:\s+(?:-[Cc]\s+(?:\"[^\"]*\"|'[^']*'|\S+)|--[\w-]+(?:=\S+)?))*\s+"
GIT_COMMIT = re.compile(_START + _GIT + r"commit(?:-tree)?(?=$|[\s;&|)'\"])")
_XARGS_COMMIT = re.compile(r"\bxargs\b[^;&|\n]*\bgit\b[^;&|\n]*\bcommit\b")
_LOOP = re.compile(r"(?:^|[;&|(\n{]|\b(?:do|then)\s)\s*(?:for|while|until)\s[^\n]*?(?:;|\n)\s*do\b")
_REBASE_EXEC = re.compile(_START + _GIT + r"rebase\b[^;&|\n]*\s(?:-x|--exec)\b")
# A forged time: an option of `git commit` that sets or copies the author time, the git date
# variables, or faketime.
_DATE_VARIABLE = re.compile(r"\bGIT_(?:AUTHOR|COMMITTER)_DATE\b|(?:^|[\s;&|(])(?:faketime|datefudge)\b|libfaketime")
_TIME_LONG = ("date", "reuse-message", "reedit-message")  # git takes a unique prefix (`--dat`)
_VALUE_LONG = {"message", "file", "author", "template", "cleanup", "fixup", "squash", "trailer",
               "pathspec-from-file", "gpg-sign"}
_VALUE_SHORT = "mFt"

# The stamp: `--human` on a set-status (CLI or main([...]), by any path to findings_io), or a stamp
# field in interpreter code that writes the ledger. A grep of the source or `pytest -k` stays allowed.
_SET_STATUS = re.compile(r"set[-_]status|\bmain\s*\(")
_HUMAN_FLAG = re.compile(r"(?:^|[\s'\"\[,(])--human(?=$|[\s'\"=;&|),\]])")
_STAMP_FIELD = re.compile(r"\b(?:human_confirmed|confirmed_by|confirmed_at|_human_stamp|HUMAN_FIELDS)\b")
_INTERPRETER = re.compile(r"(?:^|[\s/;&|(])(?:python[0-9.]*|node|deno|bun|perl|ruby|php|jq)(?=\s)")
_LEDGER = re.compile(r"\.jsonl\b|findings_io|rewrite_findings|set_status|append_finding")


def _sets_time(rest: str) -> bool:
    """The `git commit` arguments in ``rest`` set or copy the author time. Unreadable text fails closed."""
    try:
        lexer = shlex.shlex(rest, posix=True, punctuation_chars=";&|()")
        lexer.whitespace_split = True
        words = list(lexer)
    except ValueError:  # an open quote (`bash -c "git commit ... --date=x"`): read the raw text
        return bool(re.search(r"\s--(?:da|reu|ree)|\s-[A-Za-z]*[cC]", rest))
    skip = False
    for word in words:
        if skip:
            skip = False
            continue
        if word and set(word) <= set(";&|()"):
            return False
        if word.startswith("--"):
            name = word[2:].split("=", 1)[0]
            if len(name) >= 2 and any(full.startswith(name) for full in _TIME_LONG):
                return True
            skip = "=" not in word and name in _VALUE_LONG
        elif word.startswith("-") and len(word) > 1:
            for n, char in enumerate(word[1:], 1):
                if char in "cC":
                    return True
                if char in _VALUE_SHORT:
                    skip = n == len(word) - 1  # the value is the next word
                    break
    return False


def evidence_guard_decision(command: str, in_run: Callable[[], bool]) -> str | None:
    """The deny text for a shell command that passes the evidence of a run, or None to allow.

    ``in_run`` tells whether a Cadence run is active for the command; it is called only when a
    rule of a run matches.
    """
    if _SET_STATUS.search(command) and _HUMAN_FLAG.search(command):
        return _human_text()
    stamp = bool(_STAMP_FIELD.search(command) and _INTERPRETER.search(command) and _LEDGER.search(command))
    commits = list(GIT_COMMIT.finditer(command))
    forged = bool(_DATE_VARIABLE.search(command)) or any(
        m.group(0).endswith("commit") and _sets_time(command[m.end():]) for m in commits)
    one_shell = (len(commits) > 1 or bool(_XARGS_COMMIT.search(command) or _REBASE_EXEC.search(command))
                 or bool(commits and _LOOP.search(command)))
    if not (stamp or forged or one_shell) or not in_run():
        return None
    if stamp:
        return _human_text()
    if forged:
        return (
            "BLOCKED: this command sets the time of a commit (`--date`, `-C`, `-c`, GIT_AUTHOR_DATE, "
            "GIT_COMMITTER_DATE or faketime). E1 reads the author time of each phase commit.\n"
            "ACTION: commit with the time that git records. Commit each phase when that phase is done"
        )
    return (
        "BLOCKED: this command writes two or more commits in one shell call (a list, a loop, xargs or rebase -x). "
        "E1 counts one phase commit for each phase that the agent did.\n"
        "ACTION: do one phase, then commit it alone in its own shell call: "
        "`git add <files> && git commit -m 'phase N: <subject>'`"
    )


def _human_text() -> str:
    return (
        "BLOCKED: only a person stamps a `manual` check (`findings_io.py set-status --human --by <name>`). "
        "The agent does not write human_confirmed, confirmed_by or confirmed_at.\n"
        "ACTION: stop and ask the person to run the set-status command in their own terminal, "
        "then run ledger_verify.py. Or give the finding a check that runs a test, and run "
        "ledger_verify.py"
    )
