"""ProcessRunner port: run a registered recipe and return collector-recorded evidence."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from sdlc.domain.identities import AgentPrincipal
from sdlc.domain.models import ArtifactRef, ExecutionRecord
from sdlc.ports.common import Adapter


@dataclass(frozen=True, slots=True)
class ExecutionRequest:
    principal: AgentPrincipal
    task_id: str
    attempt_id: str
    execution_id: str
    recipe_id: str
    #: Argument array only; never a shell string (and shell=False alone is not isolation).
    argv: tuple[str, ...]
    working_directory: Path
    source_ref: ArtifactRef
    environment_ref: ArtifactRef
    suite_ref: ArtifactRef | None
    timeout_seconds: int
    #: Allow-listed environment variable names; values are supplied by the runner profile.
    environment_allowlist: tuple[str, ...] = ()
    network: bool = False
    limits: dict[str, int] = field(default_factory=dict)


class ProcessRunner(Adapter, Protocol):
    async def execute(self, request: ExecutionRequest) -> ExecutionRecord: ...
