"""Task-graph validation and persistence (handoff docs/06 s4, docs/07 s2).

A leader proposes a list of ``Task`` records for its own team. The runtime admits the
graph only if every task:

* belongs to the proposing team and runs a registered, enabled operation;
* has valid parameters for that operation;
* depends only on tasks of the same team (inside the proposal or already persisted);
* declares inputs that its accepted deliveries actually grant;
* uses a backend profile that exists in the configuration;

and the whole graph is acyclic and within the fan-out bound. Admission freezes the task
intent (``task_json``) and the input manifest digest; nothing mutates a task later.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sdlc.application.projects import ProjectContext
from sdlc.application.views import TeamView
from sdlc.domain.errors import DeniedScope, InvalidSchema, MissingInput
from sdlc.domain.models import Task, TeamId
from sdlc.domain.states import TaskState
from sdlc.domain.teams import Team
from sdlc.operations import OperationRegistry, default_registry
from sdlc.storage import repositories as repo
from sdlc.verification.canonical import canonical_bytes, digest
from sdlc.verification.fixtures import assert_dag

MAX_TASKS_PER_GRAPH = 50


@dataclass(frozen=True, slots=True)
class AdmittedGraph:
    team: TeamId
    task_graph_revision: int
    task_ids: tuple[str, ...]


def input_manifest_digest(task: Task) -> str:
    return digest({"input_deliveries": list(task.input_deliveries), "input_refs": [r.to_json_dict() for r in task.input_refs]})


class TaskGraphService:
    def __init__(self, context: ProjectContext, registry: OperationRegistry | None = None) -> None:
        self.ctx = context
        self.registry = registry or default_registry()

    # ------------------------------------------------------------------ validation
    def validate(self, team: TeamId, tasks: list[Task]) -> None:
        if not tasks:
            raise InvalidSchema("a task graph needs at least one task")
        if len(tasks) > MAX_TASKS_PER_GRAPH:
            raise InvalidSchema("task graph exceeds fan-out bound", limit=MAX_TASKS_PER_GRAPH, found=len(tasks))
        ids = [t.task_id for t in tasks]
        if len(ids) != len(set(ids)):
            raise InvalidSchema("duplicate task id in proposal")
        existing_same_team = {
            row["task_id"]: row["state"]
            for row in self.ctx.db.all(
                "SELECT task_id, state FROM tasks WHERE project_id=? AND team_id=?", (self.ctx.project_id, team.value)
            )
        }
        for task_id in ids:
            if task_id in existing_same_team:
                raise InvalidSchema("task id already exists", task_id=task_id)
        view = TeamView(self.ctx, team)
        graph: dict[str, list[str]] = {}
        for task in tasks:
            if task.project_id != self.ctx.project_id:
                raise DeniedScope("task project differs from the open project", task_id=task.task_id)
            if task.team_id != team:
                raise DeniedScope("a leader may only propose tasks for its own team", task_id=task.task_id, team=task.team_id.value)
            operation = self.registry.resolve_for_task(Team(team.value), task.operation, task.operation_version, task.parameters)
            if not operation.enabled:
                raise InvalidSchema(
                    "operation is registered but its prerequisite policy is not implemented yet",
                    operation=task.operation,
                    policy=operation.prerequisite_policy,
                )
            if task.expected_output_schema not in (operation.proposal_schema, operation.proposal_schema.removeprefix("#/$defs/")):
                raise InvalidSchema("expected_output_schema differs from the operation's proposal schema", task_id=task.task_id)
            if task.backend_profile not in self.ctx.config.connections:
                raise MissingInput("backend profile is not a configured connection", task_id=task.task_id, profile=task.backend_profile)
            for dependency in task.depends_on:
                if dependency not in ids and dependency not in existing_same_team:
                    raise DeniedScope("task dependency is missing or belongs to another team", task_id=task.task_id, dependency=dependency)
            granted = view.grants(list(task.input_deliveries))  # raises DeniedScope for unaccepted/foreign deliveries
            for ref in task.input_refs:
                artifact = repo.get_artifact_by_ref(self.ctx.db, self.ctx.project_id, ref)
                same_team_output = artifact is not None and artifact.owner_team.value == team.value
                if ref.key not in granted and not same_team_output:
                    raise DeniedScope("task input is not granted by its declared deliveries", task_id=task.task_id, ref=ref.to_json_dict())
            graph[task.task_id] = [d for d in task.depends_on if d in ids]
        assert_dag(graph, "task graph")

    # ------------------------------------------------------------------ admission
    def admit(self, team: TeamId, tasks: list[Task]) -> AdmittedGraph:
        self.validate(team, tasks)
        now = self.ctx.clock()
        with self.ctx.db.transaction():
            row = self.ctx.db.one(
                "SELECT COALESCE(MAX(task_graph_revision), 0) AS n FROM tasks WHERE project_id=? AND team_id=?",
                (self.ctx.project_id, team.value),
            )
            revision = int(row["n"]) + 1
            for task in tasks:
                self.ctx.db.execute(
                    "INSERT INTO tasks(project_id, task_id, cycle_id, team_id, backend_profile, state, revision, task_graph_revision,"
                    " priority, input_manifest_sha256, input_deliveries_json, input_refs_json, task_json, successor_of_task_id,"
                    " next_attempt_at, retry_count, created_at, updated_at) VALUES (?,?,?,?,?,'pending',1,?,0,?,?,?,?,NULL,NULL,0,?,?)",
                    (
                        self.ctx.project_id,
                        task.task_id,
                        task.cycle_id,
                        team.value,
                        task.backend_profile,
                        revision,
                        input_manifest_digest(task),
                        canonical_bytes(list(task.input_deliveries)).decode(),
                        canonical_bytes([r.to_json_dict() for r in task.input_refs]).decode(),
                        canonical_bytes(task.to_json_dict()).decode(),
                        now,
                        now,
                    ),
                )
                for dependency in task.depends_on:
                    self.ctx.db.execute(
                        "INSERT INTO task_dependencies(project_id, task_id, requires_task_id) VALUES (?,?,?)",
                        (self.ctx.project_id, task.task_id, dependency),
                    )
                repo.insert_event(
                    self.ctx.db,
                    self.ctx.project_id,
                    self.ctx.ids("event"),
                    "task.created",
                    {"task": task.to_json_dict(), "task_graph_revision": revision},
                    now,
                    team_id=team.value,
                    task_id=task.task_id,
                )
        return AdmittedGraph(team=team, task_graph_revision=revision, task_ids=tuple(t.task_id for t in tasks))

    # ------------------------------------------------------------------ queries
    def load_task(self, task_id: str) -> Task:
        row = self.ctx.db.one("SELECT task_json FROM tasks WHERE project_id=? AND task_id=?", (self.ctx.project_id, task_id))
        if row is None:
            raise MissingInput("unknown task", task_id=task_id)
        import json

        return Task.model_validate(json.loads(row["task_json"]))

    def task_row(self, task_id: str) -> dict[str, Any]:
        row = self.ctx.db.one("SELECT * FROM tasks WHERE project_id=? AND task_id=?", (self.ctx.project_id, task_id))
        if row is None:
            raise MissingInput("unknown task", task_id=task_id)
        return dict(row)

    def list_tasks(self, team: TeamId | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM tasks WHERE project_id=?"
        params: tuple = (self.ctx.project_id,)
        if team is not None:
            sql += " AND team_id=?"
            params += (team.value,)
        return [dict(r) for r in self.ctx.db.all(sql + " ORDER BY created_at, task_id", params)]

    def dependency_outputs(self, task_id: str) -> list[ArtifactRef]:
        """Artifacts produced by this task's completed same-team dependencies (transitively).

        Same-team reuse (handoff docs/07 s2): outputs unknown at planning time are bound
        when the successor starts. This is not cross-team access.
        """
        import json

        from sdlc.domain.models import ArtifactRef as _Ref

        # Transitive closure of same-team dependencies: an assessment that depends on the
        # analysis still needs the execution records the analysis was based on.
        deps: list[str] = []
        frontier_deps = [task_id]
        while frontier_deps:
            rows_d = self.ctx.db.all(
                "SELECT task_id, requires_task_id FROM task_dependencies WHERE project_id=? AND task_id IN (%s) ORDER BY requires_task_id" % ",".join("?" * len(frontier_deps)),
                (self.ctx.project_id, *frontier_deps),
            )
            frontier_deps = [r["requires_task_id"] for r in rows_d if r["requires_task_id"] not in deps]
            deps.extend(frontier_deps)
        refs: list[ArtifactRef] = []
        for dep in deps:
            # A rerun creates a successor (successor_of_task_id); the newest completed task in
            # that chain stands for the dependency, so a corrected check feeds the review.
            chain, frontier = [dep], [dep]
            while frontier:
                nxt = [r["task_id"] for r in self.ctx.db.all(
                    "SELECT task_id FROM tasks WHERE project_id=? AND successor_of_task_id IN (%s)" % ",".join("?" * len(frontier)), (self.ctx.project_id, *frontier)
                )]
                chain.extend(nxt)
                frontier = nxt
            rows = self.ctx.db.all(
                "SELECT r.result_json FROM task_results r WHERE r.project_id=? AND r.task_id IN (%s) AND r.outcome='completed' ORDER BY r.created_at" % ",".join("?" * len(chain)),
                (self.ctx.project_id, *chain),
            )
            if rows:
                refs.extend(_Ref.model_validate(r) for r in json.loads(rows[-1]["result_json"])["artifact_refs"])
        return refs

    def dependencies_completed(self, task_id: str) -> bool:
        rows = self.ctx.db.all(
            "SELECT t.state FROM task_dependencies d JOIN tasks t ON t.project_id=d.project_id AND t.task_id=d.requires_task_id"
            " WHERE d.project_id=? AND d.task_id=?",
            (self.ctx.project_id, task_id),
        )
        return all(r["state"] == TaskState.COMPLETED.value for r in rows)
