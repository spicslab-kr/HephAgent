"""Planning operations: prerequisites and output obligations (handoff docs/04 s2).

| Operation                    | Must produce                                  |
|------------------------------|-----------------------------------------------|
| planning.frame_problem       | `problem_frame` Markdown                      |
| planning.research_context    | `research_summary` Markdown citing evidence   |
| planning.assess_feasibility  | `feasibility` Markdown                        |
| planning.plan_delivery       | `delivery_plan` Markdown                      |
| planning.assess_risks        | `risk_register` Markdown                      |

The readable `project_plan` is produced by ``planning.prepare_handoff`` (common policy).
Research evidence reaching the model is retrieved by the runtime through the team's
research connection and registered as ``research_evidence`` artifacts; the model only
synthesises and must cite evidence ids.
"""

from __future__ import annotations

from dataclasses import dataclass

from sdlc.application.views import TeamView
from sdlc.domain.models import Finding, Task, WorkerProposal
from sdlc.operations import OperationRegistry
from sdlc.teams.policies import BasePolicy, PolicyRegistry, candidates_of_kind, finding
from sdlc.verification.outputs import check_candidates, require_kinds

REQUIRED_KINDS: dict[str, tuple[str, ...]] = {
    "frame_problem": ("problem_frame",),
    "research_context": ("research_summary",),
    "assess_feasibility": ("feasibility",),
    "plan_delivery": ("delivery_plan",),
    "assess_risks": ("risk_register",),
}


@dataclass(frozen=True, slots=True)
class PlanningPolicy(BasePolicy):
    short_name: str = ""

    def check(self, task: Task, view: TeamView) -> list[Finding]:
        if not task.input_deliveries:
            return [finding("missing_input", "planning operations need the participant brief delivered to the planning team")]
        return []

    def required_kinds(self, task: Task) -> tuple[str, ...]:
        return REQUIRED_KINDS[self.short_name]

    def check_outputs(self, task: Task, proposal: WorkerProposal, view: TeamView | None = None) -> list[Finding]:
        findings = check_candidates(list(proposal.artifact_candidates))
        findings += require_kinds(list(proposal.artifact_candidates), REQUIRED_KINDS[self.short_name])
        if self.short_name == "research_context":
            for candidate in candidates_of_kind(proposal, "research_summary"):
                text = candidate.content_text or ""
                if "evidence-" not in text and "synthetic" not in text.lower():
                    findings.append(finding("uncited_research", "research_summary must cite evidence ids or label role simulation as synthetic"))
        return findings


def install(registry: OperationRegistry, policies: PolicyRegistry) -> None:
    for short in REQUIRED_KINDS:
        name = f"planning.{short}"
        policies.register(registry, name, 1, PlanningPolicy(name=f"{name}.v1", short_name=short))
