"""CodingBackend port: bounded repository work on an isolated task snapshot."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from sdlc.domain.identities import AgentPrincipal, SessionOwnershipKey
from sdlc.domain.models import BackendEvent, Checkpoint
from sdlc.ports.common import Adapter


@dataclass(frozen=True, slots=True)
class CodingJob:
    principal: AgentPrincipal
    task_id: str
    attempt_id: str
    ownership: SessionOwnershipKey
    #: Isolated clone/copy prepared by the runtime; the only writable location.
    workspace: Path
    #: "read-only" for inspection/spikes, "workspace-write" for authorised coding.
    sandbox: str
    instructions: str
    #: JSON Schema for the final structured summary (WorkerProposal projection).
    output_schema: dict
    timeout_seconds: int
    #: Explicit child environment; provider credentials never reach target processes.
    environment: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CancelReceipt:
    attempt_id: str
    stopped: bool
    partial_outputs_preserved: bool
    reconciliation_required: bool
    notes: tuple[str, ...] = ()


class CodingBackend(Adapter, Protocol):
    def start(self, job: CodingJob) -> AsyncIterator[BackendEvent]: ...

    def resume(self, job: CodingJob, checkpoint: Checkpoint) -> AsyncIterator[BackendEvent]:
        """Resume only the same logical task under the same ownership key."""
        ...

    async def cancel(self, attempt_id: str) -> CancelReceipt: ...
