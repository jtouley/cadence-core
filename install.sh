#!/usr/bin/env bash
# Cadence installer — writes only under $HOME. Idempotent.
# Symlink skill + deep-merge hooks/hooks-cursor.json into ~/.cursor/hooks.json,
# and the hooks in hooks/hooks.json (the Claude Code plugin hooks) into
# ~/.claude/settings.json when Claude Code is present.
# Claude Code users can install the plugin instead: /plugin marketplace add jtouley/cadence-core
set -euo pipefail

CADENCE_HOME="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export CADENCE_HOME
FRAGMENT="$CADENCE_HOME/hooks/hooks-cursor.json"
HOOKS_PATH="${CADENCE_HOOKS_PATH:-$HOME/.cursor/hooks.json}"
PLUGIN_HOOKS="$CADENCE_HOME/hooks/hooks.json"
CLAUDE_SETTINGS_PATH="${CADENCE_CLAUDE_SETTINGS_PATH:-$HOME/.claude/settings.json}"

link_skill() {
  # Link into <host>/skills whenever the host directory exists. Creating the
  # skills dir here matters: hooks are registered against <host>/skills/cadence,
  # so skipping the link would leave every hook pointing at a missing script.
  local dest_dir="$1"
  [ -d "$(dirname "$dest_dir")" ] || return 0
  mkdir -p "$dest_dir"
  local target="$dest_dir/cadence"
  if [ -L "$target" ]; then
    [ "$(readlink "$target")" = "$CADENCE_HOME" ] && { echo "ok (already linked): $target"; return 0; }
    rm "$target"
  elif [ -e "$target" ]; then
    echo "refusing to overwrite non-symlink: $target" >&2
    return 1
  fi
  ln -s "$CADENCE_HOME" "$target"
  echo "linked: $target -> $CADENCE_HOME"
}

merge_hooks() {
  if [ ! -f "$FRAGMENT" ]; then
    echo "missing fragment: $FRAGMENT" >&2
    return 1
  fi
  check_hook_targets "$FRAGMENT" || return 1
  mkdir -p "$(dirname "$HOOKS_PATH")"
  if [ -f "$HOOKS_PATH" ]; then
    local bak="${HOOKS_PATH}.bak.$(date +%Y%m%d%H%M%S)"
    cp "$HOOKS_PATH" "$bak"
    echo "backed up hooks: $bak"
  fi
  python3 - "$HOOKS_PATH" "$FRAGMENT" <<'PY'
import json, re, sys
from pathlib import Path

# Every Cadence hook entry, whatever its flags. They are dropped before the
# fragment is added, so an entry with older flags cannot survive as a duplicate.
OURS = re.compile(r"(?<![\w.-])cadence\}?/hooks/[\w.-]+\.py")

hooks_path = Path(sys.argv[1])
fragment_path = Path(sys.argv[2])
fragment = json.loads(fragment_path.read_text(encoding="utf-8"))
if hooks_path.is_file():
    existing = json.loads(hooks_path.read_text(encoding="utf-8"))
else:
    existing = {"version": 1, "hooks": {}}

if not isinstance(existing, dict):
    existing = {"version": 1, "hooks": {}}
existing.setdefault("version", fragment.get("version", 1))
hooks = existing.setdefault("hooks", {})
if not isinstance(hooks, dict):
    hooks = {}
    existing["hooks"] = hooks

for event in list(hooks):
    if isinstance(hooks[event], list):
        hooks[event] = [
            item for item in hooks[event]
            if not (isinstance(item, dict) and OURS.search(str(item.get("command", ""))))
        ]

frag_hooks = fragment.get("hooks") or {}
for event, entries in frag_hooks.items():
    if not isinstance(entries, list):
        continue
    current = hooks.get(event)
    if not isinstance(current, list):
        current = []
    by_cmd = {}
    for item in current:
        if isinstance(item, dict) and "command" in item:
            by_cmd[item["command"]] = item
    for item in entries:
        if isinstance(item, dict) and "command" in item:
            by_cmd[item["command"]] = item
    hooks[event] = list(by_cmd.values())

tmp = hooks_path.with_suffix(hooks_path.suffix + ".tmp")
tmp.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
tmp.replace(hooks_path)
print(f"merged hooks: {hooks_path}")
PY
}

vendor_shared_scripts() {
  local dest="$HOME/.cursor/scripts"
  local src="$CADENCE_HOME/shared/scripts"
  [ -d "$src" ] || return 0
  mkdir -p "$dest"
  local f
  for f in "$src"/*.py; do
    [ -f "$f" ] || continue
    cp "$f" "$dest/$(basename "$f")"
    echo "vendored: $dest/$(basename "$f")"
  done
}

claude_fragment() {
  # The plugin hooks with ${CLAUDE_PLUGIN_ROOT} replaced by the linked skill path.
  python3 - "$PLUGIN_HOOKS" "$1" <<'PY'
import sys
from pathlib import Path

text = Path(sys.argv[1]).read_text(encoding="utf-8")
Path(sys.argv[2]).write_text(
    text.replace("${CLAUDE_PLUGIN_ROOT}", "${CADENCE_SKILL_ROOT:-$HOME/.claude/skills/cadence}"),
    encoding="utf-8",
)
PY
}

merge_claude_hooks() {
  [ -d "$(dirname "$CLAUDE_SETTINGS_PATH")" ] || return 0
  if [ ! -f "$PLUGIN_HOOKS" ]; then
    echo "missing hooks: $PLUGIN_HOOKS" >&2
    return 1
  fi
  CLAUDE_FRAGMENT="$(mktemp)"
  # EXIT, not RETURN: set -e can leave the function without a return.
  trap 'rm -f "$CLAUDE_FRAGMENT"' EXIT
  claude_fragment "$CLAUDE_FRAGMENT"
  check_hook_targets "$CLAUDE_FRAGMENT" || return 1
  if [ -f "$CLAUDE_SETTINGS_PATH" ]; then
    local bak="${CLAUDE_SETTINGS_PATH}.bak.$(date +%Y%m%d%H%M%S)"
    cp "$CLAUDE_SETTINGS_PATH" "$bak"
    echo "backed up settings: $bak"
  fi
  python3 - "$CLAUDE_SETTINGS_PATH" "$CLAUDE_FRAGMENT" <<'PY'
import json, re, sys
from pathlib import Path

settings_path = Path(sys.argv[1])
fragment = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
if settings_path.is_file():
    try:
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        sys.exit(f"refusing to edit unparseable {settings_path}: {exc}")
else:
    settings = {}
if not isinstance(settings, dict):
    sys.exit(f"refusing to edit {settings_path}: top level is not an object")
hooks = settings.get("hooks")
if not isinstance(hooks, dict):
    hooks = {}
    settings["hooks"] = hooks


OURS = re.compile(r"(?<![\w.-])cadence\}?/hooks/[\w.-]+\.py")


def ours(entry):
    cmd = entry.get("command", "") if isinstance(entry, dict) else ""
    return bool(OURS.search(cmd))


for event, groups in (fragment.get("hooks") or {}).items():
    kept = []
    for group in hooks.get(event) or []:
        if not isinstance(group, dict):
            kept.append(group)
            continue
        entries = [e for e in group.get("hooks") or [] if not ours(e)]
        if entries:
            kept.append({**group, "hooks": entries})
        elif not group.get("hooks"):
            kept.append(group)
    hooks[event] = kept + groups

settings_path.parent.mkdir(parents=True, exist_ok=True)
tmp = settings_path.with_suffix(settings_path.suffix + ".tmp")
tmp.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
tmp.replace(settings_path)
print(f"merged hooks: {settings_path}")
PY
}

check_hook_targets() {
  # Fail if any Cadence hook in the given files (default: the installed hook files)
  # points at a missing script. Run on each fragment before merging it, so a
  # failure leaves the user's hooks untouched, and again after install.
  if [ $# -eq 0 ]; then set -- "$HOOKS_PATH" "$CLAUDE_SETTINGS_PATH"; fi
  python3 - "$@" <<'PY'
import json, os, re, sys
from pathlib import Path

pattern = re.compile(r"\$\{CADENCE_SKILL_ROOT:-([^}]*)\}(/hooks/[\w.-]+\.py)")
missing = []
for raw in sys.argv[1:]:
    path = Path(raw)
    if not path.is_file():
        continue
    text = path.read_text(encoding="utf-8")
    for default, script in pattern.findall(text):
        root = os.environ.get("CADENCE_SKILL_ROOT") or default.replace("$HOME", os.environ["HOME"])
        if not Path(root + script).is_file():
            missing.append(f"{path}: {root}{script}")
if missing:
    print("hook targets missing (hooks would block every action):", *sorted(set(missing)), sep="\n  ", file=sys.stderr)
    sys.exit(1)
PY
}

link_file() {
  # Link $1 -> $2. Our own link: no change. Any other file or link: back it up, then link.
  local path="$1" target="$2"
  mkdir -p "$(dirname "$path")"
  if [ -L "$path" ] && [ "$(readlink "$path")" = "$target" ]; then
    echo "ok (already linked): $path"
    return 0
  fi
  if [ -e "$path" ] || [ -L "$path" ]; then
    local bak="$path.bak.$(date +%Y%m%d%H%M%S)"
    mv "$path" "$bak"
    echo "backed up: $path -> $bak"
  fi
  ln -s "$target" "$path"
  echo "linked: $path -> $target"
}

link_pi() {
  # Pi (cd-84): the extension pi/cadence-gates.ts runs the gates. Only when ~/.pi/agent exists.
  local agent="$HOME/.pi/agent"
  [ -d "$agent" ] || return 0
  link_file "$agent/extensions/cadence-gates.ts" "$CADENCE_HOME/pi/cadence-gates.ts"
  local listed
  listed="$(python3 - "$agent/settings.json" <<'PY'
import json, os, re, sys
try:
    skills = json.load(open(sys.argv[1], encoding="utf-8")).get("skills")
except (OSError, ValueError, AttributeError):
    skills = None
def is_cadence(d):
    try:
        text = open(os.path.join(d, "SKILL.md"), encoding="utf-8").read(4096)
    except OSError:
        return False
    return re.search(r"^name:\s*cadence\s*$", text, re.M) is not None

for entry in skills if isinstance(skills, list) else []:
    if isinstance(entry, str):
        base = os.path.realpath(os.path.expanduser(entry))
        base = os.path.dirname(base) if base.endswith("SKILL.md") else base
        found = [d for d in (base, os.path.join(base, "cadence")) if is_cadence(d)]
        if found:
            print(found[0])
            break
PY
)"
  if [ -z "$listed" ]; then
    link_file "$agent/skills/cadence" "$CADENCE_HOME"
    return 0
  fi
  echo "ok (Pi settings already list a cadence skill): $listed"
  if [ "$(cd "$listed" && pwd -P)" != "$(cd "$CADENCE_HOME" && pwd -P)" ]; then
    echo "warning: Pi settings list a cadence skill from another checkout: $listed"
  fi
}

check_installed_links() {
  # Fail if an installed skill or command link that points into this checkout does
  # not resolve, or if the linked cadence skill does not declare `name: cadence`.
  # Links that point elsewhere belong to the user and are not checked.
  python3 - "$CADENCE_HOME" "$HOME/.cursor" "$HOME/.claude" "$HOME/.pi/agent" <<'PY'
import os, re, sys
from pathlib import Path

checkout = Path(sys.argv[1])
problems = []

def lexical(link):
    raw = link.readlink()
    joined = raw if raw.is_absolute() else (link.parent / raw)
    return Path(os.path.normpath(joined))

def inside(path, root):
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True

def dangling_into_checkout(link):
    if not link.is_symlink() or link.exists():
        return False
    logical = lexical(link)
    roots = [Path(os.path.normpath(checkout))]
    real_root = Path(os.path.realpath(checkout))
    if real_root not in roots:
        roots.append(real_root)
    candidates = [logical]
    real_candidate = Path(os.path.realpath(logical))
    if real_candidate not in candidates:
        candidates.append(real_candidate)
    return any(inside(candidate, root) for candidate in candidates for root in roots)

for host in map(Path, sys.argv[2:]):
    for sub in ("skills", "commands"):
        directory = host / sub
        if not directory.is_dir():
            continue
        for link in sorted(directory.iterdir()):
            if dangling_into_checkout(link):
                problems.append(f"dangling link: {link} -> {link.readlink()}")
    skill = host / "skills" / "cadence" / "SKILL.md"
    if skill.is_file() and not re.search(r"^name:\s*cadence\s*$", skill.read_text(encoding="utf-8"), re.M):
        problems.append(f"skill does not declare 'name: cadence': {skill}")
if problems:
    print("installed links broken (remove each link and run install.sh again):", *problems, sep="\n  ", file=sys.stderr)
    sys.exit(1)
PY
}

remove_claude() {
  # Undo the Claude Code half of this installer, to move to the plugin.
  # Cursor links and ~/.cursor/hooks.json stay as they are.
  python3 - "$CADENCE_HOME" "$HOME/.claude" "$CLAUDE_SETTINGS_PATH" <<'PY'
import json, os, re, shutil, sys
from datetime import datetime
from pathlib import Path

checkout = os.path.realpath(sys.argv[1])
claude, settings_path = Path(sys.argv[2]), Path(sys.argv[3])
for sub in ("skills", "commands"):
    directory = claude / sub
    if not directory.is_dir():
        continue
    for link in sorted(directory.iterdir()):
        target = os.path.realpath(link)
        if link.is_symlink() and (target == checkout or target.startswith(checkout + os.sep)):
            link.unlink()
            print(f"removed link: {link}")
if settings_path.is_file():
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    ours = re.compile(r"(?<![\w.-])cadence\}?/hooks/[\w.-]+\.py")
    hooks = settings.get("hooks") if isinstance(settings, dict) else None
    removed = 0
    if isinstance(hooks, dict):
        for event in list(hooks):
            if not isinstance(hooks[event], list):
                continue  # not a hook list: not ours to change
            kept = []
            for group in hooks[event]:
                if not isinstance(group, dict) or not isinstance(group.get("hooks") or [], list):
                    kept.append(group)  # not a hook group we know: keep it as is (#40)
                    continue
                entries = [e for e in group.get("hooks") or []
                           if not (isinstance(e, dict) and ours.search(str(e.get("command", ""))))]
                removed += len(group.get("hooks") or []) - len(entries)
                if entries or not group.get("hooks"):
                    kept.append({**group, "hooks": entries} if entries else group)
            if kept:
                hooks[event] = kept
            else:
                del hooks[event]
    if removed:
        shutil.copy(settings_path, f"{settings_path}.bak.{datetime.now():%Y%m%d%H%M%S}")
        settings_path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
        print(f"removed {removed} Cadence hooks: {settings_path}")
PY
  echo "Now install the plugin: /plugin marketplace add jtouley/cadence-core, then /plugin install cadence@cadence-core"
}

install_tools() {
  # The pinned tools of the R3 measures `bugs` and `types` (TOOLS in scripts/tool_measures.py).
  # A gate run never fetches a tool. Without these two, both measures report `unsupported`.
  # A cache warm: `uv tool run --offline` then finds them, and nothing goes on PATH, so the
  # user's own ruff and mypy stay (PR #168 R1 N-8).
  uv tool run ruff==0.17.0 --version
  uv tool run mypy==2.4.0 --version
}

if [ "${1:-}" = "--tools" ]; then
  install_tools
  exit 0
fi

if [ "${1:-}" = "--remove-claude" ]; then
  remove_claude
  exit 0
fi

mkdir -p "$(dirname "$HOOKS_PATH")"
link_skill "$HOME/.cursor/skills"
link_skill "$HOME/.claude/skills"
link_pi
python3 "$CADENCE_HOME/scripts/publish_peers.py" \
  --home "$CADENCE_HOME" \
  --skills-dir "$HOME/.cursor/skills" \
  --commands-dir "${CADENCE_COMMANDS_DIR:-$HOME/.cursor/commands}" \
  --skip-existing
if [ -d "$HOME/.claude/skills" ]; then
  python3 "$CADENCE_HOME/scripts/publish_peers.py" \
    --home "$CADENCE_HOME" \
    --skills-dir "$HOME/.claude/skills" \
    --commands-dir "$HOME/.claude/commands" \
    --skip-existing
fi
# Report each user entry with a peer's name (a peer shadow). The entries stay. cd-53.
python3 "$CADENCE_HOME/scripts/peer_shadows.py" --root "$CADENCE_HOME"
merge_hooks
merge_claude_hooks
vendor_shared_scripts
check_hook_targets
check_installed_links

echo
echo "Cadence installed from: $CADENCE_HOME"
echo "Restart Claude Code and Cursor sessions that were open during install."
echo "A running session does not load new skills or commands. Start a new session to see /cadence."
echo "Uninstall: remove symlinks; delete hook entries whose command contains skills/cadence"
echo "           (in ~/.cursor/hooks.json and ~/.claude/settings.json);"
echo "           for Pi, remove ~/.pi/agent/extensions/cadence-gates.ts and ~/.pi/agent/skills/cadence"
echo "R3 measures bugs and types need two pinned tools in the uv cache. Fill it once with ./install.sh --tools"
echo "           (uv tool run ruff==0.17.0 --version and uv tool run mypy==2.4.0 --version)."
if [ -L "$HOME/.pi/agent/extensions/cadence-gates.ts" ]; then
  echo "Pi: the extension at ~/.pi/agent/extensions/cadence-gates.ts runs the gates. Start a new Pi session."
else
  echo "Pi / headless: use shared/skill_gate.py CLI — see README."
fi
