"""Release gate and release manifest (handoff docs/04 s6-7, docs/06 s8-9, docs/09 s6).

A release is a runtime record, not a narrative: it is created only when the Testing
team's current test report recommends RELEASE and the collector's records back it (no
failed or unexecuted declared case). The ``release_manifest`` artifact (contract
``ReleaseManifest``) pins the exact candidate, requirements, design, benches, execution
records, decisions, runbook and rollback references. Its conformance label is decided
by the runtime: every result produced under development isolation is *nonconforming*,
so the release is recorded as ``conformance: nonconforming`` and can never claim the
compliant lifecycle gate (docs/06 s9). A later release never deletes an earlier one.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from sdlc.application.artifacts import ArtifactService
from sdlc.application.handoffs import Actor, HandoffService
from sdlc.application.projects import ProjectContext
from sdlc.domain.errors import Conflict, MissingInput
from sdlc.domain.models import Artifact, ArtifactRef, ExecutionRecord, Origin, OwnerTeam, TeamId
from sdlc.storage import repositories as repo
from sdlc.teams.testing.mapping import case_ids_from_documents, case_outcomes
from sdlc.verification.canonical import canonical_bytes
from sdlc.verification.schemas import validate_record

RECOMMENDATIONS = ("RELEASE", "HOLD", "REJECT")


@dataclass(frozen=True, slots=True)
class ReleaseGate:
    """What the evidence allows, computed from records and documents, never from prose alone."""

    recommendation: str | None
    report_ref: ArtifactRef | None
    candidate_ref: ArtifactRef | None
    counts: dict[str, int]
    failed_cases: list[str]
    not_executed: list[str]
    execution_refs: list[ArtifactRef]
    bench_refs: list[ArtifactRef]
    conformance: str  # conforming | nonconforming
    reasons: list[str]

    @property
    def allowed(self) -> bool:
        return not self.reasons


class ReleaseService:
    def __init__(self, context: ProjectContext) -> None:
        self.ctx = context
        self.artifacts = ArtifactService(context)
        self.handoffs = HandoffService(context)

    # ------------------------------------------------------------------ evidence
    def _current(self, owner: OwnerTeam, kinds: tuple[str, ...]) -> list[Artifact]:
        latest: dict[str, Artifact] = {}
        for artifact in self.artifacts.list(owner):
            if artifact.kind not in kinds or self.artifacts.status(artifact.ref) in ("superseded", "stale"):
                continue
            if artifact.artifact_id not in latest or artifact.revision > latest[artifact.artifact_id].revision:
                latest[artifact.artifact_id] = artifact
        return sorted(latest.values(), key=lambda a: (str(a.created_at), a.artifact_id))

    def _operation_of(self, task_id: str | None) -> str | None:
        if not task_id:
            return None
        row = self.ctx.db.one("SELECT task_json FROM tasks WHERE project_id=? AND task_id=?", (self.ctx.project_id, task_id))
        return json.loads(row["task_json"]).get("operation") if row else None

    def gate(self) -> ReleaseGate:
        reasons: list[str] = []
        reports = self._current(OwnerTeam.TESTING, ("test_report",))
        assessed = [a for a in reports if self._operation_of(a.created_by_task) == "testing.assess_release"]
        report = (assessed or reports)[-1] if reports else None
        recommendation = None
        if report is None:
            reasons.append("no test report from the Testing team's release assessment yet")
        else:
            text = self.artifacts.read_bytes(report.ref).decode("utf-8", "replace")
            words = [w for w in RECOMMENDATIONS if re.search(rf"\b{w}\b", text)]
            recommendation = words[0] if len(words) == 1 else None
            if recommendation != "RELEASE":
                reasons.append(f"the test report's recommendation is {recommendation or 'ambiguous/missing'}, not RELEASE")
        records: list[tuple[ArtifactRef, ExecutionRecord]] = []
        for artifact in self._current(OwnerTeam.TESTING, ("execution_record",)):
            records.append((artifact.ref, ExecutionRecord.model_validate(json.loads(self.artifacts.read_bytes(artifact.ref).decode("utf-8")))))
        if not records:
            reasons.append("no execution record from the Testing team (nothing was run)")
        candidate = records[-1][1].source_ref if records else None
        same_candidate = [(ref, rec) for ref, rec in records if candidate is not None and rec.source_ref.key == candidate.key]
        case_ids = case_ids_from_documents([self.artifacts.read_bytes(a.ref).decode("utf-8") for a in self._current(OwnerTeam.TESTING, ("test_cases", "test_plan"))])
        outcomes = case_outcomes(case_ids, [rec for _, rec in same_candidate])
        not_executed = [c for c, s in outcomes.statuses.items() if s == "not_executed"]
        if outcomes.failed_cases:
            reasons.append(f"failed cases on the candidate: {outcomes.failed_cases}")
        if not_executed:
            reasons.append(f"declared cases never executed on the candidate: {not_executed}")
        if any(rec.fixture_mode for _, rec in same_candidate):
            reasons.append("fixture-origin execution records cannot satisfy a release gate")
        conformance = "conforming" if self.ctx.config.runtime.isolation == "strict" else "nonconforming"
        return ReleaseGate(recommendation, report.ref if report else None, candidate, outcomes.counts, outcomes.failed_cases, not_executed,
                           [ref for ref, _ in same_candidate], [a.ref for a in self._current(OwnerTeam.TESTING, ("test_bench",))], conformance, reasons)

    # ------------------------------------------------------------------ create
    def releases(self) -> list[Artifact]:
        return [a for a in self.artifacts.list(OwnerTeam.TESTING) if a.kind == "release_manifest"]

    def create(self, actor: Actor, *, rationale: str = "release gate passed") -> tuple[Artifact, ReleaseGate]:
        gate = self.gate()
        if not gate.allowed:
            raise Conflict("the evidence does not allow a release", reasons=gate.reasons)
        assert gate.candidate_ref is not None and gate.report_ref is not None
        requirements = self._current(OwnerTeam.REQUIREMENTS, ("requirements", "requirements_document"))
        design = self._current(OwnerTeam.DESIGN, ("design", "design_document"))
        runbooks = self._current(OwnerTeam.IMPLEMENTATION, ("implementation_document",))
        if not requirements or not design:
            raise MissingInput("a release needs current requirements and design documents", requirements=len(requirements), design=len(design))
        runbook_ref = runbooks[-1].ref if runbooks else gate.report_ref
        number = len(self.releases()) + 1
        release_id = f"release-{number:03d}"
        decision_id = self.handoffs.record_decision(actor, scope="release", refs={"release_id": release_id, "candidate": gate.candidate_ref.to_json_dict(), "test_report": gate.report_ref.to_json_dict()},
                                                     rationale=f"{rationale}; conformance {gate.conformance}", disposition="accepted", team=TeamId.TESTING)
        known_issues = [f"conformance: {gate.conformance} (results produced under {self.ctx.config.runtime.isolation} isolation; the compliant lifecycle gate is not satisfied)"] if gate.conformance != "conforming" else []
        for artifact in self._current(OwnerTeam.TESTING, ("defect",)):
            payload = json.loads(self.artifacts.read_bytes(artifact.ref).decode("utf-8"))
            if payload.get("severity") == "minor":
                known_issues.append(f"{payload.get('defect_id')}: {payload.get('observed', '')[:160]}")
        manifest = {
            "schema_version": "1.0", "release_id": release_id, "source_ref": gate.candidate_ref.to_json_dict(),
            "requirements_ref": requirements[-1].ref.to_json_dict(), "design_ref": design[-1].ref.to_json_dict(),
            "bench_refs": [r.to_json_dict() for r in gate.bench_refs], "execution_refs": [r.to_json_dict() for r in gate.execution_refs],
            "decision_refs": [decision_id], "known_issues": known_issues, "runbook_ref": runbook_ref.to_json_dict(), "rollback_ref": gate.report_ref.to_json_dict(),
        }
        validate_record(manifest, "ReleaseManifest")
        depends = [gate.candidate_ref, requirements[-1].ref, design[-1].ref, runbook_ref, gate.report_ref, *gate.bench_refs, *gate.execution_refs]
        artifact = self.artifacts.register(owner=OwnerTeam.TESTING, artifact_id=f"{release_id}-manifest", kind="release_manifest", media_type="application/json",
                                           relative_path=f"releases/{release_id}/RELEASE_MANIFEST.json", data=canonical_bytes(manifest), origin=Origin.TOOL, depends_on=depends)
        summary = (f"# Release {release_id}\n\n- candidate: {gate.candidate_ref.artifact_id}@{gate.candidate_ref.revision}\n- conformance: **{gate.conformance}**\n"
                   f"- per-case verdicts: {json.dumps(gate.counts, sort_keys=True)}\n- decision: {decision_id}\n- known issues: {len(known_issues)}\n")
        self.artifacts.register(owner=OwnerTeam.TESTING, artifact_id=f"{release_id}-summary", kind="release_summary", media_type="text/markdown",
                                relative_path=f"releases/{release_id}/RELEASE.md", data=summary.encode("utf-8"), origin=Origin.TOOL, depends_on=[artifact.ref])
        with self.ctx.db.transaction():
            repo.insert_event(self.ctx.db, self.ctx.project_id, self.ctx.ids("event"), "release.created",
                              {"release_id": release_id, "manifest": artifact.ref.to_json_dict(), "conformance": gate.conformance, "compliant": gate.conformance == "conforming", "decision_id": decision_id},
                              self.ctx.clock(), team_id="testing")
        return artifact, gate

    def manifest(self, release_id: str | None = None) -> tuple[Artifact, dict]:
        releases = self.releases()
        if not releases:
            raise MissingInput("no release has been created yet; run the testing stage to a RELEASE recommendation, then: sdlc release NAME")
        chosen = releases[-1] if release_id is None else next((a for a in releases if a.artifact_id == f"{release_id}-manifest"), None)
        if chosen is None:
            raise MissingInput("unknown release id", release_id=release_id, known=[a.artifact_id.removesuffix("-manifest") for a in releases])
        return chosen, json.loads(self.artifacts.read_bytes(chosen.ref).decode("utf-8"))
