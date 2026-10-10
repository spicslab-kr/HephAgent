"""Prompt assets: editable Markdown under ``prompts/<team>/<operation>.md``.

Students rewrite prompts without touching Python. Each file may start with a
``version:`` line; the loader records the version and the SHA-256 of the body so a
task's resolved prompt can be traced. Missing team-specific files fall back to
``prompts/common/<operation>.md`` and finally to ``prompts/common/generic.md``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from sdlc.verification.canonical import sha256_text


@dataclass(frozen=True, slots=True)
class Prompt:
    team: str
    operation: str
    version: str
    body: str
    sha256: str
    path: Path


def prompts_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "prompts"
        if (candidate / "common" / "preamble.md").exists():
            return candidate
    raise FileNotFoundError("prompts/ directory not found next to the package")


@lru_cache(maxsize=None)
def load(team: str, operation: str) -> Prompt:
    root = prompts_root()
    for path in (root / team / f"{operation}.md", root / "common" / f"{operation}.md", root / "common" / "generic.md"):
        if path.exists():
            text = path.read_text(encoding="utf-8")
            version = "unversioned"
            match = re.match(r"^version:\s*(\S+)\s*\n", text)
            if match:
                version = match.group(1)
                text = text[match.end() :]
            body = text.strip()
            return Prompt(team=team, operation=operation, version=version, body=body, sha256=sha256_text(body), path=path)
    raise FileNotFoundError(f"no prompt for {team}.{operation} and no prompts/common/generic.md")


def load_prompt(team: str, operation: str) -> str:
    return load(team, operation).body
