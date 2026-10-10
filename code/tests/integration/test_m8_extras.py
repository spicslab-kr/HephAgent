"""M8: extra import formats, diagram rendering through optional tools, the local endpoint
profile command, the HTML overview, and the isolation launcher (docker profile, canary probe,
strict-mode gating) exercised with fake executables, since this machine has no container engine.
"""

from __future__ import annotations

import json
import os
import stat
import sys
import textwrap
from pathlib import Path

import pytest

from sdlc.artifacts.diagrams import render, render_status
from sdlc.artifacts.imports import extract
from sdlc.runtime.launcher import LauncherProfile, last_probe, probe, profile_from_config, usable_profile
from tests.integration.test_m5_implementation import EXAMPLE, run

# ----------------------------------------------------------------------------- imports


def test_html_json_and_csv_imports_extract_readable_text():
    html = "<html><head><style>x{}</style><script>1</script></head><body><h1>Spec</h1><p>Reserve a &amp; copy</p><h2>Rules</h2><ul><li>one</li><li>two</li></ul></body></html>"
    out = extract(html.encode("utf-8"), "text/html")
    assert out.method == "html-strip" and "# Spec" in out.text and "## Rules" in out.text and "Reserve a & copy" in out.text and "1" not in out.text.split("Reserve")[0]
    assert [l.text for l in out.locators] == ["Spec", "Rules"]
    out = extract(b'{"cases": [{"id": "TC-1"}]}', "application/json")
    assert out.method == "json" and '"TC-1"' in out.text and out.completeness == "complete"
    assert extract(b"{not json", "application/json").completeness == "unreadable"
    out = extract("id,title\nREQ-1,reserve a copy\nREQ-2,cancel\n".encode("utf-8"), "text/csv")
    assert out.text.splitlines()[0] == "| id | title |" and "| REQ-2 | cancel |" in out.text
    assert any(l.detail == "table 1, row 2, col 1" and l.text == "REQ-1" for l in out.locators)


def test_imported_files_of_new_types_reach_a_team(tmp_path, capsys):
    home = str(tmp_path / "home")
    assert run(capsys, "new", "demo", "--home", home, "--brief", str(EXAMPLE / "brief.md"), "--brain", "fixture")[0] == 0
    memo = tmp_path / "ops.csv"
    memo.write_text("date,symptom\n2026-10-10,empty member id accepted\n", encoding="utf-8")
    code, env = run(capsys, "request", "demo", str(memo), "--home", home)
    assert code == 0 and env["data"]["extraction"] in ("complete", "text") or env["data"]["delivered_as"].startswith("delivery-")


# ----------------------------------------------------------------------------- diagrams


def _fake_executable(folder: Path, name: str, body: str) -> Path:
    """A fake command on PATH: on Windows a .cmd shim calling python; elsewhere a python script."""
    script = folder / f"{name}.py"
    script.write_text(body, encoding="utf-8")
    if sys.platform == "win32":
        shim = folder / f"{name}.cmd"
        shim.write_text(f'@echo off\r\n"{sys.executable}" "{script}" %*\r\n', encoding="mbcs")  # cmd reads batch files in the ANSI code page (this repo's path is Korean)
        return shim
    shim = folder / name
    shim.write_text(f"#!/bin/sh\nexec '{sys.executable}' '{script}' \"$@\"\n", encoding="utf-8")
    shim.chmod(shim.stat().st_mode | stat.S_IXUSR)
    return shim


def test_diagram_renders_only_when_a_renderer_is_installed(tmp_path, monkeypatch):
    source = "flowchart TD\n  A --> B\n"
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    (tmp_path / "empty").mkdir()
    status = render(source, "text/vnd.mermaid", tmp_path / "out" / "d.svg")
    assert status["installed"] is False and status["rendered"] is False and status["label"] == "unrendered"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _fake_executable(bin_dir, "mmdc", textwrap.dedent("""
        import sys
        args = sys.argv[1:]
        out = args[args.index("-o") + 1]
        open(out, "w", encoding="utf-8").write("<svg>fake</svg>")
        """))
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ.get("SYSTEMROOT", ""))
    assert render_status("text/vnd.mermaid", None)["installed"] is True
    status = render(source, "text/vnd.mermaid", tmp_path / "out" / "d.svg")
    assert status["rendered"] is True and (tmp_path / "out" / "d.svg").read_text(encoding="utf-8") == "<svg>fake</svg>"


# ----------------------------------------------------------------------------- local endpoint profile


def test_brain_local_needs_the_client_and_a_server_address(tmp_path, capsys):
    home = str(tmp_path / "home")
    assert run(capsys, "new", "demo", "--home", home, "--brief", str(EXAMPLE / "brief.md"), "--brain", "fixture")[0] == 0
    code, env = run(capsys, "brain", "demo", "local", "--home", home)
    assert code != 0
    text = json.dumps(env)
    assert ("pip install -e .[local]" in text) or ("--base-url" in text)
    try:
        import openai  # noqa: F401
    except ImportError:
        return
    code, env = run(capsys, "brain", "demo", "local", "--home", home, "--base-url", "http://127.0.0.1:9/v1", "--model", "tiny")
    assert code == 0 and env["data"]["brain"]["planning"] == "local-model"


# ----------------------------------------------------------------------------- launcher: wrap, probe, gating


def test_docker_profile_wraps_commands_with_only_the_workspace_mounted(tmp_path):
    profile = LauncherProfile(name="docker", type="docker", image="python:3.12")
    argv = profile.wrap(["{python}", "-m", "pytest", "-v"], tmp_path)
    assert argv[:5] == ["docker", "run", "--rm", "--network", "none"]
    assert f"{tmp_path.resolve()}:/work" in argv and "-w" in argv and argv[-4:] == ["python3", "-m", "pytest", "-v"]
    assert not any(a.endswith("_API_KEY") for a in argv)


class _Proc:
    def __init__(self, stdout: str, returncode: int = 0, stderr: str = ""):
        self.stdout, self.returncode, self.stderr = stdout, returncode, stderr


def _ctx(tmp_path):
    from sdlc.application.projects import create_project, open_project

    home = tmp_path / "home"
    create_project(home, "demo", Path(__file__).resolve().parents[2] / "config" / "project.example.yaml")
    return open_project(home, "demo")


def test_probe_passes_only_when_every_canary_check_holds(tmp_path):
    ctx = _ctx(tmp_path)
    try:
        profile = LauncherProfile(name="docker", type="docker", image="python:3.12")
        good = {"workspace_readable": True, "canary_readable": False, "home_readable": False, "network": "blocked", "env_leak": False}
        outcome = probe(ctx, profile, runner=lambda argv, **kw: _Proc(json.dumps(good)))
        assert outcome.passed and outcome.command[:2] == ["docker", "run"]
        assert last_probe(ctx, "docker").passed
        leaky = {**good, "canary_readable": True, "network": "reachable"}
        outcome = probe(ctx, profile, runner=lambda argv, **kw: _Proc(json.dumps(leaky)))
        assert not outcome.passed and len(outcome.reasons) == 2 and not last_probe(ctx, "docker").passed
        outcome = probe(ctx, profile, runner=lambda argv, **kw: _Proc("", returncode=125, stderr="docker: image not found"))
        assert not outcome.passed and "did not run" in outcome.reasons[0]
    finally:
        ctx.close()


def test_strict_mode_refuses_until_a_probe_passes_then_runs_checks_inside_the_boundary(tmp_path, capsys, monkeypatch):
    """End to end with a fake docker: strict mode blocks the check with the precise reason, the
    probe passes through the fake engine, the check then runs *inside* the fake boundary (/work
    mapped back to the mounted workspace) and its environment says conforming; coding stays refused."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    # a fake `docker run --rm --network none ... -v HOST:/work -w /work IMAGE cmd...`: runs cmd in HOST
    _fake_executable(bin_dir, "docker", textwrap.dedent(f"""
        import os, subprocess, sys
        args = sys.argv[1:]
        host = args[args.index("-v") + 1].rsplit(":", 1)[0]
        image = args[args.index("-w") + 2]
        cmd = args[args.index(image) + 1:]
        cmd = [{sys.executable!r} if c == "python3" else c.replace("/work", host) for c in cmd]
        env = {{"PATH": os.environ.get("PATH", ""), "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""), "LANG": "C.UTF-8"}}
        proc = subprocess.run(cmd, cwd=host, env=env, capture_output=True, text=True)
        sys.stdout.write(proc.stdout); sys.stderr.write(proc.stderr); sys.exit(proc.returncode)
        """))
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ["PATH"])
    home = str(tmp_path / "home")
    script = tmp_path / "script.json"
    script.write_text((EXAMPLE / "fixture-script.json").read_text(encoding="utf-8"), encoding="utf-8")
    assert run(capsys, "new", "demo", "--home", home, "--brief", str(EXAMPLE / "brief.md"), "--brain", "fixture")[0] == 0
    code, env = run(capsys, "isolation", "demo", "strict", "--home", home, "--launcher", "docker", "--image", "python:3.12")
    assert code == 0 and env["data"]["launcher"] == "docker" and "refused until" in env["data"]["note"]
    code, env = run(capsys, "isolation", "demo", "probe", "--home", home)
    # the fake engine really executes the probe script on this host: the canary IS readable, so the probe must fail honestly
    assert code == 0 and env["data"]["passed"] is False and any("outside the workspace was readable" in r for r in env["data"]["reasons"])
    # make the probe pass by recording a passing probe through the service with a scripted engine result
    from sdlc.application.projects import open_project

    ctx = open_project(Path(home), "demo")
    try:
        good = {"workspace_readable": True, "canary_readable": False, "home_readable": False, "network": "blocked", "env_leak": False}
        assert probe(ctx, profile_from_config(ctx), runner=lambda argv, **kw: _Proc(json.dumps(good))).passed
        assert usable_profile(ctx)[0] is not None
    finally:
        ctx.close()
    code, env = run(capsys, "run", "demo", "--home", home, "--until", "requirements", "--script", str(script))
    assert code == 0 and not env["data"]["stopped"], env
    # implementation under strict: coding is refused with the precise reason (zero launches), checks would run inside the boundary
    from sdlc.application.artifacts import ArtifactService
    from sdlc.application.handoffs import HandoffService
    from sdlc.domain.models import BundleKind, Origin, OwnerTeam, TeamId
    from tests.integration.test_m5_implementation import PARTICIPANT

    ctx = open_project(Path(home), "demo")
    try:
        svc, handoffs = ArtifactService(ctx), HandoffService(ctx)
        svc.register(owner=OwnerTeam.DESIGN, artifact_id="design-doc", kind="design_document", media_type="text/markdown", relative_path="DESIGN.md", data=b"# Design\n", origin=Origin.FIXTURE)
        _, ref = handoffs.publish(source=OwnerTeam.DESIGN, target=TeamId.IMPLEMENTATION, kind=BundleKind.FORWARD, bundle_id="b-design", cycle_id="cycle-001", baseline_id="bl", artifact_refs=handoffs.compose_manifest(TeamId.DESIGN), summary="design")
        d = handoffs.deliver(bundle_ref=ref, expected_target_baseline=None)
        handoffs.receive(d.delivery_id)
        handoffs.accept_baseline(team=TeamId.IMPLEMENTATION, delivery_id=d.delivery_id, actor=PARTICIPANT, rationale="ok")
    finally:
        ctx.close()
    code, env = run(capsys, "run", "demo", "--home", home, "--stage", "implementation", "--script", str(script))
    steps = " ".join(next(s for s in env["data"]["stages"] if s["stage"] == "implementation")["what_happened"])
    assert "impl-code: needs_input" in steps and "coding clients are not launched inside an isolation profile" in steps
    # a check against a snapshot delivered by hand runs inside the (fake) boundary and is conforming
    from sdlc.adapters.brains.fixture import FixtureScript
    from sdlc.adapters.registry import AdapterRegistry
    from sdlc.artifacts.snapshots import create_snapshot
    from sdlc.domain.models import Task, TaskLimits
    from sdlc.domain.states import TaskState
    from sdlc.runtime.scheduler import Scheduler
    import asyncio

    ctx = open_project(Path(home), "demo")
    try:
        svc = ArtifactService(ctx)
        src = tmp_path / "cand"
        src.mkdir()
        (src / "test_ok.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
        archive, _ = create_snapshot(src)
        snap = svc.register(owner=OwnerTeam.IMPLEMENTATION, artifact_id="hand-candidate", kind="source_snapshot", media_type="application/zip", relative_path="snapshots/hand.zip", data=archive, origin=Origin.PARTICIPANT)
        design_ref = next(a.ref for a in svc.list(OwnerTeam.DESIGN) if a.artifact_id == "design-doc")
        delivery = [d["delivery_id"] for d in __import__("sdlc.application.views", fromlist=["TeamView"]).TeamView(ctx, TeamId.IMPLEMENTATION).list_inbox()][0]
        task = Task(schema_version="1.0", project_id=ctx.project_id, cycle_id="cycle-001", task_id="strict-check", team_id=TeamId.IMPLEMENTATION, agent_instance_id="impl-check",
                    operation="implementation.check", operation_version=1, input_deliveries=[delivery], input_refs=[design_ref, snap.ref], depends_on=[],
                    parameters={"objective": "check", "scope_ids": [], "requested_artifact_kinds": [], "diagram_request": None}, expected_output_schema="#/$defs/WorkerProposal",
                    backend_profile="fixture", workspace_ref=None, limits=TaskLimits(max_attempts=3, timeout_seconds=120, max_output_tokens=100, max_cost_microunits=0))
        s = Scheduler(ctx, AdapterRegistry(ctx.config, ctx.root, fixture_script=FixtureScript.from_raw(json.loads(script.read_text(encoding="utf-8")))), owner_id="coord")
        s.graphs.admit(TeamId.IMPLEMENTATION, [task])
        outcome = asyncio.run(s.run_task("strict-check"))
        assert outcome.state == TaskState.COMPLETED, outcome.reason
        env_doc = next(json.loads(svc.read_bytes(r).decode()) for r in outcome.artifacts if svc.get(r).kind == "environment")
        assert env_doc["conformance"] == "conforming" and env_doc["launcher"]["image"] == "python:3.12"
        record = next(json.loads(svc.read_bytes(r).decode()) for r in outcome.artifacts if svc.get(r).kind == "execution_record")
        assert record["status"] == "passed" and record["environment_variable_names"] == []
        assert any("docker" in a.lower() for a in record["argv"][:3]) and "run" in record["argv"][:4] and "--network" in record["argv"]  # launched through the engine (a cmd shim on Windows)
        assert "nonconforming_isolation" not in [f.code for f in outcome.result.findings]
    finally:
        ctx.close()


# ----------------------------------------------------------------------------- overview


def test_export_html_writes_a_self_contained_overview(tmp_path, capsys):
    home = str(tmp_path / "home")
    script = tmp_path / "script.json"
    script.write_text((EXAMPLE / "fixture-script.json").read_text(encoding="utf-8"), encoding="utf-8")
    assert run(capsys, "new", "demo", "--home", home, "--brief", str(EXAMPLE / "brief.md"), "--brain", "fixture")[0] == 0
    code, env = run(capsys, "run", "demo", "--home", home, "--until", "requirements", "--script", str(script))
    assert code == 0 and not env["data"]["stopped"]
    code, env = run(capsys, "export", "demo", "--home", home, "--to", str(tmp_path / "out"), "--html")
    assert code == 0 and env["data"]["overview"].endswith("index.html")
    page = (tmp_path / "out" / "index.html").read_text(encoding="utf-8")
    assert "<h2>Requirements</h2>" in page and "requirements_document" in page and "href='requirements/" in page and "<script" not in page.lower().replace("<style", "")
