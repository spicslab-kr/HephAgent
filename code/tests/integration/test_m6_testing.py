"""M6: Testing team, benches, result truth and the defect loop (handoff docs/04 s6, AT-016).

Offline, fixture-driven. The library-reservation fixture plants a defect (a cancelled copy
can never be reserved again); the Testing team's bench exposes it (TC-003), Testing files
DEF-001, the runtime routes the defect package to Implementation, Implementation repairs
under fresh task ids, and Testing re-executes on the exact corrected snapshot.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from sdlc.adapters.brains.fixture import FixtureScript
from sdlc.adapters.registry import AdapterRegistry
from sdlc.application.artifacts import ArtifactService
from sdlc.application.handoffs import HandoffService
from sdlc.application.ingress import import_material
from sdlc.domain.models import BundleKind, CaseObservation, CaseStatus, ExecutionRecord, ExecutionStatus, Origin, OwnerTeam, Task, TaskLimits, TeamId
from sdlc.domain.states import TaskState
from sdlc.runtime.scheduler import Scheduler
from sdlc.teams.testing.mapping import case_ids_from_documents, case_outcomes, map_nodes_to_cases, normalize_id
from tests.integration.test_m5_implementation import EXAMPLE, PARTICIPANT, run

# ----------------------------------------------------------------------------- mapping (pure)


def test_node_to_case_mapping_is_textual_and_deterministic():
    nodes = ["test_bench_tc.py::test_tc_001_reserve", "test_bench_tc.py::test_tc_003_cancel_frees_copy[a-b]", "test_reservation.py::test_cancel_only_own"]
    mapping = map_nodes_to_cases(nodes, ["TC-001", "TC-002", "TC-003"])
    assert mapping["TC-001"] == [nodes[0]] and mapping["TC-003"] == [nodes[1]] and mapping["TC-002"] == []
    assert normalize_id("TC-003") == "tc_003"
    assert case_ids_from_documents(['{"cases": [{"case_id": "TC-9"}, {"case_id": "TC-9"}]}', "not json"]) == ["TC-9"]


def _record(observations, status=ExecutionStatus.PASSED):
    from sdlc.domain.models import ArtifactRef

    ref = ArtifactRef(artifact_id="x", revision=1, sha256="0" * 64)
    return ExecutionRecord(schema_version="1.0", execution_id="e", project_id="p", team_id="testing", task_id="t", attempt_id="a", source_ref=ref, suite_ref=None, environment_ref=ref,
                           recipe_id="pytest", argv=["pytest"], working_directory=".", environment_variable_names=[], started_at="2026-10-10T00:00:00.000Z", ended_at="2026-10-10T00:00:01.000Z",
                           status=status, exit_code=0, stdout_ref=None, stderr_ref=None, report_refs=[], case_observations=observations, collector_id="local-runner-dev", fixture_mode=False)


def test_unexecuted_and_skipped_cases_never_count_as_passes():
    obs = [CaseObservation(case_id="t1", status=CaseStatus.PASSED, message="b.py::test_tc_001_a"),
           CaseObservation(case_id="t2", status=CaseStatus.SKIPPED, message="b.py::test_tc_002_b"),
           CaseObservation(case_id="t3", status=CaseStatus.FAILED, message="b.py::test_tc_003_c"),
           CaseObservation(case_id="t4", status=CaseStatus.PASSED, message="b.py::test_tc_003_d")]
    outcomes = case_outcomes(["TC-001", "TC-002", "TC-003", "TC-004"], [_record(obs)])
    assert outcomes.statuses == {"TC-001": "passed", "TC-002": "skipped", "TC-003": "failed", "TC-004": "not_executed"}
    assert not outcomes.all_passed and outcomes.failed_cases == ["TC-003"]


# ----------------------------------------------------------------------------- the whole stage, offline


@pytest.fixture
def demo(tmp_path, capsys):
    home = str(tmp_path / "home")
    script = tmp_path / "script.json"
    script.write_text((EXAMPLE / "fixture-script.json").read_text(encoding="utf-8"), encoding="utf-8")
    assert run(capsys, "new", "demo", "--home", home, "--brief", str(EXAMPLE / "brief.md"), "--brain", "fixture")[0] == 0
    assert run(capsys, "isolation", "demo", "development", "--home", home)[0] == 0
    code, env = run(capsys, "run", "demo", "--home", home, "--until", "requirements", "--script", str(script))
    assert code == 0 and not env["data"]["stopped"], env
    from sdlc.application.projects import open_project

    ctx = open_project(Path(home), "demo")  # the fixture design leader is unscripted: hand a design package in directly
    try:
        svc, handoffs = ArtifactService(ctx), HandoffService(ctx)
        # requirements -> design first, so the design package forwards the requirements downstream
        _, rref = handoffs.publish(source=OwnerTeam.REQUIREMENTS, target=TeamId.DESIGN, kind=BundleKind.FORWARD, bundle_id="b-req", cycle_id="cycle-001", baseline_id="bl-req", artifact_refs=handoffs.compose_manifest(TeamId.REQUIREMENTS), summary="requirements")
        rd = handoffs.deliver(bundle_ref=rref, expected_target_baseline=None)
        handoffs.receive(rd.delivery_id)
        handoffs.accept_baseline(team=TeamId.DESIGN, delivery_id=rd.delivery_id, actor=PARTICIPANT, rationale="ok")
        design = svc.register(owner=OwnerTeam.DESIGN, artifact_id="design-doc", kind="design_document", media_type="text/markdown", relative_path="DESIGN.md", data=b"# Design\n\nReservationService.\n", origin=Origin.FIXTURE)
        refs = handoffs.compose_manifest(TeamId.DESIGN)
        _, ref = handoffs.publish(source=OwnerTeam.DESIGN, target=TeamId.IMPLEMENTATION, kind=BundleKind.FORWARD, bundle_id="b-design", cycle_id="cycle-001", baseline_id="bl", artifact_refs=refs or [design.ref], summary="design")
        d = handoffs.deliver(bundle_ref=ref, expected_target_baseline=None)
        handoffs.receive(d.delivery_id)
        handoffs.accept_baseline(team=TeamId.IMPLEMENTATION, delivery_id=d.delivery_id, actor=PARTICIPANT, rationale="ok")
    finally:
        ctx.close()
    return {"home": home, "script": str(script), "capsys": capsys}


def test_planted_defect_is_found_routed_repaired_and_retested(demo):
    """AT-016 + M6 exit criterion: the bench exposes the planted defect, the defect goes back as an
    artifact package, Implementation repairs, Testing re-executes on the exact corrected snapshot."""
    capsys, home, script = demo["capsys"], demo["home"], demo["script"]
    code, env = run(capsys, "run", "demo", "--home", home, "--stage", "implementation", "--until", "testing", "--script", str(script))
    assert code == 0, env
    data = env["data"]
    stages = {s["stage"]: s for s in data["stages"]}
    assert not data["stopped"], data
    names = [s["stage"] for s in data["stages"]]
    assert "testing" in names and "testing->implementation (defects)" in names and "implementation (repair loop 1)" in names and "testing (re-test 1)" in names, names
    first = " ".join(stages["testing"]["what_happened"])
    assert "test-execute: completed" in first and "test-analyze: completed" in first
    repair = " ".join(stages["implementation (repair loop 1)"]["what_happened"])
    assert "impl-code-r2: completed" in repair and "impl-check-r2: completed" in repair
    retest = " ".join(stages["testing (re-test 1)"]["what_happened"])
    assert "test-execute-r2: completed" in retest and "test-assess-r2: completed" in retest

    from sdlc.application.projects import open_project

    ctx = open_project(Path(home), "demo")
    try:
        svc = ArtifactService(ctx)
        records = {}
        for artifact in svc.list(OwnerTeam.TESTING):
            if artifact.kind == "execution_record":
                record = ExecutionRecord.model_validate(json.loads(svc.read_bytes(artifact.ref).decode()))
                records[artifact.created_by_task] = record
        first_rec, second_rec = records["test-execute"], records["test-execute-r2"]
        # exact candidates: the planted one, then the corrected one; the bench is the suite, not the source
        assert first_rec.source_ref.artifact_id == "impl-code-candidate-snapshot" and second_rec.source_ref.artifact_id == "impl-code-r2-candidate-snapshot"
        assert first_rec.suite_ref is not None and first_rec.suite_ref.artifact_id == "test-bench-candidate-snapshot"
        assert first_rec.status == ExecutionStatus.ASSERTION_FAILED and second_rec.status == ExecutionStatus.PASSED
        failed_first = [o.message for o in first_rec.case_observations if o.status == CaseStatus.FAILED]
        assert failed_first and all("tc_003" in m for m in failed_first)
        defects = [a for a in svc.list(OwnerTeam.TESTING) if a.kind == "defect"]
        assert len(defects) == 1 and json.loads(svc.read_bytes(defects[0].ref).decode())["execution_refs"][0]["artifact_id"].endswith("-pytest-record")
        from sdlc.storage import repositories as repo

        inbox = repo.accepted_deliveries(ctx.db, ctx.project_id, "implementation")
        assert any(d.from_team == TeamId.TESTING and d.bundle_ref.bundle_id.startswith("b-defects-") for d in inbox)  # defects travelled as an artifact package
        reports = [a for a in svc.list(OwnerTeam.TESTING) if a.kind == "test_report" and a.created_by_task.startswith("test-assess")]
        texts = {a.created_by_task: svc.read_bytes(a.ref).decode() for a in reports}
        assert "HOLD" in texts["test-assess"] and "RELEASE" in texts["test-assess-r2"]
        assert "## Observed counts (runtime)" in texts["test-assess-r2"] and '"passed": 3' in texts["test-assess-r2"]
    finally:
        ctx.close()
    code, env = run(capsys, "show", "demo", "--home", home, "--stage", "testing")
    kinds = {d["kind"] for d in env["data"]["documents"]}
    assert {"test_plan", "test_cases", "bench_design", "preflight_report", "execution_record", "execution_report", "test_bench", "defect", "defect_report", "test_report"} <= kinds


# ----------------------------------------------------------------------------- rules enforced by the runtime


def _world(tmp_path, capsys, mutate=None):
    """A project at the start of the testing stage with the fixture script (optionally mutated)."""
    home = str(tmp_path / "home")
    raw = json.loads((EXAMPLE / "fixture-script.json").read_text(encoding="utf-8"))
    if mutate:
        mutate(raw)
    script = tmp_path / "script.json"
    script.write_text(json.dumps(raw), encoding="utf-8")
    assert run(capsys, "new", "demo", "--home", home, "--brief", str(EXAMPLE / "brief.md"), "--brain", "fixture")[0] == 0
    assert run(capsys, "isolation", "demo", "development", "--home", home)[0] == 0
    code, env = run(capsys, "run", "demo", "--home", home, "--until", "requirements", "--script", str(script))
    assert code == 0 and not env["data"]["stopped"], env
    from sdlc.application.projects import open_project

    ctx = open_project(Path(home), "demo")
    try:
        svc, handoffs = ArtifactService(ctx), HandoffService(ctx)
        # requirements -> design first, so the design package forwards the requirements downstream
        _, rref = handoffs.publish(source=OwnerTeam.REQUIREMENTS, target=TeamId.DESIGN, kind=BundleKind.FORWARD, bundle_id="b-req", cycle_id="cycle-001", baseline_id="bl-req", artifact_refs=handoffs.compose_manifest(TeamId.REQUIREMENTS), summary="requirements")
        rd = handoffs.deliver(bundle_ref=rref, expected_target_baseline=None)
        handoffs.receive(rd.delivery_id)
        handoffs.accept_baseline(team=TeamId.DESIGN, delivery_id=rd.delivery_id, actor=PARTICIPANT, rationale="ok")
        svc.register(owner=OwnerTeam.DESIGN, artifact_id="design-doc", kind="design_document", media_type="text/markdown", relative_path="DESIGN.md", data=b"# Design\n", origin=Origin.FIXTURE)
        _, ref = handoffs.publish(source=OwnerTeam.DESIGN, target=TeamId.IMPLEMENTATION, kind=BundleKind.FORWARD, bundle_id="b-design", cycle_id="cycle-001", baseline_id="bl", artifact_refs=handoffs.compose_manifest(TeamId.DESIGN), summary="design")
        d = handoffs.deliver(bundle_ref=ref, expected_target_baseline=None)
        handoffs.receive(d.delivery_id)
        handoffs.accept_baseline(team=TeamId.IMPLEMENTATION, delivery_id=d.delivery_id, actor=PARTICIPANT, rationale="ok")
    finally:
        ctx.close()
    code, env = run(capsys, "run", "demo", "--home", home, "--stage", "implementation", "--script", str(script))
    assert code == 0 and not env["data"]["stopped"], env
    return home, str(script)


def test_bench_that_edits_the_candidate_is_refused(tmp_path, capsys):
    def mutate(raw):
        raw["coding"]["task:test-bench"]["files"]["reservation.py"] = "# tampered by the bench\n"

    home, script = _world(tmp_path, capsys, mutate)
    code, env = run(capsys, "run", "demo", "--home", home, "--stage", "testing", "--script", script)
    assert code == 0 and env["data"]["stopped"]
    steps = " ".join(next(s for s in env["data"]["stages"] if s["stage"] == "testing")["what_happened"])
    assert "test-bench: needs_input" in steps and "bench_modified_source" in steps


def test_bench_collecting_zero_tests_blocks_preflight(tmp_path, capsys):
    def mutate(raw):
        raw["coding"]["task:test-bench"]["files"] = {"README_BENCH.md": "no tests here\n"}
        raw["coding"]["task:impl-code"]["files"].pop("test_reservation.py")  # a candidate without developer tests either

    home, script = _world(tmp_path, capsys, mutate)
    code, env = run(capsys, "run", "demo", "--home", home, "--stage", "testing", "--script", script)
    assert env["data"]["stopped"]
    steps = " ".join(next(s for s in env["data"]["stages"] if s["stage"] == "testing")["what_happened"])
    assert "test-preflight: needs_input" in steps and "no_tests_collected" in steps


def test_analyze_must_file_a_defect_for_a_failed_case_and_cite_real_records(tmp_path, capsys):
    def mutate(raw):
        raw["steps"]["operation:testing.analyze"] = [raw["steps"]["operation:testing.analyze"][1]]  # "all passed" while TC-003 fails

    home, script = _world(tmp_path, capsys, mutate)
    code, env = run(capsys, "run", "demo", "--home", home, "--stage", "testing", "--script", script)
    assert env["data"]["stopped"]
    steps = " ".join(next(s for s in env["data"]["stages"] if s["stage"] == "testing")["what_happened"])
    assert "test-analyze" in steps and "missing_defects" in steps


def test_release_recommendation_needs_evidence(tmp_path, capsys):
    def mutate(raw):
        raw["steps"]["operation:testing.assess_release"] = [raw["steps"]["operation:testing.assess_release"][1]]  # RELEASE while TC-003 fails

    home, script = _world(tmp_path, capsys, mutate)
    code, env = run(capsys, "run", "demo", "--home", home, "--stage", "testing", "--script", script)
    assert env["data"]["stopped"]
    steps = " ".join(next(s for s in env["data"]["stages"] if s["stage"] == "testing")["what_happened"])
    assert "test-assess" in steps and "optimistic_recommendation" in steps


# ----------------------------------------------------------------------------- tolerance learned from the live Codex run


def test_finding_refs_without_a_real_hash_are_dropped_not_fatal():
    from sdlc.runtime.scheduler import _drop_unusable_finding_refs

    structured = {"findings": [{"finding_id": "f", "code": "x", "severity": "warning", "message": "m",
                                "related_refs": [{"artifact_id": "a", "revision": 1, "sha256": ""}, {"artifact_id": "b", "revision": 1, "sha256": "ab" * 32}]}]}
    _drop_unusable_finding_refs(structured)
    assert [r["artifact_id"] for r in structured["findings"][0]["related_refs"]] == ["b"]


def test_bare_case_list_is_wrapped_into_the_contract_shape():
    from sdlc.domain.models import ArtifactCandidate, WorkerProposal
    from sdlc.teams.testing.operations import TestingPolicy

    case = {"case_id": "TC-001", "requirement_ids": ["REQ-001"], "level": "unit", "purpose": "p", "preconditions": [], "steps": ["s"], "expected": ["e"], "technique": "t", "automation": "automated", "recipe_id": "pytest"}
    proposal = WorkerProposal(schema_version="1.0", task_id="t", summary="s", findings=[], questions=[], suggested_next_operations=[],
                              artifact_candidates=[ArtifactCandidate(kind="test_cases", media_type="text/markdown", relative_path="tests/TEST_CASES.json", workspace_path=None, content_text=json.dumps([case]))])
    policy = TestingPolicy(name="testing.derive_cases.v1", short_name="derive_cases")
    normalized, notes = policy.normalize(None, proposal, None)
    assert json.loads(normalized.artifact_candidates[0].content_text) == {"cases": [case]} and normalized.artifact_candidates[0].media_type == "application/json"
    assert [n.code for n in notes] == ["test_cases_wrapped"]
    assert not policy.check_outputs(None, normalized, None)  # a valid case list passes the obligations


def test_mapping_falls_back_to_the_case_number_when_benches_shorten_ids():
    """Live Codex run: cases named TC-M0M1-007, tests named test_tc_007_*; the number decides."""
    nodes = ["tests/b.py::test_tc_007_x[a-b]", "tests/b.py::test_tc_0071_other", "tests/b.py::test_tc_m0m1_003_exact"]
    mapping = map_nodes_to_cases(nodes, ["TC-M0M1-003", "TC-M0M1-007", "TC-M0M1-071"])
    assert mapping["TC-M0M1-007"] == [nodes[0]] and mapping["TC-M0M1-003"] == [nodes[2]] and mapping["TC-M0M1-071"] == []
