"""Every relative link and image in the public docs points at a file that exists."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DOCS = sorted({*ROOT.glob("*.md"), *ROOT.glob("docs/**/*.md"), *ROOT.glob("references/*.md"),
               *ROOT.glob("examples/*/README.md"), *ROOT.glob("packs/README.md")})
# A Markdown link or image target, or an HTML src or href. Code spans and fences are removed first.
LINK = re.compile(r"\]\(([^)\s]+)\)|(?:src|href)=\"([^\"]+)\"")
FENCE = re.compile(r"```.*?```", re.S)
CODE = re.compile(r"`[^`\n]*`")


def targets(text: str) -> list[str]:
    text = CODE.sub("", FENCE.sub("", text))
    return [a or b for a, b in LINK.findall(text)]


@pytest.mark.parametrize("doc", DOCS, ids=[str(d.relative_to(ROOT)) for d in DOCS])
def test_each_relative_link_resolves(doc):
    broken = []
    for target in targets(doc.read_text(encoding="utf-8")):
        path = target.split("#", 1)[0]
        if not path or re.match(r"^[a-z][a-z0-9+.-]*:", path):  # an anchor, or a URL with a scheme
            continue
        if not (doc.parent / path).exists():
            broken.append(target)
    assert broken == [], f"{doc.relative_to(ROOT)} links to missing files: {broken}"


def test_the_readme_shows_the_pipeline_and_names_the_evidence_gates():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "assets/pipeline.svg" in text and "entry-invariant" in text and "ledger_verify.py" in text
