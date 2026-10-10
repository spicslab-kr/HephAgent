"""Scheduler: fenced attempts, dispatch, completion, error routing and recovery.

Follows the pseudocode of handoff docs/06 s4:

    schedule: recover expired attempts -> for each ready task: CAS pending->running,
              create a fenced attempt, commit, dispatch OUTSIDE the transaction.
    complete: validate the proposal, materialise artifacts, then in ONE transaction
              assert the live fence, append TaskResult + artifacts, CAS task -> completed.

Only this module changes task state. Adapters stream observations; they never decide
completion. Error categories map to states exactly as docs/06 s6 prescribes.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sdlc.adapters.registry import AdapterRegistry
from sdlc.application.artifacts import ArtifactService
from sdlc.application.handoffs import HandoffService
from sdlc.application.projects import ProjectContext
from sdlc.application.views import TeamView
from sdlc.common import new_id
from sdlc.config import Readiness
from sdlc.domain.errors import Conflict, DeniedScope, InvalidSchema, ServiceError, StaleRevision
from sdlc.domain.identities import AgentPrincipal
from sdlc.domain.models import (
    ArtifactRef,
    SideEffect,
    SideEffectOutcome,
    BackendEvent,
    Checkpoint,
    CompletionCertainty,
    ErrorCategory,
    EventKind,
    Finding,
    NormalizedError,
    Origin,
    Outcome,
    OwnerTeam,
    Severity,
    Task,
    TaskResult,
    TeamId,
    Usage,
    WorkerProposal,
)
from sdlc.domain.states import TERMINAL_STATES, TaskState, assert_transition
from sdlc.domain.teams import Team
from sdlc.operations import Operation, OperationRegistry
from sdlc.ports.brain import BrainReply, BrainRequest, ContextDocument
from sdlc.runtime.graphs import TaskGraphService
from sdlc.runtime.workspaces import WorkspaceManager
from sdlc.ports.coding import CodingJob
from sdlc.ports.execution import ExecutionRequest
from sdlc.domain.identities import SessionOwnershipKey
from sdlc.runtime.leases import Lease, LeaseManager
from sdlc.runtime.quotas import QuotaRegistry
from sdlc.storage import repositories as repo
from sdlc.ports.research import ResearchRequest
from sdlc.teams import design as design_team, testing as testing_team, maintenance as maintenance_team
from sdlc.teams import implementation as implementation_team
from sdlc.teams import planning as planning_team
from sdlc.teams import requirements as requirements_team
from sdlc.teams.common import install_common_policies, leader_context, system_prompt
from sdlc.teams.policies import PolicyRegistry
from sdlc.verification.canonical import canonical_bytes, digest
from sdlc.verification.schemas import load_schema, validate_record

ATTEMPT_LEASE_SECONDS = 120
REPAIR_ATTEMPTS = 1  # bounded schema repair for invalid_output (docs/06 s6)


@dataclass(frozen=True, slots=True)
class TaskOutcome:
    task_id: str
    state: TaskState
    attempt_id: str | None
    reason: str | None = None
    result: TaskResult | None = None
    artifacts: tuple[ArtifactRef, ...] = ()


@dataclass
class _Attempt:
    attempt_id: str
    attempt_number: int
    lease: Lease
    quota_scope_id: str
    connection_name: str
    task_revision: int
    #: Filled by coding/check dispatch: artifacts the runtime produced for the result.
    extra_refs: list = field(default_factory=list)
    extra_findings: list = field(default_factory=list)
    candidate_ref: object = None
    patch_ref: object = None
    backend_session_id: str | None = None
    side_effects_unknown: bool = False
    workspace_id: str | None = None


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _format(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


class Scheduler:
    def __init__(
        self,
        context: ProjectContext,
        adapters: AdapterRegistry,
        *,
        registry: OperationRegistry | None = None,
        owner_id: str | None = None,
    ) -> None:
        self.ctx = context
        self.adapters = adapters
        self.registry = registry or OperationRegistry.from_contract()
        self.policies = PolicyRegistry()
        install_common_policies(self.registry, self.policies)
        planning_team.install(self.registry, self.policies)
        requirements_team.install(self.registry, self.policies)
        design_team.install(self.registry, self.policies)
        implementation_team.install(self.registry, self.policies)
        testing_team.install(self.registry, self.policies)
        maintenance_team.install(self.registry, self.policies)
        self.graphs = TaskGraphService(context, self.registry)
        self.owner_id = owner_id or new_id("coordinator")
        self.leases = LeaseManager(context, self.owner_id)
        self.quotas = QuotaRegistry(context)
        self.artifacts = ArtifactService(context)

    # ------------------------------------------------------------------ readiness
    def ready(self, team: TeamId | None = None) -> list[dict[str, Any]]:
        """Pending tasks whose dependencies completed; elapsed ``retry_wait`` tasks are promoted."""
        now = self.ctx.clock()
        self._promote_elapsed_retries(now)
        rows = self.graphs.list_tasks(team)
        ready = []
        for row in rows:
            if row["state"] != TaskState.PENDING.value:
                continue
            if row["next_attempt_at"] and row["next_attempt_at"] > now:
                continue
            if not self.graphs.dependencies_completed(row["task_id"]):
                continue
            ready.append(row)
        ready.sort(key=lambda r: (-r["priority"], r["created_at"], r["task_id"]))
        return ready

    def _promote_elapsed_retries(self, now: str) -> None:
        rows = self.ctx.db.all(
            "SELECT task_id, revision FROM tasks WHERE project_id=? AND state='retry_wait' AND next_attempt_at<=?",
            (self.ctx.project_id, now),
        )
        for row in rows:
            self._transition(row["task_id"], TaskState.RETRY_WAIT, TaskState.PENDING, row["revision"], "backoff elapsed")

    # ------------------------------------------------------------------ run
    async def run_ready(self, team: TeamId | None = None) -> list[TaskOutcome]:
        """Run tasks as they become ready, until a pass dispatches nothing new.

        Tasks left pending by a quota pause are reported once and not retried in the
        same call; a retry_wait task waits for its persisted time.
        """
        self.recover()
        outcomes: list[TaskOutcome] = []
        seen: set[str] = set()
        while True:
            progressed = False
            for row in self.ready(team):
                if row["task_id"] in seen:
                    continue
                seen.add(row["task_id"])
                outcome = await self.run_task(row["task_id"])
                outcomes.append(outcome)
                progressed = progressed or outcome.state == TaskState.COMPLETED
            if not progressed:
                return outcomes

    async def run_task(self, task_id: str) -> TaskOutcome:
        task = self.graphs.load_task(task_id)
        row = self.graphs.task_row(task_id)
        if row["state"] != TaskState.PENDING.value:
            raise Conflict("task is not pending", task_id=task_id, state=row["state"])
        operation = self.registry.get(task.operation, task.operation_version)
        connection_name = task.backend_profile
        connection = self.ctx.config.connections[connection_name]

        # --- preflight: no model call, no attempt, no lease until everything is admissible
        pause = self.quotas.is_paused(connection.quota_scope_id)
        if pause is not None:
            return TaskOutcome(task_id, TaskState.PENDING, None, reason=f"quota scope {pause.quota_scope_id!r} is paused: {pause.message}")
        blocked = self._capability_findings(operation, connection_name)
        if not blocked:
            policy = self.policies.get(operation.prerequisite_policy)
            if policy is None:
                blocked = [_finding("capability_missing", f"no prerequisite policy for {operation.name}")]
            else:
                blocked = [f for f in policy.check(task, TeamView(self.ctx, task.team_id)) if f.severity == Severity.BLOCKING]
        if blocked:
            self._block(task, row["revision"], blocked)
            return TaskOutcome(task_id, TaskState.NEEDS_INPUT, None, reason="; ".join(f.message for f in blocked))

        attempt = self._start_attempt(task, row, connection_name, connection.quota_scope_id)
        try:
            if "scoped_repository" in operation.required_capabilities:
                reply = await self._dispatch_coding(task, operation, attempt)
            elif "isolated_execution" in operation.required_capabilities:
                reply = await self._dispatch_check(task, operation, attempt)
            else:
                reply = await self._dispatch(task, operation, attempt)
        except ServiceError as exc:
            reply = BrainReply(structured=None, text=None, error=_unknown_error(f"{exc.code}: {exc.message}"), usage=_unknown_usage())
        except Exception as exc:  # adapter bug or transport failure: never lose the attempt record
            reply = BrainReply(structured=None, text=None, error=_unknown_error(f"adapter raised {type(exc).__name__}: {exc}"), usage=_unknown_usage())
        return self._finalize(task, operation, attempt, reply)

    # ------------------------------------------------------------------ preflight helpers
    def _capability_findings(self, operation: Operation, connection_name: str) -> list[Finding]:
        connection = self.ctx.config.connections[connection_name]
        if connection.readiness != Readiness.READY:
            from sdlc.application.connections import ConnectionService

            allowed, why = ConnectionService(self.ctx).dispatchable(connection_name)
            if not allowed:
                return [
                    _finding(
                        "capability_missing",
                        f"connection {connection_name!r} is {connection.readiness.value}: {why}",
                    )
                ]
        try:
            description = self.adapters.brain(connection_name).describe()
        except ServiceError as exc:
            return [_finding("capability_missing", exc.message)]
        missing = [c for c in sorted(operation.required_capabilities) if not description.supports(c)]
        team = Team(operation.owner_team.value)
        for capability, role in (("scoped_repository", "coding"), ("isolated_execution", "execution")):
            if capability not in missing:
                continue
            if self.ctx.config.runtime.isolation == "strict":
                from sdlc.runtime.launcher import usable_profile

                profile, why = usable_profile(self.ctx)
                if role == "coding":
                    return [_finding("capability_missing", f"{operation.name} needs {capability} under strict isolation: coding clients are not launched inside an isolation profile yet "
                                     f"(an image with the client and its login would be required). Run coding in the explicitly nonconforming development mode: sdlc isolation {self.ctx.project_id} development")]
                if profile is None:
                    return [_finding("capability_missing", f"{operation.name} needs {capability} under strict isolation: {why}. "
                                     f"Or run in the explicitly nonconforming development mode: sdlc isolation {self.ctx.project_id} development")]
            bound = getattr(self.ctx.config.teams[team], role)
            if bound is None:
                return [_finding("capability_missing", f"{operation.name} needs {capability}: bind a {role} connection for {team.value} (sdlc brain ... / sdlc connection bind --role {role})")]
            try:
                adapter = self.adapters.coding(bound) if role == "coding" else self.adapters.runner(bound)
            except ServiceError as exc:
                return [_finding("capability_missing", exc.message)]
            if role == "coding" and not adapter.describe().supports(capability):
                return [_finding("capability_missing", f"connection {bound!r} cannot do repository work")]
            if role == "coding":
                from sdlc.application.connections import ConnectionService

                allowed, why = ConnectionService(self.ctx).dispatchable(bound)
                if not allowed:
                    return [_finding("capability_missing", f"coding connection {bound!r}: {why}")]
            missing.remove(capability)
        if "research_or_imported_evidence" in missing:
            research_name = self.ctx.config.teams[Team(operation.owner_team.value)].research
            if research_name is not None:
                try:
                    if self.adapters.research(research_name).describe().supports("research_or_imported_evidence"):
                        missing.remove("research_or_imported_evidence")
                except ServiceError as exc:
                    return [_finding("capability_missing", exc.message)]
        if missing:
            return [
                _finding(
                    "capability_missing",
                    f"connection {connection_name!r} ({description.adapter_id}) does not provide {missing}; "
                    "select a configured adapter that does, import the evidence, or take over the task manually",
                )
            ]
        return []

    def _block(self, task: Task, revision: int, findings: list[Finding]) -> None:
        error = NormalizedError(
            category=ErrorCategory.CAPABILITY_MISSING if any(f.code == "capability_missing" for f in findings) else ErrorCategory.PERMISSION_REQUIRED,
            provider_code=None,
            message="; ".join(f.message for f in findings),
            automatic_retry_now=False,
            retry_after_seconds=None,
            reset_at=None,
            completion_certainty=CompletionCertainty.NOT_COMPLETED,
            next_action=f"resolve the findings, then run: sdlc task resume {task.task_id}",
            diagnostic_ref=None,
        )
        with self.ctx.db.transaction():
            self._cas_state(task.task_id, TaskState.PENDING, TaskState.NEEDS_INPUT, revision)
            repo.insert_event(
                self.ctx.db,
                self.ctx.project_id,
                self.ctx.ids("event"),
                "task.blocked",
                {"findings": [f.to_json_dict() for f in findings], "error": error.to_json_dict(), "invocations": 0},
                self.ctx.clock(),
                team_id=task.team_id.value,
                task_id=task.task_id,
            )

    # ------------------------------------------------------------------ attempts
    def _start_attempt(self, task: Task, row: dict[str, Any], connection_name: str, quota_scope_id: str) -> _Attempt:
        connection = self.ctx.config.connections[connection_name]
        attempt_id = self.ctx.ids("attempt")
        lease = self.leases.acquire("task_attempt", f"{self.ctx.project_id}/{task.task_id}", ATTEMPT_LEASE_SECONDS)
        resolved = {
            "profile": connection_name,
            "type": connection.type.value,
            "auth": connection.auth.value,
            "model": connection.model,
            "executable": connection.executable,
            "base_url": connection.base_url,
            "credential_ref": connection.credential_ref,
            "isolation": self.ctx.config.runtime.isolation,
            "launcher_profile": connection.launcher_profile or self.ctx.config.runtime.launcher_profile,
        }
        now = self.ctx.clock()
        try:
            with self.ctx.db.transaction():
                self._cas_state(task.task_id, TaskState.PENDING, TaskState.RUNNING, row["revision"])
                number_row = self.ctx.db.one(
                    "SELECT COALESCE(MAX(attempt_number),0) AS n FROM attempts WHERE project_id=? AND task_id=?", (self.ctx.project_id, task.task_id)
                )
                attempt_number = int(number_row["n"]) + 1
                self.ctx.db.execute(
                    "INSERT INTO attempts(project_id, attempt_id, task_id, cycle_id, team_id, attempt_number, revision, fencing_generation,"
                    " lease_owner, lease_expires_at, started_at, ended_at, backend_id, connection_ref, resolved_connection_json,"
                    " resolved_connection_sha256, account_binding_id, quota_scope_id, workspace_lineage_id, profile_override_decision_id,"
                    " backend_session_id, input_manifest_sha256, workspace_id, process_identity_json, last_event_id, last_checkpoint_id,"
                    " normalized_error_json, result_id) VALUES (?,?,?,?,?,?,1,?,?,?,?,NULL,?,?,?,?,?,?,NULL,NULL,NULL,?,NULL,?,NULL,NULL,NULL,NULL)",
                    (
                        self.ctx.project_id,
                        attempt_id,
                        task.task_id,
                        task.cycle_id,
                        task.team_id.value,
                        attempt_number,
                        lease.fencing_generation,
                        lease.owner_id,
                        lease.expires_at,
                        now,
                        connection.type.value,
                        connection_name,
                        canonical_bytes(resolved).decode(),
                        digest(resolved),
                        connection.expected_account_id or f"declared:{connection_name}",
                        quota_scope_id,
                        row["input_manifest_sha256"],
                        canonical_bytes({"supervisor": self.owner_id}).decode(),
                    ),
                )
                repo.insert_event(
                    self.ctx.db,
                    self.ctx.project_id,
                    self.ctx.ids("event"),
                    "attempt.started",
                    {"attempt_id": attempt_id, "attempt_number": attempt_number, "connection": connection_name, "fencing_generation": lease.fencing_generation},
                    now,
                    team_id=task.team_id.value,
                    task_id=task.task_id,
                    attempt_id=attempt_id,
                )
        except Exception:
            self.leases.release(lease)
            raise
        return _Attempt(attempt_id, attempt_number, lease, quota_scope_id, connection_name, row["revision"] + 1)

    # ------------------------------------------------------------------ dispatch
    async def _dispatch(self, task: Task, operation: Operation, attempt: _Attempt) -> BrainReply:
        view = TeamView(self.ctx, task.team_id)
        documents: list[ContextDocument] = []
        omitted: list[str] = []
        for ref in task.input_refs:
            granted = view.authorize(ref, list(task.input_deliveries))
            if self.artifacts.status(ref) in ("superseded", "stale"):
                omitted.append(f"{ref.artifact_id}@{ref.revision} ({granted.artifact.relative_path}, {self.artifacts.status(ref)})")
                continue
            data = self.artifacts.read_bytes(ref)
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                text = f"[binary {granted.artifact.media_type}, {len(data)} bytes; extraction pending]"
            documents.append(
                ContextDocument(
                    artifact_id=ref.artifact_id,
                    revision=ref.revision,
                    sha256=ref.sha256,
                    relative_path=granted.artifact.relative_path,
                    media_type=granted.artifact.media_type,
                    text=text,
                )
            )
        if omitted:
            note = "These granted documents were replaced by newer ones or made stale by an accepted change and are NOT supplied; do not look for them:\n- " + "\n- ".join(omitted) + "\n"
            documents.append(ContextDocument("runtime-omitted", 1, digest(omitted), "runtime/omitted-superseded.txt", "text/plain", note))
        documents.extend(self._dependency_documents(task))
        policy_for_docs = self.policies.get(operation.prerequisite_policy)
        if policy_for_docs is not None:
            from sdlc.domain.models import ARTIFACT_KIND_SCHEMAS
            from sdlc.verification.schemas import schema_subset

            for kind in policy_for_docs.required_kinds(task):
                definition = ARTIFACT_KIND_SCHEMAS.get(kind) or (STRUCTURED_LIST_KINDS.get(kind) or (None,))[0]
                if definition:
                    schema_text = json.dumps(schema_subset(definition), ensure_ascii=False)
                    documents.append(ContextDocument(f"schema-{definition}", 1, digest(schema_subset(definition)), f"runtime/schema-{definition}.json", "application/schema+json", schema_text))
        if operation.short_name == "plan_work":
            context_bytes = canonical_bytes(leader_context(task, self.registry))
            documents.append(ContextDocument("leader-context", 1, digest(leader_context(task, self.registry)), "runtime/leader-context.json", "application/json", context_bytes.decode("utf-8")))
        if "research_or_imported_evidence" in operation.required_capabilities:
            documents.extend(await self._retrieve_evidence(task, attempt))
        request = BrainRequest(
            principal=AgentPrincipal(self.ctx.project_id, task.cycle_id, Team(task.team_id.value), task.agent_instance_id),
            task_id=task.task_id,
            attempt_id=attempt.attempt_id,
            operation=task.operation,
            operation_version=task.operation_version,
            system_prompt=system_prompt(task.team_id.value, task.operation),
            instructions=self._instructions(task, operation),
            documents=tuple(documents),
            output_schema={"$ref": operation.proposal_schema, "$defs": load_schema()["$defs"]},
            output_definition=operation.proposal_schema.removeprefix("#/$defs/"),
            max_output_tokens=task.limits.max_output_tokens,
            timeout_seconds=task.limits.timeout_seconds,
            parameters={**task.parameters, **self._replay_counters(task, attempt)},
        )
        self._backend_event(task, attempt, EventKind.STARTED, {"connection": attempt.connection_name, "documents": len(documents)})
        reply = await self.adapters.brain(attempt.connection_name).generate(request)
        kind = EventKind.ERROR if reply.error else EventKind.COMPLETED
        self._backend_event(task, attempt, kind, {"provider": reply.provider, "usage": reply.usage.to_json_dict()}, error=reply.error)
        return reply

    def _instructions(self, task: Task, operation: Operation) -> str:
        """Objective + the exact artifact kinds required + the previous attempt's findings on repair."""
        lines = [str(task.parameters.get("objective", ""))]
        policy = self.policies.get(operation.prerequisite_policy)
        kinds = policy.required_kinds(task) if policy is not None else ()
        if kinds:
            from sdlc.domain.models import ARTIFACT_KIND_SCHEMAS

            described = []
            for kind in kinds:
                definition = ARTIFACT_KIND_SCHEMAS.get(kind)
                if definition:
                    described.append(f"{kind} (media_type application/json; content_text must be a JSON string validating as {definition}, schema supplied as document runtime/schema-{definition}.json)")
                elif kind in JSON_OBJECT_KINDS:
                    described.append(f"{kind} (media_type application/json; content_text must be a JSON string of {JSON_OBJECT_KINDS[kind]})")
                elif kind in STRUCTURED_LIST_KINDS:
                    item, shape = STRUCTURED_LIST_KINDS[kind]
                    described.append(f"{kind} (media_type application/json; content_text must be a JSON string of the form {shape}, each item validating as {item}, schema supplied as document runtime/schema-{item}.json)")
                elif kind == "task_graph":
                    described.append("task_graph (media_type application/json; content_text is a JSON string of the form {\"tasks\": [Task records]} following runtime/leader-context.json)")
                elif kind == "diagram_source":
                    described.append("diagram_source (media_type text/vnd.mermaid or text/x-plantuml; content_text is the diagram source)")
                else:
                    from sdlc.verification.outputs import MARKDOWN_SECTIONS

                    sections = MARKDOWN_SECTIONS.get(kind)
                    headings = f"; it must contain Markdown headings whose text includes each of: {', '.join(sections)}" if sections else ""
                    described.append(f"{kind} (media_type text/markdown; content_text is the Markdown document{headings})")
            lines.append("Required artifact candidate kinds, use these exact `kind` values: " + "; ".join(described))
        if policy is not None:
            try:
                lines.extend(policy.context_notes(task, TeamView(self.ctx, task.team_id)))
            except ServiceError:
                pass
        last = self.ctx.db.one(
            "SELECT normalized_error_json FROM attempts WHERE project_id=? AND task_id=? AND ended_at IS NOT NULL ORDER BY attempt_number DESC LIMIT 1",
            (self.ctx.project_id, task.task_id),
        )
        if last is not None and last["normalized_error_json"]:
            error = json.loads(last["normalized_error_json"])
            if error.get("category") == "invalid_output":
                lines.append("REPAIR: your previous reply was rejected by the runtime for this reason, fix exactly this: " + str(error.get("message", ""))[:600])
        return "\n".join(lines)

    async def _retrieve_evidence(self, task: Task, attempt: _Attempt) -> list[ContextDocument]:
        """Retrieve through the team's research connection; register each item as an artifact."""
        research_name = self.ctx.config.teams[Team(task.team_id.value)].research
        if research_name is None:
            return []
        backend = self.adapters.research(research_name)
        request = ResearchRequest(
            principal=AgentPrincipal(self.ctx.project_id, task.cycle_id, Team(task.team_id.value), task.agent_instance_id),
            task_id=task.task_id,
            attempt_id=attempt.attempt_id,
            query=str(task.parameters.get("objective", "")),
            max_results=10,
            timeout_seconds=task.limits.timeout_seconds,
        )
        reply = await backend.retrieve(request)
        self._backend_event(
            task, attempt, EventKind.PROGRESS,
            {"research": research_name, "evidence": len(reply.evidence), "failures": list(reply.failures), "limitations": list(reply.limitations)},
            error=reply.error,
        )
        origin = Origin.FIXTURE if backend.describe().connection_type == "fixture" else Origin.TOOL
        documents: list[ContextDocument] = []
        for index, evidence in enumerate(reply.evidence, start=1):
            payload = canonical_bytes(evidence.to_json_dict())
            artifact = self.artifacts.register(
                owner=OwnerTeam(task.team_id.value),
                artifact_id=f"{task.task_id}-evidence-{index}",
                kind="research_evidence",
                media_type="application/json",
                relative_path=f"research/{task.task_id}/evidence-{index}.json",
                data=payload,
                origin=origin,
                created_by_task=task.task_id,
            )
            documents.append(ContextDocument(artifact.artifact_id, artifact.revision, artifact.sha256, artifact.relative_path, "application/json", payload.decode("utf-8")))
        return documents

    def _dependency_documents(self, task: Task) -> list[ContextDocument]:
        """Outputs of this task's completed same-team dependencies (docs/07 s2: same-team reuse).

        They did not exist when the task was planned, so they are bound now and journaled.
        Text artifacts are supplied in full; binary ones (snapshots) are listed by reference.
        """
        refs = self.graphs.dependency_outputs(task.task_id)
        if not refs:
            return []
        docs: list[ContextDocument] = []
        listed: list[str] = []
        for ref in refs:
            artifact = self.artifacts.get(ref)
            if artifact.kind in ("checkpoint_summary", "rejected_output"):
                continue
            label = f"{ref.artifact_id}@{ref.revision} kind={artifact.kind} path={artifact.relative_path} sha256={ref.sha256}"
            listed.append(label)
            data = self.artifacts.read_bytes(ref)
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                continue  # binary (zip): referenced in the index below, not inlined
            docs.append(ContextDocument(ref.artifact_id, ref.revision, ref.sha256, artifact.relative_path, artifact.media_type, text))
        index = "Outputs of the tasks this task depends on (bound at start; cite them by these exact references):\n- " + "\n- ".join(listed) + "\n"
        docs.insert(0, ContextDocument("runtime-dependency-outputs", 1, digest(listed), "runtime/dependency-outputs.txt", "text/plain", index))
        with self.ctx.db.transaction():
            repo.insert_event(self.ctx.db, self.ctx.project_id, self.ctx.ids("event"), "task.inputs_bound",
                              {"task_id": task.task_id, "dependency_outputs": [r.to_json_dict() for r in refs], "reason": "dependency outputs bound at start"},
                              self.ctx.clock(), team_id=task.team_id.value, task_id=task.task_id)
        return docs

    def _replay_counters(self, task: Task, attempt: _Attempt) -> dict[str, int]:
        """How many attempts this task and this team's operation had before now. Deterministic
        fixtures replay sequences by these counts, so a scripted flow survives process restarts."""
        same_operation = 0
        for row in self.ctx.db.all("SELECT task_id, task_json FROM tasks WHERE project_id=? AND team_id=?", (self.ctx.project_id, task.team_id.value)):
            if row["task_id"] == task.task_id or json.loads(row["task_json"]).get("operation") != task.operation:
                continue
            same_operation += int(self.ctx.db.one("SELECT count(*) AS n FROM attempts WHERE project_id=? AND task_id=?", (self.ctx.project_id, row["task_id"]))["n"])
        return {"_task_attempts_before": attempt.attempt_number - 1, "_operation_attempts_before": same_operation + attempt.attempt_number - 1}

    # ------------------------------------------------------------------ coding dispatch
    def _nonconforming(self) -> Finding:
        return Finding(finding_id="finding-nonconforming", code="nonconforming_isolation", severity=Severity.WARNING,
                       message="produced under development isolation (client sandbox only, no OS boundary); cannot satisfy the compliant lifecycle gate", related_refs=[])

    async def _dispatch_coding(self, task: Task, operation: Operation, attempt: _Attempt) -> BrainReply:
        """Run the task's bounded change through the team's coding connection inside a private workspace."""
        bound = self.ctx.config.teams[Team(task.team_id.value)].coding
        backend = self.adapters.coding(bound)
        manager = WorkspaceManager(self.ctx, self.owner_id)
        workspace = manager.prepare(task)
        attempt.workspace_id = str(workspace.root)
        self.ctx.db.execute("UPDATE attempts SET workspace_id=?, workspace_lineage_id=?, revision=revision+1 WHERE project_id=? AND attempt_id=?",
                            (attempt.workspace_id, f"{task.team_id.value}/{task.task_id}", self.ctx.project_id, attempt.attempt_id))
        job = CodingJob(
            principal=AgentPrincipal(self.ctx.project_id, task.cycle_id, Team(task.team_id.value), task.agent_instance_id),
            task_id=task.task_id, attempt_id=attempt.attempt_id,
            ownership=SessionOwnershipKey(self.ctx.project_id, task.cycle_id, Team(task.team_id.value), task.task_id, bound, f"declared:{bound}", f"{task.team_id.value}/{task.task_id}"),
            workspace=workspace.root, sandbox="workspace-write",
            instructions=f"{operation.name}: " + self._instructions(task, operation) + "\n\n" + system_prompt(task.team_id.value, task.operation),
            output_schema={"title": "WorkerProposal"}, timeout_seconds=task.limits.timeout_seconds,
        )
        terminal = None
        error: NormalizedError | None = None
        try:
            async for event in backend.start(job):
                self._record_adapter_event(task, attempt, event)
                if event.kind == EventKind.COMPLETED:
                    terminal = event
                elif event.kind in (EventKind.ERROR, EventKind.INTERRUPTED):
                    terminal = event
                    error = event.error or _unknown_error("coding backend reported an error without details")
                session = event.details.get("session_id") or event.details.get("thread_id")
                if session:
                    attempt.backend_session_id = str(session)
        except Exception as exc:  # noqa: BLE001
            error = _unknown_error(f"coding backend raised {type(exc).__name__}: {exc}")
        if terminal is None and error is None:
            error = _unknown_error("coding process ended without a terminal event; partial edits preserved").model_copy(update={"completion_certainty": CompletionCertainty.UNKNOWN})
        # Preserve whatever is in the workspace, finished or not.
        try:
            captured = manager.capture(workspace, task, label="candidate" if error is None else "partial")
            attempt.candidate_ref, attempt.patch_ref = captured["snapshot"].ref, captured["patch"].ref
            if error is None:
                attempt.extra_refs = [captured["snapshot"].ref, captured["patch"].ref, captured["changes"].ref]
                if task.team_id == TeamId.TESTING:
                    # A bench may add tests, fixtures and doubles; it never edits the candidate under test.
                    changes = json.loads(self.artifacts.read_bytes(captured["changes"].ref).decode("utf-8"))
                    touched = list(changes.get("modified", [])) + list(changes.get("deleted", []))
                    if touched:
                        attempt.extra_findings.append(Finding(finding_id="finding-bench-modified-source", code="bench_modified_source", severity=Severity.BLOCKING,
                                                              message=f"the bench changed files of the candidate under test: {touched[:10]}; a Testing job must not modify production source (handoff docs/04 s6)", related_refs=[captured["patch"].ref]))
        except ServiceError as exc:
            error = error or _unknown_error(f"could not capture the workspace: {exc.message}")
        finally:
            manager.release(workspace)
        attempt.extra_findings.append(self._nonconforming())
        if error is not None:
            attempt.side_effects_unknown = True
            return BrainReply(structured=None, text=None, error=error, usage=_unknown_usage(), provider={"adapter": backend.describe().adapter_id, "origin": "native"})
        details = terminal.details if terminal is not None else {}
        proposal = {"schema_version": "1.0", "task_id": task.task_id, "summary": str(details.get("summary") or "change applied"), "artifact_candidates": [],
                    "findings": list(details.get("findings") or []), "questions": list(details.get("questions") or []), "suggested_next_operations": []}
        usage_raw = details.get("usage") or {}
        usage = Usage.model_validate(usage_raw) if {"input_tokens", "output_tokens", "cost_microunits", "currency", "measurement"} <= set(usage_raw) else _unknown_usage()
        return BrainReply(structured=proposal, text=json.dumps(proposal), error=None, usage=usage, provider={"adapter": backend.describe().adapter_id, "origin": details.get("origin", "native")})

    def _record_adapter_event(self, task: Task, attempt: _Attempt, event: BackendEvent) -> None:
        count = self.ctx.db.one("SELECT count(*) AS n FROM events WHERE project_id=? AND attempt_id=? AND event_type='backend.event'", (self.ctx.project_id, attempt.attempt_id))["n"]
        stored = event.model_copy(update={"event_id": self.ctx.ids("bevent"), "sequence": int(count) + 1, "project_id": self.ctx.project_id})
        with self.ctx.db.transaction():
            repo.insert_event(self.ctx.db, self.ctx.project_id, stored.event_id, "backend.event", stored.to_json_dict(), stored.recorded_at, team_id=task.team_id.value, task_id=task.task_id, attempt_id=attempt.attempt_id)
            self.ctx.db.execute("UPDATE attempts SET last_event_id=?, revision=revision+1 WHERE project_id=? AND attempt_id=?", (stored.event_id, self.ctx.project_id, attempt.attempt_id))

    # ------------------------------------------------------------------ check dispatch
    async def _dispatch_check(self, task: Task, operation: Operation, attempt: _Attempt) -> BrainReply:
        """Run registered recipes (or, for ``testing.preflight``, a collect-only pass) against the
        task's tree through the bound execution connection; the collector writes the evidence.

        * ``implementation.check``: recipes on the candidate -> execution records + CHECK_REPORT.md
        * ``testing.preflight``: collect tests, map node ids to case ids -> preflight_report
        * ``testing.execute``: recipes on the bench (candidate + tests) -> execution records with
          ``source_ref`` = the delivered candidate and ``suite_ref`` = the bench snapshot, a
          ``test_bench`` record and EXECUTION_REPORT.md with one verdict per declared case.
        """
        from sdlc.domain.models import ExecutionStatus
        from sdlc.teams.testing.mapping import NOT_EXECUTED, case_outcomes, map_nodes_to_cases

        bound = self.ctx.config.teams[Team(task.team_id.value)].execution
        owner = OwnerTeam(task.team_id.value)
        short = operation.short_name

        def writer(name: str, data: bytes):
            return self.artifacts.register(owner=owner, artifact_id=name.rsplit('.', 1)[0], kind="execution_log", media_type="text/plain",
                                           relative_path=f"runs/{task.task_id}/{name}", data=data, origin=Origin.TOOL, created_by_task=task.task_id).ref

        from sdlc.runtime.launcher import usable_profile

        launcher = None
        if self.ctx.config.runtime.isolation == "strict":
            launcher, _why = usable_profile(self.ctx)  # the preflight gate already refused when None
        runner = self.adapters.runner(bound, artifacts_writer=writer, launcher=launcher)
        manager = WorkspaceManager(self.ctx, self.owner_id)
        workspace = manager.prepare(task)
        if workspace.baseline_ref is None:
            manager.release(workspace)
            return BrainReply(None, None, normalized_missing("no candidate source_snapshot is pinned for this run"), _unknown_usage())
        environment = {"isolation": self.ctx.config.runtime.isolation, "platform": sys.platform if launcher is None else f"container:{launcher.image}",
                       "python": sys.version.split()[0] if launcher is None else launcher.python, "runner": runner.describe().adapter_id,
                       "launcher": launcher.describe() if launcher else None, "conformance": "conforming" if launcher else "nonconforming"}
        env_artifact = self.artifacts.register(owner=owner, artifact_id=f"{attempt.attempt_id}-environment", kind="environment", media_type="application/json",
                                               relative_path=f"runs/{task.task_id}/environment.json", data=canonical_bytes(environment), origin=Origin.TOOL, created_by_task=task.task_id)
        recipes = self.ctx.config.execution.recipes
        scope = [str(s) for s in (task.parameters.get("scope_ids") or [])]
        # scope_ids name recipes only when every one of them is a registered recipe;
        # otherwise they are requirement ids (the usual case) and every recipe runs.
        requested = scope if scope and all(s in recipes for s in scope) else list(recipes)
        candidate_ref = self._pinned_candidate(task) or workspace.baseline_ref
        suite_ref = workspace.baseline_ref if workspace.baseline_ref.key != candidate_ref.key else None
        case_ids = self._declared_case_ids(task) if task.team_id == TeamId.TESTING else []
        principal = AgentPrincipal(self.ctx.project_id, task.cycle_id, Team(task.team_id.value), task.agent_instance_id)
        findings: list[Finding] = []
        records = []
        lines = [f"# {'Preflight' if short == 'preflight' else 'Execution' if short == 'execute' else 'Check'} report", "",
                 f"Candidate: {candidate_ref.artifact_id}@{candidate_ref.revision}",
                 f"Bench (suite): {suite_ref.artifact_id}@{suite_ref.revision}" if suite_ref else "Bench (suite): candidate tree only",
                 f"Isolation: {self.ctx.config.runtime.isolation}" + (f" via launcher {launcher.name} ({launcher.image}; conforming execution)" if launcher else " (nonconforming: no OS boundary)"), ""]
        if scope and requested is not scope:
            lines.append(f"Scope: {', '.join(scope)} (requirement ids; all registered recipes run)")
        extra_refs: list[ArtifactRef] = [env_artifact.ref]
        try:
            if short == "preflight":
                base = list(recipes.get("pytest") or next(iter(recipes.values())))
                argv = [sys.executable if a == "{python}" else a for a in base if a != "-v"] + ["--collect-only", "-q"]
                request = ExecutionRequest(principal=principal, task_id=task.task_id, attempt_id=attempt.attempt_id, execution_id=f"{attempt.attempt_id}-collect", recipe_id="collect",
                                           argv=tuple(argv), working_directory=workspace.root, source_ref=candidate_ref, environment_ref=env_artifact.ref, suite_ref=suite_ref, timeout_seconds=task.limits.timeout_seconds)
                record = await runner.execute(request)
                stdout = self.artifacts.read_bytes(record.stdout_ref).decode("utf-8", "replace") if record.stdout_ref else ""
                nodes = [l.strip() for l in stdout.splitlines() if "::" in l and not l.startswith(("=", "-"))]
                mapping = map_nodes_to_cases(nodes, case_ids)
                unmapped_cases = [c for c, ns in mapping.items() if not ns]
                mapped_nodes = {n for ns in mapping.values() for n in ns}
                unmapped_nodes = [n for n in nodes if n not in mapped_nodes]
                report = {"collected": len(nodes), "exit_code": record.exit_code, "status": record.status.value, "nodes": nodes, "declared_cases": case_ids,
                          "mapping": mapping, "unmapped_cases": unmapped_cases, "unmapped_nodes": unmapped_nodes}
                pre = self.artifacts.register(owner=owner, artifact_id=f"{attempt.attempt_id}-preflight", kind="preflight_report", media_type="application/json",
                                              relative_path=f"runs/{task.task_id}/preflight.json", data=canonical_bytes(report), origin=Origin.TOOL, depends_on=[workspace.baseline_ref], created_by_task=task.task_id)
                extra_refs.append(pre.ref)
                lines += [f"- collected test nodes: {len(nodes)} (exit {record.exit_code})", f"- declared cases: {len(case_ids)}; mapped: {len(case_ids) - len(unmapped_cases)}; without a test: {unmapped_cases or 'none'}",
                          f"- tests matching no case: {unmapped_nodes or 'none'}"]
                if not nodes:
                    findings.append(Finding(finding_id="finding-no-tests", code="no_tests_collected", severity=Severity.BLOCKING, message="the bench collected zero tests; nothing can be executed (fix the bench or its entry point)", related_refs=[pre.ref]))
                if unmapped_cases:
                    findings.append(Finding(finding_id="finding-unmapped-cases", code="cases_without_tests", severity=Severity.WARNING, message=f"declared cases with no test named after them: {unmapped_cases}; they will be reported not_executed", related_refs=[pre.ref]))
            else:
                for name in requested:
                    argv_t = recipes.get(name)
                    if argv_t is None:
                        lines.append(f"- {name}: not a registered recipe (skipped)")
                        continue
                    argv = [sys.executable if a == "{python}" else a for a in argv_t]
                    request = ExecutionRequest(principal=principal, task_id=task.task_id, attempt_id=attempt.attempt_id, execution_id=f"{attempt.attempt_id}-{name}", recipe_id=name, argv=tuple(argv),
                                               working_directory=workspace.root, source_ref=candidate_ref, environment_ref=env_artifact.ref, suite_ref=suite_ref, timeout_seconds=task.limits.timeout_seconds)
                    record = await runner.execute(request)
                    record_artifact = self.artifacts.register(owner=owner, artifact_id=f"{attempt.attempt_id}-{name}-record", kind="execution_record", media_type="application/json",
                                                              relative_path=f"runs/{task.task_id}/{name}.json", data=canonical_bytes(record.to_json_dict()), origin=Origin.TOOL, depends_on=[workspace.baseline_ref], created_by_task=task.task_id)
                    records.append((name, record, record_artifact))
                    counts = {s.value: sum(1 for o in record.case_observations if o.status == s) for s in {o.status for o in record.case_observations}}
                    lines.append(f"- {name}: {record.status.value} (exit {record.exit_code}; cases {counts or 'none parsed'})")
        finally:
            manager.release(workspace)
        record_refs = [r[2].ref for r in records]
        if short == "execute":
            outcomes = case_outcomes(case_ids, [r[1] for r in records])
            lines += ["", "## Per-case verdicts (from the collector; not_executed never counts as a pass)", "", "| case | verdict | tests |", "|---|---|---|"]
            for case_id, status in outcomes.statuses.items():
                lines.append(f"| {case_id} | {status} | {', '.join(outcomes.nodes[case_id]) or '-'} |")
            if outcomes.unmapped_nodes:
                lines += ["", f"Tests matching no declared case: {outcomes.unmapped_nodes}"]
            lines += ["", f"Counts: {json.dumps(outcomes.counts, sort_keys=True)}"]
            not_executed = [c for c, s in outcomes.statuses.items() if s == NOT_EXECUTED]
            if not_executed:
                findings.append(Finding(finding_id="finding-not-executed", code="cases_not_executed", severity=Severity.WARNING, message=f"declared cases without an observation: {not_executed}", related_refs=record_refs))
            unstable = self._unstable_cases(owner, candidate_ref, suite_ref, outcomes)
            if unstable:
                lines += ["", f"Instability: {unstable} failed in an earlier run of the same candidate and bench; the earlier failure is retained."]
                findings.append(Finding(finding_id="finding-unstable", code="unstable_cases", severity=Severity.WARNING, message=f"cases that failed before and passed now on the same candidate+bench: {unstable}", related_refs=record_refs))
            bench = {"schema_version": "1.0", "bench_id": f"bench-{task.task_id}", "plan_ref": (self._first_input_of_kind(task, ("test_plan", "test_cases")) or candidate_ref).to_json_dict(),
                     "suite_snapshot_ref": (suite_ref or workspace.baseline_ref).to_json_dict(), "fixture_refs": [], "recipe_ids": [n for n, _, _ in records] or list(recipes),
                     "environment_ref": env_artifact.ref.to_json_dict(), "seed": None, "clock_policy": "wall clock", "cleanup_recipe_id": None}
            validate_record(bench, "TestBench")
            bench_artifact = self.artifacts.register(owner=owner, artifact_id=f"{attempt.attempt_id}-test-bench", kind="test_bench", media_type="application/json",
                                                     relative_path=f"runs/{task.task_id}/test_bench.json", data=canonical_bytes(bench), origin=Origin.TOOL, depends_on=[workspace.baseline_ref], created_by_task=task.task_id)
            extra_refs.append(bench_artifact.ref)
        report_kind, report_name = {"preflight": ("preflight_summary", "PREFLIGHT.md"), "execute": ("execution_report", "EXECUTION_REPORT.md")}.get(short, ("check_report", "CHECK_REPORT.md"))
        report = self.artifacts.register(owner=owner, artifact_id=f"{attempt.attempt_id}-{report_kind.replace('_', '-')}", kind=report_kind, media_type="text/markdown",
                                         relative_path=f"runs/{task.task_id}/{report_name}", data="\n".join(lines).encode("utf-8"), origin=Origin.TOOL, depends_on=[workspace.baseline_ref], created_by_task=task.task_id)
        attempt.extra_refs = extra_refs + [report.ref] + record_refs
        if launcher is None:
            attempt.extra_findings.append(self._nonconforming())
        failed = [name for name, rec, _ in records if rec.status != ExecutionStatus.PASSED]
        if failed:
            attempt.extra_findings.append(Finding(finding_id="finding-checks-failed", code="checks_failed", severity=Severity.WARNING, message=f"recipes not passing: {failed} (a failed real check is a valid observation, not a provider error)", related_refs=record_refs))
        summary = f"ran {len(records)} recipe(s): " + ", ".join(f"{n}={r.status.value}" for n, r, _ in records) if short != "preflight" else "collected the bench and mapped tests to cases"
        proposal = {"schema_version": "1.0", "task_id": task.task_id, "summary": summary, "artifact_candidates": [], "findings": [f.to_json_dict() for f in findings], "questions": [], "suggested_next_operations": []}
        return BrainReply(structured=proposal, text=json.dumps(proposal), error=None, usage=_unknown_usage(), provider={"adapter": runner.describe().adapter_id, "origin": "tool"})

    def _pinned_candidate(self, task: Task) -> ArtifactRef | None:
        """The delivered candidate under test: a pinned source_snapshot owned by another team, else any pinned one."""
        from sdlc.runtime.workspaces import CONTINUES_OWN_CANDIDATE, pinned_snapshots

        ranked = pinned_snapshots(self.ctx, task, prefer_own=CONTINUES_OWN_CANDIDATE.get(task.team_id.value, True))
        return ranked[0] if ranked else None

    def _first_input_of_kind(self, task: Task, kinds: tuple[str, ...]) -> ArtifactRef | None:
        for ref in list(task.input_refs) + self.graphs.dependency_outputs(task.task_id):
            if self.artifacts.get(ref).kind in kinds:
                return ref
        return None

    def _declared_case_ids(self, task: Task) -> list[str]:
        from sdlc.teams.testing.mapping import case_ids_from_documents

        texts = []
        for ref in list(task.input_refs) + self.graphs.dependency_outputs(task.task_id):
            if self.artifacts.get(ref).kind in ("test_cases", "test_plan"):
                texts.append(self.artifacts.read_bytes(ref).decode("utf-8"))
        return case_ids_from_documents(texts)

    def _unstable_cases(self, owner: OwnerTeam, candidate_ref: ArtifactRef, suite_ref: ArtifactRef | None, outcomes) -> list[str]:
        """Cases that failed in an earlier execution record of the same candidate and bench but pass now."""
        from sdlc.domain.models import ExecutionRecord

        earlier_failed: set[str] = set()
        for artifact in self.artifacts.list(owner):
            if artifact.kind != "execution_record":
                continue
            record = ExecutionRecord.model_validate(json.loads(self.artifacts.read_bytes(artifact.ref).decode("utf-8")))
            same_suite = (record.suite_ref.key if record.suite_ref else None) == (suite_ref.key if suite_ref else None)
            if record.source_ref.key != candidate_ref.key or not same_suite:
                continue
            for observation in record.case_observations:
                if observation.status.value in ("failed", "error"):
                    earlier_failed.add(observation.message or observation.case_id)
        unstable = []
        for case_id, status in outcomes.statuses.items():
            if status == "passed" and any(node in earlier_failed for node in outcomes.nodes[case_id]):
                unstable.append(case_id)
        return unstable

    def _backend_event(self, task: Task, attempt: _Attempt, kind: EventKind, details: dict[str, Any], error: NormalizedError | None = None) -> None:
        count = self.ctx.db.one(
            "SELECT count(*) AS n FROM events WHERE project_id=? AND attempt_id=? AND event_type='backend.event'", (self.ctx.project_id, attempt.attempt_id)
        )["n"]
        event = BackendEvent(
            schema_version="1.0",
            event_id=self.ctx.ids("bevent"),
            project_id=self.ctx.project_id,
            task_id=task.task_id,
            attempt_id=attempt.attempt_id,
            team_id=task.team_id,
            sequence=int(count) + 1,
            kind=kind,
            recorded_at=self.ctx.clock(),
            details=details,
            error=error,
        )
        with self.ctx.db.transaction():
            repo.insert_event(
                self.ctx.db, self.ctx.project_id, event.event_id, "backend.event", event.to_json_dict(), event.recorded_at,
                team_id=task.team_id.value, task_id=task.task_id, attempt_id=attempt.attempt_id,
            )
            self.ctx.db.execute(
                "UPDATE attempts SET last_event_id=?, revision=revision+1 WHERE project_id=? AND attempt_id=?",
                (event.event_id, self.ctx.project_id, attempt.attempt_id),
            )

    # ------------------------------------------------------------------ finalize
    def _finalize(self, task: Task, operation: Operation, attempt: _Attempt, reply: BrainReply) -> TaskOutcome:
        if reply.error is None and reply.structured is None:
            reply = BrainReply(structured=None, text=reply.text, error=_invalid_output("adapter returned no structured payload"), usage=reply.usage, provider=reply.provider)
        try:
            if reply.error is None:
                try:
                    return self._complete(task, operation, attempt, reply)
                except InvalidSchema as exc:
                    reply = BrainReply(structured=None, text=reply.text, error=_invalid_output(exc.message, exc.details), usage=reply.usage, provider=reply.provider)
                except StaleRevision:
                    raise
                except Exception as exc:  # a check raised on unexpected content: reject the reply, never strand the task
                    reply = BrainReply(structured=None, text=reply.text, error=_invalid_output(f"runtime check failed on the reply: {type(exc).__name__}: {exc}"), usage=reply.usage, provider=reply.provider)
            return self._fail(task, attempt, reply)
        except StaleRevision as exc:
            return self._discard_late_result(task, attempt, exc)

    def _discard_late_result(self, task: Task, attempt: _Attempt, exc: StaleRevision) -> TaskOutcome:
        """The attempt lost its fence (lease taken over / task state moved on): nothing is committed."""
        row = self.graphs.task_row(task.task_id)
        with self.ctx.db.transaction():
            self.ctx.db.execute(
                "UPDATE attempts SET ended_at=?, normalized_error_json=?, revision=revision+1 WHERE project_id=? AND attempt_id=? AND ended_at IS NULL",
                (self.ctx.clock(), canonical_bytes(_unknown_error(exc.message).to_json_dict()).decode(), self.ctx.project_id, attempt.attempt_id),
            )
            repo.insert_event(
                self.ctx.db, self.ctx.project_id, self.ctx.ids("event"), "attempt.fenced",
                {"attempt_id": attempt.attempt_id, "reason": exc.message, "task_state": row["state"]}, self.ctx.clock(),
                team_id=task.team_id.value, task_id=task.task_id, attempt_id=attempt.attempt_id,
            )
        return TaskOutcome(task.task_id, TaskState(row["state"]), attempt.attempt_id, reason=f"late result discarded: {exc.message}")

    def _complete(self, task: Task, operation: Operation, attempt: _Attempt, reply: BrainReply) -> TaskOutcome:
        assert reply.structured is not None
        _drop_unusable_finding_refs(reply.structured)  # a reference without a real hash carries nothing; do not spend the repair on it
        proposal = validate_record(reply.structured, operation.proposal_schema.removeprefix("#/$defs/"))
        if not isinstance(proposal, WorkerProposal) or proposal.task_id != task.task_id:
            raise InvalidSchema("proposal task_id differs from the running task", expected=task.task_id)
        policy = self.policies.get(operation.prerequisite_policy)
        view = TeamView(self.ctx, task.team_id)
        normalization_findings: list[Finding] = []
        if policy is not None:
            proposal, normalization_findings = policy.normalize(task, proposal, view)
        output_findings = (policy.check_outputs(task, proposal, view) if policy is not None else []) + normalization_findings
        blocking_outputs = [f for f in output_findings if f.severity == Severity.BLOCKING]
        if blocking_outputs:
            raise InvalidSchema("proposal violates the operation's output obligations", errors=[f"{f.code}: {f.message}" for f in blocking_outputs])
        origin = Origin.FIXTURE if reply.provider.get("origin") == "fixture" else Origin.MODEL
        accepted_mode = self._accepted_mode(task)
        refs: list[ArtifactRef] = []
        for candidate in proposal.artifact_candidates:
            if candidate.content_text is None:
                raise InvalidSchema("workspace_path candidates need a coding workspace (milestone M5)", path=candidate.workspace_path)
            artifact = self.artifacts.register(
                owner=OwnerTeam(task.team_id.value),
                artifact_id=f"{task.task_id}-{candidate.relative_path.rsplit('/', 1)[-1].rsplit('.', 1)[0]}",
                kind=candidate.kind,
                media_type=candidate.media_type,
                relative_path=candidate.relative_path,
                data=candidate.content_text.encode("utf-8"),
                origin=origin,
                depends_on=list(task.input_refs),
                created_by_task=task.task_id,
            )
            refs.append(artifact.ref)
            if accepted_mode:
                self.artifacts.set_status(artifact.ref, "reviewable", reason="produced in accepted mode from baseline inputs")
        refs = list(attempt.extra_refs) + refs
        findings = list(proposal.findings) + [f for f in output_findings if f.severity != Severity.BLOCKING] + list(attempt.extra_findings)
        if not accepted_mode:
            findings.append(Finding(finding_id="finding-provisional", code="provisional", severity=Severity.WARNING, message="produced in provisional mode: inputs are not yet the team's accepted baseline; outputs stay draft", related_refs=[]))
        blocking = [f for f in proposal.findings if f.severity == Severity.BLOCKING] + [f for f in attempt.extra_findings if f.severity == Severity.BLOCKING]
        if blocking:
            # The worker says it cannot proceed as instructed (a design conflict, a missing
            # decision). That is a person's call, not a completed task: keep whatever it
            # produced, record the questions, and stop here (docs/06 s6 needs_input).
            return self._blocked_by_worker(task, attempt, proposal, refs, findings, blocking, reply)
        result = TaskResult(
            schema_version="1.0",
            task_id=task.task_id,
            attempt_id=attempt.attempt_id,
            outcome=Outcome.COMPLETED,
            artifact_refs=refs,
            candidate_workspace_ref=None,
            findings=findings,
            questions=list(proposal.questions),
            usage=reply.usage,
            error=None,
        )
        self._commit_result(task, attempt, result, TaskState.COMPLETED, {"summary": proposal.summary, "origin": origin.value, "accepted_mode": accepted_mode})
        return TaskOutcome(task.task_id, TaskState.COMPLETED, attempt.attempt_id, result=result, artifacts=tuple(refs))

    def _blocked_by_worker(self, task: Task, attempt: _Attempt, proposal: WorkerProposal, refs: list[ArtifactRef], findings: list[Finding], blocking: list[Finding], reply: BrainReply) -> TaskOutcome:
        detail = "; ".join(f"{f.code}: {f.message}" for f in blocking)
        questions = list(proposal.questions) or [f"Resolve and answer: {f.message}" for f in blocking]
        error = NormalizedError(
            category=ErrorCategory.PERMISSION_REQUIRED, message=f"worker reports blocking findings: {detail[:900]}", provider_code=None,
            retry_after_seconds=None, reset_at=None, automatic_retry_now=False, completion_certainty=CompletionCertainty.NOT_COMPLETED,
            next_action="answer the worker's questions (new inputs or a decision), then: sdlc task resume " + task.task_id, diagnostic_ref=None,
        )
        result = TaskResult(schema_version="1.0", task_id=task.task_id, attempt_id=attempt.attempt_id, outcome=Outcome.NEEDS_INPUT, artifact_refs=refs,
                            candidate_workspace_ref=None, findings=findings, questions=questions, usage=reply.usage, error=error)
        checkpoint = self._checkpoint(task, attempt, error)
        self._commit_result(task, attempt, result, TaskState.NEEDS_INPUT, {"checkpoint_id": checkpoint.checkpoint_id, "pause_scope": False, "summary": proposal.summary, "questions": questions}, checkpoint=checkpoint)
        return TaskOutcome(task.task_id, TaskState.NEEDS_INPUT, attempt.attempt_id, reason=error.message, result=result, artifacts=tuple(refs))

    def _accepted_mode(self, task: Task) -> bool:
        """Accepted mode: every declared input delivery carries the team's current baseline bundle (docs/04 s1)."""
        if not task.input_deliveries:
            return False
        baseline, _ = repo.get_team_baseline(self.ctx.db, self.ctx.project_id, task.team_id.value)
        if baseline is None:
            return False
        for delivery_id in task.input_deliveries:
            delivery = repo.get_delivery(self.ctx.db, self.ctx.project_id, delivery_id)
            if delivery is None or delivery.bundle_ref.bundle_id != baseline:
                return False
        return True

    def _fail(self, task: Task, attempt: _Attempt, reply: BrainReply) -> TaskOutcome:
        error = reply.error
        assert error is not None
        row = self.graphs.task_row(task.task_id)
        retry_count = int(row["retry_count"])
        category = error.category
        diagnostic = self._preserve_rejected_output(task, reply)
        if diagnostic is not None:
            error = error.model_copy(update={"diagnostic_ref": diagnostic})
        if category in (ErrorCategory.RATE_LIMITED, ErrorCategory.PROVIDER_UNAVAILABLE):
            if self._retry_budget_left(task, retry_count):
                return self._retry_wait(task, attempt, error, retry_count)
            error = error.model_copy(update={"next_action": "retry budget exhausted; " + error.next_action})
            return self._settle(task, attempt, error, TaskState.PAUSED, Outcome.PAUSED)
        if category == ErrorCategory.INVALID_OUTPUT:
            # One bounded repair after each *first* invalid output: a task resumed by a person
            # after new inputs gets its repair chance again; two invalid outputs in a row stop.
            if attempt.attempt_number <= REPAIR_ATTEMPTS or not self._previous_attempt_invalid(task, attempt):
                return self._retry_wait(task, attempt, error, retry_count, immediate=True)
            return self._settle(task, attempt, error, TaskState.NEEDS_INPUT, Outcome.NEEDS_INPUT)
        if category in (ErrorCategory.QUOTA_EXHAUSTED, ErrorCategory.AUTH_REQUIRED):
            return self._settle(task, attempt, error, TaskState.PAUSED, Outcome.PAUSED, pause_scope=True)
        if category in (ErrorCategory.CONTEXT_LIMIT, ErrorCategory.PERMISSION_REQUIRED, ErrorCategory.CAPABILITY_MISSING):
            return self._settle(task, attempt, error, TaskState.NEEDS_INPUT, Outcome.NEEDS_INPUT)
        if category == ErrorCategory.CANCELLED:
            return self._settle(task, attempt, error, TaskState.CANCELLED, Outcome.CANCELLED)
        return self._settle(task, attempt, error, TaskState.PAUSED, Outcome.PAUSED)  # unknown: pause, no blind replay

    def _preserve_rejected_output(self, task: Task, reply: BrainReply) -> ArtifactRef | None:
        if reply.text is None and reply.structured is None:
            return None
        payload = reply.text if reply.text is not None else json.dumps(reply.structured, ensure_ascii=False)
        artifact = self.artifacts.register(
            owner=OwnerTeam(task.team_id.value),
            artifact_id=f"{task.task_id}-rejected-output",
            kind="rejected_output",
            media_type="text/plain",
            relative_path=f"diagnostics/{task.task_id}/rejected-output.txt",
            data=payload.encode("utf-8"),
            origin=Origin.TOOL,
            created_by_task=task.task_id,
        )
        return artifact.ref

    def _retry_budget_left(self, task: Task, retry_count: int) -> bool:
        policy = self.ctx.config.runtime.retry
        attempts_left = retry_count + 1 < min(task.limits.max_attempts, self.ctx.config.runtime.max_attempts)
        first = self.ctx.db.one(
            "SELECT MIN(started_at) AS t FROM attempts WHERE project_id=? AND task_id=?", (self.ctx.project_id, task.task_id)
        )["t"]
        elapsed = (_parse(self.ctx.clock()) - _parse(first)).total_seconds() if first else 0
        return attempts_left and elapsed < policy.total_elapsed_seconds

    def _previous_attempt_invalid(self, task: Task, attempt: _Attempt) -> bool:
        row = self.ctx.db.one(
            "SELECT payload_json FROM events WHERE project_id=? AND task_id=? AND attempt_id<>? AND event_type IN ('task.retry_wait','task.needs_input','task.paused','task.completed','task.cancelled') ORDER BY sequence_number DESC LIMIT 1",
            (self.ctx.project_id, task.task_id, attempt.attempt_id),
        )
        if row is None:
            return False
        payload = json.loads(row["payload_json"])
        error = payload.get("error") or (payload.get("result") or {}).get("error") or {}
        return error.get("category") == ErrorCategory.INVALID_OUTPUT.value

    def _retry_wait(self, task: Task, attempt: _Attempt, error: NormalizedError, retry_count: int, immediate: bool = False) -> TaskOutcome:
        policy = self.ctx.config.runtime.retry
        if immediate:
            delay = 0
        elif error.retry_after_seconds is not None:
            delay = min(error.retry_after_seconds, policy.maximum_seconds)
        else:
            delay = min(policy.initial_seconds * (2**retry_count), policy.maximum_seconds)
        next_at = _format(_parse(self.ctx.clock()) + timedelta(seconds=delay))
        now = self.ctx.clock()
        with self.ctx.db.transaction():
            self._assert_fence(task, attempt)
            self._cas_state(task.task_id, TaskState.RUNNING, TaskState.RETRY_WAIT, attempt.task_revision)
            self.ctx.db.execute(
                "UPDATE tasks SET next_attempt_at=?, retry_count=retry_count+1, updated_at=? WHERE project_id=? AND task_id=?",
                (next_at, now, self.ctx.project_id, task.task_id),
            )
            self._end_attempt(attempt, error, None)
            repo.insert_event(
                self.ctx.db, self.ctx.project_id, self.ctx.ids("event"), "task.retry_wait",
                {"error": error.to_json_dict(), "next_attempt_at": next_at, "retry_count": retry_count + 1}, now,
                team_id=task.team_id.value, task_id=task.task_id, attempt_id=attempt.attempt_id,
            )
        self.leases.release(attempt.lease)
        return TaskOutcome(task.task_id, TaskState.RETRY_WAIT, attempt.attempt_id, reason=f"{error.category.value}: {error.message[:300]}; next attempt at {next_at}")

    def _settle(self, task: Task, attempt: _Attempt, error: NormalizedError, state: TaskState, outcome: Outcome, pause_scope: bool = False) -> TaskOutcome:
        result = TaskResult(
            schema_version="1.0",
            task_id=task.task_id,
            attempt_id=attempt.attempt_id,
            outcome=outcome,
            artifact_refs=[],
            candidate_workspace_ref=None,
            findings=[],
            questions=[],
            usage=_unknown_usage(),
            error=error,
        )
        checkpoint = self._checkpoint(task, attempt, error)
        self._commit_result(task, attempt, result, state, {"checkpoint_id": checkpoint.checkpoint_id, "pause_scope": pause_scope}, checkpoint=checkpoint, pause_scope=pause_scope)
        return TaskOutcome(task.task_id, state, attempt.attempt_id, reason=error.message, result=result)

    def _checkpoint(self, task: Task, attempt: _Attempt, error: NormalizedError) -> Checkpoint:
        summary = {
            "task_id": task.task_id,
            "operation": task.operation,
            "attempt_id": attempt.attempt_id,
            "error": error.to_json_dict(),
            "note": "Brain-only task: no workspace side effects; completed sibling tasks are preserved.",
        }
        summary_artifact = self.artifacts.register(
            owner=OwnerTeam(task.team_id.value),
            artifact_id=f"{attempt.attempt_id}-checkpoint",
            kind="checkpoint_summary",
            media_type="application/json",
            relative_path=f"checkpoints/{task.task_id}/{attempt.attempt_id}.json",
            data=canonical_bytes(summary),
            origin=Origin.TOOL,
            created_by_task=task.task_id,
        )
        row = self.graphs.task_row(task.task_id)
        return Checkpoint(
            schema_version="1.0",
            checkpoint_id=self.ctx.ids("checkpoint"),
            task_id=task.task_id,
            attempt_id=attempt.attempt_id,
            team_id=task.team_id,
            created_at=self.ctx.clock(),
            fencing_generation=attempt.lease.fencing_generation,
            task_graph_revision=int(row["task_graph_revision"]),
            input_deliveries=list(task.input_deliveries),
            input_refs=list(task.input_refs),
            baseline_source_ref=None,
            candidate_source_ref=attempt.candidate_ref,
            patch_ref=attempt.patch_ref,
            intermediate_refs=[],
            last_event_id=None,
            backend_session_id=attempt.backend_session_id,
            summary_ref=summary_artifact.ref,
            outstanding_questions=[error.next_action],
            side_effects=[SideEffect(operation_id=f"op-{attempt.attempt_id}", kind="workspace_edit", external_id=attempt.backend_session_id, evidence_ref=attempt.patch_ref, outcome=SideEffectOutcome.UNKNOWN)] if attempt.side_effects_unknown else [],
            reconciliation_required=attempt.side_effects_unknown,
        )

    def _commit_result(
        self,
        task: Task,
        attempt: _Attempt,
        result: TaskResult,
        state: TaskState,
        event_details: dict[str, Any],
        *,
        checkpoint: Checkpoint | None = None,
        pause_scope: bool = False,
    ) -> None:
        now = self.ctx.clock()
        result_id = self.ctx.ids("result")
        with self.ctx.db.transaction():
            self._assert_fence(task, attempt)
            self._cas_state(task.task_id, TaskState.RUNNING, state, attempt.task_revision)
            self.ctx.db.execute(
                "INSERT INTO task_results(project_id, result_id, task_id, attempt_id, team_id, outcome, created_at, result_json) VALUES (?,?,?,?,?,?,?,?)",
                (self.ctx.project_id, result_id, task.task_id, attempt.attempt_id, task.team_id.value, result.outcome.value, now, canonical_bytes(result.to_json_dict()).decode()),
            )
            if checkpoint is not None:
                self._insert_checkpoint(checkpoint)
            self._end_attempt(attempt, result.error, result_id, checkpoint.checkpoint_id if checkpoint else None)
            if pause_scope and result.error is not None:
                self.quotas.pause(attempt.quota_scope_id, result.error, task.task_id)
            repo.insert_event(
                self.ctx.db, self.ctx.project_id, self.ctx.ids("event"), f"task.{state.value}",
                {"result": result.to_json_dict(), **event_details}, now,
                team_id=task.team_id.value, task_id=task.task_id, attempt_id=attempt.attempt_id,
            )
        self.leases.release(attempt.lease)

    def _insert_checkpoint(self, cp: Checkpoint) -> None:
        d = cp.to_json_dict()
        self.ctx.db.execute(
            "INSERT INTO checkpoints(project_id, checkpoint_id, task_id, attempt_id, team_id, created_at, fencing_generation, task_graph_revision,"
            " input_deliveries_json, input_refs_json, baseline_source_ref_json, candidate_source_ref_json, patch_ref_json, intermediate_refs_json,"
            " last_event_id, backend_session_id, summary_ref_json, outstanding_questions_json, side_effects_json, reconciliation_required)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                self.ctx.project_id, cp.checkpoint_id, cp.task_id, cp.attempt_id, cp.team_id.value, cp.created_at, cp.fencing_generation,
                cp.task_graph_revision, json.dumps(d["input_deliveries"]), json.dumps(d["input_refs"]),
                json.dumps(d["baseline_source_ref"]) if d["baseline_source_ref"] else None,
                json.dumps(d["candidate_source_ref"]) if d["candidate_source_ref"] else None,
                json.dumps(d["patch_ref"]) if d["patch_ref"] else None,
                json.dumps(d["intermediate_refs"]), cp.last_event_id, cp.backend_session_id, json.dumps(d["summary_ref"]),
                json.dumps(d["outstanding_questions"]), json.dumps(d["side_effects"]), int(cp.reconciliation_required),
            ),
        )

    def _end_attempt(self, attempt: _Attempt, error: NormalizedError | None, result_id: str | None, checkpoint_id: str | None = None) -> None:
        self.ctx.db.execute(
            "UPDATE attempts SET ended_at=?, normalized_error_json=?, result_id=?, last_checkpoint_id=COALESCE(?, last_checkpoint_id), revision=revision+1"
            " WHERE project_id=? AND attempt_id=?",
            (self.ctx.clock(), canonical_bytes(error.to_json_dict()).decode() if error else None, result_id, checkpoint_id, self.ctx.project_id, attempt.attempt_id),
        )

    def _assert_fence(self, task: Task, attempt: _Attempt) -> None:
        """Inside a transaction: the attempt must still own the task and the lease generation."""
        row = self.ctx.db.one("SELECT state, revision FROM tasks WHERE project_id=? AND task_id=?", (self.ctx.project_id, task.task_id))
        lease = self.ctx.db.one(
            "SELECT owner_id, fencing_generation FROM leases WHERE scope_kind='task_attempt' AND scope_key=?", (f"{self.ctx.project_id}/{task.task_id}",)
        )
        live = (
            row is not None
            and row["state"] == TaskState.RUNNING.value
            and row["revision"] == attempt.task_revision
            and lease is not None
            and lease["owner_id"] == attempt.lease.owner_id
            and int(lease["fencing_generation"]) == attempt.lease.fencing_generation
        )
        if not live:
            raise StaleRevision("attempt lost its fence; a late result cannot be committed", task_id=task.task_id, attempt_id=attempt.attempt_id)

    # ------------------------------------------------------------------ state helpers
    def _cas_state(self, task_id: str, current: TaskState, nxt: TaskState, expected_revision: int) -> None:
        assert_transition(current, nxt)
        cursor = self.ctx.db.execute(
            "UPDATE tasks SET state=?, revision=revision+1, updated_at=? WHERE project_id=? AND task_id=? AND state=? AND revision=?",
            (nxt.value, self.ctx.clock(), self.ctx.project_id, task_id, current.value, expected_revision),
        )
        if cursor.rowcount != 1:
            raise StaleRevision("task state changed concurrently", task_id=task_id, expected_state=current.value, expected_revision=expected_revision)

    def _transition(self, task_id: str, current: TaskState, nxt: TaskState, revision: int, reason: str, **payload: Any) -> None:
        row = self.graphs.task_row(task_id)
        with self.ctx.db.transaction():
            self._cas_state(task_id, current, nxt, revision)
            repo.insert_event(
                self.ctx.db, self.ctx.project_id, self.ctx.ids("event"), f"task.{nxt.value}",
                {"from": current.value, "reason": reason, **payload}, self.ctx.clock(), team_id=row["team_id"], task_id=task_id,
            )

    # ------------------------------------------------------------------ recovery
    def recover(self) -> list[dict[str, Any]]:
        """Mark attempts whose lease expired as interrupted, then reconcile them."""
        actions = []
        rows = self.ctx.db.all(
            "SELECT a.attempt_id, a.task_id, a.lease_expires_at, t.state, t.revision, t.team_id, a.workspace_id FROM attempts a"
            " JOIN tasks t ON t.project_id=a.project_id AND t.task_id=a.task_id WHERE a.project_id=? AND a.ended_at IS NULL",
            (self.ctx.project_id,),
        )
        now = self.ctx.clock()
        for row in rows:
            if row["state"] != TaskState.RUNNING.value or row["lease_expires_at"] > now:
                continue
            self._transition(row["task_id"], TaskState.RUNNING, TaskState.INTERRUPTED, row["revision"], "attempt lease expired without a result", attempt_id=row["attempt_id"])
            actions.append(self.reconcile(row["task_id"], row["attempt_id"]))
        return actions

    def reconcile(self, task_id: str, attempt_id: str) -> dict[str, Any]:
        """Settle an interrupted attempt. Brain-only tasks have no side effects and may be replayed."""
        row = self.graphs.task_row(task_id)
        if row["state"] != TaskState.INTERRUPTED.value:
            raise Conflict("task is not interrupted", task_id=task_id, state=row["state"])
        has_result = self.ctx.db.one("SELECT 1 FROM task_results WHERE project_id=? AND attempt_id=?", (self.ctx.project_id, attempt_id))
        attempt_row = self.ctx.db.one("SELECT workspace_id FROM attempts WHERE project_id=? AND attempt_id=?", (self.ctx.project_id, attempt_id))
        error = _unknown_error("process or coordinator lost before a result was recorded")
        error = error.model_copy(update={"completion_certainty": CompletionCertainty.UNKNOWN, "next_action": "replayed from pinned inputs"})
        if has_result:
            nxt, reason = TaskState.COMPLETED, "result had been committed before interruption"
        elif (attempt_row is None or attempt_row["workspace_id"] is None) and self.graphs.load_task(task_id).workspace_ref is None:
            nxt, reason = TaskState.PENDING, "no workspace side effects; safe to replay from pinned inputs"
        else:
            nxt, reason = TaskState.NEEDS_INPUT, "coding workspace may hold partial edits; reconcile before retry"
        with self.ctx.db.transaction():
            self._cas_state(task_id, TaskState.INTERRUPTED, nxt, row["revision"])
            self.ctx.db.execute(
                "UPDATE attempts SET ended_at=?, normalized_error_json=?, revision=revision+1 WHERE project_id=? AND attempt_id=? AND ended_at IS NULL",
                (self.ctx.clock(), canonical_bytes(error.to_json_dict()).decode(), self.ctx.project_id, attempt_id),
            )
            repo.insert_event(
                self.ctx.db, self.ctx.project_id, self.ctx.ids("event"), "task.reconciled",
                {"attempt_id": attempt_id, "to": nxt.value, "reason": reason}, self.ctx.clock(), team_id=row["team_id"], task_id=task_id, attempt_id=attempt_id,
            )
        return {"task_id": task_id, "attempt_id": attempt_id, "state": nxt.value, "reason": reason}

    # ------------------------------------------------------------------ participant actions
    def resume(self, task_id: str, *, dry_run: bool = False, quota_restored_by: str | None = None) -> dict[str, Any]:
        """Return a paused/needs_input task to pending. ``dry_run`` makes no change and no call."""
        task = self.graphs.load_task(task_id)
        row = self.graphs.task_row(task_id)
        state = TaskState(row["state"])
        connection = self.ctx.config.connections[task.backend_profile]
        plan: dict[str, Any] = {
            "task_id": task_id,
            "team": task.team_id.value,
            "operation": task.operation,
            "state_before": state.value,
            "connection": task.backend_profile,
            "billable": connection.type.value != "fixture",
            "inputs": [r.to_json_dict() for r in task.input_refs],
            "dry_run": dry_run,
            "model_calls_made": 0,
        }
        if state not in (TaskState.PAUSED, TaskState.NEEDS_INPUT):
            plan["blocked"] = f"task is {state.value}; only paused or needs_input tasks can be resumed"
            return plan
        pause = self.quotas.is_paused(connection.quota_scope_id)
        if pause is not None and quota_restored_by is None:
            plan["blocked"] = (
                f"quota scope {pause.quota_scope_id!r} is still recorded as paused ({pause.message}). "
                "When your limit has reset, rerun with --quota-restored; the runtime never assumes it."
            )
            return plan
        plan["will"] = "set task to pending; the next `sdlc stage run` dispatches it"
        if dry_run:
            return plan
        if pause is not None and quota_restored_by is not None:
            self.quotas.restore(pause.quota_scope_id, quota_restored_by, f"declared restored while resuming {task_id}")
            plan["quota_restored"] = pause.quota_scope_id
        self._transition(task_id, state, TaskState.PENDING, row["revision"], "participant resumed", actor=quota_restored_by or "participant")
        plan["state_after"] = TaskState.PENDING.value
        return plan

    def cancel(self, task_id: str, reason: str) -> dict[str, Any]:
        row = self.graphs.task_row(task_id)
        state = TaskState(row["state"])
        if state in TERMINAL_STATES:
            raise Conflict("task already terminal", task_id=task_id, state=state.value)
        self._transition(task_id, state, TaskState.CANCELLED, row["revision"], reason)
        self.ctx.db.execute(
            "UPDATE attempts SET ended_at=?, revision=revision+1 WHERE project_id=? AND task_id=? AND ended_at IS NULL", (self.ctx.clock(), self.ctx.project_id, task_id)
        )
        return {"task_id": task_id, "state": TaskState.CANCELLED.value}

    def rerun(self, task_id: str) -> str:
        """A successor task for a terminal task: new id, same intent, linked by successor_of_task_id."""
        row = self.graphs.task_row(task_id)
        if row["state"] not in {s.value for s in TERMINAL_STATES}:
            raise Conflict("only terminal tasks get a successor; resume non-terminal tasks instead", task_id=task_id, state=row["state"])
        task = self.graphs.load_task(task_id)
        n = 2
        while self.ctx.db.one("SELECT 1 FROM tasks WHERE project_id=? AND task_id=?", (self.ctx.project_id, f"{task_id}-r{n}")):
            n += 1
        successor = task.model_copy(update={"task_id": f"{task_id}-r{n}", "agent_instance_id": f"{task.agent_instance_id}-r{n}"})
        self.graphs.admit(task.team_id, [successor])
        self.ctx.db.execute(
            "UPDATE tasks SET successor_of_task_id=? WHERE project_id=? AND task_id=?", (task_id, self.ctx.project_id, successor.task_id)
        )
        return successor.task_id

    # ------------------------------------------------------------------ status
    def describe(self, team: TeamId | None = None) -> list[dict[str, Any]]:
        """Human-readable status per task: state, cause, preserved work, next action."""
        out = []
        for row in self.graphs.list_tasks(team):
            task = self.graphs.load_task(row["task_id"])
            entry: dict[str, Any] = {
                "task_id": row["task_id"],
                "team": row["team_id"],
                "operation": task.operation,
                "state": row["state"],
                "attempts": int(self.ctx.db.one("SELECT count(*) AS n FROM attempts WHERE project_id=? AND task_id=?", (self.ctx.project_id, row["task_id"]))["n"]),
                "retry_count": row["retry_count"],
                "next_attempt_at": row["next_attempt_at"],
            }
            last = self.ctx.db.one(
                "SELECT event_type, payload_json FROM events WHERE project_id=? AND task_id=? ORDER BY sequence_number DESC LIMIT 1",
                (self.ctx.project_id, row["task_id"]),
            )
            if last is not None:
                payload = json.loads(last["payload_json"])
                error = (payload.get("result") or {}).get("error") or payload.get("error")
                if error:
                    entry["cause"] = f"{error['category']}: {error['message']}"
                    entry["next_action"] = error["next_action"]
                if "checkpoint_id" in payload:
                    entry["preserved"] = f"checkpoint {payload['checkpoint_id']}"
            if row["state"] == TaskState.COMPLETED.value:
                result = self.ctx.db.one("SELECT result_json FROM task_results WHERE project_id=? AND task_id=? ORDER BY created_at DESC LIMIT 1", (self.ctx.project_id, row["task_id"]))
                if result:
                    entry["artifacts"] = [f"{r['artifact_id']}@{r['revision']}" for r in json.loads(result["result_json"])["artifact_refs"]]
            if row["state"] in (TaskState.PAUSED.value, TaskState.NEEDS_INPUT.value):
                entry["resume"] = f"sdlc task resume {row['task_id']} --project {self.ctx.project_id}"
                result = self.ctx.db.one("SELECT result_json FROM task_results WHERE project_id=? AND task_id=? ORDER BY created_at DESC LIMIT 1", (self.ctx.project_id, row["task_id"]))
                questions = json.loads(result["result_json"]).get("questions") if result else None
                if questions:
                    entry["questions"] = questions
                    entry["preserved_documents"] = [f"{r['artifact_id']}@{r['revision']}" for r in json.loads(result["result_json"])["artifact_refs"]]
            out.append(entry)
        return out


#: JSON document kinds described by their fields (no schema definition of their own).
JSON_OBJECT_KINDS: dict[str, str] = {
    "impact_analysis": "an object with fields earliest_owner (team name), classification (corrective|adaptive|perfective|preventive), affected (list of {artifact_id, revision, reason}), requirement_ids (list), rationale (string), revalidation (list of strings)",
}

#: JSON document kinds whose contract is a list of records: kind -> (item definition, shape).
STRUCTURED_LIST_KINDS: dict[str, tuple[str, str]] = {"test_cases": ("TestCase", '{"cases": [TestCase, ...]}')}


# ----------------------------------------------------------------------------- helpers


def _drop_unusable_finding_refs(structured: Any) -> None:
    """Remove ``findings[].related_refs`` entries whose sha256 is not 64 hex characters (models
    often emit placeholders); an empty reference is noise, not a reason to reject the proposal."""
    if not isinstance(structured, dict):
        return
    for finding in structured.get("findings") or []:
        refs = finding.get("related_refs") if isinstance(finding, dict) else None
        if isinstance(refs, list):
            finding["related_refs"] = [r for r in refs if isinstance(r, dict) and isinstance(r.get("sha256"), str) and len(r["sha256"]) == 64 and all(c in "0123456789abcdef" for c in r["sha256"].lower())]


def _finding(code: str, message: str) -> Finding:
    return Finding(finding_id=f"finding-{code}", code=code, severity=Severity.BLOCKING, message=message, related_refs=[])


def _unknown_usage() -> Usage:
    return Usage(input_tokens=None, output_tokens=None, cost_microunits=None, currency=None, measurement="unknown")


def _unknown_error(message: str) -> NormalizedError:
    return NormalizedError(
        category=ErrorCategory.UNKNOWN,
        provider_code=None,
        message=message,
        automatic_retry_now=False,
        retry_after_seconds=None,
        reset_at=None,
        completion_certainty=CompletionCertainty.UNKNOWN,
        next_action="inspect the diagnostics, then resume the task",
        diagnostic_ref=None,
    )


def _invalid_output(message: str, details: dict[str, Any] | None = None) -> NormalizedError:
    detail = ""
    if details and details.get("errors"):
        detail = " " + json.dumps(details["errors"][:3], ensure_ascii=False, default=str)[:300]
    return NormalizedError(
        category=ErrorCategory.INVALID_OUTPUT,
        provider_code=None,
        message=f"{message}{detail}",
        automatic_retry_now=True,
        retry_after_seconds=0,
        reset_at=None,
        completion_certainty=CompletionCertainty.NOT_COMPLETED,
        next_action="the runtime retries once with the same inputs; afterwards review the rejected output",
        diagnostic_ref=None,
    )


def normalized_missing(message: str) -> NormalizedError:
    return NormalizedError(category=ErrorCategory.PERMISSION_REQUIRED, provider_code=None, message=message, automatic_retry_now=False, retry_after_seconds=None, reset_at=None,
                           completion_certainty=CompletionCertainty.NOT_COMPLETED, next_action="pin a candidate snapshot (run implementation.code) and resume", diagnostic_ref=None)
