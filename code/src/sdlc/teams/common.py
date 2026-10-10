"""Lifecycle operations shared by all six teams and the prompt assembly they use.

``TEAM.intake``, ``plan_work``, ``review``, ``prepare_handoff`` and ``request_diagram``
exist for every team (handoff docs/04 s1). ``plan_work`` is how a leader proposes its
team's task graph: the proposal must contain a ``task_graph`` JSON candidate that the
runtime validates and admits separately (see :mod:`sdlc.application.workflow`).
"""

from __future__ import annotations

from dataclasses import dataclass

from sdlc.application.views import TeamView
import re

from sdlc.domain.models import Finding, RequirementsSet, Task, WorkerProposal
from sdlc.domain.teams import TEAM_ORDER
from sdlc.operations import OperationRegistry
from sdlc.teams.policies import BasePolicy, PolicyRegistry, candidates_of_kind, finding
from sdlc.teams.prompts import load_prompt
from sdlc.verification.canonical import load_json_strict, strip_code_fence
from sdlc.verification.outputs import check_candidates, check_diagram_source

COMMON_OPERATIONS = ("intake", "plan_work", "review", "prepare_handoff", "request_diagram")
HANDOFF_DOCUMENT_KINDS = {
    "planning": "project_plan",
    "requirements": "requirements_document",
    "design": "design_document",
    "implementation": "implementation_document",
    "testing": "test_report",
    "maintenance": "change_spec",
}


@dataclass(frozen=True, slots=True)
class CommonLifecyclePolicy(BasePolicy):
    short_name: str = ""
    team: str = ""

    def check(self, task: Task, view: TeamView) -> list[Finding]:
        findings: list[Finding] = []
        if self.short_name in ("intake", "plan_work") and not task.input_deliveries:
            findings.append(finding("missing_input", "declare at least one accepted delivery (the team's inbox is the only cross-team input)"))
        if self.short_name in ("review", "prepare_handoff") and not task.input_refs and not view.list_owned():
            findings.append(finding("missing_input", "nothing to review: no input refs and no same-team artifacts yet"))
        if self.short_name == "request_diagram" and not task.parameters.get("diagram_request"):
            findings.append(finding("missing_input", "request_diagram needs parameters.diagram_request"))
        return findings

    def context_notes(self, task: Task, view: TeamView) -> list[str]:
        if self.team == "testing" and self.short_name == "plan_work":
            return [
                "Failing developer checks, open reviews or limitations in the delivered package are inputs for independent testing, not reasons to stop: plan the cases and bench that establish the facts. "
                "Report a blocking finding only if no delivered requirements or candidate exist at all.",
                "Order the graph: testing.plan -> testing.derive_cases -> testing.design_bench -> testing.implement_bench -> testing.preflight (collects the bench and maps tests to case ids; never skip it) -> testing.execute -> testing.analyze -> testing.assess_release -> testing.prepare_handoff.",
            ]
        if self.team in ("requirements", "design") and self.short_name == "prepare_handoff":
            known = known_requirement_ids(task, None, view)
            if known:
                return [f"Valid requirement ids, cite only these and never renumber them: {sorted(known)}"]
        return []

    def required_kinds(self, task: Task) -> tuple[str, ...]:
        return {
            "intake": ("input_assessment",),
            "plan_work": ("task_graph",),
            "review": ("review",),
            "prepare_handoff": (HANDOFF_DOCUMENT_KINDS[self.team],),
            "request_diagram": ("diagram_source",),
        }[self.short_name]

    def check_outputs(self, task: Task, proposal: WorkerProposal, view: TeamView | None = None) -> list[Finding]:
        findings = check_candidates(list(proposal.artifact_candidates))
        if self.short_name == "plan_work":
            graphs = candidates_of_kind(proposal, "task_graph")
            if len(graphs) != 1 or graphs[0].content_text is None:
                findings.append(finding("missing_output", "plan_work must return exactly one task_graph JSON candidate"))
            else:
                try:
                    raw = load_json_strict(strip_code_fence(graphs[0].content_text))
                    tasks = raw["tasks"] if isinstance(raw, dict) else raw
                    if not isinstance(tasks, list) or not tasks:
                        findings.append(finding("invalid_record", "task_graph must contain a non-empty list of Task records"))
                except (ValueError, KeyError) as exc:
                    findings.append(finding("invalid_record", f"task_graph is not valid JSON: {exc}"))
        if self.short_name == "prepare_handoff":
            kind = HANDOFF_DOCUMENT_KINDS[self.team]
            if not candidates_of_kind(proposal, kind):
                findings.append(finding("missing_output", f"prepare_handoff must produce the readable {kind} document"))
            if self.team in ("requirements", "design"):
                findings += check_document_ids(proposal, known_requirement_ids(task, proposal, view))
        if self.short_name == "request_diagram":
            sources = candidates_of_kind(proposal, "diagram_source")
            if not sources:
                findings.append(finding("missing_output", "request_diagram must return a diagram_source candidate (or a capability finding)"))
            for source in sources:
                if source.content_text is not None:
                    findings.extend(check_diagram_source(source.content_text, source.media_type))
        return findings


REQ_ID = re.compile(r"\bREQ-[A-Z]*-?\d+\b")


def requirement_ids_in(text: str) -> set[str]:
    return set(REQ_ID.findall(text or ""))


def _ids_of(text: str) -> set[str]:
    try:
        return {r.requirement_id for r in RequirementsSet.model_validate(load_json_strict(strip_code_fence(text))).requirements}
    except ValueError:
        return set()


def known_requirement_ids(task: Task, proposal: WorkerProposal | None, view: TeamView | None) -> set[str]:
    """Ids of the canonical RequirementsSet: from a `requirements` candidate in the proposal,
    else from pinned input refs of kind requirements, else from the team's own latest one."""
    ids: set[str] = set()
    if proposal is not None:
        for candidate in candidates_of_kind(proposal, "requirements"):
            if candidate.content_text:
                ids |= _ids_of(candidate.content_text)
    if ids or view is None:
        return ids
    for ref in task.input_refs:
        try:
            granted = view.authorize(ref, list(task.input_deliveries))
        except Exception:
            continue
        if granted.artifact.kind == "requirements":
            ids |= _ids_of(view.read_text(ref, list(task.input_deliveries)))
    if not ids:
        for artifact in view.list_owned():
            if artifact.kind == "requirements":
                ids |= _ids_of(view.artifacts.read_bytes(artifact.ref).decode("utf-8"))
    return ids


def check_document_ids(proposal: WorkerProposal, known: set[str]) -> list[Finding]:
    """Readable documents may cite only requirement ids that exist in the canonical set."""
    if not known:
        return []
    findings: list[Finding] = []
    for candidate in proposal.artifact_candidates:
        if candidate.content_text is None or "markdown" not in candidate.media_type:
            continue
        unknown = sorted(requirement_ids_in(candidate.content_text) - known)
        if unknown:
            findings.append(finding("unknown_requirement_id", f"{candidate.relative_path} cites requirement ids that are not in the canonical requirements set: {unknown[:10]}; valid ids: {sorted(known)}"))
    return findings


def install_common_policies(registry: OperationRegistry, policies: PolicyRegistry) -> None:
    for team in TEAM_ORDER:
        for short in COMMON_OPERATIONS:
            name = f"{team.value}.{short}"
            policies.register(registry, name, 1, CommonLifecyclePolicy(name=f"{name}.v1", short_name=short, team=team.value))


def leader_context(task: Task, registry: OperationRegistry) -> dict:
    """What a leader needs to write admissible Task records for ``TEAM.plan_work``.

    Supplied by the runtime as a document (the operation's parameter schema is closed).
    The runtime, not the model, decides what is admitted.
    """
    from sdlc.domain.teams import Team

    team = task.team_id.value
    operations = [
        {"operation": op.name, "capabilities": sorted(op.required_capabilities)}
        for op in registry.for_team(Team(team))
        if op.enabled and op.short_name not in ("plan_work", "intake", "review", "request_diagram")
    ]
    return {
        "inbox_delivery_ids": list(task.input_deliveries),
        "available_operations": operations,
        "task_record_template": {
            "task_id": f"{team}-<short-name>",
            "team_id": team,
            "cycle_id": task.cycle_id,
            "agent_instance_id": f"{team}-<role>",
            "operation": f"{team}.<operation>",
            "operation_version": 1,
            "input_deliveries": list(task.input_deliveries),
            "input_refs": [],
            "depends_on": ["<earlier task_id>"],
            "parameters": {"objective": "<one sentence>", "scope_ids": ["<REQ ids when relevant>"], "requested_artifact_kinds": ["<kind>"], "diagram_request": None},
        },
        "rules": [
            f"Use only operations from available_operations; the final task must be {team}.prepare_handoff depending on the others.",
            "Leave input_refs empty: the runtime pins the exact granted artifacts at approval.",
            "Keep the graph small (3 to 6 tasks); depends_on may name only tasks in this graph.",
        ],
    }


def system_prompt(team: str, operation: str) -> str:
    """Editable prompt body from ``prompts/<team>/<op>.md`` with the common preamble."""
    short = operation.split(".", 1)[1]
    body = load_prompt(team, short)
    preamble = load_prompt("common", "preamble").replace("{team}", team).replace("{operation}", operation)
    return preamble + "\n\n" + body
