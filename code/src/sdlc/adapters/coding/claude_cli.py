"""Claude Code CLI as a CodingBackend (handoff docs/05 s5).

    claude -p --output-format json --permission-mode acceptEdits
           --tools Read,Edit,Write,Glob,Grep,Bash --json-schema SCHEMA [--model M] [--resume SESSION]

Run with ``cwd`` = the task workspace. Claude Code confines file tools to the working
directory (plus ``--add-dir``, which we never pass); under development isolation that
is the only boundary. ``--permission-mode acceptEdits`` auto-approves edits inside the
workspace; shell commands are limited by the Bash tool's own prompts, so commands that
would need approval fail rather than run (``permission_denials`` in the envelope are
reported as a ``permission_required`` finding).
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import AsyncIterator
from pathlib import Path

from sdlc.adapters.cli_common import child_environment, classify, find_executable, measured_usage, normalized_error
from sdlc.adapters.coding.common import make_event, render_job_prompt
from sdlc.common import now_utc
from sdlc.domain.models import BackendEvent, Checkpoint, CompletionCertainty, ErrorCategory, EventKind
from sdlc.ports.coding import CancelReceipt, CodingJob
from sdlc.ports.common import AdapterDescription, CapabilityStatus, ProbeResult, Support
from sdlc.verification.schemas import schema_subset

ADAPTER_ID = "claude-cli-coding"
TOOLS = "Read,Edit,Write,Glob,Grep,Bash"


class ClaudeCliCoding:
    def __init__(self, *, executable: str = "claude", model: str | None = None) -> None:
        self.executable_name = executable
        self.model = model
        self._processes: dict[str, subprocess.Popen] = {}
        self._ids = 0

    def describe(self) -> AdapterDescription:
        return AdapterDescription(
            adapter_id=ADAPTER_ID, adapter_version="1", connection_type="claude-cli", auth_mode="native", cost_source="native_subscription",
            capabilities=(
                CapabilityStatus("scoped_repository", Support.SUPPORTED, "file tools confined to the workspace cwd (development isolation)"),
                CapabilityStatus("structured_proposal", Support.SUPPORTED, "--json-schema"),
                CapabilityStatus("native_resume", Support.SUPPORTED, "--resume SESSION_ID"),
                CapabilityStatus("interrupt", Support.SUPPORTED, "process termination"),
                CapabilityStatus("usage_events", Support.SUPPORTED, "usage + total_cost_usd"),
            ),
            limits={"isolation": "client working-directory confinement only; not an OS boundary"},
        )

    async def probe(self, live: bool = False) -> ProbeResult:
        exe = find_executable(self.executable_name)
        if exe is None:
            return ProbeResult(ok=False, live=False, messages=("claude not found on PATH",))
        return ProbeResult(ok=True, live=False, messages=("claude present",), identity="identity_unverified")

    def _event(self, job: CodingJob, seq: int, kind: EventKind, details: dict, error=None) -> BackendEvent:
        self._ids += 1
        return make_event(job, seq, kind, details, recorded_at=now_utc(), error=error, event_id=f"clc-{job.attempt_id}-{self._ids}")

    async def start(self, job: CodingJob) -> AsyncIterator[BackendEvent]:
        async for event in self._run(job, None):
            yield event

    async def resume(self, job: CodingJob, checkpoint: Checkpoint) -> AsyncIterator[BackendEvent]:
        async for event in self._run(job, checkpoint.backend_session_id):
            yield event

    async def _run(self, job: CodingJob, session: str | None) -> AsyncIterator[BackendEvent]:
        exe = find_executable(self.executable_name)
        if exe is None:
            yield self._event(job, 1, EventKind.ERROR, {}, error=normalized_error(ErrorCategory.CAPABILITY_MISSING, "claude not installed", provider_code=None))
            return
        workspace = Path(job.workspace).resolve()
        argv = [exe, "-p", "--output-format", "json", "--permission-mode", "acceptEdits", "--tools", TOOLS, "--json-schema", json.dumps(schema_subset("WorkerProposal"), separators=(",", ":"))]
        if self.model:
            argv += ["--model", self.model]
        if session:
            argv += ["--resume", session]
        env = child_environment(native=True, extra=job.environment)
        seq = 1
        yield self._event(job, seq, EventKind.STARTED, {"adapter": ADAPTER_ID, "workspace": str(workspace), "tools": TOOLS, "resume": session})
        try:
            proc = subprocess.Popen(argv, cwd=str(workspace), env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", shell=False)
        except OSError as exc:
            yield self._event(job, seq + 1, EventKind.ERROR, {}, error=normalized_error(ErrorCategory.UNKNOWN, f"could not start claude: {exc}", provider_code=None))
            return
        self._processes[job.attempt_id] = proc
        try:
            stdout, stderr = proc.communicate(render_job_prompt(job), timeout=job.timeout_seconds)
        except subprocess.TimeoutExpired:
            proc.kill()
            seq += 1
            yield self._event(job, seq, EventKind.INTERRUPTED, {"reason": "timeout"}, error=normalized_error(ErrorCategory.UNKNOWN, "claude timed out", provider_code="timeout", certainty=CompletionCertainty.UNKNOWN))
            return
        finally:
            self._processes.pop(job.attempt_id, None)
        envelope = None
        for line in reversed(stdout.strip().splitlines()):
            if line.startswith("{"):
                try:
                    envelope = json.loads(line)
                    break
                except ValueError:
                    continue
        if envelope is None:
            message = (stderr or stdout).strip() or f"claude exited with {proc.returncode}"
            seq += 1
            yield self._event(job, seq, EventKind.ERROR, {}, error=normalized_error(classify(message), message, provider_code=str(proc.returncode), certainty=CompletionCertainty.UNKNOWN))
            return
        usage_raw = envelope.get("usage") or {}
        usage = measured_usage(usage_raw.get("input_tokens"), usage_raw.get("output_tokens"), envelope.get("total_cost_usd")).to_json_dict()
        denials = envelope.get("permission_denials") or []
        if denials:
            seq += 1
            yield self._event(job, seq, EventKind.PERMISSION_REQUEST, {"denials": denials[:10]})
        if envelope.get("is_error"):
            message = str(envelope.get("result") or "claude reported an error")
            seq += 1
            yield self._event(job, seq, EventKind.ERROR, {"session_id": envelope.get("session_id")}, error=normalized_error(classify(message), message, provider_code=str(envelope.get("api_error_status") or proc.returncode)))
            return
        structured = envelope.get("structured_output") if isinstance(envelope.get("structured_output"), dict) else None
        seq += 1
        yield self._event(job, seq, EventKind.COMPLETED, {
            "session_id": envelope.get("session_id"), "usage": usage, "summary": (structured or {}).get("summary") or str(envelope.get("result") or "")[:500],
            "findings": (structured or {}).get("findings", []), "questions": (structured or {}).get("questions", []), "origin": "native", "num_turns": envelope.get("num_turns"),
        })

    async def cancel(self, attempt_id: str) -> CancelReceipt:
        proc = self._processes.get(attempt_id)
        if proc is None:
            return CancelReceipt(attempt_id=attempt_id, stopped=False, partial_outputs_preserved=True, reconciliation_required=True, notes=("no running process",))
        proc.kill()
        return CancelReceipt(attempt_id=attempt_id, stopped=True, partial_outputs_preserved=True, reconciliation_required=True, notes=("process killed; workspace retained",))
