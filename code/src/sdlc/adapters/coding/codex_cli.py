"""Codex CLI as a CodingBackend (handoff docs/05 s4): ``codex exec --sandbox workspace-write``.

    codex exec --json --sandbox workspace-write -C WORKSPACE --output-schema FILE -o FILE [-m MODEL] -

The prompt is passed on stdin. Stdout is JSONL; we translate ``item.*`` events to
BackendEvents (``progress`` for commands and file changes, ``completed`` with the final
message, ``error`` for failures) and record the thread id for same-task resume
(``codex exec resume THREAD_ID``). The workspace is a git repository created by the
runtime, so the CLI's repository check passes. Codex's own sandbox limits writes to the
workspace; under development isolation that is the only boundary, and results are
labeled accordingly.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import AsyncIterator
from pathlib import Path

from sdlc.adapters.cli_common import child_environment, classify, find_executable, normalized_error
from sdlc.adapters.coding.common import final_json, make_event, render_job_prompt
from sdlc.common import now_utc
from sdlc.domain.models import BackendEvent, Checkpoint, CompletionCertainty, ErrorCategory, EventKind
from sdlc.ports.coding import CancelReceipt, CodingJob
from sdlc.ports.common import AdapterDescription, CapabilityStatus, ProbeResult, Support
from sdlc.verification.schemas import transport_projection

ADAPTER_ID = "codex-cli-coding"


class CodexCliCoding:
    def __init__(self, *, executable: str = "codex", model: str | None = None) -> None:
        self.executable_name = executable
        self.model = model
        self._processes: dict[str, subprocess.Popen] = {}
        self._ids = 0

    def describe(self) -> AdapterDescription:
        return AdapterDescription(
            adapter_id=ADAPTER_ID, adapter_version="1", connection_type="codex-cli", auth_mode="native", cost_source="native_subscription",
            capabilities=(
                CapabilityStatus("scoped_repository", Support.SUPPORTED, "workspace-write sandbox inside the task workspace (development isolation)"),
                CapabilityStatus("structured_proposal", Support.SUPPORTED, "--output-schema"),
                CapabilityStatus("native_resume", Support.SUPPORTED, "codex exec resume THREAD_ID"),
                CapabilityStatus("interrupt", Support.SUPPORTED, "process termination"),
                CapabilityStatus("usage_events", Support.SUPPORTED, "turn.completed usage"),
            ),
            limits={"isolation": "client sandbox only; not an OS boundary"},
        )

    async def probe(self, live: bool = False) -> ProbeResult:
        exe = find_executable(self.executable_name)
        if exe is None:
            return ProbeResult(ok=False, live=False, messages=("codex not found on PATH",))
        return ProbeResult(ok=True, live=False, messages=("codex present",), identity="identity_unverified")

    def _event(self, job: CodingJob, seq: int, kind: EventKind, details: dict, error=None) -> BackendEvent:
        self._ids += 1
        return make_event(job, seq, kind, details, recorded_at=now_utc(), error=error, event_id=f"cdx-{job.attempt_id}-{self._ids}")

    async def start(self, job: CodingJob) -> AsyncIterator[BackendEvent]:
        async for event in self._run(job, resume_thread=None):
            yield event

    async def resume(self, job: CodingJob, checkpoint: Checkpoint) -> AsyncIterator[BackendEvent]:
        async for event in self._run(job, resume_thread=checkpoint.backend_session_id):
            yield event

    async def _run(self, job: CodingJob, resume_thread: str | None) -> AsyncIterator[BackendEvent]:
        exe = find_executable(self.executable_name)
        if exe is None:
            yield self._event(job, 1, EventKind.ERROR, {}, error=normalized_error(ErrorCategory.CAPABILITY_MISSING, "codex not installed", provider_code=None))
            return
        workspace = Path(job.workspace).resolve()
        schema_file = workspace.parent / f"{job.task_id}.output-schema.json"
        last_file = workspace.parent / f"{job.task_id}.last-message.json"
        schema_file.write_text(json.dumps(transport_projection("WorkerProposal")), encoding="utf-8")
        argv = [exe, "exec"]
        if resume_thread:
            argv += ["resume", resume_thread]
        argv += ["--json", "--sandbox", "workspace-write", "-C", str(workspace), "--output-schema", str(schema_file), "-o", str(last_file)]
        if self.model:
            argv += ["-m", self.model]
        argv.append("-")
        env = child_environment(native=True, extra=job.environment)
        seq = 1
        yield self._event(job, seq, EventKind.STARTED, {"adapter": ADAPTER_ID, "argv": argv[:6] + ["..."], "workspace": str(workspace), "resume": resume_thread})
        try:
            proc = subprocess.Popen(argv, cwd=str(workspace), env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", shell=False)
        except OSError as exc:
            yield self._event(job, seq + 1, EventKind.ERROR, {}, error=normalized_error(ErrorCategory.UNKNOWN, f"could not start codex: {exc}", provider_code=None, certainty=CompletionCertainty.NOT_COMPLETED))
            return
        self._processes[job.attempt_id] = proc
        assert proc.stdin is not None and proc.stdout is not None
        proc.stdin.write(render_job_prompt(job))
        proc.stdin.close()
        thread_id = resume_thread
        final_text: str | None = None
        failed: str | None = None
        usage: dict = {}
        try:
            for line in proc.stdout:
                line = line.strip()
                if not line.startswith("{"):
                    continue
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                kind = event.get("type")
                if kind == "thread.started":
                    thread_id = event.get("thread_id")
                    seq += 1
                    yield self._event(job, seq, EventKind.PROGRESS, {"thread_id": thread_id})
                elif kind == "item.completed":
                    item = event.get("item") or {}
                    itype = item.get("type")
                    if itype == "agent_message":
                        final_text = item.get("text")
                    elif itype in ("command_execution", "file_change", "reasoning"):
                        seq += 1
                        yield self._event(job, seq, EventKind.PROGRESS, {"item": itype, "command": item.get("command"), "changes": item.get("changes"), "exit_code": item.get("exit_code")})
                elif kind == "turn.completed":
                    usage = event.get("usage") or {}
                elif kind in ("turn.failed", "error"):
                    failed = json.dumps(event.get("error") or event, ensure_ascii=False)
            proc.wait(timeout=job.timeout_seconds)
        except subprocess.TimeoutExpired:
            proc.kill()
            seq += 1
            yield self._event(job, seq, EventKind.INTERRUPTED, {"reason": "timeout"}, error=normalized_error(ErrorCategory.UNKNOWN, "codex timed out", provider_code="timeout", certainty=CompletionCertainty.UNKNOWN))
            return
        finally:
            self._processes.pop(job.attempt_id, None)
        if final_text is None and last_file.exists():
            final_text = last_file.read_text(encoding="utf-8")
        if failed or proc.returncode not in (0, None):
            message = failed or (proc.stderr.read() if proc.stderr else "") or f"codex exited with {proc.returncode}"
            seq += 1
            yield self._event(job, seq, EventKind.ERROR, {"thread_id": thread_id}, error=normalized_error(classify(message), message, provider_code=str(proc.returncode), certainty=CompletionCertainty.UNKNOWN))
            return
        structured = final_json(final_text)
        seq += 1
        yield self._event(job, seq, EventKind.COMPLETED, {"thread_id": thread_id, "usage": usage, "summary": (structured or {}).get("summary") or (final_text or "")[:500], "findings": (structured or {}).get("findings", []), "questions": (structured or {}).get("questions", []), "origin": "native"})

    async def cancel(self, attempt_id: str) -> CancelReceipt:
        proc = self._processes.get(attempt_id)
        if proc is None:
            return CancelReceipt(attempt_id=attempt_id, stopped=False, partial_outputs_preserved=True, reconciliation_required=True, notes=("no running process for this attempt",))
        proc.kill()
        return CancelReceipt(attempt_id=attempt_id, stopped=True, partial_outputs_preserved=True, reconciliation_required=True, notes=("process killed; workspace retained",))
