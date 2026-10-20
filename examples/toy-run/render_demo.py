#!/usr/bin/env python3
"""Render assets/toy-run.svg: an animated terminal of the toy run, from transcript.txt.

Each scene shows lines of the committed transcript, in order. A scene names each line by its
start, so the demo can only show what the toy run prints, and a test fails when the transcript
changes and the SVG does not. Standard library only.

    python3 examples/toy-run/render_demo.py           # write assets/toy-run.svg
    python3 examples/toy-run/render_demo.py --check   # exit 1 when the SVG is out of date
"""

from __future__ import annotations

import argparse
import html
import sys
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
TRANSCRIPT = HERE / "transcript.txt"
OUT = ROOT / "assets" / "toy-run.svg"

COMMAND = "python3 examples/toy-run/run.py"
# (scene title, the start of each transcript line that the scene shows, in transcript order)
SCENES: list[tuple[str, list[str]]] = [
    ("A code edit before the plan passed review", [
        "== The edit guard, before the plan passed review",
        "  Edit src/toy_pkg/core.py                     DENY",
        "    BLOCKED: gated path `src/toy_pkg/core.py` requires entry invariant",
        "  Shell \"echo '# x' >> src/toy_pkg/core.py\"    DENY",
        "  Edit README.md                               allow",
        "  Read .env                                    DENY",
        "    BLOCKED: .env matches a secret path",
    ]),
    ("The gates decide when a block is done", [
        "== A1-E4  Gates in stages.json order",
        "OK    A1  activate-session",
        "FAIL  P1  plan-create",
        "        - plan-create: no artifact matching plans/*.plan.md",
        "OK    P1  plan-create",
        "OK    P3  plan-review",
        "OK    P4  plan-update",
        "OK    P9  entry-invariant",
        "FAIL  E1  implement",
        "  Edit src/toy_pkg/core.py                     allow",
        "  committed: feat: subtract()",
        "OK    E1  implement",
    ]),
    ("A finding closes on evidence, not on a claim", [
        "== R4-R5  The findings ledger",
        "  A reviewer files a must-fix finding:",
        "  The agent sets it to verified:",
        "  The agent sets it to fixed, with no commit:",
        "  ledger_verify.py runs the check of the row:",
        "  The agent adds the missing test and commits it.",
        "  The agent sets fixed_in to the fix commit:",
        "  ledger_verify.py runs the check again:",
        "  Closure:                                     exit 0",
    ]),
]

WIDTH, COLS = 880, 104
FONT, LINE = 13, 19
TOP, LEFT = 64, 20
TYPE_S, LINE_S, HOLD_S, FADE_S = 1.6, 0.42, 3.2, 0.4


@dataclass
class Line:
    text: str
    start: float  # seconds after the start of the cycle
    end: float


def pick(transcript: list[str]) -> list[list[str]]:
    """The transcript lines of each scene. Raises ValueError when a start matches no later line."""
    scenes, at = [], 0
    for title, starts in SCENES:
        lines = []
        for start in starts:
            found = next((i for i in range(at, len(transcript)) if transcript[i].startswith(start)), None)
            if found is None:
                raise ValueError(f"scene {title!r}: no transcript line after line {at} starts with {start!r}")
            lines.append(transcript[found])
            at = found + 1
        scenes.append(lines)
    return scenes


def clip(text: str) -> str:
    return text if len(text) <= COLS else text[: COLS - 1] + "…"


def css_class(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("=="):
        return "h"
    if stripped.startswith("OK "):
        return "ok"
    if stripped.startswith("FAIL "):
        return "bad"
    if stripped.startswith(("BLOCKED", "- ", "error")):
        return "dim"
    if stripped.endswith("DENY") or " exit 1 " in text:
        return "bad"
    if stripped.endswith("allow") or " exit 0 " in text or stripped.startswith("committed"):
        return "ok"
    return "fg"


def spans(text: str) -> str:
    """The line as tspans: the verdict word in its color, the rest in the line's color."""
    cls = css_class(text)
    for word, word_cls in (("DENY", "bad"), ("allow", "ok"), ("exit 1", "bad"), ("exit 0", "ok")):
        head, sep, tail = text.partition(f" {word}")
        if sep and cls not in ("dim", "h"):
            return (f'<tspan class="fg">{html.escape(head)} </tspan><tspan class="{word_cls}">{word}</tspan>'
                    f'<tspan class="dim">{html.escape(tail)}</tspan>')
    return f'<tspan class="{cls}">{html.escape(text)}</tspan>'


def timeline(scenes: list[list[str]]) -> tuple[list[tuple[str, list[Line]]], float]:
    out, t = [], TYPE_S + 0.4
    for (title, _starts), lines in zip(SCENES, scenes, strict=True):
        shown = [Line(clip(text), t + i * LINE_S, 0.0) for i, text in enumerate(lines)]
        end = t + len(lines) * LINE_S + HOLD_S
        for line in shown:
            line.end = end
        out.append((title, shown))
        t = end + FADE_S
    return out, t


def keyframes(name: str, start: float, end: float, total: float) -> str:
    a, b = 100 * start / total, 100 * end / total
    fade = 100 * FADE_S / total
    return (f"@keyframes {name}{{0%,{a:.2f}%{{opacity:0}}{min(a + 0.6, b):.2f}%,{b:.2f}%{{opacity:1}}"
            f"{min(b + fade, 100):.2f}%,100%{{opacity:0}}}}")


def render(transcript_text: str) -> str:
    scenes, total = timeline(pick(transcript_text.splitlines()))
    rows = max(len(lines) for _t, lines in scenes)
    height = TOP + (rows + 2) * LINE + 24
    css, body = [], []
    for n, (title, lines) in enumerate(scenes):
        # With reduced motion, the last scene stays on screen and nothing moves.
        cls = "a last" if n == len(scenes) - 1 else "a"
        css.append(keyframes(f"t{n}", lines[0].start - 0.3, lines[0].end, total))
        body.append(f'<text class="title {cls}" style="animation-name:t{n}" x="{WIDTH - LEFT}" y="{TOP - 8}" '
                    f'text-anchor="end">{html.escape(title)}</text>')
        for i, line in enumerate(lines):
            name = f"s{n}l{i}"
            css.append(keyframes(name, line.start, line.end, total))
            body.append(f'<text class="{cls}" style="animation-name:{name}" x="{LEFT}" y="{TOP + (i + 1) * LINE}" '
                        f'xml:space="preserve">{spans(line.text)}</text>')
    type_end = 100 * TYPE_S / total
    return "\n".join([
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {height}" width="{WIDTH}" height="{height}" '
        'role="img" aria-labelledby="t d">',
        "  <title id=\"t\">Cadence toy run</title>",
        "  <desc id=\"d\">A terminal runs the Cadence toy run. The edit guard denies a code edit and a secret read "
        "before the plan passed review. The gates walk the blocks and fail each one until its evidence exists. "
        "The findings ledger refuses a verified status that the agent sets, and closes the finding only after "
        "the check passes on the fix commit.</desc>",
        "  <style>",
        f"    text{{font:{FONT}px ui-monospace,SFMono-Regular,Menlo,Consolas,'Liberation Mono',monospace;white-space:pre}}",
        "    .win{fill:#0d1117;stroke:#30363d}.bar{fill:#161b22}.title{fill:#8b949e;font-size:12px}",
        "    .fg{fill:#e6edf3}.dim{fill:#8b949e}.h{fill:#79c0ff;font-weight:700}.ok{fill:#3fb950}.bad{fill:#ff7b72}"
        ".p{fill:#d2a8ff}",
        f"    .a{{opacity:0;animation-duration:{total:.2f}s;animation-iteration-count:infinite;"
        "animation-timing-function:linear}",
        f"    .type{{animation:type {total:.2f}s steps({len(COMMAND)},end) infinite}}",
        f"    @keyframes type{{0%{{width:0}}{type_end:.2f}%,100%{{width:{len(COMMAND) * 7.9:.0f}px}}}}",
        *(f"    {rule}" for rule in css),
        "    @media (prefers-reduced-motion:reduce){.a,.type{animation:none}.type{width:100%}.last{opacity:1}}",
        "  </style>",
        f'  <rect class="win" x="0.5" y="0.5" width="{WIDTH - 1}" height="{height - 1}" rx="10"/>',
        f'  <path class="bar" d="M10.5 .5H{WIDTH - 10.5}A10 10 0 0 1 {WIDTH - .5} 10.5V30.5H.5V10.5A10 10 0 0 1 10.5 .5Z"/>',
        '  <circle cx="20" cy="16" r="6" fill="#ff5f56"/><circle cx="40" cy="16" r="6" fill="#ffbd2e"/>'
        '<circle cx="60" cy="16" r="6" fill="#27c93f"/>',
        f'  <text class="title" x="{WIDTH / 2}" y="20" text-anchor="middle">cadence-core — toy run, no model, no network</text>',
        f'  <clipPath id="c"><rect class="type" x="{LEFT + 16}" y="{TOP - 24}" width="{WIDTH}" height="24"/></clipPath>',
        f'  <text class="p" x="{LEFT}" y="{TOP - 8}">$</text>',
        f'  <g clip-path="url(#c)"><text class="fg" x="{LEFT + 16}" y="{TOP - 8}">{COMMAND}</text></g>',
        *(f"  {item}" for item in body),
        "</svg>",
        "",
    ])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--check", action="store_true", help="exit 1 when assets/toy-run.svg is out of date")
    args = parser.parse_args(argv)
    svg = render(TRANSCRIPT.read_text(encoding="utf-8"))
    if args.check:
        if not OUT.is_file() or OUT.read_text(encoding="utf-8") != svg:
            print(f"{OUT.relative_to(ROOT)} is out of date: run python3 {Path(__file__).relative_to(ROOT)}",
                  file=sys.stderr)
            return 1
        return 0
    OUT.write_text(svg, encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
