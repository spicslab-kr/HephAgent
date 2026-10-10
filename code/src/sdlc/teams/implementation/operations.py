"""Implementation operations (handoff docs/04 s5).

| Operation                        | How it runs                | Must produce                                   |
|----------------------------------|----------------------------|------------------------------------------------|
| implementation.plan_changes      | Brain                      | `change_plan` Markdown                         |
| implementation.code              | CodingBackend in workspace | candidate snapshot + patch (runtime-captured)  |
| implementation.developer_tests   | CodingBackend in workspace | candidate snapshot + patch with tests          |
| implementation.check             | ProcessRunner recipes      | `execution_record` (collector) + `check_report`|
| implementation.review_changes    | Brain (reads the patch)    | `code_review` Markdown                         |

``prepare_workspace`` and ``integrate`` are runtime steps performed by the scheduler
(workspace preparation before a coding job; the integration workspace is the team's
latest accepted candidate). The readable `implementation_document` comes from
``implementation.prepare_handoff`` (common policy).

Coding operations require ``scoped_repository``; ``check`` requires
``isolated_execution``. Both are satisfied only by a bound coding/execution connection
under the project's isolation mode: ``development`` (labeled nonconforming) today,
``strict`` once an isolation launcher exists.
"""

from __future__ import annotations

from dataclasses import dataclass

from sdlc.application.views import TeamView
from sdlc.domain.models import Finding, Task, WorkerProposal
from sdlc.operations import OperationRegistry
from sdlc.teams.policies import BasePolicy, PolicyRegistry, finding
from sdlc.verification.outputs import check_candidates, require_kinds

REQUIRED_KINDS: dict[str, tuple[str, ...]] = {
    "plan_changes": ("change_plan",),
    "code": (),  # the runtime captures the workspace into a snapshot + patch
    "developer_tests": (),
    "check": (),  # the collector writes execution records; the brain is not involved
    "review_changes": ("code_review",),
}


@dataclass(frozen=True, slots=True)
class ImplementationPolicy(BasePolicy):
    short_name: str = ""

    def check(self, task: Task, view: TeamView) -> list[Finding]:
        findings: list[Finding] = []
        if not task.input_deliveries:
            findings.append(finding("missing_input", "implementation operations need the design handoff delivered to this team"))
        kinds = {view.authorize(r, list(task.input_deliveries)).artifact.kind for r in task.input_refs} if task.input_deliveries else set()
        if self.short_name in ("code", "developer_tests", "plan_changes") and "design" not in kinds and "design_document" not in kinds:
            findings.append(finding("missing_input", f"{self.short_name} needs the design (DesignSet or DESIGN.md) pinned in input_refs"))
        if self.short_name in ("check", "review_changes") and "source_snapshot" not in kinds and not any(a.kind == "source_snapshot" for a in view.list_owned()):
            findings.append(finding("missing_input", f"{self.short_name} needs a candidate source_snapshot (run implementation.code first)"))
        return findings

    def required_kinds(self, task: Task) -> tuple[str, ...]:
        return REQUIRED_KINDS[self.short_name]

    def check_outputs(self, task: Task, proposal: WorkerProposal, view: TeamView | None = None) -> list[Finding]:
        candidates = list(proposal.artifact_candidates)
        findings = check_candidates(candidates) + require_kinds(candidates, REQUIRED_KINDS[self.short_name])
        for candidate in candidates:
            if candidate.kind in ("execution_record", "test_report"):
                findings.append(finding("fabricated_evidence", "only the execution collector records test runs"))
        return findings


def install(registry: OperationRegistry, policies: PolicyRegistry) -> None:
    for short in REQUIRED_KINDS:
        name = f"implementation.{short}"
        policies.register(registry, name, 1, ImplementationPolicy(name=f"{name}.v1", short_name=short))
