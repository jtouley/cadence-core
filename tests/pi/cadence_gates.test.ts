// Node tests for pi/cadence-gates.ts (cd-84). Run through tests/pi/test_pi_extension_node.py,
// which builds the real repo states (CADENCE_PI_REPOS) with tests/pi/tests_pi_helpers.py.
import { test } from "node:test";
import assert from "node:assert/strict";
import { existsSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, symlinkSync, writeFileSync } from "node:fs";
import { homedir, tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import cadenceGates, { decide, hooksFor, register, resolvePiPath, skillRoot, toPayload } from "../../pi/cadence-gates.ts";

const REPO = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..");
const FIXTURES = join(REPO, "tests", "fixtures", "pi");

function repos(): Record<string, string> {
  const raw = process.env.CADENCE_PI_REPOS;
  if (!raw) throw new Error("CADENCE_PI_REPOS not set: run through tests/pi/test_pi_extension_node.py");
  return JSON.parse(raw);
}

function fakePi() {
  const handlers: Record<string, any> = {};
  return { handlers, on(event: string, handler: any) { handlers[event] = handler; } };
}

function makeCtx(cwd: string, sid = "pi", hasUI = false) {
  const notes: string[] = [];
  return { cwd, hasUI, notes, ui: { notify: (m: string) => notes.push(m) },
           sessionManager: { getSessionId: () => sid } };
}

// Pi gives canContinue: false at each normal end of turn (dist/core/agent-session.js:463-481).
function settleEvent(outcome: string, entries: any[] = []) {
  return { type: "agent_before_settle", outcome, entries, continue: false, context: { canContinue: false } };
}

// A fake skill root (F01): hooks.json from `pre` and `stop` rows, one script for each row.
function fakeRoot(opts: { pre?: any[]; stop?: any[]; raw?: string } = {}) {
  const root = mkdtempSync(join(tmpdir(), "cadence-fake-"));
  mkdirSync(join(root, "hooks"));
  writeFileSync(join(root, "hooks", "guard_skill_gate.py"), "");
  const group = (row: any) => {
    writeFileSync(join(root, "hooks", row.script), row.body ?? "");
    const hook: any = { type: "command", command: `python3 "\${CLAUDE_PLUGIN_ROOT}/hooks/${row.script}" --host claude` };
    if (row.timeout !== undefined) hook.timeout = row.timeout;
    return row.matcher === undefined ? { hooks: [hook] } : { matcher: row.matcher, hooks: [hook] };
  };
  const hooks = { PreToolUse: (opts.pre ?? []).map(group), Stop: (opts.stop ?? []).map(group) };
  if (opts.raw !== "absent") writeFileSync(join(root, "hooks", "hooks.json"), opts.raw ?? JSON.stringify({ hooks }));
  return root;
}

// A stop guard that names a new block at each call (the run makes progress).
function countingStop(prefix: string) {
  const n = JSON.stringify(join(mkdtempSync(join(tmpdir(), "cadence-count-")), "n"));
  return `import json, os\np = ${n}\ni = int(open(p).read()) + 1 if os.path.exists(p) else 1\n` +
         `open(p, "w").write(str(i))\nprint(json.dumps({"decision": "block", "reason": f"${prefix} {i}"}))\n`;
}

const PRINT_BLOCK = 'import json; print(json.dumps({"decision": "block", "reason": "hold: open block"}))';
const marker = (root: string) => `open(${JSON.stringify(join(root, "ran"))}, "a").write("x")\n`;
const write = (path: string) => ({ toolName: "write", input: { path, content: "x" } });

async function call(pi: any, toolName: string, input: any, ctx: any) {
  return pi.handlers.tool_call({ type: "tool_call", toolCallId: "t1", toolName, input }, ctx);
}

test("2.1 payload contract, hook lists and Pi path resolution", () => {
  for (const name of readdirSync(FIXTURES)) {
    const fx = JSON.parse(readFileSync(join(FIXTURES, name), "utf-8"));
    const cwd = "/tmp/proj";
    const got = toPayload(fx.pi.toolName, fx.pi.input, cwd, "pi");
    if (!fx.payload) { assert.equal(got, null, name); continue; }
    const want = structuredClone(fx.payload);
    if (want.tool_input.file_path) {
      want.tool_input.file_path = resolve(cwd, want.tool_input.file_path.replace("<HOME>", homedir()));
    }
    assert.deepEqual(got, { ...want, cwd, session_id: "pi" }, name);
    const hooks = hooksFor(REPO, "PreToolUse", want.tool_name);
    assert.deepEqual(hooks.map((h: any) => h.script), Object.keys(fx.hooks), name);
    for (const h of hooks) { assert.deepEqual(h.args, ["--host", "claude"]); assert.equal(h.timeoutMs, 15000); }
  }
  assert.deepEqual(hooksFor(REPO, "Stop").map((h: any) => [h.script, h.args]),
                   [["pipeline_stop_guard.py", ["--host", "claude"]]]);
  const cwd = "/tmp/proj";
  assert.equal(resolvePiPath("\u00A0a.py", cwd), "/tmp/proj/ a.py");
  assert.equal(resolvePiPath("@@x", cwd), "/tmp/proj/@x");
  assert.equal(resolvePiPath("/abs/p", cwd), "/abs/p");
  assert.equal(resolvePiPath("@~/x.txt", cwd), join(homedir(), "x.txt"));
  assert.equal(resolvePiPath("file:///tmp/proj/src/a.py", cwd), "/tmp/proj/src/a.py");
  const root = fakeRoot({ pre: [{ matcher: "Write", script: "a.py", timeout: 2 }, { matcher: "Write", script: "b.py" }] });
  assert.deepEqual(hooksFor(root, "PreToolUse", "Write").map((h: any) => h.timeoutMs), [2000, 15000]);
});

test("2.2 decision table", () => {
  const block = (r: any) => { const d = decide("h.py", r); assert.equal(d.kind, "block", JSON.stringify(r));
                              assert.match(d.reason, /^Cadence:/); return d; };
  assert.equal(decide("h.py", { code: 0, stdout: "  " }).kind, "allow");
  assert.match(block({ code: 0, stdout: JSON.stringify({ hookSpecificOutput: { permissionDecision: "deny",
               permissionDecisionReason: "BLOCKED: no" } }) }).reason, /BLOCKED: no/);
  assert.match(block({ code: 0, stdout: '{"decision": "block", "reason": "hold"}' }).reason, /hold/);
  const notice = decide("h.py", { code: 0, stdout: '{"systemMessage": "note"}' });
  assert.deepEqual([notice.kind, notice.notice], ["allow", "note"]);
  assert.equal(decide("h.py", { code: 0, stdout: "{}" }).kind, "allow");
  for (const stdout of ["oops", "[1]", "3"]) assert.match(block({ code: 0, stdout }).reason, /not JSON/);
  for (const r of [{ code: 1, stdout: "", stderr: "boom" }, { code: 2, stdout: "" },
                   { code: null, signal: "SIGTERM", stdout: "" }, { timedOut: true, stdout: "" },
                   { spawnError: "ENOENT", stdout: "" }]) block(r);
  for (const hso of [{ permissionDecision: "ask" }, { permissionDecision: "allow", updatedInput: {} }]) {
    assert.match(block({ code: 0, stdout: JSON.stringify({ hookSpecificOutput: hso }) }).reason,
                 /asked for a change that Pi cannot apply/);
  }
});

test("2.3 2.4 2.5 2.8 real hooks for each run state", async () => {
  const r = repos();
  const pi = fakePi(); register(pi, {});
  const start = process.cwd();
  process.chdir(tmpdir()); // 2.8: the hook runs in ctx.cwd, not in the process cwd
  try {
    const edit = { path: "src/a.py", edits: [{ oldText: "a = 1", newText: "a = 2" }] };
    assert.match((await call(pi, "edit", edit, makeCtx(r.before_p9))).reason, /entry invariant/);
    assert.match((await call(pi, "edit", { ...edit, path: "@src/a.py" }, makeCtx(r.before_p9))).reason, /entry invariant/);
    assert.equal((await call(pi, "bash", { command: "sed -i '' 's/a/b/' src/a.py" }, makeCtx(r.before_p9))).block, true);
    assert.match((await call(pi, "write", { path: ".context/gates/entry-invariant.ok", content: "ok" },
                             makeCtx(r.before_p9))).reason, /pipeline state/);
    assert.equal((await call(pi, "write", { path: ".context/findings.jsonl", content: '{"status": "verified"}\n' },
                             makeCtx(r.e1))).block, true);
    // After P9 (the per-run marker of #81), an edit under src/ is allowed in E1.
    assert.equal(await call(pi, "edit", edit, makeCtx(r.e1)), undefined);
    for (const state of ["finished", "none"]) {
      assert.equal(await call(pi, "write", { path: "src/a.py", content: "a = 3\n" }, makeCtx(r[state])), undefined);
      assert.equal(await call(pi, "bash", { command: "echo hi > src/a.py" }, makeCtx(r[state])), undefined);
    }
  } finally { process.chdir(start); }
});

test("2.6 fail closed on a bad hook process", async () => {
  const cases = [["sys.exit(1)", undefined, /exit 1/], ["print('not json')", undefined, /not JSON/],
                 ["import time; time.sleep(5)", 1, /timeout/]];
  for (const [body, timeout, want] of cases) {
    const root = fakeRoot({ pre: [{ matcher: "Write", script: "g.py", body: `import sys\n${body}\n`, timeout }] });
    const pi = fakePi(); register(pi, { root });
    const out = await call(pi, "write", { path: "x", content: "" }, makeCtx(tmpdir()));
    assert.equal(out.block, true); assert.match(out.reason, want as RegExp);
  }
  const root = fakeRoot({ pre: [{ matcher: "Write", script: "g.py" }] });
  const pi = fakePi(); register(pi, { root });
  process.env.CADENCE_PYTHON = "/nonexistent/python3";
  try {
    const out = await call(pi, "write", { path: "x", content: "" }, makeCtx(tmpdir()));
    assert.equal(out.block, true); assert.match(out.reason, /could not start/);
  } finally { delete process.env.CADENCE_PYTHON; }
});

test("2.7 2.7b a thrown error blocks the call and holds the turn", async () => {
  const pi = fakePi(); register(pi, {});
  const bad: any = makeCtx("/tmp");
  Object.defineProperty(bad, "cwd", { get() { throw new Error("no cwd"); } });
  const blocked = await call(pi, "write", { path: "x", content: "" }, bad);
  assert.equal(blocked.block, true);
  assert.match(blocked.reason, /^Cadence: gate error: no cwd/);
  for (let i = 0; i < 3; i++) {
    const out = await pi.handlers.agent_before_settle(settleEvent("completed"), bad);
    assert.equal(out.continue, true);
    assert.match(out.entries.at(-1).content, /^Cadence: stop guard error:/);
  }
  assert.equal(await pi.handlers.agent_before_settle(settleEvent("completed"), bad), undefined);
});

test("2.9 2.10 2.11 settle holds, keeps the entries and stops after three", async () => {
  const log = mkdtempSync(join(tmpdir(), "cadence-stop-"));
  const body = `import sys\nopen(${JSON.stringify(join(log, "in"))}, "a").write(sys.stdin.read() + "\\n")\n${PRINT_BLOCK}\n`;
  const root = fakeRoot({ stop: [{ script: "stop.py", body }] });
  const pi = fakePi(); register(pi, { root });
  const ctx = makeCtx(tmpdir());
  for (const outcome of ["aborted", "error"]) {
    assert.equal(await pi.handlers.agent_before_settle(settleEvent(outcome), ctx), undefined);
  }
  assert.equal(existsSync(join(log, "in")), false);
  const e0 = { id: "e0" }, e1 = { id: "e1" };
  const first = await pi.handlers.agent_before_settle(settleEvent("completed", [e0, e1]), ctx);
  assert.equal(first.continue, true);
  assert.equal(first.entries[0], e0); assert.equal(first.entries[1], e1);
  assert.deepEqual({ ...first.entries[2], content: undefined },
                   { type: "custom_message", customType: "cadence-stop-guard", content: undefined, display: true });
  assert.match(first.entries[2].content, /hold: open block/);
  assert.equal((await pi.handlers.agent_before_settle(settleEvent("completed"), ctx)).continue, true);
  assert.equal((await pi.handlers.agent_before_settle(settleEvent("completed"), ctx)).continue, true);
  assert.equal(await pi.handlers.agent_before_settle(settleEvent("completed"), ctx), undefined);
  const sent = readFileSync(join(log, "in"), "utf-8").trim().split("\n").map((l) => JSON.parse(l));
  // The fourth settle runs the guard; the same reason a fourth time ends the turn.
  assert.deepEqual(sent.map((p) => p.stop_hook_active), [false, true, true, true]);
  assert.equal(sent[0].hook_event_name, "Stop");
  await pi.handlers.input({ type: "input", text: "x", source: "extension" }, ctx);
  assert.equal(await pi.handlers.agent_before_settle(settleEvent("completed"), ctx), undefined);
  await pi.handlers.input({ type: "input", text: "x", source: "interactive" }, ctx);
  assert.equal((await pi.handlers.agent_before_settle(settleEvent("completed"), ctx)).continue, true);
});

test("2.12 settle allows a finished run and shows a stop notice", async () => {
  const pi = fakePi(); register(pi, {});
  assert.equal(await pi.handlers.agent_before_settle(settleEvent("completed"), makeCtx(repos().finished)), undefined);
  const root = fakeRoot({ stop: [{ script: "s.py", body: 'print(\'{"systemMessage": "escalate"}\')\n' }] });
  const pi2 = fakePi(); register(pi2, { root });
  const ui = makeCtx(tmpdir(), "pi", true);
  assert.equal(await pi2.handlers.agent_before_settle(settleEvent("completed"), ui), undefined);
  assert.deepEqual(ui.notes, ["escalate"]);
});

test("2.13 skill root through the install link, else block", async () => {
  const dir = mkdtempSync(join(tmpdir(), "cadence-link-"));
  symlinkSync(join(REPO, "pi", "cadence-gates.ts"), join(dir, "cadence-gates.ts"));
  assert.equal(skillRoot(pathToFileURL(join(dir, "cadence-gates.ts")).href, {}), REPO);
  const lost = pathToFileURL(join(dir, "nowhere", "x.ts")).href;
  assert.equal(skillRoot(lost, {}), null);
  const pi = fakePi(); register(pi, { selfUrl: lost, env: {} });
  const out = await call(pi, "write", { path: "x", content: "" }, makeCtx(tmpdir()));
  assert.equal(out.block, true);
  assert.match(out.reason, /skill root not found/);
});

test("2.14 tools that are not judged pass with no hook", async () => {
  const root = fakeRoot({ pre: [{ matcher: ".*", script: "g.py" }] });
  writeFileSync(join(root, "hooks", "g.py"), marker(root));
  const pi = fakePi(); register(pi, { root });
  for (const tool of ["read", "grep", "find", "ls", "my_custom_tool"]) {
    assert.equal(await call(pi, tool, { path: "x" }, makeCtx(tmpdir())), undefined);
  }
  assert.equal(existsSync(join(root, "ran")), false);
});

test("2.15 a bad hooks.json fails closed", async () => {
  const roots = [fakeRoot({ raw: "not json" }), fakeRoot({ raw: "absent" }),
                 fakeRoot({ pre: [{ matcher: "Bash", script: "g.py" }] }),
                 fakeRoot({ pre: [{ matcher: "Write", script: "g.py", timeout: "x" }] }),
                 fakeRoot({ pre: [{ matcher: "Wr(ite", script: "g.py" }] })];
  for (const root of roots) {
    const pi = fakePi(); register(pi, { root });
    const out = await call(pi, "write", { path: "x", content: "" }, makeCtx(tmpdir()));
    assert.equal(out.block, true); assert.match(out.reason, /^Cadence: hooks\.json/);
  }
  const pi = fakePi(); register(pi, { root: roots[0] });
  const held = await pi.handlers.agent_before_settle(settleEvent("completed"), makeCtx(tmpdir()));
  assert.match(held.entries.at(-1).content, /^Cadence: hooks\.json/);
});

test("2.16 hooks run in file order and the first block stops the list", async () => {
  const deny = 'import json; print(json.dumps({"hookSpecificOutput": {"permissionDecision": "deny", "permissionDecisionReason": "B says no"}}))\n';
  const a = fakeRoot({ pre: [{ matcher: "Write", script: "a.py" }, { matcher: "Write", script: "b.py", body: deny }] });
  const pi = fakePi(); register(pi, { root: a });
  assert.match((await call(pi, "write", { path: "x", content: "" }, makeCtx(tmpdir()))).reason, /B says no/);
  const b = fakeRoot({ pre: [{ matcher: "Write", script: "b.py", body: deny }, { matcher: "Write", script: "a.py" }] });
  writeFileSync(join(b, "hooks", "a.py"), marker(b));
  const pi2 = fakePi(); register(pi2, { root: b });
  assert.equal((await call(pi2, "write", { path: "x", content: "" }, makeCtx(tmpdir()))).block, true);
  assert.equal(existsSync(join(b, "ran")), false);
});

test("2.17 the stop guard judges the run that this Pi session owns", async () => {
  const repo = repos().e1_owned;
  const pi = fakePi(); register(pi, {});
  const held = await pi.handlers.agent_before_settle(settleEvent("completed"), makeCtx(repo, "pi-sess-1"));
  assert.equal(held.continue, true);
  const pi2 = fakePi(); register(pi2, {});
  assert.equal(await pi2.handlers.agent_before_settle(settleEvent("completed"), makeCtx(repo, "other")), undefined);
});

test("2.18 shell calls set CADENCE_OWNER_ID to the Pi session id", async () => {
  const saved = { ...process.env };
  try {
    process.env.CLAUDE_CODE_SESSION_ID = "claude-x";
    const none = repos().none;
    const pi = fakePi(); register(pi, {});
    for (const tool of ["bash", "powershell"]) {
      delete process.env.CADENCE_OWNER_ID;
      await call(pi, tool, { command: "echo hi" }, makeCtx(none, "pi-sess-1"));
      assert.equal(process.env.CADENCE_OWNER_ID, "pi-sess-1", tool);
    }
    delete process.env.CADENCE_OWNER_ID;
    await call(pi, "bash", { command: "echo hi" }, makeCtx(none, ""));
    await call(pi, "write", { path: "src/a.py", content: "" }, makeCtx(none, "pi-sess-1"));
    assert.equal(process.env.CADENCE_OWNER_ID, undefined);
  } finally {
    for (const key of Object.keys(process.env)) if (!(key in saved)) delete process.env[key];
    Object.assign(process.env, saved);
  }
});

test("2.19 the extension removes CLAUDECODE at load", () => {
  // The wrapper starts node with CLAUDECODE=1. A Pi bash child must not look like Claude Code.
  assert.equal(process.env.CLAUDECODE, undefined);
});

test("2.20 shell calls set CADENCE_HOME to the extension's skill root", async () => {
  // Pi has no SessionStart hook: without this, SKILL.md's SKILL_ROOT falls back to
  // ~/.cursor/skills/cadence, which can be another checkout (live run, cd-84 Phase 5).
  const saved = process.env.CADENCE_HOME;
  try {
    process.env.CADENCE_HOME = "/some/other/checkout";
    const pi = fakePi(); register(pi, {});
    await call(pi, "bash", { command: "echo hi" }, makeCtx(repos().none, "pi-sess-1"));
    assert.equal(process.env.CADENCE_HOME, REPO);
    const lost = pathToFileURL(join(tmpdir(), "nowhere", "x.ts")).href;
    process.env.CADENCE_HOME = "/kept";
    const pi2 = fakePi(); register(pi2, { selfUrl: lost, env: {} });
    await call(pi2, "bash", { command: "echo hi" }, makeCtx(repos().none, "pi-sess-1"));
    assert.equal(process.env.CADENCE_HOME, "/kept");
  } finally {
    if (saved === undefined) delete process.env.CADENCE_HOME; else process.env.CADENCE_HOME = saved;
  }
});

test("2.21 powershell is blocked during an active run and judged with no run", async () => {
  // The skill gate parses bash, not PowerShell (Set-Content, Out-File), so the extension fails closed.
  const r = repos();
  const pi = fakePi(); register(pi, {});
  for (const state of ["before_p9", "e1"]) {
    const out = await call(pi, "powershell", { command: "Set-Content -Path src/a.py -Value 'x'" }, makeCtx(r[state]));
    assert.equal(out.block, true, state);
    assert.match(out.reason, /powershell is not judged during a Cadence run/);
  }
  assert.equal(await call(pi, "powershell", { command: "Get-ChildItem" }, makeCtx(r.none)), undefined);
  assert.equal(await call(pi, "powershell", { command: "Get-ChildItem" }, makeCtx(r.finished)), undefined);
});

test("2.22 holds go on while the stop guard names a new block", async () => {
  // The stop guard counts its refusals for each block; progress gives a new reason.
  const body = countingStop("next block");
  const root = fakeRoot({ stop: [{ script: "stop.py", body }] });
  const pi = fakePi(); register(pi, { root });
  for (let i = 1; i <= 6; i++) {
    const out = await pi.handlers.agent_before_settle(settleEvent("completed"), makeCtx(tmpdir()));
    assert.match(out.entries.at(-1).content, new RegExp(`next block ${i}`));
  }
});

test("2.23 the default export (what Pi loads) registers the three gates", async () => {
  const pi = fakePi(); cadenceGates(pi);
  assert.deepEqual(Object.keys(pi.handlers).sort(), ["agent_before_settle", "input", "tool_call"]);
  const out = await call(pi, "edit", { path: "src/a.py", edits: [] }, makeCtx(repos().before_p9));
  assert.equal(out.block, true);
  assert.match(out.reason, /entry invariant/);
});

test("2.24 skill root falls back to CADENCE_HOME, then CADENCE_SKILL_ROOT", () => {
  const lost = pathToFileURL(join(tmpdir(), "nowhere", "x.ts")).href;
  const empty = mkdtempSync(join(tmpdir(), "cadence-empty-"));
  assert.equal(skillRoot(lost, { CADENCE_HOME: REPO }), REPO);
  assert.equal(skillRoot(lost, { CADENCE_HOME: empty, CADENCE_SKILL_ROOT: REPO }), REPO);
  assert.equal(skillRoot(lost, { CADENCE_HOME: empty, CADENCE_SKILL_ROOT: empty }), null);
});

test("2.25 the hook env names the project and the root, and drops host markers", async () => {
  const root = fakeRoot({ pre: [{ matcher: "Write", script: "env.py" }] });
  const seen = join(root, "env.json");
  writeFileSync(join(root, "hooks", "env.py"), `import json, os\njson.dump({k: os.environ.get(k) for k in ` +
    `("CLAUDE_PROJECT_DIR", "CADENCE_HOME", "CLAUDECODE", "CURSOR_PROJECT_DIR")}, open(${JSON.stringify(seen)}, "w"))\n`);
  const saved = process.env.CURSOR_PROJECT_DIR;
  process.env.CURSOR_PROJECT_DIR = "/elsewhere";
  try {
    const pi = fakePi(); register(pi, { root });
    const cwd = mkdtempSync(join(tmpdir(), "cadence-cwd-"));
    assert.equal(await call(pi, "write", { path: "x", content: "" }, makeCtx(cwd)), undefined);
    assert.deepEqual(JSON.parse(readFileSync(seen, "utf-8")),
                     { CLAUDE_PROJECT_DIR: cwd, CADENCE_HOME: root, CLAUDECODE: null, CURSOR_PROJECT_DIR: null });
  } finally {
    if (saved === undefined) delete process.env.CURSOR_PROJECT_DIR; else process.env.CURSOR_PROJECT_DIR = saved;
  }
});

test("2.26 matcher forms and bad timeouts", () => {
  for (const matcher of [undefined, "", "*"]) {
    const root = fakeRoot({ pre: [{ matcher, script: "g.py" }] });
    assert.deepEqual(hooksFor(root, "PreToolUse", "Bash").map((h: any) => h.script), ["g.py"], String(matcher));
  }
  const stop = fakeRoot({ stop: [{ matcher: "Nothing", script: "s.py" }] });
  assert.deepEqual(hooksFor(stop, "Stop").map((h: any) => h.script), ["s.py"]);
  for (const timeout of [0, -1]) {
    const root = fakeRoot({ pre: [{ matcher: "Write", script: "g.py", timeout }] });
    assert.throws(() => hooksFor(root, "PreToolUse", "Write"), /^Error: Cadence: hooks\.json timeout/);
  }
});

test("2.27 abort kills a running hook; notices without a UI go to stderr; signals are named", async () => {
  const root = fakeRoot({ pre: [{ matcher: "Write", script: "slow.py", body: "import time\ntime.sleep(30)\n" }] });
  const pi = fakePi(); register(pi, { root });
  const ctl = new AbortController();
  const ctx: any = { ...makeCtx(tmpdir()), signal: ctl.signal };
  const started = Date.now();
  setTimeout(() => ctl.abort(), 300);
  const out = await call(pi, "write", { path: "x", content: "" }, ctx);
  assert.equal(out.block, true);
  assert.match(out.reason, /signal SIGKILL/);
  assert.ok(Date.now() - started < 5000);
  const note = fakeRoot({ stop: [{ script: "s.py", body: 'print(\'{"systemMessage": "to stderr"}\')\n' }] });
  const pi2 = fakePi(); register(pi2, { root: note });
  const writes: string[] = [];
  const real = process.stderr.write.bind(process.stderr);
  (process.stderr as any).write = (chunk: any) => { writes.push(String(chunk)); return true; };
  try {
    assert.equal(await pi2.handlers.agent_before_settle(settleEvent("completed"), makeCtx(tmpdir())), undefined);
  } finally { (process.stderr as any).write = real; }
  assert.ok(writes.some((w) => w.includes("to stderr")), writes.join(""));
});

test("2.28 powershell fails closed when the run check cannot answer", async () => {
  const bodies = ["import sys\nsys.exit(1)\n", "print('oops')\n", "print('{}')\n",
                  "import sys\nprint('{\"active\": false}')\nsys.exit(1)\n"];
  for (const body of bodies) {
    const root = fakeRoot({ pre: [{ matcher: "Bash", script: "g.py" }] });
    mkdirSync(join(root, "shared"));
    writeFileSync(join(root, "shared", "skill_gate.py"), body);
    const pi = fakePi(); register(pi, { root });
    const out = await call(pi, "powershell", { command: "Get-ChildItem" }, makeCtx(tmpdir()));
    assert.equal(out.block, true, body);
    assert.match(out.reason, /powershell is not judged/);
  }
});

test("2.29 a safety cap of 50 holds for each user input", async () => {
  const body = countingStop("block");
  const root = fakeRoot({ stop: [{ script: "stop.py", body }] });
  const pi = fakePi(); register(pi, { root });
  const ctx = makeCtx(tmpdir());
  for (let i = 1; i <= 50; i++) assert.equal((await pi.handlers.agent_before_settle(settleEvent("completed"), ctx)).continue, true);
  assert.equal(await pi.handlers.agent_before_settle(settleEvent("completed"), ctx), undefined);
  await pi.handlers.input({ type: "input", text: "x", source: "interactive" }, ctx);
  assert.equal((await pi.handlers.agent_before_settle(settleEvent("completed"), ctx)).continue, true);
});

test("2.30 small rules: write keeps CADENCE_HOME, bare ~, a session id that is not text", async () => {
  assert.equal(resolvePiPath("~", "/tmp/proj"), homedir());
  const saved = process.env.CADENCE_HOME;
  try {
    process.env.CADENCE_HOME = "/kept";
    const pi = fakePi(); register(pi, {});
    await call(pi, "write", { path: "src/a.py", content: "a = 3\n" }, makeCtx(repos().none));
    assert.equal(process.env.CADENCE_HOME, "/kept");
  } finally {
    if (saved === undefined) delete process.env.CADENCE_HOME; else process.env.CADENCE_HOME = saved;
  }
  const log = mkdtempSync(join(tmpdir(), "cadence-sid-"));
  const body = `import sys\nopen(${JSON.stringify(join(log, "in"))}, "w").write(sys.stdin.read())\n`;
  const root = fakeRoot({ pre: [{ matcher: "Write", script: "g.py", body }] });
  const pi = fakePi(); register(pi, { root });
  const ctx: any = { ...makeCtx(tmpdir()), sessionManager: { getSessionId: () => 42 } };
  await call(pi, "write", { path: "x", content: "" }, ctx);
  assert.equal(JSON.parse(readFileSync(join(log, "in"), "utf-8")).session_id, "pi");
});

test("2.31 more fail-closed and text rules", async () => {
  const bad = (pre: any) => { const root = fakeRoot({ pre: [pre] });
    return () => hooksFor(root, "PreToolUse", "Write"); };
  assert.throws(bad({ matcher: "Write", script: "g.py", timeout: "5" }), /hooks\.json timeout/);
  const noList = fakeRoot({ raw: JSON.stringify({ hooks: {} }) });
  assert.throws(() => hooksFor(noList, "PreToolUse", "Write"), /^Error: Cadence: hooks\.json has no PreToolUse list/);
  const noScript = fakeRoot({ raw: JSON.stringify({ hooks: { PreToolUse: [{ hooks: [{ command: "echo hi" }] }] } }) });
  assert.throws(() => hooksFor(noScript, "PreToolUse", "Write"), /has no hooks\/<name>\.py/);
  const half = mkdtempSync(join(tmpdir(), "cadence-half-"));
  mkdirSync(join(half, "hooks"));
  assert.equal(skillRoot(pathToFileURL(join(tmpdir(), "nowhere.ts")).href, { CADENCE_HOME: half }), null);
  const lost = pathToFileURL(join(tmpdir(), "nowhere", "x.ts")).href;
  const pi = fakePi(); register(pi, { selfUrl: lost, env: {} });
  const held = await pi.handlers.agent_before_settle({ ...settleEvent("completed"), entries: "not a list" }, makeCtx(tmpdir()));
  assert.equal(held.entries.length, 1);
  assert.match(held.entries[0].content, /skill root not found/);
  assert.deepEqual(decide("h.py", { code: 0, stdout: "" }), { kind: "allow" });
  assert.match(decide("h.py", { code: 1, stdout: "", stderr: "Traceback: boom" }).reason, /exit 1\): Traceback: boom/);
});
