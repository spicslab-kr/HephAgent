"""Stage driver for both decision modes (handoff BR-001/BR-002, docs/03 s9).

``CycleRunner.run`` walks the forward stages for one project: for each team it proposes
the leader plan, runs it, approves the proposed graph, runs the workers, composes and
delivers the handoff, and accepts the next team's baseline. Two decision modes:

* **auto** (project mode ``continuous``): gates are decided by the policy and recorded
  with ``actor_kind: policy``; never as a participant signature.
* **ask** (project mode ``collaborative``): a ``gate`` callback asks the participant at
  every approval and baseline acceptance; decisions are recorded as the participant.

The driver is restartable: it inspects durable state and skips what is done. It stops
with a readable reason whenever the runtime would need a person: a task in
``needs_input`` or ``paused``, a rejected leader graph, a stale delivery, a missing
input, or a gate answered "no".
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sdlc.application.handoffs import Actor, ActorKind, HandoffService
from sdlc.application.projects import ProjectContext
from sdlc.application.views import TeamView
from sdlc.application.workflow import WorkflowService
from sdlc.domain.errors import Conflict, ServiceError
from sdlc.domain.models import ArtifactRef, BundleKind, OwnerTeam, TeamId
from sdlc.domain.states import TERMINAL_STATES, TaskState
from sdlc.domain.teams import TEAM_ORDER
from sdlc.runtime.scheduler import Scheduler
from sdlc.storage import repositories as repo

POLICY_REVISION = "cycle-run-v1"
#: Stages whose team operations are implemented; later ones are refused with a clear message.
IMPLEMENTED_STAGES: tuple[str, ...] = ("planning", "requirements", "design", "implementation", "testing", "maintenance")

#: How many times one `run` routes a Maintenance change package to its owner and re-runs the cycle.
DEFAULT_CHANGE_LOOPS = 1

#: How many times one `run` sends defects back to Implementation and re-tests the corrected candidate.
DEFAULT_DEFECT_LOOPS = 1
DEFAULT_OBJECTIVES = {
    "planning": "Produce the project plan: problem, scope, feasibility, work plan, risks",
    "requirements": "Produce testable functional and nonfunctional requirements with acceptance conditions",
    "design": "Produce an implementable architecture, contracts and behaviour that realise every must requirement",
    "implementation": "Implement the accepted design as a reproducible candidate",
    "testing": "Build and execute the testbench and recommend a release decision",
    "maintenance": "Triage and route change requests against the released baseline",
}

#: gate(kind, summary) -> True to proceed. kind is "approve_plan" or "accept_handoff".
Gate = Callable[[str, dict[str, Any]], bool]


@dataclass
class StageReport:
    team: str
    status: str  # completed | stopped | skipped
    steps: list[str] = field(default_factory=list)
    reason: str | None = None


@dataclass
class CycleReport:
    stages: list[StageReport] = field(default_factory=list)
    stopped: bool = False
    next_action: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "stages": [{"team": s.team, "status": s.status, "steps": s.steps, "reason": s.reason} for s in self.stages],
            "stopped": self.stopped,
            "next_action": self.next_action,
        }


def _is_plan(row: dict, team: TeamId) -> bool:
    """The leader's planning task, recognised by its operation (a worker may be *named* ...-plan-...)."""
    return json.loads(row["task_json"]).get("operation") == f"{team.value}.plan_work"


class CycleRunner:
    def __init__(self, context: ProjectContext, scheduler: Scheduler, *, gate: Gate | None = None, participant_id: str = "participant") -> None:
        self.ctx = context
        self.scheduler = scheduler
        self.handoffs = HandoffService(context)
        self.workflow = WorkflowService(context, scheduler)
        self.gate = gate
        if context.config.project.mode == "continuous":
            self.actor = Actor(ActorKind.POLICY, "cycle-run", policy_revision=POLICY_REVISION)
        else:
            if gate is None:
                raise Conflict(
                    "collaborative mode needs a participant at each gate: run with --ask, or switch the project to automatic mode",
                    mode=context.config.project.mode,
                )
            self.actor = Actor(ActorKind.PARTICIPANT, participant_id)

    # ------------------------------------------------------------------ driver
    def run(self, *, start: TeamId, end: TeamId, objectives: dict[str, str] | None = None, max_rounds: int = 6, replan: bool = False, defect_loops: int = DEFAULT_DEFECT_LOOPS, change_loops: int = DEFAULT_CHANGE_LOOPS) -> CycleReport:
        """``replan`` redoes the *start* stage: its unfinished tasks are cancelled and the leader plans again."""
        order = [TeamId(t.value) for t in TEAM_ORDER]
        if order.index(end) < order.index(start):
            raise Conflict("end stage comes before start stage", start=start.value, end=end.value)
        report = CycleReport()
        for team in order[order.index(start) : order.index(end) + 1]:
            if team.value not in IMPLEMENTED_STAGES:
                report.stages.append(StageReport(team=team.value, status="skipped", reason=f"the {team.value} stage is not implemented yet (milestones M5-M7); stopping here"))
                report.stopped, report.next_action = True, f"{team.value} is not available yet; the finished stages are intact"
                return report
            index = order.index(team)
            inbox_from_previous = index > 0 and any(d["from"] == order[index - 1].value for d in TeamView(self.ctx, team).list_inbox())
            if index > 0 and not inbox_from_previous and self.stage_finished(order[index - 1]):
                # A previous run stopped after the earlier stage (e.g. --until); hand its package on now
                # (a participant memo already in the inbox does not count as the previous stage's package).
                pending = self._handoff(order[index - 1], team)
                report.stages.append(pending)
                if pending.status == "stopped":
                    report.stopped, report.next_action = True, pending.reason
                    return report
            stage = self._run_stage(team, (objectives or {}).get(team.value) or DEFAULT_OBJECTIVES[team.value], max_rounds, replan=replan and team == start)
            report.stages.append(stage)
            if stage.status == "stopped":
                report.stopped, report.next_action = True, stage.reason
                return report
            if team == TeamId.TESTING:
                if not self._defect_loop(report, objectives or {}, max_rounds, defect_loops):
                    return report
                report.stages.append(self._release_step())
            if team == TeamId.MAINTENANCE and not self._change_loop(report, objectives or {}, max_rounds, defect_loops, change_loops):
                return report
            if team != end and index + 1 < len(order):
                handoff = self._handoff(team, order[index + 1])
                stage.steps.extend(handoff.steps)
                if handoff.status == "stopped":
                    report.stopped, report.next_action = True, handoff.reason
                    return report
        return report

    def next_stage(self) -> TeamId | None:
        """The first implemented stage that is not finished, or None when all are done."""
        for team in [TeamId(t.value) for t in TEAM_ORDER]:
            if team.value not in IMPLEMENTED_STAGES:
                return None
            if not self.stage_finished(team):
                return team
        return None

    def stage_finished(self, team: TeamId) -> bool:
        tasks = self.scheduler.graphs.list_tasks(team)
        workers = [t for t in tasks if not _is_plan(t, team) and t["state"] != TaskState.CANCELLED.value]
        return bool(workers) and all(t["state"] == TaskState.COMPLETED.value for t in workers)

    # ------------------------------------------------------------------ one stage
    def _run_stage(self, team: TeamId, objective: str, max_rounds: int, replan: bool = False) -> StageReport:
        stage = StageReport(team=team.value, status="completed")
        view = TeamView(self.ctx, team)
        if not view.list_inbox():
            stage.status, stage.reason = "stopped", f"{team.value} has nothing to work from yet: import a brief (sdlc new), a request memo (sdlc request) or finish the previous stage"
            return stage
        tasks = self.scheduler.graphs.list_tasks(team)
        plan_tasks = [t for t in tasks if _is_plan(t, team) and t["state"] != TaskState.CANCELLED.value]
        worker_tasks = [t for t in tasks if not _is_plan(t, team) and t["state"] != TaskState.CANCELLED.value]
        if replan and tasks:
            # Redo the stage: unfinished work is cancelled (completed tasks and their documents
            # stay), the leader plans again against the current inputs under fresh task ids.
            for row in worker_tasks:
                if TaskState(row["state"]) not in TERMINAL_STATES:
                    self.scheduler.cancel(row["task_id"], "stage redone by the participant (sdlc run --again)")
                    stage.steps.append(f"{row['task_id']}: cancelled (stage redone)")
            used = sorted(t["task_id"] for t in tasks)
            objective = (
                f"{objective}\n\nThis is a re-plan of the {team.value} stage: inputs changed. "
                f"Task ids already used in this project (choose NEW ids, e.g. with a -r2 suffix): {', '.join(used)}"
            )
            worker_tasks, plan_tasks = [], []
        if not worker_tasks:
            plan = plan_tasks[-1] if plan_tasks else None
            if plan is None:
                ref = self.workflow.propose_stage_plan(team, objective=objective)
                plan = self.scheduler.graphs.task_row(ref.task_id)
                stage.steps.append(f"{team.value} leader asked for a work plan ({ref.task_id})")
            if plan["state"] != TaskState.COMPLETED.value:
                outcome = self._run_until_settled(team, plan["task_id"], max_rounds)
                if outcome != TaskState.COMPLETED:
                    stage.status, stage.reason = "stopped", f"the {team.value} leader's plan task is {outcome.value}; see: sdlc show {self.ctx.project_id} --stage {team.value}"
                    return stage
                stage.steps.append("leader proposed a task graph")
            proposed = self.workflow.preview_plan(plan["task_id"])
            if self.gate is not None and not self.gate("approve_plan", {"team": team.value, "tasks": proposed}):
                stage.status, stage.reason = "stopped", f"you declined the {team.value} task graph; edit the objective or rerun the leader with: sdlc task rerun {plan['task_id']}"
                return stage
            try:
                approved = self.workflow.approve_plan(plan["task_id"], self.actor, rationale=self._rationale("task graph admitted"))
            except ServiceError as exc:
                stage.status, stage.reason = "stopped", f"the proposed task graph was rejected by the runtime: {exc.message} {exc.details}"
                return stage
            stage.steps.append(f"task graph of {len(approved['tasks'])} tasks admitted ({self.actor.kind.value} decision {approved['decision_id']})")
        for _ in range(max_rounds):
            outcomes = asyncio.run(self.scheduler.run_ready(team))
            for o in outcomes:
                stage.steps.append(f"{o.task_id}: {o.state.value}" + (f" ({o.reason[:140]})" if o.reason and o.state != TaskState.COMPLETED else ""))
            states = {o.state for o in outcomes}
            if not outcomes or states <= {TaskState.COMPLETED} or TaskState.RETRY_WAIT not in states:
                break
        remaining = [t for t in self.scheduler.graphs.list_tasks(team) if t["state"] not in (TaskState.COMPLETED.value, TaskState.CANCELLED.value)]
        if remaining:
            blocked = ", ".join(f"{t['task_id']} ({t['state']})" for t in remaining)
            stage.status, stage.reason = "stopped", f"{team.value} has unfinished work: {blocked}. See why with: sdlc show {self.ctx.project_id} --stage {team.value}; then: sdlc continue {self.ctx.project_id}"
        return stage

    # ------------------------------------------------------------------ release (docs/04 s6-7)
    def _release_step(self) -> StageReport:
        """Record a release when the evidence allows it; otherwise say exactly why not."""
        from sdlc.application.release import ReleaseService

        step = StageReport(team="release", status="completed")
        service = ReleaseService(self.ctx)
        gate = service.gate()
        if not gate.allowed:
            step.steps.append("no release: " + "; ".join(gate.reasons))
            return step
        if self.gate is not None and not self.gate("release", {"candidate": gate.candidate_ref.to_json_dict() if gate.candidate_ref else None, "counts": gate.counts, "conformance": gate.conformance}):
            step.steps.append("release declined by you; the candidate stays tested but unreleased")
            return step
        artifact, gate = service.create(self.actor, rationale=self._rationale("release gate passed"))
        step.steps.append(f"{artifact.artifact_id.removesuffix('-manifest')} recorded ({gate.conformance}; verdicts {gate.counts})")
        return step

    # ------------------------------------------------------------------ change loop (docs/04 s7)
    def _change_loop(self, report: CycleReport, objectives: dict[str, str], max_rounds: int, defect_loops: int, budget: int) -> bool:
        """After the maintenance stage: route the change package to the earliest affected owner,
        stale the dependent outputs, re-run owner..testing (fresh plans, forced handoffs, the
        defect loop, a new release), then let Maintenance verify closure."""
        from sdlc.application.changes import ChangeService
        from sdlc.teams.maintenance.operations import latest_impact

        order = [TeamId(x.value) for x in TEAM_ORDER]
        for round_no in range(1, budget + 2):
            found = latest_impact(TeamView(self.ctx, TeamId.MAINTENANCE))
            if found is None:
                return True
            impact_ref, impact = found
            if self._already_routed(impact_ref):
                return True
            owner = TeamId(impact["earliest_owner"])
            step = StageReport(team=f"maintenance->{owner.value} (change)", status="completed")
            if self.gate is not None and not self.gate("accept_handoff", {"from": "maintenance", "to": owner.value, "kind": "change_request", "impact": impact}):
                step.status, step.reason = "stopped", f"you declined the change package; edit the memo or impact analysis, then: sdlc continue {self.ctx.project_id}"
                report.stages.append(step)
                report.stopped, report.next_action = True, step.reason
                return False
            result = ChangeService(self.ctx, self.actor).deliver_change_package(owner, summary=f"change request ({impact.get('classification')}): {impact.get('rationale', '')[:160]}")
            self._record_routed(impact_ref, result)
            step.steps.append(f"change package {result.bundle_id} delivered to {owner.value}; stale outputs: {result.stale_artifacts}")
            report.stages.append(step)
            if round_no > budget:  # the package is delivered and dependents are stale; the re-run is the participant's call
                report.stopped = True
                report.next_action = (f"the change package is with {owner.value} and dependent outputs are stale; the automatic re-run budget ({budget}) is spent. "
                                      f"Run the cycle yourself: sdlc run {self.ctx.project_id} --stage {owner.value} --until maintenance --again")
                return False
            # owner .. testing, each on fresh plans and fresh packages
            previous: TeamId | None = None
            for team in order[order.index(owner): order.index(TeamId.TESTING) + 1]:
                if previous is not None:
                    forward = self._handoff(previous, team, force=True)
                    report.stages.append(forward)
                    if forward.status == "stopped":
                        report.stopped, report.next_action = True, forward.reason
                        return False
                objective = (objectives.get(team.value) or DEFAULT_OBJECTIVES[team.value]) + f". A change request from Maintenance reopened this work (round {round_no}); apply CHANGE_SPEC.md on the current baseline."
                stage = self._run_stage(team, objective, max_rounds, replan=True)
                stage.team = f"{team.value} (change round {round_no})"
                report.stages.append(stage)
                if stage.status == "stopped":
                    report.stopped, report.next_action = True, stage.reason
                    return False
                if team == TeamId.TESTING:
                    if not self._defect_loop(report, objectives, max_rounds, defect_loops):
                        return False
                    report.stages.append(self._release_step())
                previous = team
            back = self._handoff(TeamId.TESTING, TeamId.MAINTENANCE, force=True)
            report.stages.append(back)
            if back.status == "stopped":
                report.stopped, report.next_action = True, back.reason
                return False
            closure = self._run_stage(TeamId.MAINTENANCE, (objectives.get("maintenance") or DEFAULT_OBJECTIVES["maintenance"]) + ". The owning team and Testing have returned revised packages: verify closure of the request and write the maintenance report.", max_rounds, replan=True)
            closure.team = f"maintenance (closure round {round_no})"
            report.stages.append(closure)
            if closure.status == "stopped":
                report.stopped, report.next_action = True, closure.reason
                return False
        return True

    def _already_routed(self, impact_ref: ArtifactRef) -> bool:
        row = self.ctx.db.one(
            "SELECT 1 FROM events WHERE project_id=? AND event_type='change.routed' AND payload_json LIKE ? LIMIT 1",
            (self.ctx.project_id, f'%{impact_ref.sha256}%'),
        )
        return row is not None

    def _record_routed(self, impact_ref: ArtifactRef, result) -> None:
        with self.ctx.db.transaction():
            repo.insert_event(self.ctx.db, self.ctx.project_id, self.ctx.ids("event"), "change.routed",
                              {"impact": impact_ref.to_json_dict(), "owner": result.owner, "delivery_id": result.delivery_id, "bundle_id": result.bundle_id, "stale": result.stale_artifacts},
                              self.ctx.clock(), team_id="maintenance")

    # ------------------------------------------------------------------ defect loop (docs/04 s6)
    def open_defects(self) -> list[dict]:
        """Critical/major `defect` records of the latest completed testing.analyze task."""
        latest = None
        for row in self.scheduler.graphs.list_tasks(TeamId.TESTING):
            if json.loads(row["task_json"]).get("operation") == "testing.analyze" and row["state"] == TaskState.COMPLETED.value:
                latest = row
        if latest is None:
            return []
        result = self.ctx.db.one("SELECT result_json FROM task_results WHERE project_id=? AND task_id=? AND outcome='completed' ORDER BY created_at DESC LIMIT 1", (self.ctx.project_id, latest["task_id"]))
        if result is None:
            return []
        defects = []
        for raw in json.loads(result["result_json"])["artifact_refs"]:
            ref = ArtifactRef.model_validate(raw)
            artifact = self.scheduler.artifacts.get(ref)
            if artifact.kind != "defect":
                continue
            payload = json.loads(self.scheduler.artifacts.read_bytes(ref).decode("utf-8"))
            if payload.get("severity") in ("critical", "major"):
                defects.append({"ref": ref, **payload})
        return defects

    def _defect_loop(self, report: CycleReport, objectives: dict[str, str], max_rounds: int, budget: int) -> bool:
        """After the testing stage: send open defects back as a defect package, let Implementation
        repair under a fresh plan, hand the corrected candidate to Testing and re-test it. Returns
        False when the run must stop (defects remain and the budget is spent, or a stage stopped)."""
        for round_no in range(1, budget + 2):
            defects = self.open_defects()
            if not defects:
                return True
            if round_no > budget:
                report.stopped = True
                report.next_action = (f"testing found {len(defects)} open defect(s) after {budget} repair loop(s): " + ", ".join(d["defect_id"] for d in defects)
                                      + f". Decide: fix by hand (sdlc takeover), or run another loop: sdlc run {self.ctx.project_id} --stage implementation --again, then --stage testing --again")
                return False
            handoff = self._defect_handoff(defects)
            report.stages.append(handoff)
            if handoff.status == "stopped":
                report.stopped, report.next_action = True, handoff.reason
                return False
            objective = (objectives.get("implementation") or DEFAULT_OBJECTIVES["implementation"]) + f". Repair the defects delivered by Testing ({', '.join(d['defect_id'] for d in defects)}) on the current candidate; keep everything that passed."
            fix = self._run_stage(TeamId.IMPLEMENTATION, objective, max_rounds, replan=True)
            fix.team = f"implementation (repair loop {round_no})"
            report.stages.append(fix)
            if fix.status == "stopped":
                report.stopped, report.next_action = True, fix.reason
                return False
            forward = self._handoff(TeamId.IMPLEMENTATION, TeamId.TESTING, force=True)
            fix.steps.extend(forward.steps)
            if forward.status == "stopped":
                report.stopped, report.next_action = True, forward.reason
                return False
            retest = self._run_stage(TeamId.TESTING, (objectives.get("testing") or DEFAULT_OBJECTIVES["testing"]) + ". Re-test the corrected candidate; keep the regression scope.", max_rounds, replan=True)
            retest.team = f"testing (re-test {round_no})"
            report.stages.append(retest)
            if retest.status == "stopped":
                report.stopped, report.next_action = True, retest.reason
                return False
        return True

    def _defect_handoff(self, defects: list[dict]) -> StageReport:
        step = StageReport(team="testing->implementation (defects)", status="completed")
        refs = self.handoffs.compose_manifest(TeamId.TESTING, kinds=["defect", "defect_report", "execution_record", "execution_report", "test_cases", "test_plan", "test_report", "bench_design"])
        if self.gate is not None and not self.gate("accept_handoff", {"from": "testing", "to": "implementation", "kind": "defect", "artifacts": [f"{r.artifact_id}@{r.revision}" for r in refs]}):
            step.status, step.reason = "stopped", f"you declined the defect package; fix by hand or rerun: sdlc continue {self.ctx.project_id}"
            return step
        count = len(repo.list_bundles(self.ctx.db, self.ctx.project_id))
        current, _ = self.handoffs.baseline(TeamId.IMPLEMENTATION)
        _, bundle_ref = self.handoffs.publish(
            source=OwnerTeam.TESTING, target=TeamId.IMPLEMENTATION, kind=BundleKind.DEFECT, bundle_id=f"b-defects-{count + 1:03d}", cycle_id="cycle-001",
            baseline_id=f"baseline-defects-{count + 1:03d}", artifact_refs=refs, summary="defect package: " + ", ".join(f"{d['defect_id']} ({d['severity']})" for d in defects),
        )
        delivery = self.handoffs.deliver(bundle_ref=bundle_ref, expected_target_baseline=current)
        receipt = self.handoffs.receive(delivery.delivery_id)
        if receipt.status.value != "accepted":
            step.status, step.reason = "stopped", f"defect delivery {delivery.delivery_id} was {receipt.status.value}: {receipt.reason}"
            return step
        decision = self.handoffs.record_decision(self.actor, scope="defect_package", refs={"delivery_id": delivery.delivery_id, "bundle": bundle_ref.to_json_dict()},
                                                 rationale=self._rationale("defects routed to implementation as an artifact package"), disposition="accepted", team=TeamId.IMPLEMENTATION)
        step.steps.append(f"defect package {bundle_ref.bundle_id} ({len(defects)} defect(s)) delivered to implementation ({self.actor.kind.value} decision {decision})")
        return step

    def _run_until_settled(self, team: TeamId, task_id: str, max_rounds: int) -> TaskState:
        for _ in range(max_rounds):
            self.scheduler.recover()
            self.scheduler.ready()
            row = self.scheduler.graphs.task_row(task_id)
            if row["state"] == TaskState.PENDING.value:
                outcome = asyncio.run(self.scheduler.run_task(task_id))
                if outcome.state == TaskState.RETRY_WAIT:
                    continue
                return outcome.state
            return TaskState(row["state"])
        return TaskState(self.scheduler.graphs.task_row(task_id)["state"])

    # ------------------------------------------------------------------ handoff
    def _handoff(self, team: TeamId, nxt: TeamId, force: bool = False) -> StageReport:
        """Publish ``team``'s current package to ``nxt``; ``force`` republishes even when ``nxt``
        already works from an earlier package of ``team`` (a corrected candidate)."""
        step = StageReport(team=f"{team.value}->{nxt.value}", status="completed")
        current, _ = self.handoffs.baseline(nxt)
        if current is not None and not force:
            found = repo.get_bundle(self.ctx.db, self.ctx.project_id, current)
            if found is not None and found[0].source_team.value == team.value:
                step.steps.append(f"{nxt.value} already works from {team.value}'s package {current}")
                return step
        refs = self.handoffs.compose_manifest(team)
        if self.gate is not None and not self.gate("accept_handoff", {"from": team.value, "to": nxt.value, "artifacts": [f"{r.artifact_id}@{r.revision}" for r in refs]}):
            step.status, step.reason = "stopped", f"you declined the {team.value} package; revise its documents, then: sdlc continue {self.ctx.project_id}"
            return step
        count = len(repo.list_bundles(self.ctx.db, self.ctx.project_id))
        _, bundle_ref = self.handoffs.publish(
            source=OwnerTeam(team.value),
            target=nxt,
            kind=BundleKind.FORWARD,
            bundle_id=f"b-{team.value}-{count + 1:03d}",
            cycle_id="cycle-001",
            baseline_id=f"baseline-{team.value}-{count + 1:03d}",
            artifact_refs=refs,
            summary=f"{team.value} package for {nxt.value}: {len(refs)} artifact(s) including forwarded inputs",
        )
        delivery = self.handoffs.deliver(bundle_ref=bundle_ref, expected_target_baseline=current)
        receipt = self.handoffs.receive(delivery.delivery_id)
        step.steps.append(f"package {bundle_ref.bundle_id} delivered to {nxt.value}: {receipt.status.value}")
        if receipt.status.value != "accepted":
            step.status, step.reason = "stopped", f"delivery {delivery.delivery_id} was {receipt.status.value}: {receipt.reason}"
            return step
        decision = self.handoffs.accept_baseline(team=nxt, delivery_id=delivery.delivery_id, actor=self.actor, rationale=self._rationale("package accepted as the stage's starting point"))
        step.steps.append(f"{nxt.value} now works from {bundle_ref.bundle_id} ({self.actor.kind.value} decision {decision})")
        return step

    def _rationale(self, what: str) -> str:
        if self.actor.kind == ActorKind.POLICY:
            return f"policy {POLICY_REVISION}: {what} automatically"
        return f"participant {self.actor.actor_id}: {what}"


def set_project_mode(context: ProjectContext, mode: str) -> None:
    """Persist project.mode (collaborative | continuous) with a journal event."""
    from sdlc.application.connections import ConnectionService

    service = ConnectionService(context)
    raw = service._raw()
    raw["project"]["mode"] = mode
    service._write(raw)
    with context.db.transaction():
        repo.insert_event(context.db, context.project_id, context.ids("event"), "project.mode", {"mode": mode}, context.clock())
