"""Codex CLI as a read-only structured Brain bridge (handoff docs/05 s4).

Invocation (argument array, prompt on stdin via ``-``)::

    codex exec --json --skip-git-repo-check --sandbox read-only
               --output-schema SCHEMA_FILE -o LAST_MESSAGE_FILE [-m MODEL] -

Checked against codex-cli 0.162.0: stdout is JSONL with ``thread.started``,
``item.completed`` (``agent_message``), ``turn.completed`` (usage) and, on failure,
``turn.failed``/``error`` items. The final message is also written to the ``-o`` file.
``codex login status`` reports the mode ("Logged in using ChatGPT") but no account id,
so identity is ``identity_unverified`` unless the participant accepts that explicitly.

Coding (``workspace-write``) jobs are a separate CodingBackend implementation that
stays capability-blocked until the isolation launcher spike passes.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

from sdlc.adapters.cli_common import (
    CliInvocationLog,
    ProcessResult,
    Runner,
    child_environment,
    classify,
    default_runner,
    find_executable,
    measured_usage,
    normalized_error,
    unknown_usage,
)
from sdlc.adapters.brains.claude_cli import _extract_json, _render_prompt
from sdlc.domain.models import CompletionCertainty, ErrorCategory
from sdlc.ports.brain import BrainReply, BrainRequest
from sdlc.ports.common import AdapterDescription, CapabilityStatus, ProbeResult, Support

ADAPTER_ID = "codex-cli-brain"
TESTED_VERSIONS = ("0.16",)


class CodexCliBrain:
    def __init__(
        self,
        *,
        executable: str = "codex",
        model: str | None = None,
        expected_account_id: str | None = None,
        runner: Runner = default_runner,
        scratch_root: Path | None = None,
    ) -> None:
        self.executable_name = executable
        self.model = model
        self.expected_account_id = expected_account_id
        self._run = runner
        self._scratch_root = scratch_root
        self._version: str | None = None
        self.invocations = 0
        self.last_log: CliInvocationLog | None = None

    def describe(self) -> AdapterDescription:
        return AdapterDescription(
            adapter_id=ADAPTER_ID,
            adapter_version=self._version or "unprobed",
            connection_type="codex-cli",
            auth_mode="native",
            cost_source="native_subscription",
            capabilities=(
                CapabilityStatus("structured_proposal", Support.SUPPORTED, "--output-schema + --json events"),
                CapabilityStatus("diagram_source_generation", Support.SUPPORTED, "text output"),
                CapabilityStatus("usage_events", Support.SUPPORTED, "turn.completed usage (tokens only; cost unknown)"),
                CapabilityStatus("native_resume", Support.SUPPORTED, "codex exec resume THREAD_ID (same task only)"),
                CapabilityStatus("scoped_repository", Support.UNKNOWN, "blocked until the isolation launcher spike passes"),
                CapabilityStatus("isolated_execution", Support.UNSUPPORTED),
                CapabilityStatus("research_or_imported_evidence", Support.UNSUPPORTED),
            ),
            limits={"sandbox": "read-only"},
        )

    async def probe(self, live: bool = False) -> ProbeResult:
        exe = find_executable(self.executable_name)
        if exe is None:
            return ProbeResult(ok=False, live=False, messages=(f"{self.executable_name} not found on PATH; run scripts/install-backends",))
        result = self._run([exe, "--version"], None, None, child_environment(native=True), 30)
        text = (result.stdout or result.stderr).strip()
        version = text.split()[-1] if text else None
        self._version = version
        messages = [f"codex {version}"]
        if version and not version.startswith(TESTED_VERSIONS):
            messages.append(f"version {version} was not tested; flags were verified on {TESTED_VERSIONS}")
        if not live:
            return ProbeResult(ok=result.exit_code == 0, live=False, messages=tuple(messages), version=version)
        status = self._run([exe, "login", "status"], None, None, child_environment(native=True), 30)
        line = (status.stdout + status.stderr).strip()
        if status.exit_code != 0 or "not logged in" in line.lower():
            return ProbeResult(ok=False, live=True, messages=(*messages, f"not logged in: run `codex login` ({line[:120]})"), identity="identity_unverified", version=version)
        identity = "identity_unverified"
        if self.expected_account_id:
            # Codex does not expose an account id; a declared expectation cannot be verified here.
            messages.append("expected_account_id cannot be verified through codex login status; proceeding requires an explicit policy")
        messages.append(line[:160])
        return ProbeResult(ok=True, live=True, messages=tuple(messages), identity=identity, version=version)

    async def generate(self, request: BrainRequest) -> BrainReply:
        exe = find_executable(self.executable_name)
        if exe is None:
            return BrainReply(None, None, normalized_error(ErrorCategory.CAPABILITY_MISSING, f"{self.executable_name} not installed", provider_code=None), unknown_usage())
        self.invocations += 1
        from sdlc.verification.schemas import transport_projection

        schema = request.output_schema
        if "$ref" in schema and schema["$ref"].startswith("#/$defs/"):
            # OpenAI's strict subset rejects regex look-around and value constraints;
            # the runtime validates the reply against the full contract afterwards.
            schema = transport_projection(schema["$ref"][len("#/$defs/") :])
        cwd = Path(tempfile.mkdtemp(prefix=f"sdlc-{request.task_id}-", dir=str(self._scratch_root) if self._scratch_root else None))
        schema_file = cwd / "output-schema.json"
        schema_file.write_text(json.dumps(schema), encoding="utf-8")
        last_file = cwd / "last-message.json"
        argv = [exe, "exec", "--json", "--skip-git-repo-check", "--sandbox", "read-only", "--output-schema", str(schema_file), "-o", str(last_file)]
        model = request.parameters.get("_model") or self.model
        if model:
            argv += ["-m", str(model)]
        argv.append("-")
        try:
            result = self._run(argv, _render_prompt(request), cwd, child_environment(native=True), request.timeout_seconds)
            self.last_log = CliInvocationLog(tuple(argv), str(cwd), result.exit_code, result.duration_seconds, result.stderr)
            return self._parse(result, last_file)
        finally:
            shutil.rmtree(cwd, ignore_errors=True)  # nothing of value lives there; the runtime keeps the reply

    def _parse(self, result: ProcessResult, last_file: Path) -> BrainReply:
        provider: dict = {"adapter": ADAPTER_ID, "origin": "native", "exit_code": result.exit_code, "log": self.last_log.to_dict() if self.last_log else None}
        if result.timed_out:
            return BrainReply(None, result.stdout or None, normalized_error(ErrorCategory.UNKNOWN, "codex timed out", provider_code="timeout", certainty=CompletionCertainty.UNKNOWN), unknown_usage(), provider)
        events = []
        for line in result.stdout.splitlines():
            if line.strip().startswith("{"):
                try:
                    events.append(json.loads(line))
                except ValueError:
                    continue
        usage = unknown_usage()
        last_text: str | None = None
        error_text: str | None = None
        for event in events:
            kind = event.get("type")
            if kind == "thread.started":
                provider["session_id"] = event.get("thread_id")
            elif kind == "turn.completed":
                u = event.get("usage") or {}
                usage = measured_usage(u.get("input_tokens"), u.get("output_tokens"), None)
            elif kind == "item.completed" and (event.get("item") or {}).get("type") == "agent_message":
                last_text = event["item"].get("text")
            elif kind in ("turn.failed", "error"):
                error_text = json.dumps(event.get("error") or event, ensure_ascii=False)
        if last_file.exists() and last_text is None:
            last_text = last_file.read_text(encoding="utf-8")
        if error_text or (result.exit_code not in (0, None) and last_text is None):
            message = error_text or result.stderr or f"codex exited with {result.exit_code}"
            return BrainReply(None, last_text, normalized_error(classify(message), message, provider_code=str(result.exit_code)), usage, provider)
        structured = _extract_json(last_text or "")
        if structured is None:
            return BrainReply(None, last_text, normalized_error(ErrorCategory.INVALID_OUTPUT, "final message is not a JSON object", provider_code="no_json"), usage, provider)
        return BrainReply(structured, last_text, None, usage, provider)
