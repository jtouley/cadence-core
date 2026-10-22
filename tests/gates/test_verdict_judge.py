"""A verdict in the peer's own words is one typed question to Jev; the gate keeps the policy.

A local stub stands in for the TypeSafe System One API (`TYPESAFE_API_URL`), so no test uses the network.
The stub answers from a table, with a confidence, and counts the questions it got.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest

from conftest import code_paths, run_py

code_paths()
import artifact_requirements as ar  # noqa: E402
import verdict_judge as vj  # noqa: E402

# line (in the question's state) -> (choice, confidence)
ANSWERS = {
    "BLOCKED. B-1 and B-2 must be fixed before approval.": ("REQUEST CHANGES", 1.0),
    "APPROVED, with the two conditions below.": ("APPROVE WITH CONDITIONS", 1.0),
    "Looks fine overall, but see the notes.": ("APPROVE WITH CONDITIONS", 0.88),
    "Do not merge yet: MF-1 is open.": ("NO MERGE", 0.99),
    "blocking": ("must-fix", 0.99),
    "PASSED": ("PASS", 1.0),
    "No idea.": ("unclear", 0.97),
}


# plan line -> the probability of the `waits` option
WAITS = {"Phase 2 does not start until the owner accepts the quoting record.": 0.87}


class _Stub(BaseHTTPRequestHandler):
    asked: list[dict] = []

    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        _Stub.asked.append({"auth": self.headers.get("Authorization"), **body})
        [(qid, question)] = body["questions"].items()
        if "waits" in question.get("criteria", {}):  # the person-wait question: the state is the one line
            p = WAITS.get(body["state"], 0.05)
            probabilities = {"waits": p, "records": (1 - p) / 2, "work": (1 - p) / 2}
            choice = max(probabilities, key=lambda k: probabilities[k])
            answer = {"type": "choice", "choice": choice, "confidence": 0.9, "probabilities": probabilities}
        else:
            line = body["state"].split(": ", 1)[1]
            choice, confidence = ANSWERS.get(line, ("unclear", 1.0))
            answer = {"type": "choice", "choice": choice, "confidence": confidence, "probabilities": {choice: confidence}}
        data = json.dumps({"model": "jev-stub", "answers": {qid: answer}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


@pytest.fixture
def jev(monkeypatch, tmp_path):
    server = HTTPServer(("127.0.0.1", 0), _Stub)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    _Stub.asked = []
    url = f"http://127.0.0.1:{server.server_address[1]}/v1/systemone"
    monkeypatch.setenv("TYPESAFE_API_URL", url)
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".context").mkdir()
    yield _Stub
    server.shutdown()


def test_an_exact_verdict_asks_nothing(jev):
    assert ar.adversarial_verdict_in_text("Verdict: REQUEST CHANGES") == "REQUEST CHANGES"
    assert jev.asked == []


@pytest.mark.parametrize(("text", "expected"), [
    ("Verdict: BLOCKED. B-1 and B-2 must be fixed before approval.", "REQUEST CHANGES"),
    ("## Verdict\n\nAPPROVED, with the two conditions below.", "APPROVE WITH CONDITIONS"),
    ("Verdict: Looks fine overall, but see the notes.", None),  # confidence 0.88 is below MIN_CONFIDENCE
    ("Verdict: No idea.", None),  # unclear
])
def test_a_verdict_in_the_peers_words_is_one_question(jev, text, expected):
    assert ar.adversarial_verdict_in_text(text) == expected
    [question] = jev.asked
    assert question["auth"] == "Bearer test-key" and question["model"] == vj.MODEL
    assert set(question["questions"]["verdict"]["criteria"]) == {*vj.MEANINGS["adversarial-review"], vj.UNCLEAR}
    assert question["state"].endswith(text.split("\n")[-1].removeprefix("Verdict: "))  # the one line, no file


def test_an_answer_is_kept_and_the_read_only_walk_asks_nothing(jev, monkeypatch):
    text = "Verdict: BLOCKED. B-1 and B-2 must be fixed before approval."
    monkeypatch.setenv("CADENCE_GATES_READONLY", "1")
    assert ar.adversarial_verdict_in_text(text) is None and jev.asked == []
    monkeypatch.delenv("CADENCE_GATES_READONLY")
    assert ar.adversarial_verdict_in_text(text) == "REQUEST CHANGES"
    monkeypatch.setenv("CADENCE_GATES_READONLY", "1")
    assert ar.adversarial_verdict_in_text(text) == "REQUEST CHANGES" and len(jev.asked) == 1
    kept = json.loads((Path(".context") / "verdict-judgments.json").read_text(encoding="utf-8"))
    assert [(r["line"], r["choice"], r["model"]) for r in kept.values()] == [
        ("BLOCKED. B-1 and B-2 must be fixed before approval.", "REQUEST CHANGES", "jev-stub")]


def test_without_a_key_nothing_is_asked_and_the_gate_fails_closed(jev, monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY")
    assert ar.adversarial_verdict_in_text("Verdict: BLOCKED. B-1 and B-2 must be fixed before approval.") is None
    assert jev.asked == []


def test_r1_and_the_scorecard_read_the_peers_words(jev):
    from gate_common import R1_VERDICTS
    from verdict_judge import judged_verdict

    assert judged_verdict("## Verdict\n\nDo not merge yet: MF-1 is open.", "pr-review", R1_VERDICTS) == "NO MERGE"
    assert vj.judge("PASSED", "scorecard") == "PASS"


def test_a_plan_finding_severity_in_the_reviewers_word(jev, tmp_path):
    from plan_wave import _severity

    assert _severity("must-fix") == "must-fix" and _severity("NIT") == "nit" and len(jev.asked) == 0
    assert _severity("blocking") == "must-fix" and _severity("No idea.") is None


def test_a_gate_subprocess_asks_jev_too(jev, tmp_path):
    """The gates run as scripts: the key and the URL reach them through the environment."""
    root = Path(__file__).resolve().parents[2]
    (tmp_path / "report.md").write_text("Verdict: BLOCKED. B-1 and B-2 must be fixed before approval.\n", encoding="utf-8")
    (tmp_path / "read.py").write_text(
        "import sys\n"
        f"sys.path[:0] = [{str(root / 'scripts')!r}, {str(root / 'shared' / 'scripts')!r}, {str(root / 'shared')!r}]\n"
        "import artifact_requirements as a\n"
        "print(a.adversarial_verdict_in_text(open('report.md').read()))\n", encoding="utf-8")
    proc = run_py(tmp_path / "read.py", cwd=tmp_path)
    assert proc.stdout.strip() == "REQUEST CHANGES", proc.stderr
    assert len(jev.asked) == 1


def test_a_plan_line_that_waits_on_a_person_is_a_must_fix_of_the_round_once(jev, tmp_path):
    """Runs stopped in E1 on a plan line that waited on the owner. Code finds the lines that name a
    person or a decision; Jev judges each one; a wait is a must-fix of the round, raised once per line."""
    from types import SimpleNamespace

    from plan_wave import _person_waits

    plan = ("# Plan\n\nRUN_BY_COMMAND: plan-create\n\n## Goal\n\nThe owner wants a CSV export.\n\n"
            "### Phase 1: query\n\nAdd the query.\n\nRun `python3 acceptance/check_export.py AC-1`.\n\n### Phase 2: render\n\n"
            "Phase 2 does not start until the owner accepts the quoting record.\n\n"
            "## Revision ledger (review abc)\n\n| MF-1 | the owner accepts it later |\n")
    rnd: Any = SimpleNamespace(text=plan)
    [row] = _person_waits(rnd, [])
    assert (row["id"], row["phase"], row["severity"], row["stage"]) == ("W-1", "Phase 2", "must-fix", "person-wait")
    asked = [q["state"] for q in jev.asked]
    assert asked == ["The owner wants a CSV export.", "Phase 2 does not start until the owner accepts the quoting record."]
    assert _person_waits(rnd, [{"p5": [row]}]) == []  # raised once: a later round does not raise the same line again


def test_without_a_key_no_plan_line_is_judged(jev, monkeypatch):
    from types import SimpleNamespace

    from plan_wave import _person_waits

    monkeypatch.delenv("TYPESAFE_API_KEY")
    stub: Any = SimpleNamespace(text="### Phase 1: x\n\nThe owner accepts it first.\n")
    assert _person_waits(stub, []) == []
    assert jev.asked == []


def test_each_judgment_a_run_uses_is_logged_once(jev):
    """The Jev answers are decisions of the run: `.context/verdict-judgments.jsonl` logs each one that a run uses,
    with the model and whether the gate took it."""
    text = "Verdict: BLOCKED. B-1 and B-2 must be fixed before approval."
    assert ar.adversarial_verdict_in_text(text) == "REQUEST CHANGES"
    assert ar.adversarial_verdict_in_text(text) == "REQUEST CHANGES"  # a second read: no new row
    assert ar.adversarial_verdict_in_text("Verdict: Looks fine overall, but see the notes.") is None
    rows = [json.loads(line) for line in (Path(".context") / "verdict-judgments.jsonl").read_text().splitlines()]
    assert [(r["kind"], r["answer"], r["taken"], r["source"]) for r in rows] == [
        ("adversarial-review", "REQUEST CHANGES", True, "api"),
        ("adversarial-review", "APPROVE WITH CONDITIONS", False, "api")]  # 0.88: logged, not taken
    assert {r["model"] for r in rows} == {"jev-stub"}


def test_the_read_only_walk_logs_nothing(jev, monkeypatch):
    ar.adversarial_verdict_in_text("Verdict: BLOCKED. B-1 and B-2 must be fixed before approval.")
    log = Path(".context") / "verdict-judgments.jsonl"
    log.unlink()
    monkeypatch.setenv("CADENCE_GATES_READONLY", "1")
    assert ar.adversarial_verdict_in_text("Verdict: BLOCKED. B-1 and B-2 must be fixed before approval.") == "REQUEST CHANGES"
    assert not log.exists()
