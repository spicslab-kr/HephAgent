"""Stage workflow use cases (handoff docs/07 s4): propose a plan, approve it, request
diagrams, revise artifacts as a participant.

``propose_stage_plan`` creates the leader's ``TEAM.plan_work`` task; when it completes,
its ``task_graph`` artifact is a *proposal*. ``approve_plan`` validates that proposal
through the ordinary graph admission and records the decision; only then do tasks
exist. ``request_diagram`` creates a ``TEAM.request_diagram`` task bound to exact source
references. ``revise_artifact`` lets a participant import an edited version as a new
revision with participant provenance; nothing is overwritten.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from sdlc.application.artifacts import ArtifactService
from sdlc.application.handoffs import Actor, HandoffService
from sdlc.application.projects import ProjectContext
from sdlc.application.views import TeamView
from sdlc.artifacts.diagrams import NOTATION, Recommendation, recommend, render_status, validate_source
from sdlc.domain.errors import Conflict, DeniedScope, InvalidSchema, MissingInput
from sdlc.domain.models import Artifact, ArtifactRef, DiagramRequest, Origin, OwnerTeam, Task, TaskLimits, TeamId
from sdlc.runtime.scheduler import Scheduler
from sdlc.storage import repositories as repo
from sdlc.verification.canonical import load_json_strict, strip_code_fence
from sdlc.verification.schemas import validate_record

DEFAULT_LIMITS = TaskLimits(max_attempts=3, timeout_seconds=900, max_output_tokens=8192, max_cost_microunits=0)


@dataclass(frozen=True, slots=True)
class ProposalRef:
    task_id: str
    team: TeamId


class WorkflowService:
    def __init__(self, context: ProjectContext, scheduler: Scheduler) -> None:
        self.ctx = context
        self.scheduler = scheduler
        self.handoffs = HandoffService(context)
        self.artifacts = ArtifactService(context)

    # ------------------------------------------------------------------ plans
    def propose_stage_plan(self, team: TeamId, *, objective: str, cycle_id: str = "cycle-001", profile: str | None = None, task_id: str | None = None) -> ProposalRef:
        """Create the leader task that will propose this stage's task graph."""
        view = TeamView(self.ctx, team)
        inbox = view.list_inbox()
        if not inbox:
            raise MissingInput("the team has no accepted delivery to plan from", team=team.value)
        # Plan from the *current* package of every upstream team (the inbox is chronological,
        # so the last accepted delivery per source wins) plus every participant memo; a
        # superseded document is never offered to the leader even if an older grant covers it.
        from sdlc.application.artifacts import ArtifactService

        latest_per_source: dict[str, dict] = {}
        participant_memos: list[dict] = []
        for d in inbox:
            (participant_memos.append(d) if d["from"] == "participant" else latest_per_source.__setitem__(d["from"], d))
        deliveries = [d["delivery_id"] for d in list(latest_per_source.values()) + participant_memos]
        artifacts = ArtifactService(self.ctx)
        refs = [ArtifactRef(**ref.to_json_dict()) for key, ref in self._granted_refs(view, deliveries).items()]
        refs = [ref for ref in refs if artifacts.status(ref) not in ("superseded", "stale")]
        task = Task(
            schema_version="1.0",
            project_id=self.ctx.project_id,
            cycle_id=cycle_id,
            task_id=task_id or f"{team.value}-plan-{self.ctx.ids('plan').rsplit('-', 1)[-1]}",
            team_id=team,
            agent_instance_id=f"{team.value}-leader",
            operation=f"{team.value}.plan_work",
            operation_version=1,
            input_deliveries=deliveries,
            input_refs=refs,
            depends_on=[],
            # The leader's planning context (inbox ids, available operations, task template)
            # is supplied by the runtime as a document at dispatch; see teams/common.py.
            parameters={"objective": objective, "scope_ids": [], "requested_artifact_kinds": ["task_graph"], "diagram_request": None},
            expected_output_schema="#/$defs/WorkerProposal",
            backend_profile=profile or self._brain_profile(team),
            workspace_ref=None,
            limits=DEFAULT_LIMITS,
        )
        self.scheduler.graphs.admit(team, [task])
        return ProposalRef(task_id=task.task_id, team=team)

    def preview_plan(self, plan_task_id: str) -> list[dict[str, Any]]:
        """The task graph a completed plan_work task proposed, as readable rows (no admission)."""
        row = self.scheduler.graphs.task_row(plan_task_id)
        if row["state"] != "completed":
            return []
        result = self.ctx.db.one("SELECT result_json FROM task_results WHERE project_id=? AND task_id=? ORDER BY created_at DESC LIMIT 1", (self.ctx.project_id, plan_task_id))
        refs = [ArtifactRef.model_validate(r) for r in json.loads(result["result_json"])["artifact_refs"]]
        graph = next((a for a in (self.artifacts.get(r) for r in refs) if a.kind == "task_graph"), None)
        if graph is None:
            return []
        raw = load_json_strict(strip_code_fence(self.artifacts.read_bytes(graph.ref).decode("utf-8")))
        items = raw["tasks"] if isinstance(raw, dict) else raw
        return [
            {"task_id": t.get("task_id"), "operation": t.get("operation"), "depends_on": t.get("depends_on", []), "objective": (t.get("parameters") or {}).get("objective", "")}
            for t in items
        ]

    def approve_plan(self, plan_task_id: str, actor: Actor, *, rationale: str = "plan approved") -> dict[str, Any]:
        """Admit the task graph proposed by a completed plan_work task, under a recorded decision."""
        row = self.scheduler.graphs.task_row(plan_task_id)
        if row["state"] != "completed":
            raise Conflict("plan task has not completed", task_id=plan_task_id, state=row["state"])
        result = self.ctx.db.one("SELECT result_json FROM task_results WHERE project_id=? AND task_id=? ORDER BY created_at DESC LIMIT 1", (self.ctx.project_id, plan_task_id))
        refs = [ArtifactRef.model_validate(r) for r in json.loads(result["result_json"])["artifact_refs"]]
        graph_artifact = next((a for a in (self.artifacts.get(r) for r in refs) if a.kind == "task_graph"), None)
        if graph_artifact is None:
            raise MissingInput("plan task produced no task_graph artifact", task_id=plan_task_id)
        raw = load_json_strict(strip_code_fence(self.artifacts.read_bytes(graph_artifact.ref).decode("utf-8")))
        items = raw["tasks"] if isinstance(raw, dict) else raw
        team = TeamId(row["team_id"])
        view = TeamView(self.ctx, team)
        tasks: list[Task] = []
        for item in items:
            item = dict(item)
            item.setdefault("schema_version", "1.0")
            item.setdefault("project_id", self.ctx.project_id)
            item.setdefault("expected_output_schema", "#/$defs/WorkerProposal")
            item.setdefault("workspace_ref", None)
            item.setdefault("limits", DEFAULT_LIMITS.to_json_dict())
            item.setdefault("backend_profile", self._brain_profile(team))
            if not item.get("input_refs") and item.get("input_deliveries"):
                # Freeze the exact granted inputs now (docs/07 s2: bind inputs before the first attempt).
                item["input_refs"] = [ref.to_json_dict() for ref in self._granted_refs(view, list(item["input_deliveries"])).values()]
            tasks.append(validate_record(item, "Task"))  # type: ignore[arg-type]
        self.scheduler.graphs.validate(team, tasks)  # fail before recording a decision
        decision_id = self.handoffs.record_decision(
            actor, scope="task_graph", refs={"plan_task": plan_task_id, "graph_artifact": graph_artifact.ref.to_json_dict(), "tasks": [t.task_id for t in tasks]},
            rationale=rationale, disposition="approve", team=team,
        )
        admitted = self.scheduler.graphs.admit(team, tasks)
        return {"decision_id": decision_id, "team": team.value, "task_graph_revision": admitted.task_graph_revision, "tasks": list(admitted.task_ids)}

    # ------------------------------------------------------------------ diagrams
    def recommend_diagrams(self, team: TeamId, ref: ArtifactRef, input_deliveries: list[str]) -> list[Recommendation]:
        text = TeamView(self.ctx, team).read_text(ref, input_deliveries)
        return recommend(team, text)

    def request_diagram(self, request: DiagramRequest, *, input_deliveries: list[str], cycle_id: str = "cycle-001", profile: str | None = None) -> str:
        """Create a TEAM.request_diagram task; source refs must be readable by the team."""
        view = TeamView(self.ctx, request.team_id)
        for ref in request.source_refs:
            view.authorize(ref, input_deliveries)
        media_type, renderer = NOTATION[request.diagram_type]
        task_id = f"{request.team_id.value}-diagram-{self.ctx.ids('diagram').rsplit('-', 1)[-1]}"
        task = Task(
            schema_version="1.0",
            project_id=self.ctx.project_id,
            cycle_id=cycle_id,
            task_id=task_id,
            team_id=request.team_id,
            agent_instance_id=f"{request.team_id.value}-diagram-worker",
            operation=f"{request.team_id.value}.request_diagram",
            operation_version=1,
            input_deliveries=input_deliveries,
            input_refs=list(request.source_refs),
            depends_on=[],
            parameters={
                "objective": request.purpose,
                "scope_ids": list(request.scope_ids),
                "requested_artifact_kinds": ["diagram_source"],
                "diagram_request": request.to_json_dict(),
            },
            expected_output_schema="#/$defs/WorkerProposal",
            backend_profile=profile or self._brain_profile(request.team_id),
            workspace_ref=None,
            limits=DEFAULT_LIMITS,
        )
        self.scheduler.graphs.admit(request.team_id, [task])
        return task_id

    def diagram_status(self, ref: ArtifactRef) -> dict[str, Any]:
        artifact = self.artifacts.get(ref)
        source = self.artifacts.read_bytes(ref).decode("utf-8")
        return {
            "ref": ref.to_json_dict(),
            "media_type": artifact.media_type,
            "origin": artifact.origin.value,
            "syntax_errors": validate_source(source, artifact.media_type),
            **render_status(artifact.media_type, None),
        }

    # ------------------------------------------------------------------ participant edits
    def revise_artifact(self, ref: ArtifactRef, data: bytes, *, actor_id: str, note: str) -> Artifact:
        """A participant's edited copy becomes the next revision; the original stays intact."""
        original = self.artifacts.get(ref)
        if original.owner_team == OwnerTeam.PARTICIPANT:
            raise DeniedScope("revise a team artifact; participant imports are re-imported instead")
        if original.kind == "diagram_source":
            errors = validate_source(data.decode("utf-8"), original.media_type)
            if errors:
                raise InvalidSchema("edited diagram source is not valid", errors=errors)
        revised = self.artifacts.register(
            owner=original.owner_team,
            artifact_id=original.artifact_id,
            kind=original.kind,
            media_type=original.media_type,
            relative_path=original.relative_path,
            data=data,
            origin=Origin.PARTICIPANT,
            depends_on=[ref],
            created_by_task=None,
        )
        self.artifacts.set_status(ref, "superseded", reason=f"participant edit by {actor_id}: {note}")
        with self.ctx.db.transaction():
            repo.insert_event(
                self.ctx.db, self.ctx.project_id, self.ctx.ids("event"), "artifact.revised",
                {"from": ref.to_json_dict(), "to": revised.ref.to_json_dict(), "actor_id": actor_id, "note": note, "reconciliation_required": True},
                self.ctx.clock(), team_id=original.owner_team.value,
            )
        return revised

    # ------------------------------------------------------------------ helpers
    def _brain_profile(self, team: TeamId) -> str:
        from sdlc.domain.teams import Team

        return self.ctx.config.teams[Team(team.value)].brain

    @staticmethod
    def _granted_refs(view: TeamView, deliveries: list[str]) -> dict:
        grants = view.grants(deliveries)
        return {key: ArtifactRef(artifact_id=key[0], revision=key[1], sha256=key[2]) for key in grants}
