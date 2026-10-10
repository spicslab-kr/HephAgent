"""Deterministic checks on worker output before anything is registered.

* JSON artifact candidates whose ``kind`` has a registered schema (``requirements``,
  ``design``, ``test_plan`` ...) must validate against it (handoff docs/07 s8).
* Markdown companions must carry the required section headings of their kind.
* Diagram sources must start with a supported Mermaid/PlantUML header.

A model cannot claim an executed test: ``execution_record`` candidates are refused here
because only the trusted collector creates them.
"""

from __future__ import annotations

import re
from typing import Any

from sdlc.domain.errors import InvalidSchema
from sdlc.domain.models import ARTIFACT_KIND_SCHEMAS, ArtifactCandidate, Finding, Severity
from sdlc.verification.canonical import load_json_strict, strip_code_fence
from sdlc.verification.schemas import validate_record

COLLECTOR_ONLY_KINDS = frozenset({"execution_record"})

#: Required section headings (case-insensitive substrings) for readable deliverables.
MARKDOWN_SECTIONS: dict[str, tuple[str, ...]] = {
    "project_plan": ("problem", "scope", "feasibility", "risk"),
    "requirements_document": ("scope", "actor", "requirement", "acceptance"),
    "design_document": ("architecture", "interface", "behavior"),
    "test_report": ("candidate", "result", "recommendation"),
}

MERMAID_HEADERS = ("flowchart", "graph", "sequenceDiagram", "stateDiagram", "stateDiagram-v2", "classDiagram", "erDiagram", "gantt", "C4Context", "journey")
PLANTUML_HEADERS = ("@startuml",)


def markdown_headings(text: str) -> list[str]:
    """Section titles of a Markdown document, lower-cased: ``# Title`` lines, lines that are only
    ``**Title**`` (models often write bold titles) and setext titles underlined with ``===``/``---``."""
    lines = text.splitlines()
    found: list[str] = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        if re.match(r"^#{1,6}\s+\S", stripped):
            found.append(stripped.lstrip("#").strip().lower())
        elif re.match(r"^\*\*[^*]+\*\*:?$", stripped):
            found.append(stripped.strip("*:").strip().lower())
        elif stripped and i + 1 < len(lines) and re.match(r"^(=+|-+)\s*$", lines[i + 1].strip()) and len(lines[i + 1].strip()) >= 3:
            found.append(stripped.lower())
    return found


def blocking(code: str, message: str) -> Finding:
    return Finding(finding_id=f"finding-{code}", code=code, severity=Severity.BLOCKING, message=message, related_refs=[])


def check_candidates(candidates: list[ArtifactCandidate]) -> list[Finding]:
    findings: list[Finding] = []
    seen: set[str] = set()
    for candidate in candidates:
        if candidate.relative_path in seen:
            findings.append(blocking("duplicate_candidate", f"two candidates share the path {candidate.relative_path}"))
        seen.add(candidate.relative_path)
        if candidate.kind in COLLECTOR_ONLY_KINDS:
            findings.append(blocking("fabricated_evidence", f"{candidate.kind} may only be produced by the execution collector, never by a model"))
            continue
        if candidate.content_text is None:
            continue  # workspace candidates are checked when the workspace is collected (M5)
        schema = ARTIFACT_KIND_SCHEMAS.get(candidate.kind)
        if schema is not None:
            if "json" not in candidate.media_type:
                findings.append(blocking("wrong_media_type", f"{candidate.kind} must be application/json, got {candidate.media_type}"))
                continue
            try:
                validate_record(load_json_strict(strip_code_fence(candidate.content_text)), schema)
            except (InvalidSchema, ValueError) as exc:
                detail = getattr(exc, "details", {}).get("errors", [str(exc)])
                findings.append(blocking("invalid_record", f"{candidate.kind} does not validate as {schema}: {str(detail)[:300]}"))
        sections = MARKDOWN_SECTIONS.get(candidate.kind)
        if sections is not None:
            present = " | ".join(markdown_headings(candidate.content_text))
            missing = [s for s in sections if s not in present]
            if missing:
                findings.append(blocking("missing_sections", f"{candidate.kind} lacks headings for {missing}"))
        if candidate.kind == "diagram_source":
            findings.extend(check_diagram_source(candidate.content_text, candidate.media_type))
    return findings


def check_diagram_source(source: str, media_type: str) -> list[Finding]:
    # Leading blank lines and Mermaid %% comments are allowed before the header.
    lines = [line for line in source.splitlines() if line.strip() and not line.lstrip().startswith("%%")]
    stripped = "\n".join(lines).lstrip()
    if media_type in ("text/vnd.mermaid", "text/x-mermaid"):
        if not stripped.startswith(MERMAID_HEADERS):
            return [blocking("unsupported_diagram_syntax", f"Mermaid source must start with one of {MERMAID_HEADERS}")]
        if stripped.count("[") != stripped.count("]") or stripped.count("(") != stripped.count(")"):
            return [blocking("unbalanced_brackets", "Mermaid source has unbalanced brackets")]
        return []
    if media_type in ("text/x-plantuml", "text/vnd.plantuml"):
        if not stripped.startswith(PLANTUML_HEADERS) or "@enduml" not in stripped:
            return [blocking("unsupported_diagram_syntax", "PlantUML source must be wrapped in @startuml ... @enduml")]
        return []
    return [blocking("unsupported_diagram_syntax", f"diagram media type {media_type} is not supported; use text/vnd.mermaid or text/x-plantuml")]


def require_kinds(candidates: list[ArtifactCandidate], required: tuple[str, ...]) -> list[Finding]:
    present = {c.kind for c in candidates}
    return [blocking("missing_output", f"operation must produce an artifact of kind {kind!r}") for kind in required if kind not in present]


def parse_json_candidate(candidates: list[ArtifactCandidate], kind: str) -> Any | None:
    for candidate in candidates:
        if candidate.kind == kind and candidate.content_text is not None:
            return load_json_strict(candidate.content_text)
    return None
