"""Maintenance operations (handoff docs/04 s7).

| Operation                   | How it runs | Must produce                                                    |
|-----------------------------|-------------|-----------------------------------------------------------------|
| maintenance.triage          | Brain       | `triage_note` Markdown (release, symptom, urgency, classification)|
| maintenance.reproduce       | Brain       | `reproduction_report` Markdown (observed or qualified uncertainty)|
| maintenance.analyze_impact  | Brain       | `impact_analysis` JSON (earliest owner, affected refs, rationale)|
| maintenance.propose_change  | Brain       | `change_spec` Markdown (CHANGE_SPEC.md)                          |
| maintenance.route_rework    | Brain       | `rework_package` Markdown (what goes to whom, revalidation)      |
| maintenance.verify_closure  | Brain       | `maintenance_report` Markdown (MAINTENANCE_REPORT.md)            |

The runtime, not the model, delivers the change package (`CycleRunner` change loop) to
the team named in ``impact_analysis.earliest_owner`` and marks the dependent outputs
of that team and every team downstream of it ``stale`` (docs/02 FR-015, FR-019). The
former release and its evidence stay available. Maintenance never patches code itself.

Inputs are documents only: the release package, the test evidence and participant
memos (`sdlc request NAME memo.md`): a bug report, a changed requirement, a runtime
observation. Where a real operator or user would speak, the memo says so; simulated
observations must be labelled as such (preamble rule).
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from sdlc.application.artifacts import ArtifactService
from sdlc.application.views import TeamView
from sdlc.domain.models import ArtifactRef, Finding, Severity, Task, WorkerProposal
from sdlc.domain.teams import TEAM_ORDER
from sdlc.operations import OperationRegistry
from sdlc.teams.policies import BasePolicy, PolicyRegistry, candidates_of_kind, finding
from sdlc.verification.outputs import check_candidates, require_kinds

REQUIRED_KINDS: dict[str, tuple[str, ...]] = {
    "triage": ("triage_note",),
    "reproduce": ("reproduction_report",),
    "analyze_impact": ("impact_analysis",),
    "propose_change": ("change_spec",),
    "route_rework": ("rework_package",),
    "verify_closure": ("maintenance_report",),
}

CLASSIFICATIONS = ("corrective", "adaptive", "perfective", "preventive")
TEAM_NAMES = tuple(t.value for t in TEAM_ORDER)

#: Headings every readable deliverable must carry (case-insensitive substrings).
SECTIONS: dict[str, tuple[str, ...]] = {
    "triage_note": ("release", "symptom", "urgency", "classification"),
    "reproduction_report": ("steps", "observed", "uncertainty"),
    "change_spec": ("motivation", "reproduction", "impact", "earliest affected stage", "scope", "acceptance", "regression"),
    "rework_package": ("target team", "contents", "revalidation"),
    "maintenance_report": ("delivered revisions", "observed verification", "release notes", "closure"),
}


def _headings(text: str) -> list[str]:
    from sdlc.verification.outputs import markdown_headings

    return markdown_headings(text)


def impact_payload(text: str) -> dict | None:
    from sdlc.verification.outputs import strip_code_fence

    try:
        payload = json.loads(strip_code_fence(text))
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


@dataclass(frozen=True, slots=True)
class MaintenancePolicy(BasePolicy):
    short_name: str = ""

    def check(self, task: Task, view: TeamView) -> list[Finding]:
        findings: list[Finding] = []
        if not task.input_deliveries:
            findings.append(finding("missing_input", "maintenance works from delivered packages: a release package and/or a participant request memo"))
        return findings

    def context_notes(self, task: Task, view: TeamView) -> list[str]:
        notes = [
            "Maintenance coordinates the next cycle; it never edits code or instructs another team's session. Routing decisions: an implementation bug with unchanged requirements/design -> implementation; "
            "an architectural or interface defect -> design; a changed business rule -> requirements; changed feasibility or scope -> planning. Every return is a new explicit delivery made by the runtime.",
            "Observations that a real operator or user would have supplied are stand-ins here; label them 'simulated' and never present them as recovered facts.",
            "You cannot execute anything in this operation; that is expected, not a blocker. Record what the delivered records show and what stays uncertain. "
            "A finding with severity blocking means the operation is impossible (no request, no release package at all); everything else is a warning or a documented uncertainty.",
        ]
        if self.short_name == "analyze_impact":
            notes.append(f"impact_analysis JSON fields: earliest_owner (one of {list(TEAM_NAMES)}), classification (one of {list(CLASSIFICATIONS)}), affected (list of {{artifact_id, revision, reason}} taken from the documents you were given), requirement_ids, rationale, revalidation (list of what must be rerun downstream).")
        if self.short_name == "verify_closure":
            notes.append("Closure compares the returned revisions and the new test evidence with the original request: cite exact artifact references; a release note is a list of observed changes, never a claim of a pass without a record.")
        return notes

    def required_kinds(self, task: Task) -> tuple[str, ...]:
        return REQUIRED_KINDS[self.short_name]

    def check_outputs(self, task: Task, proposal: WorkerProposal, view: TeamView | None = None) -> list[Finding]:
        candidates = list(proposal.artifact_candidates)
        findings = check_candidates(candidates) + require_kinds(candidates, REQUIRED_KINDS[self.short_name])
        for candidate in candidates:
            sections = SECTIONS.get(candidate.kind)
            if sections and candidate.content_text is not None:
                present = " ".join(_headings(candidate.content_text))
                missing = [s for s in sections if s not in present]
                if missing:
                    findings.append(finding("missing_sections", f"{candidate.kind} must contain headings including: {missing}"))
            if candidate.kind == "execution_record":
                findings.append(finding("fabricated_evidence", "maintenance never authors execution evidence"))
        if self.short_name == "analyze_impact":
            findings += self._check_impact(task, proposal, view)
        return findings

    def _check_impact(self, task: Task, proposal: WorkerProposal, view: TeamView | None) -> list[Finding]:
        out: list[Finding] = []
        for candidate in candidates_of_kind(proposal, "impact_analysis"):
            payload = impact_payload(candidate.content_text or "")
            if payload is None:
                out.append(finding("invalid_impact", "impact_analysis must be a JSON object"))
                continue
            if payload.get("earliest_owner") not in TEAM_NAMES:
                out.append(finding("invalid_impact", f"earliest_owner must be one of {list(TEAM_NAMES)}"))
            if payload.get("classification") not in CLASSIFICATIONS:
                out.append(finding("invalid_impact", f"classification must be one of {list(CLASSIFICATIONS)}"))
            if not isinstance(payload.get("rationale"), str) or not payload.get("rationale"):
                out.append(finding("invalid_impact", "rationale is required"))
            affected = payload.get("affected")
            if not isinstance(affected, list):
                out.append(finding("invalid_impact", "affected must be a list of {artifact_id, revision, reason}"))
            elif view is not None:
                known_ids = {r.artifact_id for r in task.input_refs}
                unknown = [item for item in affected if not isinstance(item, dict) or item.get("artifact_id") not in known_ids]
                if unknown:  # advisory: the routing decision stands, the list is flagged for the reader
                    out.append(finding("unverified_affected", f"affected items that are not documents this task received (kept, flagged): {unknown[:5]}", Severity.WARNING))
        return out


def install(registry: OperationRegistry, policies: PolicyRegistry) -> None:
    for short in REQUIRED_KINDS:
        name = f"maintenance.{short}"
        policies.register(registry, name, 1, MaintenancePolicy(name=f"{name}.v1", short_name=short))


def latest_impact(view: TeamView) -> tuple[ArtifactRef, dict] | None:
    """The newest non-stale impact_analysis the Maintenance team produced, if any."""
    artifacts = ArtifactService(view.ctx)
    found = None
    for artifact in view.list_owned():
        if artifact.kind == "impact_analysis" and artifacts.status(artifact.ref) not in ("superseded", "stale"):
            payload = impact_payload(artifacts.read_bytes(artifact.ref).decode("utf-8"))
            if payload:
                found = (artifact.ref, payload)
    return found
