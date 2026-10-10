"""Brain port: produce a typed proposal from an explicit, runtime-built context packet."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from sdlc.domain.identities import AgentPrincipal
from sdlc.domain.models import NormalizedError, Usage
from sdlc.ports.common import Adapter


@dataclass(frozen=True, slots=True)
class ContextDocument:
    """One granted input, already materialised and digest-checked by the runtime."""

    artifact_id: str
    revision: int
    sha256: str
    relative_path: str
    media_type: str
    text: str


@dataclass(frozen=True, slots=True)
class BrainRequest:
    principal: AgentPrincipal
    task_id: str
    attempt_id: str
    operation: str
    operation_version: int
    #: Editable prompt body selected by the team module (prompts/<team>/...).
    system_prompt: str
    instructions: str
    documents: tuple[ContextDocument, ...]
    #: JSON Schema the reply must satisfy; the runtime validates regardless of provider support.
    output_schema: dict[str, Any]
    output_definition: str
    max_output_tokens: int
    timeout_seconds: int
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class BrainReply:
    """A candidate payload plus provider metadata. It never commits task completion."""

    structured: dict[str, Any] | None
    text: str | None
    error: NormalizedError | None
    usage: Usage
    provider: dict[str, Any] = field(default_factory=dict)
    refusal: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.structured is not None


class Brain(Adapter, Protocol):
    async def generate(self, request: BrainRequest) -> BrainReply: ...
