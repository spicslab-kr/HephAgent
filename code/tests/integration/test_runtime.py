"""Durable runtime: graphs, fenced attempts, error routing, quota pause/resume, recovery.

Covers slices of AT-004 (unenforceable capability -> needs_input, zero calls), AT-011
(quota exhaustion survives restart), AT-012 (bounded transient retries under a virtual
clock), AT-013 (no duplicate side effects after interruption) and AT-018 (state
machine, one active owner, late results cannot publish).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from sdlc.adapters.brains.fixture import FixtureBrain, FixtureScript, proposal_payload, quota_exhausted_error
from sdlc.adapters.registry import AdapterRegistry
from sdlc.application.fixtures import load_fixture
from sdlc.domain.errors import Conflict, DeniedScope, InvalidSchema, MissingInput
from sdlc.domain.models import CompletionCertainty, ErrorCategory, NormalizedError, Task, TaskLimits, TeamId
from sdlc.domain.states import TaskState
from sdlc.runtime.leases import LeaseManager
from sdlc.runtime.scheduler import ATTEMPT_LEASE_SECONDS, Scheduler
from sdlc.storage import repositories as repo

PARAMS = {"objective": "do the thing", "scope_ids": [], "requested_artifact_kinds": [], "diagram_request": None}


def _error(category: ErrorCategory, retry_after: int | None = None) -> dict:
    return NormalizedError(
        category=category, provider_code="x", message=f"scripted {category.value}", automatic_retry_now=retry_after is not None,
        retry_after_seconds=retry_after, reset_at=None, completion_certainty=CompletionCertainty.NOT_COMPLETED,
        next_action="scripted", diagnostic_ref=None,
    ).to_json_dict()


def _candidate(name: str) -> dict:
    return {"kind": "document", "media_type": "text/markdown", "relative_path": f"docs/{name}.md", "content_text": f"# {name}", "workspace_path": None}


@pytest.fixture
def world(context, cycle_fixture):
    load_fixture(context, cycle_fixture)
    req = next(a for a in cycle_fixture.artifacts if a.artifact_id == "a-requirements")

    def task(task_id, operation="design.intake", deps=(), team=TeamId.DESIGN, deliveries=("d-02",), refs=None, profile="fixture", workspace=None):
        return Task(
            schema_version="1.0", project_id=context.project_id, cycle_id="cycle-001", task_id=task_id, team_id=team,
            agent_instance_id=f"agent-{task_id}", operation=operation, operation_version=1, input_deliveries=list(deliveries),
            input_refs=[req.ref] if refs is None else refs, depends_on=list(deps), parameters=dict(PARAMS),
            expected_output_schema="#/$defs/WorkerProposal", backend_profile=profile, workspace_ref=workspace,
            limits=TaskLimits(max_attempts=3, timeout_seconds=10, max_output_tokens=100, max_cost_microunits=0),
        )

    def scheduler(script: dict | None = None, owner="coord-1", adapters=None):
        adapters = adapters or AdapterRegistry(context.config, context.root, fixture_script=FixtureScript.from_raw(script or {"steps": {}}))
        return Scheduler(context, adapters, owner_id=owner)

    def advance(seconds: int):
        now = datetime.fromisoformat(context.clock.value.replace("Z", "+00:00"))
        context.clock.value = (now + timedelta(seconds=seconds)).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    return {"ctx": context, "task": task, "scheduler": scheduler, "advance": advance, "req": req}


def run(sched, *args):
    return asyncio.run(sched.run_ready(*args))


# ----------------------------------------------------------------------------- graphs


def test_graph_rejects_cross_team_disabled_unknown_and_cycles(world):
    s, t = world["scheduler"](), world["task"]
    with pytest.raises(DeniedScope, match="own team"):
        s.graphs.admit(TeamId.DESIGN, [t("x", team=TeamId.TESTING, operation="testing.intake", deliveries=())])
    with pytest.raises(InvalidSchema, match="prerequisite policy"):
        s.graphs.admit(TeamId.DESIGN, [t("x", operation="design.inspect_system")])
    with pytest.raises(MissingInput, match="backend profile"):
        s.graphs.admit(TeamId.DESIGN, [t("x", profile="nope")])
    with pytest.raises(DeniedScope, match="dependency"):
        s.graphs.admit(TeamId.DESIGN, [t("x", deps=["ghost"])])
    with pytest.raises(InvalidSchema, match="cycle"):
        s.graphs.admit(TeamId.DESIGN, [t("a", deps=["b"]), t("b", deps=["a"])])
    with pytest.raises(DeniedScope, match="not accepted"):
        s.graphs.admit(TeamId.DESIGN, [t("x", deliveries=["d-04"])])  # testing's delivery
    assert s.graphs.admit(TeamId.DESIGN, [t("ok")]).task_ids == ("ok",)
    with pytest.raises(InvalidSchema, match="already exists"):
        s.graphs.admit(TeamId.DESIGN, [t("ok")])


# ----------------------------------------------------------------------------- capability


def test_unconfigured_connection_blocks_with_zero_invocations(world):
    s, t = world["scheduler"](), world["task"]
    s.graphs.admit(TeamId.DESIGN, [t("code", profile="codex-native")])
    [outcome] = run(s)
    assert outcome.state == TaskState.NEEDS_INPUT and "unconfigured" in outcome.reason
    assert s.graphs.task_row("code")["state"] == "needs_input"
    assert repo.list_events(world["ctx"].db, world["ctx"].project_id, "attempt.started") == []
    blocked = repo.list_events(world["ctx"].db, world["ctx"].project_id, "task.blocked")
    assert blocked[-1]["payload"]["invocations"] == 0 and blocked[-1]["payload"]["error"]["category"] == "capability_missing"


def test_policy_prerequisite_blocks_before_dispatch(world):
    s, t = world["scheduler"](), world["task"]
    s.graphs.admit(TeamId.DESIGN, [t("no-inputs", deliveries=(), refs=[])])
    [outcome] = run(s)
    assert outcome.state == TaskState.NEEDS_INPUT and "accepted delivery" in outcome.reason
    assert s.adapters.brain("fixture").total_invocations == 0


# ----------------------------------------------------------------------------- success path


def test_completed_task_registers_team_owned_artifacts_with_provenance(world):
    script = {"steps": {"task:intake": {"proposal": proposal_payload("intake", "assessed", [_candidate("INTAKE")])}}}
    s, t = world["scheduler"](script), world["task"]
    s.graphs.admit(TeamId.DESIGN, [t("intake")])
    [outcome] = run(s)
    assert outcome.state == TaskState.COMPLETED and len(outcome.artifacts) == 1
    artifact = s.artifacts.get(outcome.artifacts[0])
    assert artifact.owner_team.value == "design" and artifact.origin.value == "fixture" and artifact.created_by_task == "intake"
    assert artifact.depends_on == [world["req"].ref]
    assert s.graphs.task_row("intake")["state"] == "completed"
    assert run(s) == []  # nothing left; completed tasks are never rerun
    with pytest.raises(Conflict):
        asyncio.run(s.run_task("intake"))


def test_dependencies_gate_readiness(world):
    script = {"steps": {"default": {"proposal": {"summary": "ok"}}}}
    s, t = world["scheduler"](script), world["task"]
    s.graphs.admit(TeamId.DESIGN, [t("first"), t("second", operation="design.review", deps=["first"])])
    assert [r["task_id"] for r in s.ready()] == ["first"]
    assert [(o.task_id, o.state) for o in run(s)] == [("first", TaskState.COMPLETED), ("second", TaskState.COMPLETED)]
    assert run(s) == []


# ----------------------------------------------------------------------------- quota


def test_quota_pause_survives_restart_and_resume_requires_declaration(world):
    script = {"steps": {
        "task:a": {"proposal": {"summary": "a done"}},
        "task:b": [{"error": quota_exhausted_error().to_json_dict()}, {"proposal": {"summary": "b done"}}],
        "task:c": {"proposal": {"summary": "c done"}},
    }}
    adapters = AdapterRegistry(world["ctx"].config, world["ctx"].root, fixture_script=FixtureScript.from_raw(script))
    s1, t = world["scheduler"](adapters=adapters), world["task"]
    s1.graphs.admit(TeamId.DESIGN, [t("a"), t("b", operation="design.review", deps=["a"]), t("c", operation="design.intake", deps=["a"])])
    outcomes = {o.task_id: o.state for o in run(s1)}
    assert outcomes["a"] == TaskState.COMPLETED and outcomes["b"] == TaskState.PAUSED
    assert outcomes["c"] == TaskState.PENDING  # same quota scope: reported blocked, never dispatched
    assert adapters.brain("fixture").total_invocations == 2
    assert s1.quotas.is_paused("fixture") is not None
    assert s1.quotas.is_paused("fixture").reset_at is None  # never invented

    # "restart": a new coordinator over the same durable state
    s2 = Scheduler(world["ctx"], adapters, owner_id="coord-2")
    assert [o.state for o in run(s2)] == [TaskState.PENDING]  # c stays pending, blocked by the pause
    assert adapters.brain("fixture").total_invocations == 2
    status = {e["task_id"]: e for e in s2.describe(TeamId.DESIGN)}
    assert status["b"]["state"] == "paused" and status["b"]["preserved"].startswith("checkpoint") and "resume" in status["b"]
    assert status["a"]["state"] == "completed"

    plan = s2.resume("b", dry_run=True)
    assert "blocked" in plan and plan["model_calls_made"] == 0 and s2.graphs.task_row("b")["state"] == "paused"
    plan = s2.resume("b", dry_run=True, quota_restored_by="instructor")
    assert plan["dry_run"] and "state_after" not in plan and s2.quotas.is_paused("fixture") is not None
    plan = s2.resume("b", quota_restored_by="instructor")
    assert plan["state_after"] == "pending" and plan["quota_restored"] == "fixture"
    outcomes = {o.task_id: o.state for o in run(s2)}
    assert outcomes == {"b": TaskState.COMPLETED, "c": TaskState.COMPLETED}
    assert adapters.brain("fixture").total_invocations == 4
    attempts = world["ctx"].db.all("SELECT task_id, attempt_number FROM attempts ORDER BY attempt_id")
    assert sorted((r[0], r[1]) for r in attempts) == [("a", 1), ("b", 1), ("b", 2), ("c", 1)]


# ----------------------------------------------------------------------------- retries


def test_rate_limit_retry_wait_honours_retry_after_and_budget(world):
    script = {"steps": {"task:r": [{"error": _error(ErrorCategory.RATE_LIMITED, retry_after=5)}, {"proposal": {"summary": "ok"}}]}}
    s, t = world["scheduler"](script), world["task"]
    s.graphs.admit(TeamId.DESIGN, [t("r")])
    [o] = run(s)
    assert o.state == TaskState.RETRY_WAIT
    row = s.graphs.task_row("r")
    assert row["retry_count"] == 1 and row["next_attempt_at"] == "2026-10-09T00:00:05.000Z"
    assert run(s) == []  # virtual clock has not reached next_attempt_at
    world["advance"](5)
    [o] = run(s)
    assert o.state == TaskState.COMPLETED
    assert s.graphs.task_row("r")["state"] == "completed"


def test_transient_failures_stop_at_attempt_budget(world):
    script = {"steps": {"task:u": {"error": _error(ErrorCategory.PROVIDER_UNAVAILABLE)}}}
    s, t = world["scheduler"](script), world["task"]
    s.graphs.admit(TeamId.DESIGN, [t("u")])  # max_attempts=3
    states = []
    for _ in range(5):
        outcomes = run(s)
        states.extend(o.state for o in outcomes)
        world["advance"](120)
    assert states == [TaskState.RETRY_WAIT, TaskState.RETRY_WAIT, TaskState.PAUSED]
    assert s.adapters.brain("fixture").total_invocations == 3
    assert "budget exhausted" in s.describe(TeamId.DESIGN)[0]["next_action"]


def test_auth_required_pauses_without_retry(world):
    script = {"steps": {"task:auth": {"error": _error(ErrorCategory.AUTH_REQUIRED)}}}
    s, t = world["scheduler"](script), world["task"]
    s.graphs.admit(TeamId.DESIGN, [t("auth")])
    [o] = run(s)
    assert o.state == TaskState.PAUSED and s.quotas.is_paused("fixture").category == "auth_required"


def test_invalid_output_gets_one_repair_then_needs_input(world):
    script = {"steps": {"task:bad": [{"raw_text": "{not json"}, {"raw_text": "still not json"}, {"proposal": {"summary": "never reached"}}]}}
    s, t = world["scheduler"](script), world["task"]
    s.graphs.admit(TeamId.DESIGN, [t("bad")])
    assert run(s)[0].state == TaskState.RETRY_WAIT  # bounded repair, immediate
    assert run(s)[0].state == TaskState.NEEDS_INPUT
    assert s.adapters.brain("fixture").total_invocations == 2
    rejected = [a for a in s.artifacts.list() if a.kind == "rejected_output"]
    assert len(rejected) == 2 and s.artifacts.read_bytes(rejected[0].ref) == b"{not json"


def test_proposal_for_another_task_is_invalid_output(world):
    script = {"steps": {"task:x": {"proposal": proposal_payload("someone-else", "wrong task")}}}
    s, t = world["scheduler"](script), world["task"]
    s.graphs.admit(TeamId.DESIGN, [t("x")])
    assert run(s)[0].state == TaskState.RETRY_WAIT
    assert run(s)[0].state == TaskState.NEEDS_INPUT


# ----------------------------------------------------------------------------- fencing and recovery


class _TakeoverBrain(FixtureBrain):
    """During generation another coordinator takes the task's lease (simulated expiry)."""

    def __init__(self, world):
        super().__init__(FixtureScript.from_raw({"steps": {"default": {"proposal": {"summary": "late"}}}}))
        self.world = world

    async def generate(self, request):
        reply = await super().generate(request)
        other = LeaseManager(self.world["ctx"], "coord-other")
        self.world["advance"](ATTEMPT_LEASE_SECONDS + 1)  # our lease expired...
        other.acquire("task_attempt", f"{self.world['ctx'].project_id}/{request.task_id}", 60)  # ...and was taken over
        return reply


def test_stale_writer_cannot_commit_late_result(world):
    adapters = AdapterRegistry(world["ctx"].config, world["ctx"].root)
    adapters.register_brain("fixture", _TakeoverBrain(world))
    s, t = world["scheduler"](adapters=adapters), world["task"]
    s.graphs.admit(TeamId.DESIGN, [t("late")])
    [o] = run(s)
    assert "late result discarded" in o.reason
    assert world["ctx"].db.one("SELECT count(*) FROM task_results")[0] == 0
    assert [a.kind for a in s.artifacts.list(None) if a.created_by_task == "late"] == []
    assert repo.list_events(world["ctx"].db, world["ctx"].project_id, "attempt.fenced")
    assert s.graphs.task_row("late")["state"] == "running"  # the new owner is responsible now


def test_interrupted_attempt_is_reconciled_and_replayed(world):
    script = {"steps": {"task:i": {"proposal": {"summary": "second time"}}}}
    s, t = world["scheduler"](script), world["task"]
    s.graphs.admit(TeamId.DESIGN, [t("i")])
    row = s.graphs.task_row("i")
    attempt = s._start_attempt(s.graphs.load_task("i"), row, "fixture", "fixture")  # crash right after dispatch intent
    assert s.graphs.task_row("i")["state"] == "running"
    assert run(s) == []  # still leased: no duplicate dispatch
    s2 = world["scheduler"](script, owner="coord-2")
    with pytest.raises(Conflict):
        asyncio.run(s2.run_task("i"))  # a second coordinator cannot start it either
    world["advance"](ATTEMPT_LEASE_SECONDS + 1)
    actions = s2.recover()
    assert actions == [{"task_id": "i", "attempt_id": attempt.attempt_id, "state": "pending", "reason": "no workspace side effects; safe to replay from pinned inputs"}]
    events = [e["event_type"] for e in repo.list_events(world["ctx"].db, world["ctx"].project_id) if e["task_id"] == "i"]
    assert "task.interrupted" in events and "task.reconciled" in events
    [o] = run(s2)
    assert o.state == TaskState.COMPLETED and o.attempt_id != attempt.attempt_id
    assert world["ctx"].db.one("SELECT count(*) FROM attempts WHERE task_id='i'")[0] == 2


def test_interrupted_workspace_task_needs_reconciliation(world):
    s, t = world["scheduler"](), world["task"]
    s.graphs.admit(TeamId.DESIGN, [t("w", workspace="ws-1")])
    attempt = s._start_attempt(s.graphs.load_task("w"), s.graphs.task_row("w"), "fixture", "fixture")
    world["advance"](ATTEMPT_LEASE_SECONDS + 1)
    [action] = s.recover()
    assert action["state"] == "needs_input" and "partial edits" in action["reason"]


# ----------------------------------------------------------------------------- participant actions


def test_cancel_and_rerun(world):
    script = {"steps": {"default": {"proposal": {"summary": "ok"}}}}
    s, t = world["scheduler"](script), world["task"]
    s.graphs.admit(TeamId.DESIGN, [t("c1"), t("c2")])
    s.cancel("c1", "changed my mind")
    assert s.graphs.task_row("c1")["state"] == "cancelled"
    assert s.resume("c2")["blocked"].startswith("task is pending")  # only paused/needs_input resume
    [o] = run(s)
    assert o.task_id == "c2" and o.state == TaskState.COMPLETED
    successor = s.rerun("c2")
    assert successor == "c2-r2" and s.graphs.task_row(successor)["successor_of_task_id"] == "c2"
    with pytest.raises(Conflict):
        s.rerun("c2-r2")  # not terminal yet
    [o] = run(s)
    assert o.task_id == "c2-r2" and o.state == TaskState.COMPLETED
