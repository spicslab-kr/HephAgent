"""M5: snapshots, workspaces, coding dispatch, checks, interruption, take-over, isolation gate.

Slices of AT-004 (strict isolation refuses with zero launches), AT-010 (manual
take-over keeps provenance), AT-013 (partial coding work preserved), AT-016 (collector
records real runs; zero tests collected is not a pass).
"""

from __future__ import annotations

import asyncio
import io
import json
import zipfile
from pathlib import Path

import pytest

from sdlc.adapters.brains.fixture import FixtureScript
from sdlc.adapters.registry import AdapterRegistry
from sdlc.application.artifacts import ArtifactService
from sdlc.application.cycle import CycleRunner
from sdlc.application.handoffs import Actor, ActorKind, HandoffService
from sdlc.application.ingress import import_material
from sdlc.artifacts.snapshots import create_snapshot, diff_trees, extract_snapshot, scan_tree
from sdlc.cli.main import main
from sdlc.domain.errors import CorruptArtifact, DeniedScope
from sdlc.domain.models import BundleKind, Origin, OwnerTeam, Task, TaskLimits, TeamId
from sdlc.domain.states import TaskState
from sdlc.runtime.scheduler import Scheduler

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "examples" / "library-reservation"
PARTICIPANT = Actor(ActorKind.PARTICIPANT, "instructor")


# ----------------------------------------------------------------------------- snapshots


def test_snapshot_round_trip_and_diff(tmp_path):
    src = tmp_path / "src"
    (src / "pkg").mkdir(parents=True)
    (src / "pkg" / "a.py").write_text("print(1)\n", encoding="utf-8")
    (src / ".git").mkdir()
    (src / ".git" / "HEAD").write_text("ref", encoding="utf-8")
    archive, manifest = create_snapshot(src)
    assert [f.path for f in manifest.files] == ["pkg/a.py"]  # .git ignored
    out = tmp_path / "out"
    extracted = extract_snapshot(archive, out)
    assert (out / "pkg" / "a.py").read_text(encoding="utf-8") == "print(1)\n" and extracted.by_path.keys() == manifest.by_path.keys()
    (out / "pkg" / "a.py").write_text("print(2)\n", encoding="utf-8")
    (out / "pkg" / "b.py").write_text("x = 1\n", encoding="utf-8")
    diff = diff_trees(src, manifest, out, scan_tree(out))
    assert diff.modified == ["pkg/a.py"] and diff.added == ["pkg/b.py"] and "+print(2)" in diff.patch


def test_snapshot_rejects_traversal_and_tampering(tmp_path):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        z.writestr(".sdlc-snapshot.json", json.dumps({"schema_version": "1.0", "files": [{"path": "../evil.txt", "sha256": "0" * 64, "size": 1}]}))
        z.writestr("../evil.txt", "x")
    with pytest.raises(DeniedScope):
        extract_snapshot(buffer.getvalue(), tmp_path / "x")
    src = tmp_path / "src2"
    src.mkdir()
    (src / "a.txt").write_text("a", encoding="utf-8")
    archive, _ = create_snapshot(src)
    buffer = io.BytesIO(archive)
    with zipfile.ZipFile(buffer) as z:
        names = z.namelist()
        payload = {n: z.read(n) for n in names}
    payload["a.txt"] = b"tampered"
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        for n, d in payload.items():
            z.writestr(n, d)
    with pytest.raises(CorruptArtifact):
        extract_snapshot(out.getvalue(), tmp_path / "y")


# ----------------------------------------------------------------------------- world


@pytest.fixture
def world(context):
    """Design baseline delivered to implementation; fixture brain + fixture coding + local runner."""
    script = json.loads((EXAMPLE / "fixture-script.json").read_text(encoding="utf-8"))
    context.config = context.config.model_copy(update={"project": context.config.project.model_copy(update={"mode": "continuous"})})
    import_material(context, team=TeamId.PLANNING, path=EXAMPLE / "brief.md", purpose="brief")
    adapters = AdapterRegistry(context.config, context.root, fixture_script=FixtureScript.from_raw(script))
    scheduler = Scheduler(context, adapters, owner_id="coord")
    handoffs = HandoffService(context)
    # a minimal design package straight to implementation (design stage itself is covered elsewhere)
    design = scheduler.artifacts.register(owner=OwnerTeam.DESIGN, artifact_id="design-doc", kind="design_document", media_type="text/markdown",
                                          relative_path="DESIGN.md", data=b"# Design\n\nReservationService with reserve/cancel; conflict raises ReservationError.\n", origin=Origin.FIXTURE)
    _, ref = handoffs.publish(source=OwnerTeam.DESIGN, target=TeamId.IMPLEMENTATION, kind=BundleKind.FORWARD, bundle_id="b-design", cycle_id="cycle-001", baseline_id="baseline-design", artifact_refs=[design.ref], summary="design")
    delivery = handoffs.deliver(bundle_ref=ref, expected_target_baseline=None)
    handoffs.receive(delivery.delivery_id)
    handoffs.accept_baseline(team=TeamId.IMPLEMENTATION, delivery_id=delivery.delivery_id, actor=PARTICIPANT, rationale="ok")
    return {"ctx": context, "scheduler": scheduler, "adapters": adapters, "delivery": delivery.delivery_id, "design": design, "script": script}


def _task(world, task_id, operation, deps=(), refs=None, scope=None):
    ctx = world["ctx"]
    return Task(schema_version="1.0", project_id=ctx.project_id, cycle_id="cycle-001", task_id=task_id, team_id=TeamId.IMPLEMENTATION, agent_instance_id=f"impl-{task_id}",
                operation=operation, operation_version=1, input_deliveries=[world["delivery"]], input_refs=refs if refs is not None else [world["design"].ref], depends_on=list(deps),
                parameters={"objective": "implement", "scope_ids": scope or [], "requested_artifact_kinds": [], "diagram_request": None},
                expected_output_schema="#/$defs/WorkerProposal", backend_profile="fixture", workspace_ref=None,
                limits=TaskLimits(max_attempts=3, timeout_seconds=120, max_output_tokens=100, max_cost_microunits=0))


def _dev_mode(world):
    ctx = world["ctx"]
    ctx.config = ctx.config.model_copy(update={"runtime": ctx.config.runtime.model_copy(update={"isolation": "development"})})


# ----------------------------------------------------------------------------- isolation gate (AT-004)


def test_strict_isolation_refuses_coding_with_zero_launches(world):
    s = world["scheduler"]
    s.graphs.admit(TeamId.IMPLEMENTATION, [_task(world, "impl-code", "implementation.code")])
    [outcome] = asyncio.run(s.run_ready(TeamId.IMPLEMENTATION))
    assert outcome.state == TaskState.NEEDS_INPUT and "strict isolation" in outcome.reason and "sdlc isolation" in outcome.reason
    assert world["adapters"].coding("fixture").invocations["impl-code"] == 0
    assert not (world["ctx"].workspaces / "implementation" / "impl-code").exists()


# ----------------------------------------------------------------------------- coding + capture


def test_coding_task_captures_snapshot_patch_and_labels_nonconforming(world):
    _dev_mode(world)
    s = world["scheduler"]
    s.graphs.admit(TeamId.IMPLEMENTATION, [_task(world, "impl-code", "implementation.code")])
    [outcome] = asyncio.run(s.run_ready(TeamId.IMPLEMENTATION))
    assert outcome.state == TaskState.COMPLETED, outcome.reason
    kinds = {s.artifacts.get(r).kind for r in outcome.artifacts}
    assert kinds == {"source_snapshot", "patch", "change_summary"}
    snapshot = next(s.artifacts.get(r) for r in outcome.artifacts if s.artifacts.get(r).kind == "source_snapshot")
    assert snapshot.owner_team == OwnerTeam.IMPLEMENTATION and snapshot.origin == Origin.TOOL and snapshot.created_by_task == "impl-code"
    manifest = extract_snapshot(s.artifacts.read_bytes(snapshot.ref), world["ctx"].root / "check")
    assert {f.path for f in manifest.files} == {"reservation.py", "test_reservation.py"}
    assert any(f.code == "nonconforming_isolation" for f in outcome.result.findings)
    patch = next(s.artifacts.get(r) for r in outcome.artifacts if s.artifacts.get(r).kind == "patch")
    assert "+class ReservationService" in s.artifacts.read_bytes(patch.ref).decode()
    events = world["ctx"].db.all("SELECT payload_json FROM events WHERE event_type='backend.event' AND task_id='impl-code' ORDER BY sequence_number")
    kinds = [json.loads(e[0])["kind"] for e in events]
    assert kinds[0] == "started" and kinds[-1] == "completed" and "progress" in kinds


def test_interrupted_coding_preserves_partial_work_and_needs_reconciliation(world):
    _dev_mode(world)
    world["adapters"].coding("fixture").script["task:impl-code"] = {"files": {"half.py": "x = 1\n"}, "interrupt": True}
    s = world["scheduler"]
    s.graphs.admit(TeamId.IMPLEMENTATION, [_task(world, "impl-code", "implementation.code")])
    [outcome] = asyncio.run(s.run_ready(TeamId.IMPLEMENTATION))
    assert outcome.state == TaskState.PAUSED and outcome.result.error.completion_certainty.value == "unknown"
    cp = world["ctx"].db.one("SELECT candidate_source_ref_json, patch_ref_json, side_effects_json, reconciliation_required FROM checkpoints WHERE task_id='impl-code'")
    assert cp["reconciliation_required"] == 1 and json.loads(cp["side_effects_json"])[0]["outcome"] == "unknown"
    partial = json.loads(cp["candidate_source_ref_json"])
    manifest = extract_snapshot(s.artifacts.read_bytes(type(world["design"].ref)(**partial)), world["ctx"].root / "partial")
    assert [f.path for f in manifest.files] == ["half.py"]  # partial edit kept as evidence, not promoted


# ----------------------------------------------------------------------------- check (collector)


def test_check_runs_recipes_and_records_evidence(world):
    _dev_mode(world)
    s = world["scheduler"]
    s.graphs.admit(TeamId.IMPLEMENTATION, [_task(world, "impl-code", "implementation.code")])
    [coded] = asyncio.run(s.run_ready(TeamId.IMPLEMENTATION))
    snapshot_ref = next(r for r in coded.artifacts if s.artifacts.get(r).kind == "source_snapshot")
    s.graphs.admit(TeamId.IMPLEMENTATION, [_task(world, "impl-check", "implementation.check", refs=[world["design"].ref, snapshot_ref], scope=["pytest"])])
    [checked] = asyncio.run(s.run_ready(TeamId.IMPLEMENTATION))
    assert checked.state == TaskState.COMPLETED, checked.reason
    records = [s.artifacts.get(r) for r in checked.artifacts if s.artifacts.get(r).kind == "execution_record"]
    assert len(records) == 1 and records[0].origin == Origin.TOOL
    record = json.loads(s.artifacts.read_bytes(records[0].ref).decode())
    assert record["collector_id"] == "local-runner-dev" and record["status"] == "passed" and record["source_ref"] == snapshot_ref.to_json_dict()
    assert {o["status"] for o in record["case_observations"]} == {"passed"} and len(record["case_observations"]) == 2
    assert not any(k.endswith("_API_KEY") for k in record["environment_variable_names"])
    assert "checks_failed" not in [f.code for f in checked.result.findings]


def test_check_with_failing_test_is_an_observation_not_a_provider_error(world):
    _dev_mode(world)
    world["adapters"].coding("fixture").script["task:impl-code"] = {"files": {"test_bad.py": "def test_bad():\n    assert 1 == 2\n"}}
    s = world["scheduler"]
    s.graphs.admit(TeamId.IMPLEMENTATION, [_task(world, "impl-code", "implementation.code")])
    [coded] = asyncio.run(s.run_ready(TeamId.IMPLEMENTATION))
    snapshot_ref = next(r for r in coded.artifacts if s.artifacts.get(r).kind == "source_snapshot")
    s.graphs.admit(TeamId.IMPLEMENTATION, [_task(world, "impl-check", "implementation.check", refs=[snapshot_ref], scope=["pytest"])])
    [checked] = asyncio.run(s.run_ready(TeamId.IMPLEMENTATION))
    assert checked.state == TaskState.COMPLETED
    record = json.loads(s.artifacts.read_bytes(next(r for r in checked.artifacts if s.artifacts.get(r).kind == "execution_record")).decode())
    assert record["status"] == "assertion_failed" and record["case_observations"][0]["status"] == "failed"
    assert any(f.code == "checks_failed" for f in checked.result.findings)


# ----------------------------------------------------------------------------- take-over / return-work


def run(capsys, *argv):
    code = main(["--json", *argv])
    return code, json.loads(capsys.readouterr().out)


def test_cycle_to_implementation_then_takeover_and_return(tmp_path, capsys):
    home = str(tmp_path / "home")
    script_path = tmp_path / "script.json"
    script_path.write_text((EXAMPLE / "fixture-script.json").read_text(encoding="utf-8"), encoding="utf-8")
    code, env = run(capsys, "new", "demo", "--home", home, "--brief", str(EXAMPLE / "brief.md"), "--brain", "fixture")
    assert code == 0, env
    code, env = run(capsys, "isolation", "demo", "development", "--home", home)
    assert code == 0 and env["data"]["isolation"] == "development"
    # the fixture design leader is unscripted: feed design through its own fixture-free path is out of scope,
    # so run planning->requirements, then hand a design package in by the advanced path, then implementation.
    code, env = run(capsys, "run", "demo", "--home", home, "--until", "requirements", "--script", str(script_path))
    assert code == 0 and not env["data"]["stopped"], env
    from sdlc.application.projects import open_project

    ctx = open_project(Path(home), "demo")
    try:
        svc = ArtifactService(ctx)
        handoffs = HandoffService(ctx)
        design = svc.register(owner=OwnerTeam.DESIGN, artifact_id="design-doc", kind="design_document", media_type="text/markdown", relative_path="DESIGN.md", data=b"# Design\n", origin=Origin.FIXTURE)
        _, ref = handoffs.publish(source=OwnerTeam.DESIGN, target=TeamId.IMPLEMENTATION, kind=BundleKind.FORWARD, bundle_id="b-design", cycle_id="cycle-001", baseline_id="bl", artifact_refs=[design.ref], summary="design")
        d = handoffs.deliver(bundle_ref=ref, expected_target_baseline=None)
        handoffs.receive(d.delivery_id)
        handoffs.accept_baseline(team=TeamId.IMPLEMENTATION, delivery_id=d.delivery_id, actor=PARTICIPANT, rationale="ok")
    finally:
        ctx.close()
    code, env = run(capsys, "run", "demo", "--home", home, "--stage", "implementation", "--script", str(script_path))
    assert code == 0 and not env["data"]["stopped"], env
    steps = " ".join(env["data"]["stages"][0]["what_happened"])
    assert "impl-code: completed" in steps and "impl-check: completed" in steps and "impl-handoff: completed" in steps
    code, env = run(capsys, "show", "demo", "--home", home, "--stage", "implementation")
    kinds = {d["kind"] for d in env["data"]["documents"]}
    assert {"source_snapshot", "patch", "execution_record", "check_report", "implementation_document"} <= kinds
    # take over the coding task, edit, return
    code, env = run(capsys, "takeover", "demo", "impl-code", "--home", home, "--to", str(tmp_path / "mine"))
    assert code == 0 and (tmp_path / "mine" / "reservation.py").exists() and not (tmp_path / "mine" / ".sdlc-inputs").exists()
    (tmp_path / "mine" / "reservation.py").write_text("# edited by hand\n", encoding="utf-8")
    code, env = run(capsys, "return-work", "demo", "impl-code", "--home", home, "--from", str(tmp_path / "mine"))
    assert code == 0 and env["data"]["origin"] == "participant"
    code, env = run(capsys, "show", "demo", "--home", home, "--stage", "implementation")
    participant = [d for d in env["data"]["documents"] if d["origin"] == "participant"]
    assert participant and participant[0]["kind"] == "source_snapshot"


def test_remove_tree_copes_with_read_only_git_objects(tmp_path):
    """Windows marks git objects read-only; rebuilding a workspace for attempt 2 must not fail."""
    import os
    import stat

    from sdlc.runtime.workspaces import remove_tree

    objects = tmp_path / "ws" / ".git" / "objects" / "00"
    objects.mkdir(parents=True)
    blob = objects / "abc"
    blob.write_bytes(b"x")
    os.chmod(blob, stat.S_IREAD)
    remove_tree(tmp_path / "ws")
    assert not (tmp_path / "ws").exists()


def test_workspace_paths_are_absolute(world):
    """Coding clients get the workspace as cwd *and* ``-C``; a relative path would resolve twice."""
    from sdlc.runtime.workspaces import WorkspaceManager

    manager = WorkspaceManager(world["ctx"], "test")
    assert manager.path_for(_task(world, "impl-abs", "implementation.code")).is_absolute()


def test_prepare_releases_lease_when_setup_fails(world, monkeypatch):
    """A crash between lease acquisition and workspace readiness must not orphan the lease."""
    from sdlc.runtime import workspaces as ws

    task = _task(world, "impl-crash", "implementation.code")
    monkeypatch.setattr(ws.WorkspaceManager, "_materialize_inputs", lambda self, t, root: (_ for _ in ()).throw(PermissionError("read-only")))
    first = ws.WorkspaceManager(world["ctx"], "owner-a")
    with pytest.raises(PermissionError):
        first.prepare(task)
    monkeypatch.undo()
    second = ws.WorkspaceManager(world["ctx"], "owner-b")
    workspace = second.prepare(task)  # would raise Conflict if owner-a still held the lease
    second.release(workspace)


# ----------------------------------------------------------------------------- fixes forced by the live Codex run


def test_blocking_findings_stop_the_task_as_needs_input(world):
    """A worker that reports a *blocking* finding (design conflict) has not completed: the task
    waits for a person, its documents are kept, and the cause names the finding."""
    _dev_mode(world)
    script = json.loads(json.dumps(world["script"]))
    script["steps"]["task:impl-plan-blocked"] = {"proposal": {
        "summary": "cannot plan: the design documents contradict each other",
        "artifact_candidates": [{"kind": "change_plan", "media_type": "text/markdown", "relative_path": "docs/CHANGE_PLAN.md", "workspace_path": None,
                                 "content_text": "# Change plan\n\n## Changes\n\nBlocked: see findings.\n\n## Order\n\nnone\n\n## Risks\n\nnone\n"}],
        "findings": [{"finding_id": "IC-01", "code": "design_conflict", "severity": "blocking", "message": "architecture.md and api_contracts.md disagree on the reservation key type", "related_refs": []}],
        "questions": ["Which document wins: architecture.md (INTEGER key) or api_contracts.md (TEXT key)?"],
        "suggested_next_operations": []}}
    ctx = world["ctx"]
    adapters = AdapterRegistry(ctx.config, ctx.root, fixture_script=FixtureScript.from_raw(script))
    s = Scheduler(ctx, adapters, owner_id="coord2")
    s.graphs.admit(TeamId.IMPLEMENTATION, [_task(world, "impl-plan-blocked", "implementation.plan_changes")])
    [outcome] = asyncio.run(s.run_ready(TeamId.IMPLEMENTATION))
    assert outcome.state == TaskState.NEEDS_INPUT, outcome.reason
    assert outcome.result is not None and outcome.result.questions and "architecture.md" in outcome.result.questions[0]
    assert any(s.artifacts.get(r).kind == "change_plan" for r in outcome.result.artifact_refs)  # work is preserved
    entry = next(e for e in s.describe(TeamId.IMPLEMENTATION) if e["task_id"] == "impl-plan-blocked")
    assert "blocking findings" in entry["cause"] and "resume" in entry


def test_compose_manifest_retires_an_older_document_at_the_same_path(world):
    """A reworked document under a new artifact id shadows the old one: the handoff carries one."""
    ctx = world["ctx"]
    svc = ArtifactService(ctx)
    old = svc.register(owner=OwnerTeam.DESIGN, artifact_id="design-arch", kind="design_document", media_type="text/markdown", relative_path="design/ARCH.md", data=b"# v1\n", origin=Origin.FIXTURE)
    new = svc.register(owner=OwnerTeam.DESIGN, artifact_id="design-arch-v2", kind="design_document", media_type="text/markdown", relative_path="design/ARCH.md", data=b"# v2\n", origin=Origin.FIXTURE)
    refs = HandoffService(ctx).compose_manifest(TeamId.DESIGN)
    ids = {r.artifact_id for r in refs}
    assert "design-arch-v2" in ids and "design-arch" not in ids
    assert svc.status(old.ref) == "superseded" and svc.status(new.ref) != "superseded"


def test_check_with_requirement_ids_as_scope_runs_every_recipe(world):
    """Leaders scope tasks by requirement id; those are not recipe names, so all recipes run."""
    _dev_mode(world)
    s = world["scheduler"]
    s.graphs.admit(TeamId.IMPLEMENTATION, [_task(world, "impl-code", "implementation.code")])
    [coded] = asyncio.run(s.run_ready(TeamId.IMPLEMENTATION))
    snapshot_ref = next(r for r in coded.artifacts if s.artifacts.get(r).kind == "source_snapshot")
    s.graphs.admit(TeamId.IMPLEMENTATION, [_task(world, "impl-check", "implementation.check", refs=[world["design"].ref, snapshot_ref], scope=["REQ-F-1", "REQ-F-2"])])
    [checked] = asyncio.run(s.run_ready(TeamId.IMPLEMENTATION))
    assert checked.state == TaskState.COMPLETED, checked.reason
    assert len([r for r in checked.artifacts if s.artifacts.get(r).kind == "execution_record"]) == 1
    report = next(s.artifacts.read_bytes(r).decode() for r in checked.artifacts if s.artifacts.get(r).kind == "check_report")
    assert "Scope: REQ-F-1, REQ-F-2" in report and "pytest: passed" in report


def test_run_again_redoes_a_stage_with_a_fresh_plan(tmp_path, capsys):
    """``sdlc run NAME --stage X --again`` cancels unfinished work and lets the leader plan anew."""
    home = str(tmp_path / "home")
    text = (EXAMPLE / "fixture-script.json").read_text(encoding="utf-8")
    first, second = tmp_path / "s1.json", tmp_path / "s2.json"
    first.write_text(text, encoding="utf-8")
    second.write_text(text.replace("impl-", "impl2-"), encoding="utf-8")  # a leader must choose fresh ids on a re-plan
    assert run(capsys, "new", "demo", "--home", home, "--brief", str(EXAMPLE / "brief.md"), "--brain", "fixture")[0] == 0
    assert run(capsys, "isolation", "demo", "development", "--home", home)[0] == 0
    code, env = run(capsys, "run", "demo", "--home", home, "--until", "requirements", "--script", str(first))
    assert code == 0 and not env["data"]["stopped"], env
    from sdlc.application.projects import open_project

    ctx = open_project(Path(home), "demo")
    try:
        svc, handoffs = ArtifactService(ctx), HandoffService(ctx)
        design = svc.register(owner=OwnerTeam.DESIGN, artifact_id="design-doc", kind="design_document", media_type="text/markdown", relative_path="DESIGN.md", data=b"# Design\n", origin=Origin.FIXTURE)
        _, ref = handoffs.publish(source=OwnerTeam.DESIGN, target=TeamId.IMPLEMENTATION, kind=BundleKind.FORWARD, bundle_id="b-design", cycle_id="cycle-001", baseline_id="bl", artifact_refs=[design.ref], summary="design")
        d = handoffs.deliver(bundle_ref=ref, expected_target_baseline=None)
        handoffs.receive(d.delivery_id)
        handoffs.accept_baseline(team=TeamId.IMPLEMENTATION, delivery_id=d.delivery_id, actor=PARTICIPANT, rationale="ok")
    finally:
        ctx.close()
    code, env = run(capsys, "run", "demo", "--home", home, "--stage", "implementation", "--script", str(first))
    assert code == 0 and not env["data"]["stopped"], env
    code, env = run(capsys, "run", "demo", "--home", home, "--stage", "implementation", "--again", "--script", str(second))
    assert code == 0 and not env["data"]["stopped"], env
    steps = " ".join(env["data"]["stages"][0]["what_happened"])
    assert "leader asked for a work plan" in steps and "impl2-code-r2: completed" in steps and "impl2-handoff-r2: completed" in steps  # deterministic replay: the second plan is the fixture's second graph
    code, env = run(capsys, "task", "list", "--project", "demo", "--home", home, "--team", "implementation")
    states = {t["task_id"]: t["state"] for t in env["data"]["tasks"]}
    assert states["impl-code"] == "completed" and states["impl2-code-r2"] == "completed"  # finished work is never cancelled
    code, env = run(capsys, "run", "demo", "--home", home, "--again", "--script", str(second))
    assert code != 0 and "--again needs --stage" in json.dumps(env)


def test_plan_uses_latest_delivery_per_source_and_skips_superseded(world):
    """Two design packages in the inbox: the leader plans from the newest one, participant
    memos are kept, and a document retired as superseded is never offered."""
    from sdlc.application.workflow import WorkflowService

    ctx = world["ctx"]
    handoffs, svc = HandoffService(ctx), ArtifactService(ctx)
    old_arch = svc.register(owner=OwnerTeam.DESIGN, artifact_id="arch", kind="design_document", media_type="text/markdown", relative_path="design/ARCH.md", data=b"# v1\n", origin=Origin.FIXTURE)
    new_arch = svc.register(owner=OwnerTeam.DESIGN, artifact_id="arch-v2", kind="design_document", media_type="text/markdown", relative_path="design/ARCH.md", data=b"# v2\n", origin=Origin.FIXTURE)
    refs = handoffs.compose_manifest(TeamId.DESIGN)  # retires arch (shadowed by arch-v2)
    _, ref = handoffs.publish(source=OwnerTeam.DESIGN, target=TeamId.IMPLEMENTATION, kind=BundleKind.FORWARD, bundle_id="b-design-2", cycle_id="cycle-001", baseline_id="baseline-design-2", artifact_refs=refs, summary="reworked design")
    current, _ = handoffs.baseline(TeamId.IMPLEMENTATION)
    d2 = handoffs.deliver(bundle_ref=ref, expected_target_baseline=current)
    handoffs.receive(d2.delivery_id)
    handoffs.accept_baseline(team=TeamId.IMPLEMENTATION, delivery_id=d2.delivery_id, actor=PARTICIPANT, rationale="ok")
    memo = import_material(ctx, team=TeamId.IMPLEMENTATION, path=EXAMPLE / "brief.md", purpose="decision memo")
    proposal = WorkflowService(ctx, world["scheduler"]).propose_stage_plan(TeamId.IMPLEMENTATION, objective="plan")
    task = world["scheduler"].graphs.load_task(proposal.task_id)
    assert set(task.input_deliveries) == {d2.delivery_id, memo.delivery.delivery_id}  # the first design delivery is history
    ids = {r.artifact_id for r in task.input_refs}
    assert "arch-v2" in ids and "arch" not in ids and svc.status(old_arch.ref) == "superseded" and new_arch.ref in task.input_refs


def test_answer_command_delivers_a_memo_and_names_the_rerun(tmp_path, capsys):
    home = str(tmp_path / "home")
    assert run(capsys, "new", "demo", "--home", home, "--brief", str(EXAMPLE / "brief.md"), "--brain", "fixture")[0] == 0
    memo = tmp_path / "decision.md"
    memo.write_text("# Decision\n\nThe API contract wins.\n", encoding="utf-8")
    code, env = run(capsys, "answer", "demo", str(memo), "--home", home, "--stage", "planning")
    assert code == 0 and env["data"]["delivered_as"].startswith("delivery-") and "--again" in env["data"]["what_to_do"]
    code, env = run(capsys, "handoff", "inbox", "--project", "demo", "--home", home, "--team", "planning")
    assert any(d["from"] == "participant" for d in env["data"]["inbox"]) and len(env["data"]["inbox"]) == 2
