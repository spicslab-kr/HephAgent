"""Scripted coding backend for offline development and recovery tests.

A script maps ``task:<id>`` / ``operation:<name>`` / ``default`` to a step with
``files`` (relative path -> text, written into the workspace; ``null`` deletes),
``summary``, optional ``error`` (NormalizedError) and optional ``interrupt`` (stop
after writing files without a completed event, simulating a lost process).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from sdlc.adapters.coding.common import make_event
from sdlc.common import now_utc
from sdlc.domain.models import BackendEvent, Checkpoint, EventKind, NormalizedError
from sdlc.ports.coding import CancelReceipt, CodingJob
from sdlc.ports.common import AdapterDescription, CapabilityStatus, ProbeResult, Support

ADAPTER_ID = "fixture-coding"


class FixtureCoding:
    def __init__(self, script: dict[str, Any] | None = None) -> None:
        self.script = script or {}
        self.invocations: Counter[str] = Counter()
        self._cursor: Counter[str] = Counter()
        self._ids = 0

    def describe(self) -> AdapterDescription:
        return AdapterDescription(
            adapter_id=ADAPTER_ID, adapter_version="1", connection_type="fixture", auth_mode="none", cost_source="none",
            capabilities=(
                CapabilityStatus("scoped_repository", Support.SUPPORTED, "writes scripted files into the workspace"),
                CapabilityStatus("structured_proposal", Support.SUPPORTED),
                CapabilityStatus("native_resume", Support.UNSUPPORTED),
                CapabilityStatus("interrupt", Support.SUPPORTED),
            ),
        )

    async def probe(self, live: bool = False) -> ProbeResult:
        return ProbeResult(ok=True, live=False, messages=("fixture coding backend",), identity="verified", version="1")

    def _select(self, job: CodingJob) -> dict[str, Any] | None:
        for selector in (f"task:{job.task_id}", f"operation:{job.instructions.split(' ', 1)[0].rstrip(':')}", "default"):
            if selector in self.script:
                entries = self.script[selector]
                if isinstance(entries, list):
                    index = min(self._cursor[selector], len(entries) - 1)
                    self._cursor[selector] += 1
                    return entries[index]
                return entries
        return None

    def _event(self, job: CodingJob, seq: int, kind: EventKind, details: dict[str, Any], error: NormalizedError | None = None) -> BackendEvent:
        self._ids += 1
        return make_event(job, seq, kind, details, recorded_at=now_utc(), error=error, event_id=f"fxc-{job.attempt_id}-{self._ids}")

    async def start(self, job: CodingJob) -> AsyncIterator[BackendEvent]:
        self.invocations[job.task_id] += 1
        step = self._select(job)
        yield self._event(job, 1, EventKind.STARTED, {"adapter": ADAPTER_ID, "workspace": str(job.workspace), "sandbox": job.sandbox})
        if step is None:
            yield self._event(job, 2, EventKind.ERROR, {"reason": "unscripted"}, error=NormalizedError.model_validate({
                "category": "invalid_output", "provider_code": "fixture_unscripted", "message": f"no coding step scripted for {job.task_id}", "automatic_retry_now": False,
                "retry_after_seconds": None, "reset_at": None, "completion_certainty": "not_completed", "next_action": "add a step to the fixture script", "diagnostic_ref": None}))
            return
        seq = 1
        for relative, text in (step.get("files") or {}).items():
            target = Path(job.workspace) / relative
            if text is None:
                target.unlink(missing_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(text, encoding="utf-8")
            seq += 1
            yield self._event(job, seq, EventKind.PROGRESS, {"file": relative, "action": "deleted" if text is None else "written"})
        if step.get("interrupt"):
            return  # process "dies": no terminal event
        if step.get("error"):
            seq += 1
            yield self._event(job, seq, EventKind.ERROR, {"reason": "scripted"}, error=NormalizedError.model_validate(step["error"]))
            return
        seq += 1
        yield self._event(job, seq, EventKind.COMPLETED, {"summary": step.get("summary", "scripted change applied"), "findings": step.get("findings", []), "questions": step.get("questions", []), "origin": "fixture"})

    async def resume(self, job: CodingJob, checkpoint: Checkpoint) -> AsyncIterator[BackendEvent]:
        async for event in self.start(job):
            yield event

    async def cancel(self, attempt_id: str) -> CancelReceipt:
        return CancelReceipt(attempt_id=attempt_id, stopped=True, partial_outputs_preserved=True, reconciliation_required=True, notes=("fixture: nothing running",))
