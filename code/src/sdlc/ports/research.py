"""ResearchBackend port: retrieve evidence with provenance. A model remembering a URL is not retrieval."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from sdlc.domain.identities import AgentPrincipal
from sdlc.domain.models import NormalizedError, ResearchEvidence
from sdlc.ports.common import Adapter


@dataclass(frozen=True, slots=True)
class ResearchRequest:
    principal: AgentPrincipal
    task_id: str
    attempt_id: str
    query: str
    max_results: int
    timeout_seconds: int


@dataclass(frozen=True, slots=True)
class ResearchReply:
    evidence: tuple[ResearchEvidence, ...]
    failures: tuple[str, ...]
    limitations: tuple[str, ...]
    error: NormalizedError | None = None


class ResearchBackend(Adapter, Protocol):
    async def retrieve(self, request: ResearchRequest) -> ResearchReply: ...
