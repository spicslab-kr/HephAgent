"""Imported evidence as a research backend (handoff docs/05 s8).

When no search connection is available (or by preference), the participant imports
``research_evidence`` JSON artifacts. This adapter serves those granted artifacts to a
task: it does not search anything, so its only capability is
``research_or_imported_evidence`` with the explicit limitation "imported".
"""

from __future__ import annotations

from collections.abc import Callable

from sdlc.domain.models import ResearchEvidence
from sdlc.ports.common import AdapterDescription, CapabilityStatus, ProbeResult, Support
from sdlc.ports.research import ResearchReply, ResearchRequest

ADAPTER_ID = "imported-evidence"


class ImportedEvidenceResearch:
    def __init__(self, loader: Callable[[ResearchRequest], list[ResearchEvidence]]) -> None:
        self._loader = loader

    def describe(self) -> AdapterDescription:
        return AdapterDescription(
            adapter_id=ADAPTER_ID,
            adapter_version="1",
            connection_type="manual",
            auth_mode="none",
            cost_source="none",
            capabilities=(CapabilityStatus("research_or_imported_evidence", Support.SUPPORTED, "participant-imported evidence only"),),
        )

    async def probe(self, live: bool = False) -> ProbeResult:
        return ProbeResult(ok=True, live=False, messages=("serves imported research_evidence artifacts",), identity="verified")

    async def retrieve(self, request: ResearchRequest) -> ResearchReply:
        evidence = self._loader(request)
        return ResearchReply(
            evidence=tuple(evidence[: request.max_results]),
            failures=() if evidence else ("no imported evidence matches this task's grants",),
            limitations=("imported evidence: no live search was performed; coverage depends on what was imported",),
        )
