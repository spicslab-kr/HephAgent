"""Shared pieces for coding backends: event construction and the job prompt."""

from __future__ import annotations

import json
from typing import Any

from sdlc.domain.models import BackendEvent, EventKind, NormalizedError
from sdlc.ports.coding import CodingJob


def make_event(job: CodingJob, sequence: int, kind: EventKind, details: dict[str, Any], *, recorded_at: str, error: NormalizedError | None = None, event_id: str) -> BackendEvent:
    return BackendEvent(
        schema_version="1.0",
        event_id=event_id,
        project_id=job.principal.project_id,
        task_id=job.task_id,
        attempt_id=job.attempt_id,
        team_id=job.principal.team_id.value,  # type: ignore[arg-type]
        sequence=sequence,
        kind=kind,
        recorded_at=recorded_at,
        details=details,
        error=error,
    )


def render_job_prompt(job: CodingJob) -> str:
    return "\n".join(
        [
            f"Task {job.task_id} for the {job.principal.team_id.value} team. You are working inside the repository at the current directory.",
            "Granted documents (design, requirements, plan) are under .sdlc-inputs/ and are read-only; do not edit them.",
            "Make only the bounded change described below; keep the code small and readable; add or update developer tests next to the code.",
            "Do not run network installs, do not touch files outside this directory, and do not claim test results you did not observe.",
            "",
            job.instructions,
            "",
            f"When done, reply with exactly one JSON object validating as {job.output_schema.get('title', 'WorkerProposal')}: summary, artifact_candidates (leave empty: the runtime snapshots your changes), findings, questions, suggested_next_operations.",
        ]
    )


def final_json(text: str | None) -> dict | None:
    if not text:
        return None
    import re

    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return None
    try:
        value = json.loads(match.group(0))
    except ValueError:
        return None
    return value if isinstance(value, dict) else None
