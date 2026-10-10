from __future__ import annotations

import json
import zipfile
from io import BytesIO

import pytest

from sdlc.application.ingress import detect_media_type, import_material
from sdlc.application.views import TeamView
from sdlc.cli.main import main
from sdlc.domain.errors import DeniedScope, InvalidSchema
from sdlc.domain.models import TeamId
from tests.conftest import EXAMPLES


def test_import_markdown_is_granted_only_to_target_team(context, tmp_path):
    brief = tmp_path / "brief.md"
    brief.write_text("# Brief\n도서관 예약 데모. Reserve a copy.\n", encoding="utf-8")
    result = import_material(context, team=TeamId.PLANNING, path=brief, purpose="course brief")
    assert result.receipt.status.value == "accepted" and result.extraction == "complete" and result.needs_input is None
    assert result.extraction_artifact is not None and result.extraction_artifact.depends_on == [result.artifact.ref]
    assert result.artifact.owner_team.value == "participant" and result.artifact.origin.value == "participant"
    planning = TeamView(context, TeamId.PLANNING)
    assert "도서관" in planning.read_text(result.artifact.ref, [result.delivery.delivery_id])
    with pytest.raises(DeniedScope):
        TeamView(context, TeamId.REQUIREMENTS).read_text(result.artifact.ref, [result.delivery.delivery_id])


def test_import_size_limit_and_unsupported_type(context, tmp_path):
    big = tmp_path / "big.md"
    big.write_bytes(b"x" * (context.config.imports.max_file_bytes + 1))
    with pytest.raises(InvalidSchema, match="max_file_bytes"):
        import_material(context, team=TeamId.PLANNING, path=big, purpose="too big")
    binary = tmp_path / "blob.bin"
    binary.write_bytes(bytes(range(256)))
    with pytest.raises(InvalidSchema, match="unsupported"):
        import_material(context, team=TeamId.PLANNING, path=binary, purpose="binary")


def test_detect_media_type_by_content():
    assert detect_media_type(b"%PDF-1.7 ...", ".txt") == ("application/pdf", "pending")
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", "<w/>")
    assert detect_media_type(buf.getvalue(), ".docx")[1] == "pending"
    assert detect_media_type("한글 텍스트".encode("utf-8"), ".md") == ("text/markdown", "text")
    with pytest.raises(InvalidSchema):
        detect_media_type(b"\xff\xfe", ".md")


def run(capsys, *argv) -> tuple[int, dict]:
    code = main(["--json", *argv])
    return code, json.loads(capsys.readouterr().out)


def test_cli_full_m1_flow(tmp_path, capsys):
    home = str(tmp_path / "home")
    code, env = run(capsys, "init", "demo", "--home", home)
    assert code == 0, env

    code, env = run(capsys, "fixture", "load", "--project", "demo", "--home", home, str(EXAMPLES / "minimal-cycle.json"))
    assert code == 0 and env["data"]["deliveries"] == 7, env

    brief = tmp_path / "brief.md"
    brief.write_text("# brief\n", encoding="utf-8")
    code, env = run(capsys, "import", "--project", "demo", "--home", home, "--team", "planning", "--file", str(brief))
    assert code == 0 and env["data"]["receipt"] == "accepted"
    delivery_id = env["data"]["delivery_id"]

    code, env = run(capsys, "handoff", "inbox", "--project", "demo", "--home", home, "--team", "planning")
    assert code == 0 and {d["delivery_id"] for d in env["data"]["inbox"]} == {"d-00", delivery_id}

    code, env = run(capsys, "review", "--project", "demo", "--home", home, "--team", "design", "--delivery", "d-02", "--decision", "accept")
    assert code == 0 and env["data"]["team_baseline"] == "b-02"

    code, env = run(capsys, "status", "--project", "demo", "--home", home)
    assert code == 0 and env["data"]["teams"]["design"]["baseline_bundle_id"] == "b-02"
    assert env["data"]["teams"]["requirements"]["baseline_bundle_id"] is None

    # Deliver b-06 (maintenance -> requirements) claiming requirements' baseline is b-01: stale.
    code, env = run(capsys, "handoff", "deliver", "--project", "demo", "--home", home, "--bundle", "b-06", "--expected-target-baseline", "b-01")
    assert code == 0 and env["data"]["receipt"] == "stale"
    code, env = run(capsys, "handoff", "deliver", "--project", "demo", "--home", home, "--bundle", "b-06")
    assert code == 1  # must state the expected baseline or --no-baseline explicitly

    code, env = run(capsys, "artifacts", "list", "--project", "demo", "--home", home, "--owner", "participant")
    assert code == 0 and env["data"]["count"] == 3  # fixture brief + imported brief + its extraction

    code, env = run(capsys, "doctor", "--project", "demo", "--home", home)
    assert code == 0 and env["data"]["store"]["corrupt_objects"] == [] and env["data"]["store"]["database_integrity"]


def test_cli_publish_from_manifest(tmp_path, capsys):
    home = str(tmp_path / "home")
    run(capsys, "init", "demo", "--home", home)
    run(capsys, "fixture", "load", "--project", "demo", "--home", home, str(EXAMPLES / "minimal-cycle.json"))
    code, env = run(capsys, "artifacts", "list", "--project", "demo", "--home", home, "--owner", "requirements")
    req_ref = env["data"]["artifacts"][0]["ref"]
    code, env = run(capsys, "artifacts", "list", "--project", "demo", "--home", home, "--owner", "planning")
    plan_ref = env["data"]["artifacts"][0]["ref"]
    code, env = run(capsys, "artifacts", "list", "--project", "demo", "--home", home, "--owner", "participant")
    brief_ref = env["data"]["artifacts"][0]["ref"]
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({
        "bundle_id": "b-req-to-testing", "baseline_id": "baseline-requirements-001", "kind": "forward",
        "target_team": "testing", "summary": "early test planning", "artifact_refs": [brief_ref, plan_ref, req_ref],
    }), encoding="utf-8")
    code, env = run(capsys, "handoff", "publish", "--project", "demo", "--home", home, "--team", "requirements", "--manifest", str(manifest))
    assert code == 0 and env["data"]["route"] == "requirements->testing", env
    code, env = run(capsys, "handoff", "deliver", "--project", "demo", "--home", home, "--bundle", "b-req-to-testing", "--no-baseline")
    assert code == 0 and env["data"]["receipt"] == "accepted", env
    # A bare filename is not an artifact reference.
    manifest.write_text(json.dumps({"bundle_id": "b-bad", "baseline_id": "x", "target_team": "design", "summary": "s", "artifact_refs": ["REQUIREMENTS.md"]}))
    code, env = run(capsys, "handoff", "publish", "--project", "demo", "--home", home, "--team", "requirements", "--manifest", str(manifest))
    assert code == 1 and "ID@REV:SHA256" in env["errors"][0]["message"]


def test_cli_stage_and_task_commands(tmp_path, capsys):
    home = str(tmp_path / "home")
    run(capsys, "init", "demo", "--home", home)
    run(capsys, "fixture", "load", "--project", "demo", "--home", home, str(EXAMPLES / "minimal-cycle.json"))
    code, env = run(capsys, "artifacts", "list", "--project", "demo", "--home", home, "--owner", "requirements")
    ident, rest = env["data"]["artifacts"][0]["ref"].split("@")
    revision, sha = rest.split(":")
    task = {
        "schema_version": "1.0", "project_id": "demo", "cycle_id": "cycle-001", "task_id": "t-intake", "team_id": "design",
        "agent_instance_id": "agent-design-1", "operation": "design.intake", "operation_version": 1, "input_deliveries": ["d-02"],
        "input_refs": [{"artifact_id": ident, "revision": int(revision), "sha256": sha}], "depends_on": [],
        "parameters": {"objective": "assess inputs", "scope_ids": [], "requested_artifact_kinds": [], "diagram_request": None},
        "expected_output_schema": "#/$defs/WorkerProposal", "backend_profile": "fixture", "workspace_ref": None,
        "limits": {"max_attempts": 3, "timeout_seconds": 60, "max_output_tokens": 512, "max_cost_microunits": 0},
    }
    proposal = tmp_path / "proposal.json"
    proposal.write_text(json.dumps([task]), encoding="utf-8")
    script = tmp_path / "script.json"
    script.write_text(json.dumps({"steps": {"task:t-intake": [
        {"error": {"category": "quota_exhausted", "provider_code": "q", "message": "limit reached", "automatic_retry_now": False,
                   "retry_after_seconds": None, "reset_at": None, "completion_certainty": "not_completed",
                   "next_action": "wait for the limit to reset", "diagnostic_ref": None}},
        {"proposal": {"summary": "intake done", "artifact_candidates": [
            {"kind": "input_assessment", "media_type": "text/markdown", "relative_path": "docs/INTAKE.md", "content_text": "# Intake\n", "workspace_path": None}]}},
    ]}}), encoding="utf-8")

    code, env = run(capsys, "stage", "plan", "--project", "demo", "--home", home, "--team", "design", "--proposal", str(proposal))
    assert code == 0 and env["data"]["tasks"] == ["t-intake"], env
    code, env = run(capsys, "stage", "run", "--project", "demo", "--home", home, "--script", str(script))
    assert code == 0 and env["data"]["outcomes"][0]["state"] == "paused", env
    code, env = run(capsys, "status", "--project", "demo", "--home", home)
    assert code == 0 and "fixture" in env["data"]["quota_pauses"] and env["data"]["teams"]["design"]["tasks"][0]["state"] == "paused"
    code, env = run(capsys, "task", "resume", "t-intake", "--project", "demo", "--home", home, "--dry-run")
    assert code == 0 and "blocked" in env["data"] and env["data"]["model_calls_made"] == 0
    code, env = run(capsys, "task", "resume", "t-intake", "--project", "demo", "--home", home, "--quota-restored")
    assert code == 0 and env["data"]["state_after"] == "pending"
    # The fixture script restarts from step one in a new process; the quota failure replays, so
    # the second CLI run shows the pause again. Use a script whose first step succeeds instead.
    script.write_text(json.dumps({"steps": {"task:t-intake": {"proposal": {"summary": "intake done", "artifact_candidates": [
        {"kind": "input_assessment", "media_type": "text/markdown", "relative_path": "docs/INTAKE.md", "content_text": "# Intake\n", "workspace_path": None}]}}}}), encoding="utf-8")
    code, env = run(capsys, "stage", "run", "--project", "demo", "--home", home, "--script", str(script))
    assert code == 0 and env["data"]["outcomes"][0]["state"] == "completed" and env["data"]["outcomes"][0]["artifacts"], env
    code, env = run(capsys, "task", "list", "--project", "demo", "--home", home, "--team", "design")
    assert code == 0 and env["data"]["tasks"][0]["attempts"] == 2
    code, env = run(capsys, "task", "rerun", "t-intake", "--project", "demo", "--home", home)
    assert code == 0 and env["data"]["successor_task_id"] == "t-intake-r2"
    code, env = run(capsys, "task", "cancel", "t-intake-r2", "--project", "demo", "--home", home)
    assert code == 0 and env["data"]["state"] == "cancelled"
