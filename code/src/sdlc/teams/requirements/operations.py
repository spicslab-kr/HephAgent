"""Requirements operations: prerequisites and output obligations (handoff docs/04 s3).

| Operation                        | Must produce                                            |
|----------------------------------|---------------------------------------------------------|
| requirements.investigate_needs   | `needs_investigation` Markdown; synthetic evidence labeled |
| requirements.extract             | `requirements` JSON (RequirementsSet) with sourced claims |
| requirements.define_acceptance   | revised `requirements` JSON + `acceptance_notes`        |
| requirements.model_scenarios     | `scenarios` Markdown                                    |
| requirements.resolve_conflicts   | `conflicts` Markdown                                    |
| requirements.validate            | `validation` Markdown; defects as blocking findings     |

Quality rules enforced deterministically: every requirement has an acceptance
condition (schema), cited claims exist (schema validator), and any claim with
``basis: synthetic`` lives in evidence marked ``synthetic: true`` with a generation
method. A ``must`` requirement with ``verification_method: automated_test`` and no
acceptance text containing an observable outcome is flagged.
"""

from __future__ import annotations

from dataclasses import dataclass

from sdlc.application.views import TeamView
from sdlc.domain.models import Finding, RequirementsSet, ResearchEvidence, Task, WorkerProposal
from sdlc.operations import OperationRegistry
from sdlc.teams.policies import BasePolicy, PolicyRegistry, candidates_of_kind, finding
from sdlc.verification.canonical import load_json_strict as _raw_load_json_strict, strip_code_fence


def _load_json_strict(text):
    return _raw_load_json_strict(strip_code_fence(text))


def load_json_strict(text):
    try:
        return _load_json_strict(text)
    except ValueError:
        return None

from sdlc.verification.outputs import check_candidates, require_kinds

REQUIRED_KINDS: dict[str, tuple[str, ...]] = {
    "investigate_needs": ("needs_investigation",),
    "extract": ("requirements",),
    "define_acceptance": ("requirements", "acceptance_notes"),
    "model_scenarios": ("scenarios",),
    "resolve_conflicts": ("conflicts",),
    "validate": ("validation",),
}


@dataclass(frozen=True, slots=True)
class RequirementsPolicy(BasePolicy):
    short_name: str = ""

    def check(self, task: Task, view: TeamView) -> list[Finding]:
        findings: list[Finding] = []
        if not task.input_deliveries:
            findings.append(finding("missing_input", "requirements operations need the planning handoff (or a participant import) delivered to this team"))
        if self.short_name in ("define_acceptance", "validate", "resolve_conflicts"):
            owned = {a.kind for a in view.list_owned()}
            has_set = "requirements" in owned or any(r.artifact_id.endswith("requirements") for r in task.input_refs)
            if not has_set:
                findings.append(finding("missing_input", f"{self.short_name} needs an existing requirements set from requirements.extract"))
        return findings

    def required_kinds(self, task: Task) -> tuple[str, ...]:
        return REQUIRED_KINDS[self.short_name]

    def check_outputs(self, task: Task, proposal: WorkerProposal, view: TeamView | None = None) -> list[Finding]:
        candidates = list(proposal.artifact_candidates)
        findings = check_candidates(candidates) + require_kinds(candidates, REQUIRED_KINDS[self.short_name])
        if any(f.code == "invalid_record" for f in findings):
            return findings
        for candidate in candidates_of_kind(proposal, "requirements"):
            if candidate.content_text is None:
                continue
            raw = load_json_strict(candidate.content_text)
            if raw is None:
                continue
            req_set = RequirementsSet.model_validate(raw)
            synthetic_claims = {c.claim_id for c in req_set.claims if c.basis.value == "synthetic"}
            for requirement in req_set.requirements:
                if requirement.priority.value == "must" and all(len(a.strip()) < 12 for a in requirement.acceptance):
                    findings.append(finding("weak_acceptance", f"{requirement.requirement_id}: acceptance conditions must state an observable outcome"))
                if requirement.priority.value == "must" and not requirement.source_claim_ids and not requirement.open_questions:
                    findings.append(finding("untraced_requirement", f"{requirement.requirement_id}: a must requirement needs source claims or an explicit open question", severity=_warning()))
                if set(requirement.source_claim_ids) and set(requirement.source_claim_ids) <= synthetic_claims and requirement.priority.value == "must":
                    findings.append(finding("synthetic_only_basis", f"{requirement.requirement_id}: a must requirement cannot rest on synthetic evidence alone; record a participant decision or assumption"))
            for claim in req_set.claims:
                if claim.basis.value == "retrieved" and not (claim.source_refs or claim.source_locators):
                    findings.append(finding("unsourced_claim", f"claim {claim.claim_id} is marked retrieved but has no source"))
        from sdlc.teams.common import check_document_ids, known_requirement_ids

        known = known_requirement_ids(task, proposal, view)
        findings += check_document_ids(proposal, known)
        if self.short_name == "define_acceptance" and view is not None:
            earlier = known_requirement_ids(task, None, view)
            if earlier and known and not (known <= earlier or earlier <= known):
                findings.append(finding("renumbered_requirements", f"define_acceptance must keep the existing requirement ids {sorted(earlier)}; got {sorted(known)}"))
        for candidate in candidates_of_kind(proposal, "research_evidence"):
            if candidate.content_text is None:
                continue
            raw = load_json_strict(candidate.content_text)
            if raw is None:
                continue
            evidence = ResearchEvidence.model_validate(raw)
            if any(c.basis.value == "synthetic" for c in evidence.claims) and not evidence.synthetic:
                findings.append(finding("mislabeled_synthetic", "evidence with synthetic claims must set synthetic: true"))
        return findings


def _warning():
    from sdlc.domain.models import Severity

    return Severity.WARNING


def install(registry: OperationRegistry, policies: PolicyRegistry) -> None:
    for short in REQUIRED_KINDS:
        name = f"requirements.{short}"
        policies.register(registry, name, 1, RequirementsPolicy(name=f"{name}.v1", short_name=short))
