"""M7: release gate and manifest, Maintenance team, change loop with stale propagation,
export round trip (handoff docs/04 s7, docs/02 FR-015/FR-019/FR-021, AT-017, AT-019).

Offline, fixture-driven, all six teams: planning -> requirements -> (design package) ->
implementation -> testing (planted defect, repair, re-test) -> release-001 -> a
participant change request -> maintenance triages, analyses impact, routes the change to
Implementation -> implementation and testing redo their work on fresh plans -> release-002
-> maintenance verifies closure. Release-001 and its evidence stay available.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sdlc.application.artifacts import ArtifactService
from sdlc.application.export import export_release, verify_export
from sdlc.application.handoffs import HandoffService
from sdlc.application.release import ReleaseService
from sdlc.domain.models import BundleKind, ExecutionRecord, Origin, OwnerTeam, TeamId
from tests.integration.test_m5_implementation import EXAMPLE, PARTICIPANT, run

MEMO = EXAMPLE / "change-request.md"


def _project(tmp_path, capsys, mutate=None):
    """A project with the fixture script, run to the end of requirements, design package injected."""
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
        _, rref = handoffs.publish(source=OwnerTeam.REQUIREMENTS, target=TeamId.DESIGN, kind=BundleKind.FORWARD, bundle_id="b-req", cycle_id="cycle-001", baseline_id="bl-req", artifact_refs=handoffs.compose_manifest(TeamId.REQUIREMENTS), summary="requirements")
        rd = handoffs.deliver(bundle_ref=rref, expected_target_baseline=None)
        handoffs.receive(rd.delivery_id)
        handoffs.accept_baseline(team=TeamId.DESIGN, delivery_id=rd.delivery_id, actor=PARTICIPANT, rationale="ok")
        svc.register(owner=OwnerTeam.DESIGN, artifact_id="design-doc", kind="design_document", media_type="text/markdown", relative_path="DESIGN.md",
                     data="# Design\n\n## Architecture\nReservationService (예약 서비스).\n\n## Interface\nreserve/cancel\n\n## Behavior\nconflict raises\n".encode("utf-8"), origin=Origin.FIXTURE)
        _, ref = handoffs.publish(source=OwnerTeam.DESIGN, target=TeamId.IMPLEMENTATION, kind=BundleKind.FORWARD, bundle_id="b-design", cycle_id="cycle-001", baseline_id="bl", artifact_refs=handoffs.compose_manifest(TeamId.DESIGN), summary="design")
        d = handoffs.deliver(bundle_ref=ref, expected_target_baseline=None)
        handoffs.receive(d.delivery_id)
        handoffs.accept_baseline(team=TeamId.IMPLEMENTATION, delivery_id=d.delivery_id, actor=PARTICIPANT, rationale="ok")
    finally:
        ctx.close()
    return home, str(script)


def _open(home):
    from sdlc.application.projects import open_project

    return open_project(Path(home), "demo")


# ----------------------------------------------------------------------------- release gate


def test_release_is_refused_until_the_evidence_allows_it(tmp_path, capsys):
    home, script = _project(tmp_path, capsys)
    code, env = run(capsys, "release", "demo", "--home", home)
    assert code == 0 and env["data"]["released"] is False and any("no test report" in r for r in env["data"]["why_not"])
    code, env = run(capsys, "run", "demo", "--home", home, "--stage", "implementation", "--until", "testing", "--defect-loops", "0", "--script", script)
    assert env["data"]["stopped"] and "open defect" in env["data"]["what_to_do"]  # HOLD on the planted defect, no automatic repair
    code, env = run(capsys, "release", "demo", "--home", home)
    assert env["data"]["released"] is False
    assert any("HOLD" in r for r in env["data"]["why_not"]) and any("failed cases" in r for r in env["data"]["why_not"])


# ----------------------------------------------------------------------------- the whole cycle


@pytest.fixture(scope="module")
def cycle(tmp_path_factory):
    """Run the six-team cycle once per module; the tests below inspect it."""
    import io
    import contextlib

    from sdlc.cli.main import main

    tmp = tmp_path_factory.mktemp("cycle")

    class _Cap:  # a stand-in for capsys: collect stdout of main()
        def __init__(self):
            self.buf = io.StringIO()

        def readouterr(self):
            out = self.buf.getvalue()
            self.buf = io.StringIO()
            return type("R", (), {"out": out, "err": ""})()

    cap = _Cap()

    def run_cli(_cap, *argv):
        with contextlib.redirect_stdout(cap.buf):
            code = main(["--json", *argv])
        return code, json.loads(cap.readouterr().out)

    import tests.integration.test_m7_maintenance as me

    original = me.run
    me.run = run_cli  # type: ignore[assignment]
    try:
        home, script = _project(tmp, cap)
        code, env = run_cli(cap, "run", "demo", "--home", home, "--stage", "implementation", "--until", "testing", "--script", script)
        assert code == 0 and not env["data"]["stopped"], env
        first = env["data"]
        code, env = run_cli(cap, "request", "demo", str(MEMO), "--home", home)
        assert code == 0 and env["data"]["delivered_as"].startswith("delivery-")
        code, env = run_cli(cap, "run", "demo", "--home", home, "--stage", "maintenance", "--script", script)
        assert code == 0, env
        second = env["data"]
    finally:
        me.run = original  # type: ignore[assignment]
    return {"home": home, "script": script, "first": first, "second": second, "run": run_cli, "cap": cap}


def test_release_001_follows_the_repaired_candidate(cycle):
    names = [s["stage"] for s in cycle["first"]["stages"]]
    assert "release" in names
    release = next(s for s in cycle["first"]["stages"] if s["stage"] == "release")
    assert any("release-001 recorded (nonconforming" in step for step in release["what_happened"]), release


def test_change_returns_to_the_earliest_owner_and_the_cycle_reruns(cycle):
    data = cycle["second"]
    assert not data["stopped"], data
    names = [s["stage"] for s in data["stages"]]
    assert "maintenance" in names and "maintenance->implementation (change)" in names
    assert "implementation (change round 1)" in names and "testing (change round 1)" in names and "maintenance (closure round 1)" in names
    assert "design" not in " ".join(names)  # requirements and design were not reopened: the change was corrective
    repair = " ".join(next(s for s in data["stages"] if s["stage"] == "implementation (change round 1)")["what_happened"])
    assert "impl-code-r3: completed" in repair
    retest = " ".join(next(s for s in data["stages"] if s["stage"] == "testing (change round 1)")["what_happened"])
    assert "test-execute-r3: completed" in retest and "test-assess-r3: completed" in retest
    releases = [s for s in data["stages"] if s["stage"] == "release"]
    assert releases and any("release-002 recorded" in step for step in releases[-1]["what_happened"])
    closure = " ".join(next(s for s in data["stages"] if s["stage"] == "maintenance (closure round 1)")["what_happened"])
    assert "maint-closure-r2: completed" in closure


def test_prior_release_and_evidence_are_preserved_and_dependents_are_stale(cycle):
    ctx = _open(cycle["home"])
    try:
        svc = ArtifactService(ctx)
        releases = ReleaseService(ctx).releases()
        assert [a.artifact_id for a in releases] == ["release-001-manifest", "release-002-manifest"]
        m1 = json.loads(svc.read_bytes(releases[0].ref).decode())
        m2 = json.loads(svc.read_bytes(releases[1].ref).decode())
        assert m1["source_ref"]["artifact_id"] == "impl-code-r2-candidate-snapshot" and m2["source_ref"]["artifact_id"] == "impl-code-r3-candidate-snapshot"
        assert svc.status(releases[0].ref) != "stale"  # releases are history, never stale
        # the old evidence still exists, readable, but stale; the new evidence is current
        old_records = [a for a in svc.list(OwnerTeam.TESTING) if a.kind == "execution_record" and a.created_by_task == "test-execute-r2"]
        new_records = [a for a in svc.list(OwnerTeam.TESTING) if a.kind == "execution_record" and a.created_by_task == "test-execute-r3"]
        assert old_records and svc.status(old_records[0].ref) == "stale" and svc.read_bytes(old_records[0].ref)
        assert new_records and svc.status(new_records[0].ref) != "stale"
        record = ExecutionRecord.model_validate(json.loads(svc.read_bytes(new_records[0].ref).decode()))
        assert record.status.value == "passed" and any("tc_004" in o.message for o in record.case_observations)
        events = [json.loads(r["payload_json"]) for r in ctx.db.all("SELECT payload_json FROM events WHERE project_id=? AND event_type='baseline.invalidated'", (ctx.project_id,))]
        assert {e["team"] for e in events} == {"implementation", "testing"}
        routed = [json.loads(r["payload_json"]) for r in ctx.db.all("SELECT payload_json FROM events WHERE project_id=? AND event_type='change.routed'", (ctx.project_id,))]
        assert len(routed) == 1 and routed[0]["owner"] == "implementation"
        reports = [a for a in svc.list(OwnerTeam.MAINTENANCE) if a.kind == "maintenance_report"]
        assert reports and "closed: release-002 supersedes release-001" in svc.read_bytes(reports[-1].ref).decode()
    finally:
        ctx.close()


def test_export_round_trip_verifies_and_detects_tampering(cycle, tmp_path):
    ctx = _open(cycle["home"])
    try:
        out = tmp_path / "export-001"
        result = export_release(ctx, out, "release-001")  # the *older* baseline
        assert result.release_id == "release-001" and result.files and result.excluded > 0
        manifest = json.loads((out / "EXPORT_MANIFEST.json").read_text(encoding="utf-8"))
        svc = ArtifactService(ctx)
        ids = {e["artifact_id"] for e in manifest["artifacts"]}
        assert "impl-code-r2-candidate-snapshot" in ids and "impl-code-r3-candidate-snapshot" not in ids  # exact selected revisions only
        assert "design-doc" in ids
        for entry in manifest["artifacts"]:  # byte-for-byte, including the Korean design text
            data = (out / Path(*entry["path"].split("/"))).read_bytes()
            assert data == svc.read_bytes(next(a.ref for a in svc.list(OwnerTeam(entry["owner"])) if a.artifact_id == entry["artifact_id"] and a.revision == entry["revision"]))
        design = next(e for e in manifest["artifacts"] if e["artifact_id"] == "design-doc")
        assert "예약 서비스" in (out / Path(*design["path"].split("/"))).read_text(encoding="utf-8")
        assert all("project.yaml" not in e["path"] and "state.sqlite" not in e["path"] for e in manifest["artifacts"])
        labels = {label for e in manifest["artifacts"] for label in e["labels"]}
        assert "nonconforming_isolation" in labels and "fixture-evidence: cannot satisfy release gates" in labels
    finally:
        ctx.close()
    assert verify_export(out).ok
    target = out / Path(*manifest["artifacts"][0]["path"].split("/"))
    target.write_bytes(target.read_bytes() + b"\n# tampered")
    (out / "stray.txt").write_text("not part of the release", encoding="utf-8")
    verdict = verify_export(out)
    assert not verdict.ok and verdict.tampered == [manifest["artifacts"][0]["path"]] and verdict.extra == ["stray.txt"]
    target.unlink()
    assert verify_export(out).missing == [manifest["artifacts"][0]["path"]]


# ----------------------------------------------------------------------------- quota pause inside the maintenance loop


def test_quota_pause_during_maintenance_resumes_after_restoration(tmp_path, capsys):
    from sdlc.adapters.brains.fixture import quota_exhausted_error

    def mutate(raw):
        raw["steps"]["operation:maintenance.triage"] = [{"error": quota_exhausted_error().to_json_dict()}, raw["steps"]["operation:maintenance.triage"]]

    home, script = _project(tmp_path, capsys, mutate)
    code, env = run(capsys, "run", "demo", "--home", home, "--stage", "implementation", "--until", "testing", "--script", script)
    assert not env["data"]["stopped"], env
    assert run(capsys, "request", "demo", str(MEMO), "--home", home)[0] == 0
    code, env = run(capsys, "run", "demo", "--home", home, "--stage", "maintenance", "--script", script)
    assert env["data"]["stopped"] and "paused" in json.dumps(env["data"]).lower()
    code, env = run(capsys, "continue", "demo", "--home", home, "--script", script)
    assert env["data"].get("blocked"), env  # not restored yet: no call is made
    code, env = run(capsys, "continue", "demo", "--home", home, "--quota-restored", "--script", script)
    assert code == 0 and env["data"].get("resumed"), env  # the paused triage task is pending again; no call was replayed blindly
    # (this synthetic project skipped the Design stage, so `continue` restarts there; run the stage explicitly)
    code, env = run(capsys, "run", "demo", "--home", home, "--stage", "maintenance", "--script", script)
    assert code == 0 and not env["data"]["stopped"], env
    names = [s["stage"] for s in env["data"]["stages"]]
    assert "maintenance (closure round 1)" in names
