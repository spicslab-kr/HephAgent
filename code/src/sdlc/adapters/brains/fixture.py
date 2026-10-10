"""Deterministic fixture Brain for development, CI and failure replay (FR-022).

A :class:`FixtureScript` maps a selector to a scripted step. Selectors are tried in
order of specificity: ``task:<task_id>``, ``operation:<name>``, then ``default``. A step
either returns a recorded :class:`WorkerProposal` payload or a recorded
:class:`NormalizedError`. Steps can be sequences, so the first call to a task can
fail with ``quota_exhausted`` and the next call succeed, which is what recovery tests
need. Every invocation is counted, so tests can assert "zero model calls".

Fixture output is labeled: the reply's provider metadata carries ``origin: fixture``.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sdlc.domain.models import (
    CompletionCertainty,
    ErrorCategory,
    Measurement,
    NormalizedError,
    Usage,
    WorkerProposal,
)
from sdlc.ports.brain import BrainReply, BrainRequest
from sdlc.ports.common import AdapterDescription, CapabilityStatus, ProbeResult, Support
from sdlc.verification.canonical import load_json_strict

ADAPTER_ID = "fixture-brain"

UNKNOWN_USAGE = Usage(
    input_tokens=None, output_tokens=None, cost_microunits=None, currency=None, measurement=Measurement.UNKNOWN
)


def quota_exhausted_error(reset_at: str | None = None) -> NormalizedError:
    """The canonical 'usage limit reached' failure. ``reset_at`` only if reliably supplied."""
    return NormalizedError(
        category=ErrorCategory.QUOTA_EXHAUSTED,
        provider_code="fixture_quota",
        message="The configured account quota is exhausted; completed work is preserved.",
        automatic_retry_now=False,
        retry_after_seconds=None,
        reset_at=reset_at,
        completion_certainty=CompletionCertainty.NOT_COMPLETED,
        next_action="Restore this connection or select an approved alternative, then resume the same task.",
        diagnostic_ref=None,
    )


@dataclass(frozen=True, slots=True)
class ScriptStep:
    """One scripted reply: either a proposal payload or a normalized error."""

    proposal: dict[str, Any] | None = None
    error: NormalizedError | None = None
    #: Raw text to return instead of a proposal, to replay malformed/truncated output.
    raw_text: str | None = None

    def __post_init__(self) -> None:
        if sum(x is not None for x in (self.proposal, self.error, self.raw_text)) != 1:
            raise ValueError("a script step needs exactly one of proposal, error or raw_text")


@dataclass
class FixtureScript:
    steps: dict[str, list[ScriptStep]] = field(default_factory=dict)
    #: Optional recorded research section (query -> evidence), served by FixtureResearch.
    research: dict[str, Any] = field(default_factory=dict)
    #: Optional recorded coding section (selector -> files/summary), served by FixtureCoding.
    coding: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_file(cls, path: Path) -> "FixtureScript":
        return cls.from_raw(load_json_strict(path.read_text(encoding="utf-8-sig")))

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> "FixtureScript":
        research = dict(raw.get("research", {}))
        coding = dict(raw.get("coding", {}))
        steps: dict[str, list[ScriptStep]] = {}
        for selector, entries in raw.get("steps", {}).items():
            if isinstance(entries, dict):
                entries = [entries]
            steps[selector] = [
                ScriptStep(
                    proposal=e.get("proposal"),
                    error=NormalizedError.model_validate(e["error"]) if e.get("error") else None,
                    raw_text=e.get("raw_text"),
                )
                for e in entries
            ]
        return cls(steps=steps, research=research, coding=coding)

    def select(self, task_id: str, operation: str) -> str | None:
        for selector in (f"task:{task_id}", f"operation:{operation}", "default"):
            if selector in self.steps:
                return selector
        return None


class FixtureBrain:
    """Replays scripted proposals/errors. Deterministic, offline, labeled as fixture."""

    def __init__(self, script: FixtureScript | None = None) -> None:
        self.script = script or FixtureScript()
        self.invocations: Counter[str] = Counter()
        self._cursor: Counter[str] = Counter()

    def describe(self) -> AdapterDescription:
        return AdapterDescription(
            adapter_id=ADAPTER_ID,
            adapter_version="1",
            connection_type="fixture",
            auth_mode="none",
            cost_source="none",
            capabilities=(
                CapabilityStatus("structured_proposal", Support.SUPPORTED, "recorded output"),
                CapabilityStatus("diagram_source_generation", Support.SUPPORTED, "recorded diagram sources"),
                CapabilityStatus("scoped_repository", Support.UNSUPPORTED, "a Brain never provides repository tools"),
                CapabilityStatus("isolated_execution", Support.UNSUPPORTED),
                CapabilityStatus("streaming", Support.UNSUPPORTED),
                CapabilityStatus("native_resume", Support.UNSUPPORTED),
                CapabilityStatus("usage_events", Support.UNSUPPORTED, "usage is always unknown"),
            ),
            limits={"calls": "unbounded", "note": "fixture"},
        )

    async def probe(self, live: bool = False) -> ProbeResult:
        return ProbeResult(ok=True, live=False, messages=("fixture brain needs no account",), identity="verified", version="1")

    @property
    def total_invocations(self) -> int:
        return sum(self.invocations.values())

    async def generate(self, request: BrainRequest) -> BrainReply:
        self.invocations[request.task_id] += 1
        selector = self.script.select(request.task_id, request.operation)
        provider = {"adapter": ADAPTER_ID, "origin": "fixture", "selector": selector}
        if selector is None:
            return BrainReply(
                structured=None,
                text=None,
                error=NormalizedError(
                    category=ErrorCategory.INVALID_OUTPUT,
                    provider_code="fixture_unscripted",
                    message=f"no fixture step scripted for task {request.task_id!r} / {request.operation!r}",
                    automatic_retry_now=False,
                    retry_after_seconds=None,
                    reset_at=None,
                    completion_certainty=CompletionCertainty.NOT_COMPLETED,
                    next_action="Add a step to the fixture script or select a live connection.",
                    diagnostic_ref=None,
                ),
                usage=UNKNOWN_USAGE,
                provider=provider,
            )
        sequence = self.script.steps[selector]
        # Replay position: the runtime's attempt counters when supplied (stable across processes),
        # else this instance's own call counter.
        counter = "_task_attempts_before" if selector.startswith("task:") else "_operation_attempts_before"
        position = request.parameters.get(counter)
        index = min(int(position) if isinstance(position, int) else self._cursor[selector], len(sequence) - 1)
        self._cursor[selector] += 1
        step = sequence[index]
        if step.error is not None:
            return BrainReply(structured=None, text=None, error=step.error, usage=UNKNOWN_USAGE, provider=provider)
        if step.raw_text is not None:
            return BrainReply(structured=None, text=step.raw_text, error=None, usage=UNKNOWN_USAGE, provider=provider)
        assert step.proposal is not None
        payload = json.loads(json.dumps(step.proposal))
        # Recorded leader graphs may use the placeholder @INBOX@ for "the delivery ids I was
        # given"; the runtime supplies those in the leader-context document.
        for document in request.documents:
            if document.artifact_id == "leader-context":
                try:
                    inbox = json.loads(document.text).get("inbox_delivery_ids") or []
                except ValueError:
                    inbox = []
                if inbox:
                    for candidate in payload.get("artifact_candidates", []):
                        if isinstance(candidate.get("content_text"), str):
                            # ["@INBOX@"] -> every inbox delivery; a bare @INBOX@ -> the first one
                            text = candidate["content_text"].replace('["@INBOX@"]', json.dumps(inbox)).replace('[\\"@INBOX@\\"]', json.dumps(inbox).replace('"', '\\"'))
                            candidate["content_text"] = text.replace("@INBOX@", inbox[0])
        # @DOC:<artifact_id>@ stands for the exact reference of that input document.
        by_id = {d.artifact_id: d for d in request.documents}
        for candidate in payload.get("artifact_candidates", []):
            text = candidate.get("content_text")
            if isinstance(text, str) and "@DOC:" in text:
                for artifact_id in set(re.findall(r"@DOC:([A-Za-z0-9_.:-]+)@", text)):
                    doc = by_id.get(artifact_id)
                    if doc is not None:
                        text = text.replace(f"@DOC:{artifact_id}@", json.dumps({"artifact_id": doc.artifact_id, "revision": doc.revision, "sha256": doc.sha256}))
                candidate["content_text"] = text
        # @REF:<kind>@ stands for the exact reference of the first dependency output of that
        # kind (the runtime lists them in its dependency-outputs index), so a scripted defect
        # can cite the real execution record the collector just produced.
        index_text = next((d.text for d in request.documents if d.artifact_id == "runtime-dependency-outputs"), "")
        for candidate in payload.get("artifact_candidates", []):
            text = candidate.get("content_text")
            if not isinstance(text, str) or "@REF:" not in text:
                continue
            for kind in set(re.findall(r"@REF:([A-Za-z0-9_]+)@", text)):
                match = re.search(rf"^- (?P<id>\S+)@(?P<rev>\d+) kind={kind} path=\S+ sha256=(?P<sha>[0-9a-f]{{64}})", index_text, re.M)
                if match:
                    ref = json.dumps({"artifact_id": match.group("id"), "revision": int(match.group("rev")), "sha256": match.group("sha")})
                    text = text.replace(f"@REF:{kind}@", ref)
            candidate["content_text"] = text
        # Script authors may write just {"summary": ...}; fill the structural defaults.
        payload.setdefault("schema_version", "1.0")
        payload.setdefault("task_id", request.task_id)
        for key in ("artifact_candidates", "findings", "questions", "suggested_next_operations"):
            payload.setdefault(key, [])
        return BrainReply(
            structured=payload, text=json.dumps(payload, ensure_ascii=False), error=None, usage=UNKNOWN_USAGE, provider=provider
        )


def proposal_payload(task_id: str, summary: str, candidates: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Helper for tests and scripts: a minimal valid WorkerProposal dict."""
    return WorkerProposal(
        schema_version="1.0",
        task_id=task_id,
        summary=summary,
        artifact_candidates=[],
        findings=[],
        questions=[],
        suggested_next_operations=[],
    ).to_json_dict() | ({"artifact_candidates": candidates} if candidates else {})
