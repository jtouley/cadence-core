"""A peer's verdict in its own words: one typed question to Jev (TypeSafe AI), else None.

A reviewer writes `Verdict: BLOCKED` or `Verdict: APPROVED, with two conditions` where the gate knows
`REQUEST CHANGES` and `APPROVE WITH CONDITIONS`. labeled_lines.verdict reads each exact option. When the
newest verdict line matches none, this module asks Jev one Choice question: which option the line states,
or `unclear`. The answer counts only at a confidence of MIN_CONFIDENCE or more, so a gate still fails closed
on a line that states no verdict. Code keeps the policy: Jev only maps words to an option of the gate.

The question holds only that one line, cut at MAX_LINE characters, never a file of the repository. Without
TYPESAFE_API_KEY it asks nothing, and the gate reads only the exact options.

Each answer is kept in `.context/verdict-judgments.json` under the sha256 of the question. A second read of
the same line asks nothing, and the stop guard's read-only walk (CADENCE_GATES_READONLY=1) reads only that
file. Each judgment that a run uses is logged once, in `.context/verdict-judgments.jsonl`: the run, the kind,
the line, the answer, its confidence, the model, whether the gate took it, and whether it came from the API
or the kept answer.
"""

from __future__ import annotations

import hashlib
import json
import os
import urllib.request
from pathlib import Path

from labeled_lines import verdict, verdict_values

API_URL = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-latest"
MIN_CONFIDENCE = 0.9  # a smoke test: clear lines 0.99 to 1.0; "Looks fine overall, but see the notes" 0.88
MAX_LINE = 400
TIMEOUT_SECONDS = 20
UNCLEAR = "unclear"
CACHE = Path(".context") / "verdict-judgments.json"
LOG = Path(".context") / "verdict-judgments.jsonl"

# What each answer means, for the question. The keys are the options of each reader.
MEANINGS = {
    "plan-review": {
        "READY TO EXECUTE": "the plan can be implemented as written; no change is needed",
        "READY WITH CHANGES": "the plan needs changes before it is implemented",
        "REQUEST CHANGES": "the reviewer asks for changes to the plan before it is implemented",
        "NOT READY": "the plan is not ready: it has major gaps",
    },
    "adversarial-review": {
        "APPROVE": "approved, with no condition",
        "APPROVE WITH CONDITIONS": "approved only when the listed conditions are met",
        "REQUEST CHANGES": "not approved: blocking issues must be fixed first; the work is blocked or declined",
    },
    "pr-review": {
        "MERGE-READY": "the pull request can merge",
        "NO MERGE": "the pull request must not merge: a must-fix item is open or the review failed",
    },
    "vibe-test": {
        "READY": "the request is ready for planning",
        "NOT READY": "the request is not ready for planning: it has blocking gaps",
    },
    "scorecard": {
        "PASS": "the tests pass the benchmark",
        "FAIL": "the tests fail the benchmark: a must-fix finding or a score below the floor",
    },
    "severity": {
        "must-fix": "the finding blocks: it must be fixed before the work goes on",
        "nit": "the finding does not block: a note, a suggestion or a minor point",
    },
}
_STATE = {"severity": "The severity that a reviewer gave one finding: {line}"}


def judged_verdict(text: str, reader: str, exact: tuple[str, ...] = ()) -> str | None:
    """The verdict of ``text`` for ``reader`` (a key of MEANINGS): an exact option (``exact``, else the keys of
    MEANINGS), else Jev's answer for the newest verdict line, else None."""
    found = verdict(text, exact or tuple(MEANINGS[reader]))
    if found:
        return found
    values = [v.strip() for v in verdict_values(text) if v.strip()]
    return judge(values[-1], reader) if values else None


def judge(value: str, reader: str) -> str | None:
    """Jev's option of MEANINGS[reader] for one line, at MIN_CONFIDENCE or more, else None."""
    meanings = MEANINGS[reader]
    line = " ".join(str(value).split())[:MAX_LINE]
    if not line:
        return None
    key = hashlib.sha256(json.dumps([reader, line, sorted(meanings.items()), MODEL]).encode("utf-8")).hexdigest()
    cache, source = _load(), "kept"
    if key not in cache and os.environ.get("CADENCE_GATES_READONLY") != "1":
        answer = _ask(_STATE.get(reader, "The verdict line of a review report: {line}").format(line=line), meanings)
        if answer is not None:
            cache[key], source = {"reader": reader, "line": line, **answer}, "api"
            _save(cache)
    answer = cache.get(key) or {}
    choice, confidence = answer.get("choice"), answer.get("confidence")
    taken = choice in meanings and isinstance(confidence, (int, float)) and confidence >= MIN_CONFIDENCE
    if answer:
        _record(key, {"kind": reader, "line": line, "answer": choice, "confidence": confidence,
                      "model": answer.get("model")}, taken=taken, source=source)
    return choice if taken else None


def option_probability(text: str, instructions: str, criteria: dict[str, str], option: str, *, kind: str,
                       threshold: float) -> float | None:
    """Jev's probability of ``option`` in one Choice question on ``text`` (one short line), else None. Kept as
    `judge` is; the log says whether the probability reached the caller's ``threshold``."""
    line = " ".join(str(text).split())[:MAX_LINE]
    if not line:
        return None
    key = hashlib.sha256(json.dumps([kind, line, instructions, sorted(criteria.items()), MODEL]).encode("utf-8")).hexdigest()
    cache, source = _load(), "kept"
    if key not in cache and os.environ.get("CADENCE_GATES_READONLY") != "1":
        answer = _post({"model": MODEL, "state": line, "questions": {"q": {"type": "choice", "instructions": instructions,
                                                                           "criteria": criteria}}})
        probabilities = (answer or {}).get("answers", {}).get("q", {}).get("probabilities")
        if isinstance(probabilities, dict):
            cache[key], source = {"reader": kind, "line": line, "probabilities": probabilities,
                                  "model": (answer or {}).get("model")}, "api"
            _save(cache)
    kept = cache.get(key) or {}
    value = (kept.get("probabilities") or {}).get(option)
    if not isinstance(value, (int, float)):
        return None
    _record(key, {"kind": kind, "line": line, "answer": option if value >= threshold else "not " + option,
                  "confidence": value, "model": kept.get("model")}, taken=value >= threshold, source=source)
    return float(value)


def enabled() -> bool:
    return bool(os.environ.get("TYPESAFE_API_KEY"))


def _ask(state: str, meanings: dict[str, str]) -> dict | None:
    data = _post({"model": MODEL, "state": state,
                  "questions": {"verdict": {"type": "choice", "instructions": "Which answer does this text state?",
                                            "criteria": {**meanings, UNCLEAR: "the text states none of them, or more than one"}}}})
    answers = data.get("answers") if isinstance(data, dict) else None
    answer = answers.get("verdict") if isinstance(answers, dict) else None
    if not isinstance(answer, dict):
        return None
    return {"choice": answer.get("choice"), "confidence": answer.get("confidence"), "model": data.get("model")}


def _post(body: dict) -> dict | None:
    """One System One request, or None (no key, no network, a bad answer): the caller then fails closed."""
    api_key = os.environ.get("TYPESAFE_API_KEY")
    if not api_key:
        return None
    request = urllib.request.Request(os.environ.get("TYPESAFE_API_URL", API_URL), data=json.dumps(body).encode(),
                                     headers={"Authorization": f"Bearer {api_key}",
                                              "Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _record(key: str, entry: dict, *, taken: bool, source: str) -> None:
    """Log the first use of a judgment in this run. The read-only walk writes nothing."""
    if os.environ.get("CADENCE_GATES_READONLY") == "1" or not LOG.parent.is_dir():
        return
    run = _run_id()
    try:
        seen = {(r.get("pipeline_run_id"), r.get("key")) for r in map(json.loads, LOG.read_text(encoding="utf-8").splitlines())}
    except (OSError, ValueError):
        seen = set()
    if (run, key) in seen:
        return
    from datetime import datetime, timezone

    row = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "pipeline_run_id": run, "key": key,
           **entry, "taken": taken, "source": source}
    with LOG.open("a", encoding="utf-8") as log:
        log.write(json.dumps(row, ensure_ascii=False) + "\n")


def _run_id() -> str:
    try:
        from pipeline_run_binding import current_session

        return str(current_session(LOG.parent).get("pipeline_run_id") or "")
    except Exception:  # noqa: BLE001 - a log row without a run id is still a log row
        return ""


def _load() -> dict:
    try:
        data = json.loads(CACHE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save(cache: dict) -> None:
    if CACHE.parent.is_dir():
        CACHE.write_text(json.dumps(cache, indent=2) + "\n", encoding="utf-8")
