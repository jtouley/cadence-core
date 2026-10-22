"""A4 ticket_gate: the adapter pattern fails closed, and the no-tracker mode (adapter none)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from conftest import code_paths, install_config, make_session, project_config

ROOT = Path(__file__).resolve().parents[2]
code_paths()
import ticket_gate  # noqa: E402
import tracker_header  # noqa: E402
from ticket_gate import _matches_pattern, run  # noqa: E402


# --- ticket_gate: Ticket gate: invalid adapter regex is fail-closed.

def test_matches_pattern_invalid_regex_raises():
    with pytest.raises(re.error):
        _matches_pattern("gh-1", "[")


def test_ticket_gate_invalid_adapter_regex_fails(tmp_path, capsys):
    install_config(tmp_path)
    project_config(tmp_path, {"adapter": "bad"})
    (tmp_path / "cadence-session.json").write_text(
        json.dumps({"active_skill": "cadence", "task_id": "gh-1"}),
        encoding="utf-8",
    )
    adapters = tmp_path / "adapters"
    adapters.mkdir()
    (adapters / "bad.json").write_text(
        json.dumps({"name": "bad", "ticket_pattern": "["}),
        encoding="utf-8",
    )
    code = run(tmp_path, check=True, skill_root=tmp_path)
    assert code == 1
    assert "invalid adapter ticket_pattern" in capsys.readouterr().err


def test_ticket_gate_accepts_normalized_task_id_for_github_adapter(tmp_path):
    install_config(tmp_path)  # the default adapter is github-issues
    (tmp_path / "cadence-session.json").write_text(
        json.dumps({"active_skill": "cadence", "task_id": "cd-1"}),
        encoding="utf-8",
    )
    adapters = tmp_path / "adapters"
    adapters.mkdir()
    (adapters / "github-issues.json").write_text(
        (ROOT / "adapters" / "github-issues.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    assert run(tmp_path, check=True, skill_root=tmp_path) == 0


def test_ticket_gate_matching_adapter_passes(tmp_path):
    install_config(tmp_path)
    make_session(tmp_path)
    adapters = tmp_path / "adapters"
    adapters.mkdir()
    (adapters / "github-issues.json").write_text(
        json.dumps({"name": "github-issues", "ticket_pattern": r"gh-\d+"}),
        encoding="utf-8",
    )
    code = run(tmp_path, check=True, skill_root=tmp_path)
    assert code == 0
    assert (tmp_path / "cadence-session.json").is_file()
    session = json.loads((tmp_path / "cadence-session.json").read_text(encoding="utf-8"))
    assert session["task_id"] == "gh-1"


@pytest.mark.parametrize("task_id", ["gh-1", ""])
def test_ticket_gate_without_the_configured_adapter_file_fails(tmp_path, capsys, task_id):
    """The configured adapter has no file: A4 fails closed. It does not pass on the task_id alone."""
    install_config(tmp_path)
    make_session(tmp_path, task_id=task_id)
    code = run(tmp_path, check=True, skill_root=tmp_path)
    session = json.loads((tmp_path / "cadence-session.json").read_text(encoding="utf-8"))
    assert code == 1
    assert "configured adapter github-issues has no file" in capsys.readouterr().err
    assert session.get("task_id") == task_id
    assert not (tmp_path / "adapters").exists()
    assert not (tmp_path / "gates").exists()


def test_ticket_gate_unreadable_adapter_fails(tmp_path, capsys):
    install_config(tmp_path)
    project_config(tmp_path, {"adapter": "bad"})
    make_session(tmp_path)
    adapters = tmp_path / "adapters"
    adapters.mkdir()
    (adapters / "bad.json").write_text("{not-json", encoding="utf-8")
    code = run(tmp_path, check=True, skill_root=tmp_path)
    err = capsys.readouterr().err
    assert code == 1
    assert "unreadable" in err
    assert (adapters / "bad.json").is_file()


def test_ticket_gate_missing_session_with_adapters_fails(tmp_path, capsys):
    install_config(tmp_path)
    adapters = tmp_path / "adapters"
    adapters.mkdir()
    (adapters / "github-issues.json").write_text(
        json.dumps({"name": "github-issues", "ticket_pattern": r"gh-\d+"}),
        encoding="utf-8",
    )
    code = run(tmp_path, check=True, skill_root=tmp_path)
    err = capsys.readouterr().err
    assert code == 1
    assert "cadence-session.json" in err
    assert not (tmp_path / "cadence-session.json").exists()


def test_ticket_gate_uses_configured_adapter_not_sibling_files(tmp_path, capsys):
    """A sibling adapter that would match must not pass when the config names another."""
    install_config(tmp_path)
    make_session(tmp_path)
    project_config(tmp_path, {"adapter": "only-zzz"})
    adapters = tmp_path / "adapters"
    adapters.mkdir()
    (adapters / "github-issues.json").write_text(
        json.dumps({"name": "github-issues", "ticket_pattern": r"#\d+"}),
        encoding="utf-8",
    )
    (adapters / "only-zzz.json").write_text(
        json.dumps({"name": "only-zzz", "ticket_pattern": r"^zzz$"}),
        encoding="utf-8",
    )
    code = run(tmp_path, check=True, skill_root=tmp_path)
    err = capsys.readouterr().err
    assert code == 1
    assert "did not match" in err


def test_ticket_gate_empty_adapter_does_not_bind_github(tmp_path, capsys):
    install_config(tmp_path)
    make_session(tmp_path)
    project = project_config(tmp_path, {"adapter": ""})
    adapters = tmp_path / "adapters"
    adapters.mkdir()
    (adapters / "github-issues.json").write_text(
        json.dumps({"name": "github-issues", "ticket_pattern": r"gh-\d+"}),
        encoding="utf-8",
    )
    code = run(tmp_path, check=True, skill_root=tmp_path)
    err = capsys.readouterr().err
    assert code == 1
    assert "bad config" in err
    assert str(project) in err
    assert "adapter must not be empty" in err


def test_ticket_gate_eng_id_does_not_match_github_issue_number(tmp_path, capsys, monkeypatch):
    import ticket_gate

    # Hermetic: the gate also reads the cwd's git branch, which must not supply a ticket.
    monkeypatch.setattr(ticket_gate, "current_branch", lambda: None)
    install_config(tmp_path)
    make_session(tmp_path, task_id="ENG-22")
    project_config(tmp_path, {"adapter": "github-issues"})
    adapters = tmp_path / "adapters"
    adapters.mkdir()
    (adapters / "github-issues.json").write_text(
        (ROOT / "adapters" / "github-issues.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (adapters / "linear.json").write_text(
        json.dumps({"name": "linear", "ticket_pattern": r"^ENG-\d+$"}),
        encoding="utf-8",
    )
    code = run(tmp_path, check=True, skill_root=tmp_path)
    err = capsys.readouterr().err
    assert code == 1
    assert "did not match" in err


def test_ticket_gate_no_matching_pattern_fails(tmp_path, capsys):
    install_config(tmp_path)
    project_config(tmp_path, {"adapter": "zzz"})
    make_session(tmp_path)
    adapters = tmp_path / "adapters"
    adapters.mkdir()
    (adapters / "zzz.json").write_text(
        json.dumps({"name": "zzz", "ticket_pattern": r"^zzz$"}),
        encoding="utf-8",
    )
    code = run(tmp_path, check=True, skill_root=tmp_path)
    err = capsys.readouterr().err
    assert code == 1
    assert "did not match" in err
    assert "gh-1" in err


# --- ticket_gate_no_tracker: A4 no-tracker mode: the resolved config names "adapter": "none".

_DROP = object()  # a none_overrides value that removes the key
_NONE_CONFIG = {"adapter": "none"}
SLUG_TASK = "feature-add-cache"


def _skill_root(tmp_path, *, config=_NONE_CONFIG, adapters=("none",), none_overrides=None) -> Path:
    """Build a skill root with the core config files and copies of adapters/<name>.json.

    `config` is the project layer `.cadence.json` of `tmp_path` (the workspace): a dict, raw
    text, or None for no project layer.
    """
    install_config(tmp_path)
    if isinstance(config, str):
        (tmp_path / ".cadence.json").write_text(config, encoding="utf-8")
    elif config is not None:
        project_config(tmp_path, config)
    if adapters:
        (tmp_path / "adapters").mkdir()
    for name in adapters:
        data = json.loads((ROOT / "adapters" / f"{name}.json").read_text(encoding="utf-8"))
        if name == "none":
            for key, value in (none_overrides or {}).items():
                if value is _DROP:
                    data.pop(key, None)
                else:
                    data[key] = value
        (tmp_path / "adapters" / f"{name}.json").write_text(json.dumps(data), encoding="utf-8")
    return tmp_path


@pytest.fixture(autouse=True)
def _gate_env(monkeypatch, request, tmp_path):
    """The gate reads the config of the cwd: the workspace is tmp_path, not this checkout."""
    monkeypatch.chdir(tmp_path)
    branch = getattr(request, "param", None)
    monkeypatch.setattr(ticket_gate, "current_branch", lambda: branch)
    monkeypatch.delenv("CADENCE_GATES_READONLY", raising=False)


def _record(tmp_path) -> dict:
    return json.loads((tmp_path / "gates" / "ticket-gate.json").read_text(encoding="utf-8"))


def test_none_adapter_slug_task_id_passes_and_records(tmp_path, capsys):
    make_session(tmp_path, task_id=SLUG_TASK)

    code = ticket_gate.run(tmp_path, check=False, skill_root=_skill_root(tmp_path))

    assert code == 0
    record = _record(tmp_path)
    assert record["tracker"] == "none"
    assert record["ticket"].startswith("TICKET_SKIPPED (none:")
    assert record["task_id"] == SLUG_TASK
    assert record["local_tracker"] == f".context/issues/{SLUG_TASK}.md"
    assert "OK  ticket-gate  tracker=none TICKET_SKIPPED" in capsys.readouterr().out


def test_none_adapter_check_mode_passes_without_writing(tmp_path):
    make_session(tmp_path, task_id=SLUG_TASK)

    code = ticket_gate.run(tmp_path, check=True, skill_root=_skill_root(tmp_path))

    assert code == 0
    assert not (tmp_path / "gates").exists()


@pytest.mark.parametrize(
    ("session", "root_kwargs", "expected"),
    [
        pytest.param({"task_id": ""}, {}, ["task_id"], id="empty_task_id"),
        pytest.param({"task_id": "  \t "}, {}, ["task_id"], id="blank_task_id"),
        pytest.param(None, {}, ["cadence-session.json"], id="missing_session"),
        pytest.param(
            {"task_id": SLUG_TASK},
            {"adapters": ()},
            ["configured adapter none has no file"],
            id="missing_adapter_file",
        ),
        pytest.param(
            {"task_id": SLUG_TASK},
            {"none_overrides": {"name": "github-issues"}},
            ["adapters/none.json", "github-issues"],
            id="adapter_name_mismatch",
        ),
        pytest.param(
            {"task_id": SLUG_TASK},
            {"none_overrides": {"local_tracker": _DROP}},
            ["adapters/none.json", "local_tracker"],
            id="local_tracker_absent",
        ),
        pytest.param(
            {"task_id": SLUG_TASK},
            {"none_overrides": {"local_tracker": ".context/issues/fixed.md"}},
            ["adapters/none.json", "local_tracker"],
            id="local_tracker_no_placeholder",
        ),
        pytest.param(
            {"task_id": SLUG_TASK},
            {"none_overrides": {"local_tracker": 5}},
            ["adapters/none.json", "local_tracker"],
            id="local_tracker_not_string",
        ),
    ],
)
def test_none_mode_fails_closed(tmp_path, capsys, session, root_kwargs, expected):
    if session is not None:
        make_session(tmp_path, **session)

    code = ticket_gate.run(tmp_path, check=False, skill_root=_skill_root(tmp_path, **root_kwargs))

    assert code == 1
    assert not (tmp_path / "gates" / "ticket-gate.json").exists()
    err = capsys.readouterr().err
    for substring in expected:
        assert substring in err


def test_scan_mode_with_none_file_does_not_skip(tmp_path, capsys):
    """A none adapter file does not turn on the no-tracker mode: the default adapter is github-issues."""
    make_session(tmp_path, task_id=SLUG_TASK)
    root = _skill_root(tmp_path, config=None, adapters=("none", "github-issues"))

    code = ticket_gate.run(tmp_path, check=False, skill_root=root)

    assert code == 1
    assert not (tmp_path / "gates" / "ticket-gate.json").exists()
    assert "1 adapter pattern(s)" in capsys.readouterr().err


@pytest.mark.parametrize("_gate_env", ["feature/LOCAL-3"], indirect=True)
def test_scan_mode_none_pattern_never_satisfies_a4(tmp_path, capsys, _gate_env):
    """A LOCAL-n id passes A4 only in the no-tracker mode, not on the github-issues adapter."""
    make_session(tmp_path, task_id="LOCAL-3")
    root = _skill_root(tmp_path, config=None, adapters=("none", "github-issues"))

    code = ticket_gate.run(tmp_path, check=False, skill_root=root)

    assert code == 1
    assert "task_id='LOCAL-3'" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("config", "expected"),
    [
        pytest.param({"adapter": "none"}, True, id="none"),
        pytest.param(None, False, id="no_project_config"),
        pytest.param("{not json", False, id="invalid_json"),
        pytest.param(["none"], False, id="not_object"),
        pytest.param({"adapter": "github-issues"}, False, id="github_issues"),
        pytest.param({"adapter": "None"}, False, id="other_case"),
        pytest.param({"adapter": " none "}, False, id="padded"),
    ],
)
def test_no_tracker_declared_rule(tmp_path, config, expected):
    root = _skill_root(tmp_path, config=config, adapters=())

    assert tracker_header.no_tracker_declared(root, tmp_path) is expected


@pytest.mark.parametrize(
    ("task_id", "none_overrides", "expected"),
    [
        ("feature/add_cache", None, ".context/issues/feature-add-cache.md"),
        ("Feature/Add_Cache", None, ".context/issues/Feature-Add-Cache.md"),
        ("..\\..\\x", None, ".context/issues/..-..-x.md"),
        (
            "feature/add_cache",
            {"local_tracker": ".context/local/{task_id}.md"},
            ".context/local/feature-add-cache.md",
        ),
    ],
)
def test_none_adapter_local_tracker_path(tmp_path, task_id, none_overrides, expected):
    make_session(tmp_path, task_id=task_id)

    code = ticket_gate.run(
        tmp_path, check=False, skill_root=_skill_root(tmp_path, none_overrides=none_overrides)
    )

    assert code == 0
    record = _record(tmp_path)
    assert record["local_tracker"] == expected
    assert record["task_id"] == task_id


# Phase 2: later stages accept the local id (P8 and the A5 header check).

_NONE_SKIP_HEADER = "**Tracker:** none\n**Ticket:** TICKET_SKIPPED (none: no tracker declared)\n"


def _repo_none_adapter() -> dict:
    return json.loads((ROOT / "adapters" / "none.json").read_text(encoding="utf-8"))


def test_a5_header_accepts_none_skip():
    assert tracker_header.tracker_header_problems(_NONE_SKIP_HEADER, _repo_none_adapter()) == []


def test_a5_header_rejects_skip_reason_without_adapter_name():
    text = "**Tracker:** none\n**Ticket:** TICKET_SKIPPED (no tracker)\n"

    problems = tracker_header.tracker_header_problems(text, _repo_none_adapter())

    assert len(problems) == 1


def test_p8_ticket_pattern_accepts_local_id(tmp_path, monkeypatch):
    monkeypatch.setenv("CADENCE_HOME", str(_skill_root(tmp_path)))
    import skill_gate

    pattern = skill_gate.ticket_pattern()

    assert pattern.search("LOCAL-3")
    assert not pattern.search("#3")
    assert not pattern.search("local-3")
    assert not pattern.search("XLOCAL-3")
    assert not pattern.search("LOCAL-3X")


_REGISTER = """## Approved deferrals

| # | Item | Why deferred | Request trace | Ripeness | Evidence | Follow-up ticket |
|---|---|---|---|---|---|---|
| 1 | Add retry | Needs the cache layer first | none | blocked-by | LOCAL-9 builds the cache layer | {ticket} |
"""


@pytest.mark.parametrize(
    ("ticket", "expected"),
    [("LOCAL-3", []), ("#3", ["deferral-missing-ticket"])],
)
def test_p8_register_accepts_local_id(tmp_path, monkeypatch, ticket, expected):
    monkeypatch.setenv("CADENCE_HOME", str(_skill_root(tmp_path)))
    from deferral_adjudication_check import adjudicate_deferral_register

    result = adjudicate_deferral_register(_REGISTER.format(ticket=ticket))

    assert [issue.code for issue in result.issues] == expected


# Phase 3: the agent text names the local file (signal 6, C-2, C-3, C-4).


@pytest.mark.parametrize(
    ("relpath", "tokens"),
    [
        ("references/activation.md", ['"adapter": "none"', ".context/issues/"]),
        ("references/activation.md", ["primary checkout"]),
        ("references/activation.md", ["each repository that this install runs on"]),
        ("references/activation.md", ["1 plus the highest"]),
        ("references/activation.md", ["keeps the case"]),
        (
            "references/peer-contract.md",
            ["**Tracker:** none", "TICKET_SKIPPED (none: no tracker declared)"],
        ),
        ("references/deferral-framework.md", ["LOCAL-<n>"]),
        (
            "docs/system-design.md",
            ["adapters/none.json", "each repository that this install runs on"],
        ),
        ("SKILL.md", [".context/issues/"]),
    ],
)
def test_no_tracker_agent_text_names_local_file(relpath, tokens):
    text = (ROOT / relpath).read_text(encoding="utf-8")

    for token in tokens:
        assert token in text, f"{relpath} lacks {token!r}"
