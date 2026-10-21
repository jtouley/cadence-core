"""With the architecture pack off (no E3), escape (a) of the PR guard is E1 done.

E3 writes the breadcrumb that escape (a) reads. In the default config E3 is off, and E4
opens the draft PR before any review, so without this escape a default run could not open it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from conftest import enable_all_packs, head_sha, pr_review

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_pr_guard_without_a_run import _guard, _repo, _session, RUN  # noqa: E402,F401


def _default_repo(tmp_path: Path) -> Path:
    """The `_repo` of the PR guard tests, in the default config, with this run's session."""
    repo = _repo(tmp_path / "r")
    (repo / ".cadence.json").write_text("{}\n", encoding="utf-8")
    _session(repo)
    return repo


def _marker(repo: Path) -> None:
    gates = repo / ".context" / "gates"
    gates.mkdir(parents=True, exist_ok=True)
    (gates / f"entry-invariant-{RUN}.ok").write_text(json.dumps({"pipeline_run_id": RUN}) + "\n")


def test_default_config_e1_done_allows_create(tmp_path):
    repo = _default_repo(tmp_path)
    _marker(repo)
    denied, out = _guard(repo, "gh pr create --draft", "cursor")
    assert not denied, out


def test_default_config_e1_done_does_not_allow_ready_without_a_review(tmp_path):
    """MF-1: E1 done is an escape for `create` only; `ready` needs a review of HEAD."""
    repo = _default_repo(tmp_path)
    _marker(repo)
    denied, out = _guard(repo, "gh pr ready", "cursor")
    assert denied and "`gh pr ready` needs a peer review of HEAD" in out


def test_default_config_ready_with_a_review_of_head_is_allowed(tmp_path):
    repo = _default_repo(tmp_path)
    _marker(repo)
    pr_review(repo / ".context", head_sha(repo))
    denied, out = _guard(repo, "gh pr ready", "cursor")
    assert not denied, out


def test_default_config_without_the_p9_marker_is_denied(tmp_path):
    repo = _default_repo(tmp_path)
    denied, out = _guard(repo, "gh pr create --draft", "cursor")
    assert denied and "no P9 marker" in out


def test_full_config_still_needs_the_e3_breadcrumb(tmp_path):
    repo = _default_repo(tmp_path)
    enable_all_packs(repo)
    _marker(repo)
    denied, out = _guard(repo, "gh pr create --draft", "cursor")
    assert denied and f"no E3 breadcrumb for run {RUN}" in out


def test_the_deny_text_names_e1_when_the_architecture_pack_is_off(tmp_path):
    """With E3 off, option (a) must not send the agent to E3."""
    repo = _default_repo(tmp_path)  # no P9 marker, so the guard denies and prints its options
    denied, out = _guard(repo, "gh pr ready", "cursor")
    assert denied and "past E1 (implement)" in out and "architecture-review-impl" not in out


def test_the_deny_text_names_e3_when_the_architecture_pack_is_on(tmp_path):
    repo = _default_repo(tmp_path)
    enable_all_packs(repo)
    denied, out = _guard(repo, "gh pr create --draft", "cursor")
    assert denied and "past E3 (architecture-review-impl)" in out
