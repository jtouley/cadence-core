// Cadence gates for Pi. Pi has no hooks, so this extension runs the
// Claude Code hooks of hooks/hooks.json with the `--host claude` payloads and obeys them:
// tool_call (write, edit, bash, powershell) -> PreToolUse; agent_before_settle -> Stop.
// Fail closed: each result that is not a clear allow blocks the call or holds the turn.
// Node built-ins only, erasable TypeScript only (Pi loads it with jiti; tests with node).
import { spawn } from "node:child_process";
import { existsSync, readFileSync, realpathSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const TOOLS: Record<string, string> = { write: "Write", edit: "Edit", bash: "Bash", powershell: "Bash" };
const SHELL_TOOLS = new Set(["bash", "powershell"]);
const MAX_SAME_HOLDS = 3; // like the stop guard's MAX_REFUSALS: 3 holds with one reason, then end
const MAX_HOLDS = 50; // a safety limit for each user input
const DEFAULT_TIMEOUT_MS = 15000;
const UNICODE_SPACES = /[  -   　]/g;

// A Pi bash child must not look like Claude Code (activation would record host "claude").
delete process.env.CLAUDECODE;

/** The path that Pi writes: the same steps, in the same order, as Pi `resolveToCwd`. */
export function resolvePiPath(p: string, cwd: string): string {
  let out = String(p).replace(UNICODE_SPACES, " ");
  if (out.startsWith("@")) out = out.slice(1);
  if (out === "~") out = homedir();
  else if (out.startsWith("~/")) out = join(homedir(), out.slice(2));
  if (out.startsWith("file://")) out = fileURLToPath(out);
  return resolve(cwd, out);
}

/** The Claude Code PreToolUse payload for a judged Pi call, else null. */
export function toPayload(toolName: string, input: any, cwd: string, sessionId: string): any {
  const name = TOOLS[toolName];
  if (!name) return null;
  const base = { hook_event_name: "PreToolUse", tool_name: name, cwd, session_id: sessionId };
  if (name === "Bash") return { ...base, tool_input: { command: input?.command } };
  const filePath = resolvePiPath(input?.path ?? "", cwd);
  const rest = name === "Write" ? { content: input?.content } : { edits: input?.edits };
  return { ...base, tool_input: { file_path: filePath, ...rest } };
}

function hooksError(detail: string): Error {
  return new Error(`Cadence: hooks.json ${detail}`);
}

/** The hooks that hooks/hooks.json registers for this event (and tool), in file order. */
export function hooksFor(root: string, event: string, toolName?: string): any[] {
  let groups: any;
  try {
    groups = JSON.parse(readFileSync(join(root, "hooks", "hooks.json"), "utf-8"))?.hooks?.[event];
  } catch (err: any) {
    throw hooksError(`cannot be read: ${err?.message ?? err}`);
  }
  if (!Array.isArray(groups)) throw hooksError(`has no ${event} list`);
  const out: any[] = [];
  for (const group of groups) {
    const matcher = group?.matcher;
    if (event !== "Stop" && matcher !== undefined && matcher !== "" && matcher !== "*") {
      let re: RegExp;
      try { re = new RegExp(`^(?:${matcher})$`); } catch { throw hooksError(`matcher is not valid: ${matcher}`); }
      if (!re.test(String(toolName))) continue;
    }
    for (const hook of group?.hooks ?? []) {
      const command = String(hook?.command ?? "");
      const m = command.match(/\/hooks\/([\w.-]+\.py)/);
      if (!m) throw hooksError(`command has no hooks/<name>.py: ${command}`);
      const tail = command.slice(command.indexOf(m[0]) + m[0].length).replace(/^["']/, "");
      const timeout = hook?.timeout;
      if (timeout !== undefined && !(typeof timeout === "number" && timeout > 0)) {
        throw hooksError(`timeout is not a positive number: ${JSON.stringify(timeout)}`);
      }
      out.push({ script: m[1], args: tail.split(/\s+/).filter(Boolean),
                 timeoutMs: timeout === undefined ? DEFAULT_TIMEOUT_MS : timeout * 1000 });
    }
  }
  if (out.length === 0) throw hooksError(`gives no ${event} hook for ${toolName ?? "the turn"}`);
  return out;
}

/** The decision for one hook result (the `--host claude` protocol, hooks/hook_protocol.py). */
export function decide(hook: string, r: any): { kind: "allow" | "block"; reason?: string; notice?: string } {
  const block = (why: string) => ({ kind: "block" as const, reason: `Cadence: ${why}` });
  if (r.spawnError) return block(`${hook} could not start: ${r.spawnError}`);
  if (r.timedOut) return block(`${hook} timeout`);
  if (r.code !== 0) {
    const how = r.code === null || r.code === undefined ? `signal ${r.signal}` : `exit ${r.code}`;
    return block(`${hook} failed (${how}): ${String(r.stderr ?? "").slice(-500)}`);
  }
  const text = String(r.stdout ?? "").trim();
  if (!text) return { kind: "allow" };
  let data: any;
  try { data = JSON.parse(text); } catch { data = undefined; }
  if (!data || typeof data !== "object" || Array.isArray(data)) return block(`${hook} output is not JSON`);
  const hso = data.hookSpecificOutput ?? {};
  if (hso.permissionDecision === "deny") return block(hso.permissionDecisionReason || `${hook} blocked this call`);
  if (hso.permissionDecision === "ask" || "updatedInput" in hso) {
    return block(`${hook} asked for a change that Pi cannot apply`);
  }
  if (data.decision === "block") return block(data.reason || `${hook} blocked this call`);
  return data.systemMessage ? { kind: "allow", notice: String(data.systemMessage) } : { kind: "allow" };
}

/** The skill root: the real path of this file (through the install link), then the env. */
export function skillRoot(selfUrl: string, env: Record<string, string | undefined>): string | null {
  const candidates: string[] = [];
  try { candidates.push(dirname(dirname(realpathSync(fileURLToPath(selfUrl))))); } catch { /* not a file */ }
  for (const value of [env.CADENCE_HOME, env.CADENCE_SKILL_ROOT]) if (value) candidates.push(value);
  return candidates.find((c) => existsSync(join(c, "hooks", "guard_skill_gate.py"))) ?? null;
}

/** Run one hook in `cwd` with the payload on stdin. Never rejects. */
export function runHook(root: string, hook: any, payload: any, cwd: string, signal?: AbortSignal): Promise<any> {
  return new Promise((done) => {
    const env: Record<string, string | undefined> = { ...process.env, CLAUDE_PROJECT_DIR: cwd, CADENCE_HOME: root };
    delete env.CURSOR_PROJECT_DIR;
    delete env.CLAUDECODE;
    const python = process.env.CADENCE_PYTHON || "python3";
    const script = hook.path ?? join(root, "hooks", hook.script);
    const child = spawn(python, [script, ...hook.args], { cwd, env });
    let stdout = "", stderr = "", timedOut = false, settled = false;
    const onAbort = () => child.kill("SIGKILL");
    const finish = (r: any) => {
      if (settled) return;
      settled = true; clearTimeout(timer); signal?.removeEventListener("abort", onAbort);
      done({ stdout, stderr, ...r });
    };
    const timer = setTimeout(() => { timedOut = true; child.kill("SIGKILL"); }, hook.timeoutMs);
    signal?.addEventListener("abort", onAbort, { once: true });
    child.stdout.on("data", (d) => { stdout += d; });
    child.stderr.on("data", (d) => { stderr += d; });
    child.on("error", (err: any) => finish({ spawnError: err?.message ?? String(err) }));
    child.on("close", (code, sig) => finish({ code, signal: sig, timedOut }));
    child.stdin.on("error", () => { /* the hook can exit before it reads stdin */ });
    child.stdin.end(JSON.stringify(payload));
  });
}

/** True when `shared/skill_gate.py state` says a Cadence run is active in `cwd`; fails closed. */
async function runActive(root: string, cwd: string, signal?: AbortSignal): Promise<boolean> {
  const state = { path: join(root, "shared", "skill_gate.py"), args: ["state", "--skill", "cadence"],
                  timeoutMs: DEFAULT_TIMEOUT_MS };
  const r = await runHook(root, state, {}, cwd, signal);
  try { return r.code !== 0 || JSON.parse(r.stdout).active !== false; } catch { return true; }
}

function sessionId(ctx: any): string {
  try {
    const id = ctx?.sessionManager?.getSessionId?.();
    if (typeof id === "string" && id) return id;
  } catch { /* no session manager */ }
  return "pi";
}

/** Register the handlers. `options` exist for the tests: root, selfUrl, env. */
export function register(pi: any, options: { root?: string; selfUrl?: string; env?: any } = {}) {
  let holds = 0, same = 0, lastReason = "";
  const findRoot = () => options.root ?? skillRoot(options.selfUrl ?? import.meta.url, options.env ?? process.env);
  const notFound = () => `Cadence: skill root not found (tried the extension path, CADENCE_HOME, CADENCE_SKILL_ROOT)`;

  async function judge(root: string, event: string, toolName: string | undefined, payload: any, ctx: any) {
    for (const hook of hooksFor(root, event, toolName)) {
      const d = decide(hook.script, await runHook(root, hook, payload, payload.cwd, ctx?.signal));
      if (d.kind === "block") return d;
      if (d.notice) ctx?.hasUI ? ctx.ui.notify(d.notice) : process.stderr.write(`${d.notice}\n`);
    }
    return { kind: "allow" as const };
  }

  pi.on("tool_call", async (event: any, ctx: any) => {
    const name = TOOLS[event?.toolName];
    if (!name) return undefined;
    try {
      const sid = sessionId(ctx);
      // MF-5: an activation in this shell call records the Pi session as the run owner.
      if (SHELL_TOOLS.has(event.toolName) && sid !== "pi") process.env.CADENCE_OWNER_ID = sid;
      const root = findRoot();
      if (!root) return { block: true, reason: notFound() };
      // Pi has no SessionStart hook: the shell must run the scripts of this skill root.
      if (SHELL_TOOLS.has(event.toolName)) process.env.CADENCE_HOME = root;
      // The skill gate parses bash, not PowerShell: fail closed while a run is active.
      if (event.toolName === "powershell" && await runActive(root, ctx.cwd, ctx?.signal)) {
        return { block: true, reason: "Cadence: powershell is not judged during a Cadence run. Use the bash tool." };
      }
      const d = await judge(root, "PreToolUse", name, toPayload(event.toolName, event.input, ctx.cwd, sid), ctx);
      return d.kind === "block" ? { block: true, reason: d.reason } : undefined;
    } catch (err: any) {
      return { block: true, reason: String(err?.message ?? err).startsWith("Cadence:") ? err.message
                                    : `Cadence: gate error: ${err?.message ?? err}` };
    }
  });

  pi.on("agent_before_settle", async (event: any, ctx: any) => {
    // Pi gives canContinue false at each normal end of turn; the hold entry makes it valid.
    if (event?.outcome !== "completed" || holds >= MAX_HOLDS) return undefined;
    const hold = (reason: string) => {
      // A new reason is progress (the stop guard counts for each block). The same reason
      // a fourth time ends the turn, so a corrupt session cannot loop.
      same = reason === lastReason ? same + 1 : 1;
      lastReason = reason;
      if (same > MAX_SAME_HOLDS) return undefined;
      holds += 1;
      const entry = { type: "custom_message", customType: "cadence-stop-guard", content: reason, display: true };
      return { entries: [...(Array.isArray(event.entries) ? event.entries : []), entry], continue: true };
    };
    try {
      const root = findRoot();
      if (!root) return hold(notFound());
      const cwd = ctx.cwd;
      const payload = { hook_event_name: "Stop", stop_hook_active: same > 0, cwd, session_id: sessionId(ctx) };
      const d = await judge(root, "Stop", undefined, payload, ctx);
      return d.kind === "block" ? hold(d.reason!) : undefined;
    } catch (err: any) {
      const message = String(err?.message ?? err);
      return hold(message.startsWith("Cadence: hooks.json") ? message : `Cadence: stop guard error: ${message}`);
    }
  });

  pi.on("input", async (event: any) => {
    if (event?.source !== "extension") { holds = 0; same = 0; lastReason = ""; }
    return undefined;
  });
}

export default function cadenceGates(pi: any) {
  register(pi);
}
