"""M4 adapters, offline: envelope parsing, error normalisation, environment hygiene,
connection service, probe-gated dispatch (AT-012/AT-014/AT-015 slices)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from sdlc.adapters.brains.api import AnthropicMessagesBrain, OpenAICompatibleBrain, OpenAIResponsesBrain
from sdlc.adapters.brains.claude_cli import ClaudeCliBrain
from sdlc.adapters.brains.codex_cli import CodexCliBrain
from sdlc.adapters.cli_common import ProcessResult, child_environment, classify, sanitize
from sdlc.adapters.registry import AdapterRegistry
from sdlc.application.connections import ConnectionService
from sdlc.application.fixtures import load_fixture
from sdlc.application.projects import create_project
from sdlc.config import ConnectionType
from sdlc.domain.errors import Conflict, InvalidSchema
from sdlc.domain.identities import AgentPrincipal
from sdlc.domain.models import ErrorCategory, Task, TaskLimits, TeamId
from sdlc.domain.states import TaskState
from sdlc.domain.teams import Team
from sdlc.ports.brain import BrainRequest
from sdlc.ports.common import ProbeResult
from sdlc.runtime.scheduler import Scheduler
from sdlc.verification.schemas import schema_subset, transport_projection, validate_record

ROOT = Path(__file__).resolve().parents[2]


def _request() -> BrainRequest:
    return BrainRequest(AgentPrincipal("p", "c", Team.PLANNING, "a"), "t1", "a1", "planning.intake", 1, "sys", "do it", (), {"$ref": "#/$defs/WorkerProposal"}, "WorkerProposal", 500, 30)


class FakeRunner:
    """Scripted subprocess results, recorded argv/env/stdin for assertions."""

    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def __call__(self, argv, stdin, cwd, env, timeout):
        call = {"argv": argv, "stdin": stdin, "cwd": cwd, "env": env, "timeout": timeout}
        if "--output-schema" in argv:  # the adapter removes its scratch directory after the call
            call["schema"] = Path(argv[argv.index("--output-schema") + 1]).read_text(encoding="utf-8")
        self.calls.append(call)
        result = self.results.pop(0)
        return ProcessResult(tuple(argv), result.get("exit", 0), result.get("stdout", ""), result.get("stderr", ""), result.get("timed_out", False), 0.1)


PROPOSAL = {"schema_version": "1.0", "task_id": "t1", "summary": "ok", "artifact_candidates": [], "findings": [], "questions": [], "suggested_next_operations": []}


# ----------------------------------------------------------------------------- schema projection


def test_schema_subset_and_transport_projection():
    subset = schema_subset("WorkerProposal")
    assert set(subset["$defs"]) == {"ArtifactCandidate", "ArtifactRef", "Finding"}
    projected = transport_projection("WorkerProposal")
    assert "pattern" not in json.dumps(projected) and "minLength" not in json.dumps(projected)
    assert projected["required"] == subset["required"] and projected["additionalProperties"] is False


# ----------------------------------------------------------------------------- environment / classification


def test_native_environment_strips_api_keys(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-secret")
    monkeypatch.setenv("HARMLESS", "1")
    env = child_environment(native=True)
    assert "OPENAI_API_KEY" not in env and "ANTHROPIC_API_KEY" not in env and env["HARMLESS"] == "1"
    assert "OPENAI_API_KEY" in child_environment(native=False)


@pytest.mark.parametrize(
    "text,category",
    [
        ("You have hit your usage limit", ErrorCategory.QUOTA_EXHAUSTED),
        ("429 rate limit exceeded", ErrorCategory.RATE_LIMITED),
        ("Not logged in. Please run claude auth login", ErrorCategory.AUTH_REQUIRED),
        ("prompt is too long for the context window", ErrorCategory.CONTEXT_LIMIT),
        ("ECONNREFUSED 127.0.0.1:11434", ErrorCategory.PROVIDER_UNAVAILABLE),
        ("something odd happened", ErrorCategory.UNKNOWN),
    ],
)
def test_classify(text, category):
    assert classify(text) == category


def test_sanitize_redacts_keys():
    assert "sk-***" in sanitize("key sk-proj-abcdefghijklmnop1234 leaked") and "abcdefghijklmnop" not in sanitize("key sk-proj-abcdefghijklmnop1234 leaked")


# ----------------------------------------------------------------------------- claude cli


def _claude_envelope(**overrides):
    base = {"is_error": False, "session_id": "sess-1", "structured_output": PROPOSAL, "usage": {"input_tokens": 10, "output_tokens": 20}, "total_cost_usd": 0.0123, "modelUsage": {}}
    base.update(overrides)
    return json.dumps(base)


def test_claude_success_parses_structured_output_and_measured_usage(monkeypatch):
    monkeypatch.setattr("sdlc.adapters.brains.claude_cli.find_executable", lambda name: "claude")
    runner = FakeRunner([{"stdout": _claude_envelope()}])
    brain = ClaudeCliBrain(model="sonnet", runner=runner)
    reply = asyncio.run(brain.generate(_request()))
    assert reply.ok and validate_record(reply.structured, "WorkerProposal")
    assert reply.usage.measurement.value == "measured" and reply.usage.cost_microunits == 12300
    assert reply.provider["session_id"] == "sess-1"
    argv = runner.calls[0]["argv"]
    assert argv[:2] == ["claude", "-p"] and "--tools" in argv and argv[argv.index("--tools") + 1] == "" and "--model" in argv
    assert "OPENAI_API_KEY" not in runner.calls[0]["env"]
    assert "DOCUMENT" not in runner.calls[0]["stdin"] and "Task t1" in runner.calls[0]["stdin"]


def test_claude_quota_and_auth_errors(monkeypatch):
    monkeypatch.setattr("sdlc.adapters.brains.claude_cli.find_executable", lambda name: "claude")
    quota = FakeRunner([{"exit": 1, "stdout": _claude_envelope(is_error=True, result="You have reached your usage limit", api_error_status=429, structured_output=None)}])
    reply = asyncio.run(ClaudeCliBrain(runner=quota).generate(_request()))
    assert reply.error.category == ErrorCategory.QUOTA_EXHAUSTED and reply.error.reset_at is None
    auth = FakeRunner([{"exit": 1, "stdout": "", "stderr": "Not logged in. Run claude auth login"}])
    reply = asyncio.run(ClaudeCliBrain(runner=auth).generate(_request()))
    assert reply.error.category == ErrorCategory.AUTH_REQUIRED
    timeout = FakeRunner([{"exit": None, "timed_out": True}])
    reply = asyncio.run(ClaudeCliBrain(runner=timeout).generate(_request()))
    assert reply.error.category == ErrorCategory.UNKNOWN and reply.error.completion_certainty.value == "unknown"
    malformed = FakeRunner([{"stdout": _claude_envelope(structured_output=None, result="here is prose, no json")}])
    reply = asyncio.run(ClaudeCliBrain(runner=malformed).generate(_request()))
    assert reply.error.category == ErrorCategory.INVALID_OUTPUT


def test_claude_probe_identity(monkeypatch):
    monkeypatch.setattr("sdlc.adapters.brains.claude_cli.find_executable", lambda name: "claude")
    status = json.dumps({"loggedIn": True, "email": "me@example.org", "authMethod": "claude.ai", "subscriptionType": "team"})
    ok = asyncio.run(ClaudeCliBrain(runner=FakeRunner([{"stdout": "2.1.295 (Claude Code)"}, {"stdout": status}])).probe(live=True))
    assert ok.ok and ok.identity == "observed" and ok.version == "2.1.295"
    mismatch = asyncio.run(ClaudeCliBrain(expected_account_id="other@example.org", runner=FakeRunner([{"stdout": "2.1.295"}, {"stdout": status}])).probe(live=True))
    assert not mismatch.ok and mismatch.identity == "mismatch"
    logged_out = asyncio.run(ClaudeCliBrain(runner=FakeRunner([{"stdout": "2.1.295"}, {"stdout": json.dumps({"loggedIn": False})}])).probe(live=True))
    assert not logged_out.ok and "claude auth login" in logged_out.messages[-1]
    monkeypatch.setattr("sdlc.adapters.brains.claude_cli.find_executable", lambda name: None)
    assert not asyncio.run(ClaudeCliBrain(runner=FakeRunner([])).probe()).ok


# ----------------------------------------------------------------------------- codex cli


def _codex_events(final: str | None, failed: bool = False) -> str:
    lines = [json.dumps({"type": "thread.started", "thread_id": "thr-1"}), json.dumps({"type": "turn.started"})]
    if final is not None:
        lines.append(json.dumps({"type": "item.completed", "item": {"id": "i", "type": "agent_message", "text": final}}))
    if failed:
        lines.append(json.dumps({"type": "turn.failed", "error": {"message": "Rate limit reached for gpt-5"}}))
    else:
        lines.append(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 100, "output_tokens": 5}}))
    return "\n".join(lines)


def test_codex_success_and_failure(monkeypatch, tmp_path):
    monkeypatch.setattr("sdlc.adapters.brains.codex_cli.find_executable", lambda name: "codex")
    runner = FakeRunner([{"stdout": _codex_events(json.dumps(PROPOSAL))}])
    reply = asyncio.run(CodexCliBrain(scratch_root=tmp_path, runner=runner).generate(_request()))
    assert reply.ok and reply.provider["session_id"] == "thr-1" and reply.usage.input_tokens == 100 and reply.usage.cost_microunits is None
    argv = runner.calls[0]["argv"]
    assert argv[1:6] == ["exec", "--json", "--skip-git-repo-check", "--sandbox", "read-only"] and argv[-1] == "-"
    assert "pattern" not in runner.calls[0]["schema"]  # transport projection (file is removed after the call)
    failed = FakeRunner([{"exit": 1, "stdout": _codex_events(None, failed=True)}])
    reply = asyncio.run(CodexCliBrain(scratch_root=tmp_path, runner=failed).generate(_request()))
    assert reply.error.category == ErrorCategory.RATE_LIMITED
    prose = FakeRunner([{"stdout": _codex_events("I cannot produce that")}])
    reply = asyncio.run(CodexCliBrain(scratch_root=tmp_path, runner=prose).generate(_request()))
    assert reply.error.category == ErrorCategory.INVALID_OUTPUT


def test_codex_probe_identity_is_unverified(monkeypatch):
    monkeypatch.setattr("sdlc.adapters.brains.codex_cli.find_executable", lambda name: "codex")
    probe = asyncio.run(CodexCliBrain(runner=FakeRunner([{"stdout": "codex-cli 0.162.0"}, {"stdout": "Logged in using ChatGPT"}])).probe(live=True))
    assert probe.ok and probe.identity == "identity_unverified" and probe.version == "0.162.0"
    out = asyncio.run(CodexCliBrain(runner=FakeRunner([{"stdout": "codex-cli 0.162.0"}, {"exit": 1, "stderr": "Not logged in"}])).probe(live=True))
    assert not out.ok


# ----------------------------------------------------------------------------- api brains with fake clients


def test_openai_responses_success_refusal_incomplete_and_429(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    captured = {}

    def client_for(response):
        def create(**kwargs):
            captured.update(kwargs)
            if isinstance(response, Exception):
                raise response
            return response

        return SimpleNamespace(responses=SimpleNamespace(create=create))

    ok = SimpleNamespace(id="r1", status="completed", output_text=json.dumps(PROPOSAL), output=[], usage=SimpleNamespace(input_tokens=5, output_tokens=7))
    brain = OpenAIResponsesBrain(model="gpt-test", credential_ref="env:OPENAI_API_KEY", client_factory=lambda: client_for(ok))
    reply = asyncio.run(brain.generate(_request()))
    assert reply.ok and reply.usage.input_tokens == 5
    assert captured["text"]["format"]["type"] == "json_schema" and captured["text"]["format"]["strict"] is True and "pattern" not in json.dumps(captured["text"]["format"]["schema"])
    refusal = SimpleNamespace(id="r2", status="completed", output_text="", output=[SimpleNamespace(content=[SimpleNamespace(type="refusal", refusal="no")])], usage=None)
    reply = asyncio.run(OpenAIResponsesBrain(model="m", credential_ref=None, client_factory=lambda: client_for(refusal)).generate(_request()))
    assert reply.refusal == "no" and reply.error.category == ErrorCategory.INVALID_OUTPUT
    incomplete = SimpleNamespace(id="r3", status="incomplete", incomplete_details=SimpleNamespace(reason="max_output_tokens"), output_text="{", output=[], usage=None)
    reply = asyncio.run(OpenAIResponsesBrain(model="m", credential_ref=None, client_factory=lambda: client_for(incomplete)).generate(_request()))
    assert reply.error.category == ErrorCategory.CONTEXT_LIMIT

    class RateLimit(Exception):
        status_code = 429
        response = SimpleNamespace(headers={"retry-after": "7"})

    reply = asyncio.run(OpenAIResponsesBrain(model="m", credential_ref=None, client_factory=lambda: client_for(RateLimit("rate limit"))).generate(_request()))
    assert reply.error.category == ErrorCategory.RATE_LIMITED and reply.error.retry_after_seconds == 7


def test_anthropic_messages_success_and_truncation():
    def client_for(message):
        return SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: message))

    ok = SimpleNamespace(id="m1", stop_reason="end_turn", content=[SimpleNamespace(type="text", text=json.dumps(PROPOSAL))], usage=SimpleNamespace(input_tokens=3, output_tokens=4))
    reply = asyncio.run(AnthropicMessagesBrain(model="claude-x", credential_ref=None, client_factory=lambda: client_for(ok)).generate(_request()))
    assert reply.ok and reply.usage.output_tokens == 4
    truncated = SimpleNamespace(id="m2", stop_reason="max_tokens", content=[SimpleNamespace(type="text", text="{")], usage=None)
    reply = asyncio.run(AnthropicMessagesBrain(model="claude-x", credential_ref=None, client_factory=lambda: client_for(truncated)).generate(_request()))
    assert reply.error.category == ErrorCategory.CONTEXT_LIMIT


def test_openai_compatible_local_server():
    def client_for(completion):
        return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kw: completion)))

    ok = SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content=json.dumps(PROPOSAL)))], usage=SimpleNamespace(prompt_tokens=1, completion_tokens=2))
    brain = OpenAICompatibleBrain(model="llama", credential_ref=None, base_url="http://127.0.0.1:11434/v1", client_factory=lambda: client_for(ok))
    assert brain.describe().cost_source == "local"
    reply = asyncio.run(brain.generate(_request()))
    assert reply.ok and reply.provider["base_url"] == "http://127.0.0.1:11434/v1"
    length = SimpleNamespace(choices=[SimpleNamespace(finish_reason="length", message=SimpleNamespace(content="{"))], usage=None)
    reply = asyncio.run(OpenAICompatibleBrain(model="llama", credential_ref=None, base_url="x", client_factory=lambda: client_for(length)).generate(_request()))
    assert reply.error.category == ErrorCategory.CONTEXT_LIMIT


def test_api_probe_reports_missing_credential(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    probe = asyncio.run(OpenAIResponsesBrain(model="m", credential_ref="env:OPENAI_API_KEY").probe())
    # Either the SDK is missing or the variable is: both are explicit, neither is a crash.
    assert not probe.ok and probe.live is False


# ----------------------------------------------------------------------------- connections + probe-gated dispatch


def test_connection_add_bind_and_dispatch_gate(tmp_path, cycle_fixture, monkeypatch):
    monkeypatch.setattr("sdlc.adapters.brains.claude_cli.find_executable", lambda name: "claude")
    context = create_project(tmp_path, "demo", ROOT / "config" / "project.example.yaml")
    try:
        service = ConnectionService(context)
        conn = service.add("claude-native", type=ConnectionType.CLAUDE_CLI, model="sonnet", quota_scope_id="claude-subscription", replace=True)
        assert conn.readiness.value == "capability_check_required" and conn.placeholders == []
        with pytest.raises(Conflict):
            service.add("claude-native", type=ConnectionType.CLAUDE_CLI)
        with pytest.raises(InvalidSchema):
            service.add("bad", type=ConnectionType.OPENAI_RESPONSES, credential_ref="sk-not-a-ref")
        assert service.dispatchable("claude-native") == (False, "no live probe recorded; run `sdlc doctor --project NAME --live`")
        config = service.bind(role="brain", connection="claude-native")
        assert config.project.fixture_mode is False and all(b.brain == "claude-native" for b in config.teams.values())
        # Scheduler refuses to dispatch until a successful live probe is recorded (zero calls).
        load_fixture(context, cycle_fixture)
        adapters = AdapterRegistry(context.config, context.root)
        fake = ClaudeCliBrain(runner=FakeRunner([{"stdout": _claude_envelope(structured_output={**PROPOSAL, "task_id": "t-live"})}]))
        adapters.register_brain("claude-native", fake)
        scheduler = Scheduler(context, adapters, owner_id="c")
        req = next(a for a in cycle_fixture.artifacts if a.artifact_id == "a-requirements")
        task = Task(schema_version="1.0", project_id="demo", cycle_id="cycle-001", task_id="t-live", team_id=TeamId.DESIGN, agent_instance_id="a", operation="design.intake", operation_version=1,
                    input_deliveries=["d-02"], input_refs=[req.ref], depends_on=[], parameters={"objective": "x", "scope_ids": [], "requested_artifact_kinds": [], "diagram_request": None},
                    expected_output_schema="#/$defs/WorkerProposal", backend_profile="claude-native", workspace_ref=None, limits=TaskLimits(max_attempts=1, timeout_seconds=5, max_output_tokens=10, max_cost_microunits=None))
        scheduler.graphs.admit(TeamId.DESIGN, [task])
        [outcome] = asyncio.run(scheduler.run_ready())
        assert outcome.state == TaskState.NEEDS_INPUT and "no live probe" in outcome.reason and fake.invocations == 0
        service.record_probe("claude-native", ProbeResult(ok=True, live=True, messages=("ok",), identity="observed", version="2.1.295"))
        assert service.dispatchable("claude-native")[0] is True
        scheduler.resume("t-live")
        [outcome] = asyncio.run(scheduler.run_ready())
        assert outcome.state == TaskState.COMPLETED and fake.invocations == 1
        # Repository work stays blocked: the adapter declares scoped_repository unknown.
        task2 = task.model_copy(update={"task_id": "t-repo", "operation": "design.inspect_system"})
        with pytest.raises(InvalidSchema):  # not enabled at all yet (M5)
            scheduler.graphs.admit(TeamId.DESIGN, [task2])
    finally:
        context.close()
