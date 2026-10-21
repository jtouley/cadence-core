#!/usr/bin/env python3
"""Subagent hooks: render peer prompts from handoff packets.

PreToolUse (matcher `Agent|Task`): for a Cadence peer call, write the packet from the
`inputs` of the stages.json row (scripts/handoff_inputs.py), then replace the router's
prompt with the packet rendering, deny the call when a required input is missing, or
trace the router's own prompt back to allowed sources. SubagentStart: add the
rule that delimited text is data. Settings: `subagent_hygiene` of the resolved config.

Peer models: when the run's session has `peer_models`, an orchestrator's
peer call must pass the configured model and must not be a fork (a fork ignores
`model`). This check runs first, in every mode, and fails closed. Each checked call
gets one row in .context/attest/<run>.models.jsonl with the final decision.

Lean record: on Claude Code, during an active run, an orchestrator's
Agent or Task call that names a peer command anywhere in its prompt must be the
slash command and its arguments only, and must not be a fork. Any other shape is
denied with the reason and a hint. An allowed call gets the lean delegation record
.context/subagent-runs/<run>/<stage>.json (`source: "PreToolUse"`), written after
the final decision. The record holds the prompt the orchestrator sent, not the
rendering. A lean deny also writes a model row when `peer_models` applies.
SubagentStart is not checked.

Context budget: before each orchestrator Agent or Task call during an
active run, the hook reads the orchestrator's last request size from the host
transcript and writes one row to .context/attest/<run>.context.jsonl. It never
denies: the call hands work to a peer. It runs first, in every host, and
fails closed on a bad config.

SubagentStart adds the data rule and the lean return rule to each subagent.

The shipped config sets the handoff to `enforce`. On Claude Code the hook rewrites the
prompt to the rendering. On another host it denies a prompt that is not the rendered
slash command and names that command. The handoff in `shadow` never denies. It logs what
`enforce` would do to .context/handoff/<run-key>/log.jsonl. The model check and
the lean check deny in every mode and fail closed.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

_HOOKS = Path(__file__).resolve().parent
_ROOT = _HOOKS.parent
os.environ.setdefault("CADENCE_HOME", str(_ROOT))
os.environ.setdefault("CADENCE_SKILL_ROOT", str(_ROOT))
for _path in (_HOOKS, _ROOT / "scripts", _ROOT / "shared" / "scripts"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

try:
    import context_budget
    import handoff
    import handoff_inputs
    import peer_attest
    import peer_return
    import peer_models
    import prompt_shape
    from context_root import context_path
    from hook_protocol import (
        candidate_contexts,
        chat_id,
        context_inject,
        extract_tool_input,
        extract_tool_name,
        parse_host,
        read_payload,
        tool_allow,
        tool_deny,
        tool_update_input,
        write_context,
    )
    import session_store
    from pipeline_run_binding import load_session, worktree_run_session
    from skill_paths import display_command
except Exception as exc:  # noqa: BLE001 - startup errors must block, not pass
    sys.stderr.write(f"cadence hook failed closed at startup: {exc!r}\n")
    raise SystemExit(2)

AGENT_TOOLS = {"Agent", "Task"}
MAX_USER_TEXT = 200_000


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def user_text(payload: dict[str, Any]) -> str:
    """The user's own messages from the host transcript (best effort)."""
    path = payload.get("transcript_path")
    if not isinstance(path, str) or not Path(path).is_file():
        return ""
    parts: list[str] = []
    for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        message = row.get("message") if isinstance(row, dict) else None
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            parts += [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
    return "\n".join(parts)[-MAX_USER_TEXT:]


def active_session(ctx: Path) -> dict[str, Any] | None:
    session = load_session(ctx)
    if isinstance(session, dict) and session.get("active_skill") == "cadence" and session.get("pipeline_run_id"):
        return session
    return None


DIFFERS = "router prompt differs from packet rendering"


def pre_agent(
    host: str, payload: dict[str, Any], cfg: dict[str, Any], ctx: Path, session: dict[str, Any],
    workspace: Path | None = None,
) -> str | None:
    """The decision this call wrote: "rewrite" or "deny". None leaves the default allow to the caller."""
    tool_input = extract_tool_input(payload)
    prompt = str(tool_input.get("prompt") or "")
    root = Path(os.environ["CADENCE_HOME"])
    block = handoff.block_for_prompt(prompt, root, workspace)
    if block is None:
        return None  # not a Cadence peer call

    run = handoff_inputs.Run(root=root, ctx=ctx, workspace=Path.cwd() if workspace is None else workspace,
                             session=session)
    packet, reasons = handoff_inputs.packet_for(block, run)
    rendered = ""
    if packet is not None:
        reasons += handoff.verify_packet(packet, run.workspace, root, ctx)
    if packet is not None and not reasons:
        try:
            rendered = handoff.render_prompt(packet, run.workspace, ctx)
        except handoff.HandoffError as exc:
            reasons.append(str(exc))

    untraced: list[str] = []
    notes: list[str] = []
    if packet is not None and rendered and not cfg["rewrite_prompt"]:
        blocking, untraced, notes = trace(prompt, packet, run, payload, cfg)
        reasons += blocking
    elif rendered and prompt.strip() not in sent_forms(host, packet, rendered):
        reasons.append(DIFFERS)

    handoff.append_log(ctx, str(session["pipeline_run_id"]), {
        "event": "PreToolUse", "block": block, "mode": cfg["mode"], "reasons": reasons,
        "untraced": untraced, "notes": notes,
        "prompt_sha256": sha(prompt), "rendered_sha256": sha(rendered) if rendered else "",
    })
    if cfg["mode"] != "enforce" or not reasons:
        return None
    return decide(host, tool_input, packet, rendered, reasons, cfg)


def sent_forms(host: str, packet: dict[str, Any] | None, rendered: str) -> set[str]:
    """The prompts that pass as they are: the rendering, and on a host with no rewrite the slash command."""
    forms = {rendered.strip()}
    if host != "claude" and packet is not None:
        forms.add(packet["command"])
    return forms


def decide(
    host: str, tool_input: dict[str, Any], packet: dict[str, Any] | None, rendered: str, reasons: list[str],
    cfg: dict[str, Any],
) -> str:
    """Write the enforce decision: the rewrite on Claude Code, else a deny."""
    if packet is not None and rendered and cfg["rewrite_prompt"] and reasons == [DIFFERS]:
        if host == "claude":
            tool_update_input(host, {**tool_input, "prompt": rendered})
            return "rewrite"
        tool_deny(host, "BLOCKED: a peer prompt is the lean slash command only. "
                        f"Call the peer again with this prompt: {packet['command']}")
        return "deny"
    tool_deny(host, "BLOCKED: unsafe peer handoff. " + " ".join(reasons))
    return "deny"


def trace(
    prompt: str, packet: dict[str, Any], run: handoff_inputs.Run, payload: dict[str, Any], cfg: dict[str, Any]
) -> tuple[list[str], list[str], list[str]]:
    """(blocking reasons, untraced sentences, log notes) of the router's own prompt (`rewrite_prompt: false`)."""
    sources = {
        "contract": "" if packet["contract"].startswith(handoff.EXTERNAL) else
                    (run.root / packet["contract"]).read_text(encoding="utf-8"),
        "packet": " ".join([packet["command"]] + [r["path"] for r in packet["inputs"]]),
        "user": user_text(payload),
    }
    for row in packet["inputs"]:
        sources["input " + row["path"]] = handoff._input_path(run.workspace, row["path"], run.ctx).read_text(
            encoding="utf-8", errors="replace"
        )
    found = handoff.trace_prompt(prompt, sources)
    untraced = [f"{u['sentence']!r} ({u['reason']})" for u in found]
    if found and cfg["classifier"] == "none":
        return [f"untraced sentence: {u}" for u in untraced], untraced, []
    if found:
        blocking, notes = classify("\n".join(u["sentence"] for u in found), cfg)
        return blocking, untraced, notes
    return [], untraced, []


def classify(text: str, cfg: dict[str, Any]) -> tuple[list[str], list[str]]:
    """(blocking reasons, log notes). Untraced text blocks only when the classifier scores it high."""
    try:
        score = handoff.run_classifier(cfg["classifier"], text, cfg["classifier_timeout"])
    except handoff.ClassifierError as exc:
        message = f"classifier failed: {exc}"
        return ([message], []) if cfg["on_classifier_error"] == "deny" else ([], [message])
    if score >= cfg["classifier_threshold"]:
        return [f"classifier scored {score:.2f} (threshold {cfg['classifier_threshold']}) for untraced text"], []
    return [], [f"classifier scored {score:.2f}, below threshold {cfg['classifier_threshold']}"]


def active_run(ctx: Path, workspace: Path | None = None) -> dict[str, Any] | None:
    """The active run of this worktree, else None.

    A linked worktree reads only its own run, so a worktree run is checked even when the
    root redirect names another run, and a worktree with no run is out of scope. Only the primary checkout follows the root redirect (plan D2). A closed run must
    not apply.
    """
    here = Path.cwd() if workspace is None else workspace  # The chosen host workspace
    session = worktree_run_session(ctx, here)
    if not session and session_store._resolve_toplevel(here) == ctx.resolve().parent:
        session = load_session(ctx)
    active = isinstance(session, dict) and session.get("active_skill") == "cadence" and session.get("status") != "completed"
    return session if active and session.get("pipeline_run_id") else None


def orchestrator_call(payload: dict[str, Any], ctx: Path) -> bool:
    """No agent_id, or an agent that ran activation. Else a peer's own nested call (G-D5)."""
    agent = payload.get("agent_id")
    return not agent or agent in peer_attest.orchestrator_ids(ctx)


def peer_call(
    payload: dict[str, Any], ctx: Path, row: dict[str, Any] | None, workspace: Path | None = None
) -> dict[str, Any] | None:
    """The run, slug and configured model of an orchestrator's peer call under `peer_models`, else None.

    A `peer_models` that is present and not a map raises (fail closed), but only for a peer call.
    """
    session = active_run(ctx, workspace)
    if session is None or row is None or "peer_models" not in session or session["peer_models"] == {}:
        return None
    slug = row["slug"]
    if slug not in peer_models.PEER_MODEL_SLUGS:
        return None
    configured = session["peer_models"]
    if not isinstance(configured, dict):
        raise TypeError("session peer_models is not a map of stage slug to model alias; start a run with --fresh")
    if not orchestrator_call(payload, ctx):
        return None
    return {"run_id": str(session["pipeline_run_id"]), "slug": slug, "block": row["block"], "model": configured.get(slug)}


def record(ctx: Path, payload: dict[str, Any], call: dict[str, Any], decision: str) -> None:
    tool_input = extract_tool_input(payload)
    peer_models.record_call(ctx, call["run_id"], {
        "stage": call["slug"], "block": call["block"], "model": tool_input.get("model"),
        "subagent_type": tool_input.get("subagent_type"), "decision": decision,
        "tool_use_id": payload.get("tool_use_id"), "agent_id": payload.get("agent_id"),
        "session_id": payload.get("session_id"), "at": time.time(),
    })


def lean_hint(row: dict[str, Any]) -> str:
    return (f"Call the peer again with the lean slash command only: {row['command']} <arguments>. "
            "If you only name the command in text, write it without the leading slash.")


def model_check(
    host: str, payload: dict[str, Any], ctx: Path, row: dict[str, Any] | None, reason: str | None,
    workspace: Path | None = None,
) -> tuple[str | None, dict[str, Any] | None]:
    """("deny", call) after writing the deny; ("allow", call) for main to record; (None, None) when out of scope."""
    call = peer_call(payload, ctx, row, workspace)
    if call is None:
        return None, None
    wanted = call["model"]
    if wanted is None:
        return "allow", call
    tool_input = extract_tool_input(payload)
    if tool_input.get("subagent_type") == "fork":
        record(ctx, payload, call, "deny")
        tool_deny(host, f"BLOCKED: peer_models sets {call['slug']} to {wanted}. A fork ignores model. "
                        f'Call the Agent tool with a non-fork subagent_type and model: "{wanted}".')
        return "deny", call
    if tool_input.get("model") == wanted:
        return "allow", call
    record(ctx, payload, call, "deny")
    again = (f"and the lean slash command only: {row['command']} <arguments>." if reason is not None
             else "and the same prompt.")
    tool_deny(host, f"BLOCKED: peer_models sets {call['slug']} to {wanted}. "
                    f'Call the Agent tool again with model: "{wanted}" {again}')
    return "deny", call


def lean_scope(
    host: str, payload: dict[str, Any], ctx: Path, row: dict[str, Any] | None, workspace: Path | None = None
) -> dict[str, Any] | None:
    """The run and record stage of an orchestrator's peer call on Claude Code during an active run, else None."""
    if host != "claude" or payload.get("hook_event_name", "PreToolUse") != "PreToolUse" or row is None:
        return None
    session = active_run(ctx, workspace)
    if session is None or not orchestrator_call(payload, ctx):
        return None
    return {"run_id": str(session["pipeline_run_id"]), "slug": row["slug"], "block": row["block"],
            "record_stage": prompt_shape.delegation_stage_for_prompt(row["command"])}


def lean_check(
    host: str, payload: dict[str, Any], scope: dict[str, Any] | None, row: dict[str, Any] | None, reason: str | None
) -> str | None:
    """"deny" after writing the deny, "allow" for main to record, None when out of scope."""
    if scope is None:
        return None
    if extract_tool_input(payload).get("subagent_type") == "fork":
        tool_deny(host, "BLOCKED: a peer call may not be a fork. A fork carries the whole parent context. "
                        "Call the Agent tool with a non-fork subagent_type and the lean slash command.")
        return "deny"
    if reason is not None:
        tool_deny(host, f"BLOCKED: lean peer prompt rejected: {reason}. {lean_hint(row)}")
        return "deny"
    return "allow"


def budget_check(payload: dict[str, Any], ctx: Path, workspace: Path | None = None) -> None:
    """Record the orchestrator's context size before each of its calls during an active run."""
    session = active_run(ctx, workspace)
    if session is None or not orchestrator_call(payload, ctx):
        return
    cfg = context_budget.load_config(Path(os.environ["CADENCE_HOME"]), workspace)
    if cfg["mode"] == "off":
        return
    transcript, source = context_budget.orchestrator_transcript(payload)
    tokens = context_budget.prompt_tokens(transcript, own=source == "subagent")
    decision = context_budget.judge(cfg, tokens)
    context_budget.record(ctx, str(session["pipeline_run_id"]), {
        "prompt_tokens": tokens, "decision": decision, "mode": cfg["mode"], "source": source,
        "warn_tokens": cfg["warn_tokens"],
        "tool_use_id": payload.get("tool_use_id"), "agent_id": payload.get("agent_id"),
    })
    if decision == "warn":
        sys.stderr.write(
            f"Cadence: the orchestrator context is {tokens} tokens, over context_budget.warn_tokens "
            f"{cfg['warn_tokens']}. The orchestrator reads only gate results and peer returns.\n"
        )


def main(argv: list[str] | None = None) -> int:
    host = parse_host(argv)
    payload = read_payload()
    event = payload.get("hook_event_name")
    is_start = event == "SubagentStart"
    if not is_start and extract_tool_name(payload) not in AGENT_TOOLS:
        return 0  # a different tool: not this hook's business, and no output
    # One context for every check and write: the process cwd run first, else the first
    # host candidate with a session. Else the first candidate, which allows.
    pairs = candidate_contexts(payload, context_path)
    workspace, ctx = write_context(pairs, context_path(Path.cwd())) or pairs[0]
    if event == "PostToolUse":
        return post_agent(payload, ctx, workspace)
    if is_start:
        return handoff_flow(host, payload, ctx, True, workspace)[0]
    code, final, row = pre_tool_use(host, payload, ctx, workspace)
    write_call_row(ctx, payload, workspace, row, final)
    return code


def post_agent(payload: dict[str, Any], ctx: Path, workspace: Path) -> int:
    """Join an orchestrator's Agent or Task call to the subagent it started.

    PostToolUse carries the call's `tool_use_id` and `tool_response.agentId`. One join row
    goes to .context/attest/<run>.joins.jsonl. No PreToolUse check runs (PD9), and the
    hook prints nothing. A lost join row is not a pass: the verdict gate then fails.
    """
    session = active_run(ctx, workspace)
    if session is None or not orchestrator_call(payload, ctx) or not payload.get("tool_use_id"):
        return 0  # A row with no tool_use_id could join anything
    response = payload.get("tool_response")
    response = response if isinstance(response, dict) else {}
    try:
        peer_attest.record_join(ctx, str(session["pipeline_run_id"]), {
            "tool_use_id": payload.get("tool_use_id"), "agent_id": response.get("agentId"),
            "resolved_model": response.get("resolvedModel"), "at": time.time(),
        })
    except Exception as exc:  # noqa: BLE001 - the call already ran; the gate fails closed
        sys.stderr.write(f"cadence: no join row written ({exc!r}); a verdict of this peer will not count\n")
    return 0


def pre_tool_use(
    host: str, payload: dict[str, Any], ctx: Path, workspace: Path
) -> tuple[int, str, dict[str, Any] | None]:
    """The checks of one Agent or Task call: (exit code, final decision, peer row).

    The one exit of PreToolUse, so the caller writes one call row for each decision.
    """
    # The peer row and the lean reason are computed once, during an active run only.
    row = reason = None
    if active_run(ctx, workspace) is not None:
        prompt = str(extract_tool_input(payload).get("prompt") or "")
        rows = handoff._peer_stages(Path(os.environ["CADENCE_HOME"]), workspace)
        row = prompt_shape.peer_command_row(prompt, rows)
        reason = prompt_shape.lean_peer_reason(prompt, row, rows) if row else None
    # The context budget runs first. A bad config fails closed.
    budget_check(payload, ctx, workspace)
    # The model check runs first, outside the shadow try block (fail closed).
    checked, call = model_check(host, payload, ctx, row, reason, workspace)
    if checked == "deny":
        return 0, "deny", row
    # The lean check runs next, in every mode (fail closed).
    scope = lean_scope(host, payload, ctx, row, workspace)
    lean = lean_check(host, payload, scope, row, reason)
    if lean == "deny":
        if checked == "allow":
            record(ctx, payload, call, "deny")
        return 0, "deny", row
    code, final = handoff_flow(host, payload, ctx, False, workspace)
    if checked == "allow":
        record(ctx, payload, call, final)  # no try block: a call with no row must not run
    if lean == "allow" and final == "allow":
        prompt_shape.write_delegation_record(
            ctx, run_id=scope["run_id"], stage=scope["record_stage"],
            command=str(extract_tool_input(payload).get("prompt") or "").strip(), source="PreToolUse",
            tool_use_id=payload.get("tool_use_id"),
        )
    return code, final, row


def write_call_row(
    ctx: Path, payload: dict[str, Any], workspace: Path, row: dict[str, Any] | None, decision: str
) -> None:
    """One call row for an orchestrator's peer call during an active run.

    The stage is the delegation stage of the peer row (PD2), else of the prompt. A verdict
    gate joins its writer to this row through the PostToolUse join row. A lost row is not
    a pass: the gate then finds no call row for the writer and fails.
    """
    session = active_run(ctx, workspace)
    if session is None or not orchestrator_call(payload, ctx) or not payload.get("tool_use_id"):
        return  # A row with no tool_use_id could join anything
    tool_input = extract_tool_input(payload)
    command = row["command"] if row else str(tool_input.get("prompt") or "")
    stage = prompt_shape.delegation_stage_for_prompt(command)
    if stage is None:
        return
    run_id = str(session["pipeline_run_id"])
    try:
        peer_attest.record_call_row(ctx, run_id, {
            "tool_use_id": payload.get("tool_use_id"), "stage": stage,
            "model_slug": peer_models.SLUG_FOR_STAGE.get(stage), "model": tool_input.get("model"),
            "decision": decision, "run_id": run_id, "at": time.time(),
        })
    except Exception as exc:  # noqa: BLE001 - the decision is already out; the gate fails closed
        sys.stderr.write(f"cadence: no call row written ({exc!r}); a verdict of this peer will not count\n")


def start_runs(ctx: Path, payload: dict[str, Any], session: dict[str, Any] | None) -> list[str]:
    """Runs that record a subagent's start.

    Each active run of this .context whose owner is this chat. With no chat id, or no
    owned run, the run that the handoff reads (`active_session`), as at e5c3a92.
    """
    chat = chat_id(payload)
    owned: list[str] = []
    if chat:
        for path in sorted((ctx / "sessions" / "by-worktree").glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if (isinstance(data, dict) and data.get("active_skill") == "cadence" and data.get("status") != "completed"
                    and data.get("owner_session_id") == chat and data.get("pipeline_run_id")):
                owned.append(str(data["pipeline_run_id"]))
    if owned:
        return owned
    return [str(session["pipeline_run_id"])] if session and session.get("pipeline_run_id") else []


def handoff_flow(
    host: str, payload: dict[str, Any], ctx: Path, is_start: bool, workspace: Path | None = None
) -> tuple[int, str]:
    """The packet handoff. Returns the exit code and the final decision ("allow" or "deny")."""
    session = active_session(ctx)
    if is_start and isinstance(payload.get("agent_id"), str) and payload["agent_id"]:
        try:
            for run_id in start_runs(ctx, payload, session):
                peer_attest.agent_event(ctx, run_id, payload["agent_id"], "start")
        except Exception:  # noqa: BLE001 - a lost record never blocks a subagent
            pass
    if session is None:
        if (ctx / "cadence-session.json").is_file() and not is_start:
            return unreadable_session(host, payload, workspace)
        return allow(host, is_start), "allow"
    cfg = handoff.load_config(Path(os.environ["CADENCE_HOME"]), workspace)
    if cfg["mode"] == "off":
        if is_start:  # the stop hook checks returns in every mode, so the rule goes out in every mode (N7)
            context_inject(host, "SubagentStart", peer_return.RETURN_RULE)
        return allow(host, is_start), "allow"
    if is_start:
        context_inject(host, "SubagentStart", f"{handoff.DATA_RULE} {peer_return.RETURN_RULE}")
        return 0, "allow"
    try:
        decided = pre_agent(host, payload, cfg, ctx, session, workspace)
    except Exception as exc:  # noqa: BLE001
        if cfg["mode"] == "enforce":
            raise
        sys.stderr.write(f"cadence handoff hook (shadow) ignored an error: {exc!r}\n")
        decided = None
    if decided is None:
        return allow(host, is_start), "allow"
    return 0, "deny" if decided == "deny" else "allow"


def unreadable_session(host: str, payload: dict[str, Any], workspace: Path | None = None) -> tuple[int, str]:
    """A session file exists but is unreadable: in `enforce`, a peer call cannot get a packet."""
    root = Path(os.environ["CADENCE_HOME"])
    cfg = handoff.load_config(root, workspace)
    prompt = str(extract_tool_input(payload).get("prompt") or "")
    if cfg["mode"] == "enforce" and handoff.block_for_prompt(prompt, root, workspace):
        tool_deny(host, (
            "BLOCKED: the cadence session file is unreadable, so no handoff packet can be found.\n"
            f"ACTION: {display_command('python3 scripts/activate_session.py --from-branch --fresh', root)} "
            "(or --deactivate to end the run)"
        ))
        return 0, "deny"
    return allow(host, False), "allow"


def allow(host: str, is_start: bool) -> int:
    if not is_start:
        tool_allow(host)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 - any crash must block, not pass
        sys.stderr.write(f"cadence hook failed closed: {exc!r}\n")
        raise SystemExit(2)
