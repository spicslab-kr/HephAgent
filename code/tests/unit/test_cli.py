from __future__ import annotations

import json

import pytest

from sdlc.cli.main import main
from tests.conftest import EXAMPLES


def run(capsys, *argv) -> tuple[int, dict]:
    code = main(["--json", *argv])
    out = capsys.readouterr().out
    return code, json.loads(out)


def test_help_exits_zero(capsys):
    with pytest.raises(SystemExit) as info:
        main(["--help"])
    assert info.value.code == 0


def test_init_doctor_status(tmp_path, capsys):
    code, env = run(capsys, "--home", str(tmp_path), "init", "demo")
    assert code == 0 and env["success"] and env["data"]["fixture_mode"] is True
    assert (tmp_path / "demo" / "project.yaml").exists()

    code, env = run(capsys, "--home", str(tmp_path), "init", "demo")
    assert code == 1 and env["errors"][0]["code"] == "conflict"

    code, env = run(capsys, "--home", str(tmp_path), "doctor", "--project", "demo")
    assert code == 0 and env["data"]["connections"]["fixture"]["readiness"] == "ready"
    assert env["data"]["connections"]["codex-native"]["readiness"] == "unconfigured"

    code, env = run(capsys, "--home", str(tmp_path), "doctor", "--project", "demo", "--live")
    assert code == 0 and "live_probe" not in env["data"]["connections"]["codex-native"]  # placeholders: nothing probed
    assert env["data"]["connections"]["codex-native"]["dispatchable"] is False

    code, env = run(capsys, "--home", str(tmp_path), "status", "--project", "demo")
    assert code == 0 and set(env["data"]["teams"]) == {
        "planning", "requirements", "design", "implementation", "testing", "maintenance",
    }


def test_validate_examples(capsys):
    code, env = run(capsys, "validate", str(EXAMPLES / "minimal-cycle.json"))
    assert code == 0 and env["data"]["semantics"]["bundles"] == 7
    code, env = run(capsys, "validate", str(EXAMPLES / "quota-task-result.json"), "--kind", "TaskResult")
    assert code == 0


def test_validate_wrong_kind_fails(capsys):
    code, env = run(capsys, "validate", str(EXAMPLES / "quota-task-result.json"), "--kind", "Delivery")
    assert code == 1 and env["errors"][0]["code"] == "invalid_schema"


def test_operations_listing(capsys):
    code, env = run(capsys, "operations", "--team", "testing")
    assert code == 0 and env["data"]["count"] == 13
    assert all(op["team"] == "testing" for op in env["data"]["operations"])
