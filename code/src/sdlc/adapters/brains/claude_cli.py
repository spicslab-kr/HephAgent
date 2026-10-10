"""Claude Code CLI as a Brain (handoff docs/05 s5), native subscription login.

Invocation (argument array, prompt on stdin, tools disabled, throwaway cwd)::

    claude -p --output-format json --json-schema SCHEMA --tools "" --model MODEL
           --append-system-prompt SYSTEM [--resume SESSION_ID]

Checked against Claude Code 2.1.295: the JSON envelope carries ``structured_output``,
``session_id``, ``is_error``, ``usage`` and ``total_cost_usd``. The adapter verifies the
installed version before trusting flags. Login stays with the official client: no
OAuth of our own, no token files are read.

Capabilities: structured proposals and diagram sources yes; repository/coding work is
declared ``unknown`` until the isolation-launcher spike passes, so the scheduler keeps
``scoped_repository`` operations blocked.
"""

from __future__ import annotations

import json
import shutil
import re
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
    sanitize,
    unknown_usage,
)
from sdlc.domain.models import CompletionCertainty, ErrorCategory
from sdlc.ports.brain import BrainReply, BrainRequest
from sdlc.ports.common import AdapterDescription, CapabilityStatus, ProbeResult, Support

ADAPTER_ID = "claude-cli-brain"
TESTED_VERSIONS = ("2.1.",)


class ClaudeCliBrain:
    def __init__(
        self,
        *,
        executable: str = "claude",
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

    # ------------------------------------------------------------------ describe / probe
    def describe(self) -> AdapterDescription:
        return AdapterDescription(
            adapter_id=ADAPTER_ID,
            adapter_version=self._version or "unprobed",
            connection_type="claude-cli",
            auth_mode="native",
            cost_source="native_subscription",
            capabilities=(
                CapabilityStatus("structured_proposal", Support.SUPPORTED, "--output-format json --json-schema"),
                CapabilityStatus("diagram_source_generation", Support.SUPPORTED, "text output"),
                CapabilityStatus("usage_events", Support.SUPPORTED, "usage and total_cost_usd in the envelope"),
                CapabilityStatus("native_resume", Support.SUPPORTED, "--resume SESSION_ID (same task only)"),
                CapabilityStatus("scoped_repository", Support.UNKNOWN, "blocked until the isolation launcher spike passes"),
                CapabilityStatus("isolated_execution", Support.UNSUPPORTED),
                CapabilityStatus("research_or_imported_evidence", Support.UNSUPPORTED, "web tools are disabled for team work"),
            ),
            limits={"tools": "disabled", "sandbox": "cwd only"},
        )

    async def probe(self, live: bool = False) -> ProbeResult:
        exe = find_executable(self.executable_name)
        if exe is None:
            return ProbeResult(ok=False, live=False, messages=(f"{self.executable_name} not found on PATH; run scripts/install-backends",))
        result = self._run([exe, "--version"], None, None, child_environment(native=True), 30)
        version = (result.stdout or result.stderr).strip().split()[0] if (result.stdout or result.stderr).strip() else None
        self._version = version
        messages = [f"claude {version}"]
        if version and not version.startswith(TESTED_VERSIONS):
            messages.append(f"version {version} was not tested; flags were verified on {TESTED_VERSIONS}")
        if not live:
            return ProbeResult(ok=result.exit_code == 0, live=False, messages=tuple(messages), version=version)
        status = self._run([exe, "auth", "status"], None, None, child_environment(native=True), 30)
        try:
            info = json.loads(status.stdout)
        except ValueError:
            return ProbeResult(ok=False, live=True, messages=(*messages, "auth status is not JSON; log in with: claude auth login"), version=version)
        if not info.get("loggedIn"):
            return ProbeResult(ok=False, live=True, messages=(*messages, "not logged in: run `claude auth login`"), identity="identity_unverified", version=version)
        email = info.get("email")
        identity = "identity_unverified"
        if self.expected_account_id:
            identity = "verified" if email == self.expected_account_id else "mismatch"
            if identity == "mismatch":
                return ProbeResult(ok=False, live=True, messages=(*messages, f"logged-in account differs from expected_account_id ({email!r})"), identity=identity, version=version)
        elif email:
            identity = "observed"
        messages.append(f"logged in as {email} via {info.get('authMethod')} ({info.get('subscriptionType', 'unknown plan')})")
        return ProbeResult(ok=True, live=True, messages=tuple(messages), identity=identity, version=version)

    # ------------------------------------------------------------------ generate
    async def generate(self, request: BrainRequest) -> BrainReply:
        exe = find_executable(self.executable_name)
        if exe is None:
            return BrainReply(None, None, normalized_error(ErrorCategory.CAPABILITY_MISSING, f"{self.executable_name} not installed", provider_code=None), unknown_usage())
        self.invocations += 1
        schema = self._compact_schema(request.output_schema)
        argv = [exe, "-p", "--output-format", "json", "--json-schema", json.dumps(schema, separators=(",", ":")), "--tools", "", "--append-system-prompt", request.system_prompt]
        model = request.parameters.get("_model") or self.model
        if model:
            argv += ["--model", str(model)]
        session = request.parameters.get("_resume_session_id")
        if session:
            argv += ["--resume", str(session)]
        prompt = _render_prompt(request)
        cwd = Path(tempfile.mkdtemp(prefix=f"sdlc-{request.task_id}-", dir=str(self._scratch_root) if self._scratch_root else None))
        env = child_environment(native=True)
        try:
            result = self._run(argv, prompt, cwd, env, request.timeout_seconds)
            self.last_log = CliInvocationLog(tuple(argv[:5]) + ("<schema>", "--tools", "", "..."), str(cwd), result.exit_code, result.duration_seconds, result.stderr)
            return self._parse(result, request)
        finally:
            shutil.rmtree(cwd, ignore_errors=True)

    def _parse(self, result: ProcessResult, request: BrainRequest) -> BrainReply:
        provider = {"adapter": ADAPTER_ID, "origin": "native", "exit_code": result.exit_code, "log": self.last_log.to_dict() if self.last_log else None}
        if result.timed_out:
            return BrainReply(None, result.stdout or None, normalized_error(ErrorCategory.UNKNOWN, "claude timed out", provider_code="timeout", certainty=CompletionCertainty.UNKNOWN), unknown_usage(), provider)
        envelope = None
        for line in reversed(result.stdout.strip().splitlines()):
            if line.startswith("{"):
                try:
                    envelope = json.loads(line)
                    break
                except ValueError:
                    continue
        if envelope is None:
            text = (result.stderr or result.stdout).strip()
            category = classify(text) if result.exit_code else ErrorCategory.INVALID_OUTPUT
            return BrainReply(None, result.stdout or None, normalized_error(category, text or f"exit {result.exit_code} with no JSON envelope", provider_code=str(result.exit_code)), unknown_usage(), provider)
        provider["session_id"] = envelope.get("session_id")
        provider["model_usage"] = {k: {"inputTokens": v.get("inputTokens"), "outputTokens": v.get("outputTokens")} for k, v in (envelope.get("modelUsage") or {}).items()}
        usage_raw = envelope.get("usage") or {}
        usage = measured_usage(usage_raw.get("input_tokens"), usage_raw.get("output_tokens"), envelope.get("total_cost_usd"))
        if envelope.get("is_error") or result.exit_code not in (0, None):
            message = str(envelope.get("result") or envelope.get("error") or result.stderr or "claude reported an error")
            category = classify(message)
            if envelope.get("api_error_status") == 429:
                category = ErrorCategory.RATE_LIMITED if "rate" in message.lower() else ErrorCategory.QUOTA_EXHAUSTED
            return BrainReply(None, message, normalized_error(category, message, provider_code=str(envelope.get("api_error_status") or result.exit_code)), usage, provider)
        structured = envelope.get("structured_output")
        if not isinstance(structured, dict):
            text = str(envelope.get("result") or "")
            structured = _extract_json(text)
            if structured is None:
                return BrainReply(None, text, normalized_error(ErrorCategory.INVALID_OUTPUT, "no structured_output in the envelope", provider_code="no_structured_output"), usage, provider, refusal=text[:300] if envelope.get("stop_reason") == "refusal" else None)
        return BrainReply(structured, json.dumps(structured, ensure_ascii=False), None, usage, provider)

    @staticmethod
    def _compact_schema(schema: dict) -> dict:
        from sdlc.verification.schemas import schema_subset

        if "$ref" in schema and schema["$ref"].startswith("#/$defs/"):
            return schema_subset(schema["$ref"][len("#/$defs/") :])
        return schema


def _render_prompt(request: BrainRequest) -> str:
    parts = [f"Task {request.task_id}: operation {request.operation} (v{request.operation_version})."]
    if request.instructions:
        parts.append(f"Objective: {request.instructions}")
    extra = {k: v for k, v in request.parameters.items() if not k.startswith("_") and k != "objective"}
    if extra:
        parts.append("Parameters: " + json.dumps(extra, ensure_ascii=False))
    for doc in request.documents:
        parts.append(f"\n===== DOCUMENT {doc.artifact_id}@{doc.revision} ({doc.media_type}, {doc.relative_path}) =====\n{doc.text}\n===== END DOCUMENT =====")
    parts.append(f"\nRespond with one JSON object that validates as {request.output_definition}; set task_id to {request.task_id!r}.")
    return "\n".join(parts)


def _extract_json(text: str) -> dict | None:
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return None
    try:
        value = json.loads(match.group(0))
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


__all__ = ["ClaudeCliBrain", "ADAPTER_ID", "sanitize"]
