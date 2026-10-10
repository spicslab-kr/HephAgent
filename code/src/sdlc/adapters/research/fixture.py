"""Recorded research results for offline development (handoff docs/05 s8).

The script maps a query (or ``default``) to ResearchEvidence payloads. Everything
returned is labeled fixture in its provenance; a fixture can also declare retrieval
failures and limitations so the synthesis step learns to handle missing evidence.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from sdlc.domain.models import ResearchEvidence
from sdlc.ports.common import AdapterDescription, CapabilityStatus, ProbeResult, Support
from sdlc.ports.research import ResearchReply, ResearchRequest
from sdlc.verification.canonical import load_json_strict

ADAPTER_ID = "fixture-research"


class FixtureResearch:
    def __init__(self, script: dict[str, Any] | None = None) -> None:
        self.script = script or {}
        self.invocations: Counter[str] = Counter()

    @classmethod
    def from_file(cls, path: Path) -> "FixtureResearch":
        return cls(load_json_strict(path.read_text(encoding="utf-8-sig")).get("research", {}))

    def describe(self) -> AdapterDescription:
        return AdapterDescription(
            adapter_id=ADAPTER_ID,
            adapter_version="1",
            connection_type="fixture",
            auth_mode="none",
            cost_source="none",
            capabilities=(CapabilityStatus("research_or_imported_evidence", Support.SUPPORTED, "recorded evidence"),),
        )

    async def probe(self, live: bool = False) -> ProbeResult:
        return ProbeResult(ok=True, live=False, messages=("fixture research needs no network",), identity="verified", version="1")

    async def retrieve(self, request: ResearchRequest) -> ResearchReply:
        self.invocations[request.query] += 1
        entry = self.script.get(request.query) or self.script.get("default")
        if entry is None:
            return ResearchReply(evidence=(), failures=(f"no recorded evidence for query {request.query!r}",), limitations=("fixture research: add the query to the script or import evidence",))
        evidence = tuple(ResearchEvidence.model_validate(e) for e in entry.get("evidence", []))[: request.max_results]
        return ResearchReply(
            evidence=evidence,
            failures=tuple(entry.get("failures", [])),
            limitations=tuple(entry.get("limitations", [])) + ("origin: fixture (recorded), not a live retrieval",),
        )
