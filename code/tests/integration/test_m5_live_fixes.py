"""Regressions found by the live Codex implementation runs (M5), kept as offline tests.

* pytest inside a workspace that lives under another project adopted that project's
  config (quiet mode) and the collector parsed zero cases;
* a reviewing brain task could not see the snapshot/check report its dependencies made;
* after ``task rerun``, dependents still read the *old* task's outputs.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from sdlc.adapters.execution.local_runner import LocalProcessRunner, parse_pytest_output
from sdlc.domain.identities import AgentPrincipal
from sdlc.domain.models import ArtifactRef, TeamId
from sdlc.domain.states import TaskState
from sdlc.domain.teams import Team
from sdlc.ports.execution import ExecutionRequest
from tests.integration.test_m5_implementation import _dev_mode, _task, world  # noqa: F401  (fixture re-export)

_REF = ArtifactRef(artifact_id="x", revision=1, sha256="0" * 64)


def _pytest_request(workdir: Path) -> ExecutionRequest:
    return ExecutionRequest(
        principal=AgentPrincipal("demo", "cycle-001", Team("implementation"), "x"), task_id="t", attempt_id="a", execution_id="e", recipe_id="pytest",
        argv=(sys.executable, "-m", "pytest", "-v", "-p", "no:cacheprovider"), working_directory=workdir, source_ref=_REF, environment_ref=_REF, suite_ref=None, timeout_seconds=120,
    )


def test_runner_keeps_pytest_inside_the_workspace(tmp_path):
    host = tmp_path / "host"
    (host / "ws" / "tests").mkdir(parents=True)
    (host / "pyproject.toml").write_text('[tool.pytest.ini_options]\naddopts = "-q"\n', encoding="utf-8")  # would silence -v
    (host / "ws" / "tests" / "test_a.py").write_text(
        "import pytest\n\n\ndef test_one():\n    assert True\n\n\n@pytest.mark.parametrize('p', ['a=b, c', 'x/y'])\ndef test_two(p):\n    assert p\n",
        encoding="utf-8",
    )
    logs: dict[str, bytes] = {}
    record = asyncio.run(LocalProcessRunner(artifacts_writer=lambda n, b: logs.__setitem__(n, b) or _REF).execute(_pytest_request(host / "ws")))
    assert record.status.value == "passed" and record.exit_code == 0
    assert len(record.case_observations) == 3, logs.get("e-stdout.txt", b"").decode()
    assert "--rootdir" in record.argv and "-c" in record.argv
    assert record.stdout_ref is not None and b"PASSED" in logs["e-stdout.txt"]


def test_pytest_parser_accepts_any_parameter_text():
    out = "tests/test_a.py::test_two[a=b, c] PASSED  [ 50%]\ntests\\test_b.py::T::test_x[1-2] FAILED\n"
    cases = parse_pytest_output(out)
    assert [c.status.value for c in cases] == ["passed", "failed"]


def test_review_task_receives_dependency_outputs(world):
    _dev_mode(world)
    from sdlc.adapters.brains.fixture import FixtureScript
    from sdlc.adapters.registry import AdapterRegistry
    from sdlc.runtime.scheduler import Scheduler

    script = json.loads(json.dumps(world["script"]))
    script["steps"]["task:impl-review"] = {"proposal": {
        "summary": "reviewed the candidate against its patch and check report",
        "artifact_candidates": [{"kind": "code_review", "media_type": "text/markdown", "relative_path": "reviews/code_review.md", "workspace_path": None,
                                 "content_text": "# Code review\n\n## Verdict\n\napprove\n\n## Findings\n\nnone\n"}],
        "findings": [], "questions": [], "suggested_next_operations": []}}
    ctx = world["ctx"]
    s = Scheduler(ctx, AdapterRegistry(ctx.config, ctx.root, fixture_script=FixtureScript.from_raw(script)), owner_id="coord3")
    s.graphs.admit(TeamId.IMPLEMENTATION, [_task(world, "impl-code", "implementation.code")])
    [coded] = asyncio.run(s.run_ready(TeamId.IMPLEMENTATION))
    assert coded.state == TaskState.COMPLETED
    s.graphs.admit(TeamId.IMPLEMENTATION, [_task(world, "impl-review", "implementation.review_changes", deps=["impl-code"])])
    [reviewed] = asyncio.run(s.run_ready(TeamId.IMPLEMENTATION))
    assert reviewed.state == TaskState.COMPLETED, reviewed.reason
    bound = [json.loads(r["payload_json"]) for r in world["ctx"].db.all(
        "SELECT payload_json FROM events WHERE project_id=? AND task_id='impl-review' AND event_type='task.inputs_bound'", (world["ctx"].project_id,))]
    assert bound and {r["artifact_id"] for r in bound[0]["dependency_outputs"]} >= {"impl-code-candidate-snapshot", "impl-code-candidate-patch"}


def test_dependency_outputs_follow_a_rerun_successor(world):
    _dev_mode(world)
    s = world["scheduler"]
    s.graphs.admit(TeamId.IMPLEMENTATION, [_task(world, "impl-code", "implementation.code")])
    [coded] = asyncio.run(s.run_ready(TeamId.IMPLEMENTATION))
    snapshot_ref = next(r for r in coded.artifacts if s.artifacts.get(r).kind == "source_snapshot")
    s.graphs.admit(TeamId.IMPLEMENTATION, [_task(world, "impl-check", "implementation.check", refs=[world["design"].ref, snapshot_ref])])
    [checked] = asyncio.run(s.run_ready(TeamId.IMPLEMENTATION))
    assert checked.state == TaskState.COMPLETED
    s.graphs.admit(TeamId.IMPLEMENTATION, [_task(world, "impl-review", "implementation.review_changes", deps=["impl-check"])])
    before = {r.artifact_id for r in s.graphs.dependency_outputs("impl-review")}
    successor = s.rerun("impl-check")
    again = asyncio.run(s.run_task(successor))
    assert again.task_id == successor and again.state == TaskState.COMPLETED
    after = {r.artifact_id for r in s.graphs.dependency_outputs("impl-review")}
    assert before and after and before != after and all(checked.attempt_id not in a for a in after)


def test_show_works_after_a_recorded_probe(tmp_path, capsys):
    """`sdlc show` listed connections with `record.__dict__`; a slotted probe record has none."""
    from sdlc.application.connections import ConnectionService
    from sdlc.application.projects import open_project
    from sdlc.ports.common import ProbeResult
    from tests.integration.test_m5_implementation import EXAMPLE, run

    home = str(tmp_path / "home")
    assert run(capsys, "new", "demo", "--home", home, "--brief", str(EXAMPLE / "brief.md"), "--brain", "fixture")[0] == 0
    ctx = open_project(Path(home), "demo")
    try:
        ConnectionService(ctx).record_probe("fixture", ProbeResult(ok=True, live=True, messages=("probed",), identity="verified"))
    finally:
        ctx.close()
    code, env = run(capsys, "show", "demo", "--home", home)
    assert code == 0 and "stages" in env["data"]
