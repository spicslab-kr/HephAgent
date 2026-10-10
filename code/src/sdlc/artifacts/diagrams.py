"""Diagram service: recommendations, source validation and render status (handoff docs/03 s7).

Shared code, team-owned outputs. A request names its team, permitted source refs,
diagram type, purpose and scope ids. The runtime turns it into a ``TEAM.request_diagram``
task; the resulting ``diagram_source`` artifact is validated for syntax and labeled
``unrendered`` unless a registered renderer ran in the constrained ProcessRunner (M6).
Participants can edit a diagram source; the edit becomes a new revision with
participant provenance and must be reconciled before regeneration.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from dataclasses import dataclass

from sdlc.domain.models import DiagramType, TeamId
from sdlc.verification.outputs import check_diagram_source

#: Which notation renders each diagram type and which renderer is preferred.
NOTATION: dict[DiagramType, tuple[str, str]] = {
    DiagramType.CONTEXT: ("text/vnd.mermaid", "mermaid"),
    DiagramType.USE_CASE: ("text/x-plantuml", "plantuml"),
    DiagramType.ACTIVITY: ("text/vnd.mermaid", "mermaid"),
    DiagramType.SEQUENCE: ("text/vnd.mermaid", "mermaid"),
    DiagramType.STATE: ("text/vnd.mermaid", "mermaid"),
    DiagramType.CLASS: ("text/vnd.mermaid", "mermaid"),
    DiagramType.COMPONENT: ("text/vnd.mermaid", "mermaid"),
    DiagramType.DEPLOYMENT: ("text/x-plantuml", "plantuml"),
    DiagramType.ERD: ("text/vnd.mermaid", "mermaid"),
    DiagramType.DFD: ("text/vnd.mermaid", "mermaid"),
    DiagramType.SCHEDULE: ("text/vnd.mermaid", "mermaid"),
    DiagramType.CUSTOM: ("text/vnd.mermaid", "mermaid"),
}


@dataclass(frozen=True, slots=True)
class Recommendation:
    diagram_type: DiagramType
    question: str
    reason: str


def recommend(team: TeamId, text: str) -> list[Recommendation]:
    """Rule-based recommendations from a specification; an agent may add its own.

    Deliberately selective (3 to 5 views), following the course guidance of recommending
    useful models rather than filling every textbook section.
    """
    lower = text.lower()
    out: list[Recommendation] = []

    def add(kind: DiagramType, question: str, reason: str) -> None:
        if all(r.diagram_type != kind for r in out):
            out.append(Recommendation(kind, question, reason))

    if team == TeamId.PLANNING:
        add(DiagramType.CONTEXT, "What is inside and outside the system?", "scope statements are present" if "scope" in lower else "scope is unclear")
        if re.search(r"\b(milestone|schedule|deadline|week|sprint)\b", lower):
            add(DiagramType.SCHEDULE, "When do increments land?", "dates or milestones are mentioned")
    if team == TeamId.REQUIREMENTS:
        if re.search(r"\b(user|member|librarian|actor|admin|customer|student)\b", lower):
            add(DiagramType.USE_CASE, "Which actor goals does the system serve?", "external actors are named")
        if re.search(r"\b(workflow|process|step|then|after)\b", lower):
            add(DiagramType.ACTIVITY, "What is the business workflow?", "sequential process language is present")
        if re.search(r"\b(status|state|expire|expiry|cancel|active|pending)\b", lower):
            add(DiagramType.STATE, "What lifecycle rules apply?", "lifecycle vocabulary (status/expiry/cancel) is present")
        if re.search(r"\b(concurrent|conflict|race|simultaneous|at\s+the\s+same\s+time)\b", lower):
            add(DiagramType.SEQUENCE, "How do conflicting requests interact?", "concurrency is mentioned")
    if team == TeamId.DESIGN:
        add(DiagramType.COMPONENT, "Where do responsibilities live?", "module boundaries always need a view")
        if re.search(r"\b(request|response|api|endpoint|call|message)\b", lower):
            add(DiagramType.SEQUENCE, "What is the ordering and error path of a key interaction?", "interactions are described")
        if re.search(r"\b(table|record|entity|database|store|persist)\b", lower):
            add(DiagramType.ERD, "What is persisted and how is it related?", "persistence vocabulary is present")
        if re.search(r"\b(status|state|transition)\b", lower):
            add(DiagramType.STATE, "Which invariants hold across transitions?", "state vocabulary is present")
    if team == TeamId.TESTING and re.search(r"\b(integration|concurrent|conflict)\b", lower):
        add(DiagramType.SEQUENCE, "Where can integration fail?", "integration risks are mentioned")
    if team == TeamId.MAINTENANCE:
        add(DiagramType.COMPONENT, "Which parts does the change touch?", "impact analysis needs a dependency view")
    return out[:5]


def render_status(media_type: str, preferred: str | None) -> dict[str, str | bool]:
    """Report whether a renderer is installed. Rendering itself runs in the ProcessRunner (M6)."""
    renderer = preferred or ("plantuml" if "plantuml" in media_type else "mermaid")
    executable = {"mermaid": "mmdc", "plantuml": "plantuml"}.get(renderer, renderer)
    found = shutil.which(executable)
    return {"renderer": renderer, "executable": executable, "installed": found is not None, "rendered": False, "label": "unrendered"}


def render(source: str, media_type: str, destination: Path, *, preferred: str | None = None, timeout: int = 60) -> dict[str, str | bool]:
    """Render a diagram to SVG with the installed renderer (``mmdc`` for Mermaid, ``plantuml`` for
    PlantUML). Optional tooling: when it is missing the source stays the deliverable and the result
    says ``unrendered``; the core never depends on it (handoff docs/09 M8)."""
    import subprocess
    import tempfile

    status = render_status(media_type, preferred)
    if not status["installed"]:
        return status
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="sdlc-diagram-") as tmp:
        src = Path(tmp) / ("diagram.mmd" if status["renderer"] == "mermaid" else "diagram.puml")
        src.write_text(source, encoding="utf-8")
        from sdlc.runtime.launcher import host_command

        if status["renderer"] == "mermaid":
            argv = [*host_command("mmdc"), "-i", str(src), "-o", str(destination), "-q"]
        else:
            argv = [*host_command("plantuml"), "-tsvg", "-o", str(destination.parent), str(src)]
        try:
            proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, shell=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {**status, "rendered": False, "label": f"render failed: {exc}"}
        if status["renderer"] == "plantuml":
            produced = destination.parent / (src.stem + ".svg")
            if produced.exists() and produced != destination:
                produced.replace(destination)
        ok = proc.returncode == 0 and destination.exists()
        return {**status, "rendered": ok, "label": "rendered" if ok else f"render failed: {(proc.stderr or proc.stdout)[:200]}"}


def validate_source(source: str, media_type: str) -> list[str]:
    return [f.message for f in check_diagram_source(source, media_type)]


def starter_source(kind: DiagramType, scope_ids: list[str], purpose: str) -> tuple[str, str]:
    """A minimal, valid, editable source a participant can start from."""
    media_type, _ = NOTATION[kind]
    ids = ", ".join(scope_ids) if scope_ids else "none"
    if media_type == "text/x-plantuml":
        return media_type, f"@startuml\n' purpose: {purpose}\n' scope: {ids}\nactor Participant\nusecase \"Edit me\" as UC1\nParticipant --> UC1\n@enduml\n"
    header = {
        DiagramType.SEQUENCE: "sequenceDiagram",
        DiagramType.STATE: "stateDiagram-v2",
        DiagramType.CLASS: "classDiagram",
        DiagramType.ERD: "erDiagram",
        DiagramType.SCHEDULE: "gantt",
    }.get(kind, "flowchart TD")
    body = {
        "sequenceDiagram": "    participant A\n    participant B\n    A->>B: edit me",
        "stateDiagram-v2": "    [*] --> Draft\n    Draft --> [*]",
        "classDiagram": "    class EditMe",
        "erDiagram": "    ENTITY ||--o{ OTHER : has",
        "gantt": "    title Edit me\n    section Work\n    Task :a1, 2026-01-01, 1d",
    }.get(header, "    A[Edit me] --> B[Second node]")
    return media_type, f"%% purpose: {purpose}\n%% scope: {ids}\n{header}\n{body}\n"
