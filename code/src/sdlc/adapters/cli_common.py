"""Shared machinery for native CLI adapters (Codex, Claude Code).

* Subprocesses are launched with argument arrays and ``shell=False``; this alone is not
  isolation, but it removes quoting bugs and shell injection.
* The child environment is built explicitly. In native (subscription) mode every
  ``*_API_KEY`` variable is removed so an inherited API key can never silently change
  the billing source (handoff docs/05 s9, FR-024).
* Output is captured with a deadline; on timeout the process tree is killed and the
  outcome is ``unknown`` with ``completion_certainty: unknown``.
* Error text is mapped to ``NormalizedError`` categories by explicit patterns; the
  original (sanitised) message and exit status are preserved.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from sdlc.domain.models import CompletionCertainty, ErrorCategory, Measurement, NormalizedError, Usage


@dataclass(frozen=True, slots=True)
class ProcessResult:
    argv: tuple[str, ...]
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool
    duration_seconds: float


#: Signature of the function that actually runs a process; tests inject a fake.
Runner = Callable[[list[str], str | None, Path | None, dict[str, str], int], ProcessResult]


def default_runner(argv: list[str], stdin: str | None, cwd: Path | None, env: dict[str, str], timeout: int) -> ProcessResult:
    import time

    start = time.monotonic()
    try:
        proc = subprocess.run(
            argv,
            input=stdin,
            cwd=str(cwd) if cwd else None,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            shell=False,
        )
        return ProcessResult(tuple(argv), proc.returncode, proc.stdout, proc.stderr, False, time.monotonic() - start)
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout.decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        err = exc.stderr.decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        return ProcessResult(tuple(argv), None, out, err, True, time.monotonic() - start)


def find_executable(name: str) -> str | None:
    """Resolve a CLI on PATH, also looking in the usual npm global locations."""
    found = shutil.which(name)
    if found:
        return found
    candidates = []
    if os.name == "nt":
        appdata = os.environ.get("APPDATA", "")
        candidates += [Path(appdata) / "npm" / f"{name}.cmd", Path(r"C:\Program Files\nodejs") / f"{name}.cmd"]
    else:
        home = Path.home()
        candidates += [home / ".npm-global" / "bin" / name, Path("/usr/local/bin") / name, home / ".local" / "bin" / name]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return None


def child_environment(*, native: bool, extra: dict[str, str] | None = None) -> dict[str, str]:
    """Explicit child environment. Native mode strips inherited API keys; never adds secrets."""
    env = dict(os.environ)
    if os.name == "nt":
        npm_dir = os.path.join(os.environ.get("APPDATA", ""), "npm")
        node_dir = r"C:\Program Files\nodejs"
        env["PATH"] = os.pathsep.join([npm_dir, node_dir, env.get("PATH", "")])
    if native:
        for key in list(env):
            if key.endswith("_API_KEY") or key in ("ANTHROPIC_AUTH_TOKEN", "OPENAI_API_KEY"):
                env.pop(key, None)
    if extra:
        env.update(extra)
    return env


# ----------------------------------------------------------------------------- normalisation

_PATTERNS: list[tuple[ErrorCategory, re.Pattern[str]]] = [
    # Order matters: "rate limit reached" is throttling, "usage limit reached" is exhaustion.
    (ErrorCategory.RATE_LIMITED, re.compile(r"rate.?limit|too many requests|\b429\b|overloaded", re.I)),
    (ErrorCategory.QUOTA_EXHAUSTED, re.compile(r"usage limit|quota|limit reached|out of (?:credits|budget)|insufficient_quota|exceeded your current", re.I)),
    (ErrorCategory.AUTH_REQUIRED, re.compile(r"not logged in|log ?in|unauthorized|authentication|invalid api key|\b401\b|login required|sign in", re.I)),
    (ErrorCategory.PERMISSION_REQUIRED, re.compile(r"permission|approval required|denied", re.I)),
    (ErrorCategory.CONTEXT_LIMIT, re.compile(r"context (?:window|length)|too long|maximum context|prompt is too large", re.I)),
    (ErrorCategory.PROVIDER_UNAVAILABLE, re.compile(r"\b5\d\d\b|unavailable|ECONNREFUSED|ENOTFOUND|network|timed? ?out|connection", re.I)),
]


def classify(text: str) -> ErrorCategory:
    for category, pattern in _PATTERNS:
        if pattern.search(text):
            return category
    return ErrorCategory.UNKNOWN


def sanitize(text: str, limit: int = 600) -> str:
    """Strip anything that looks like a secret and bound the length."""
    text = re.sub(r"\b(sk-(?:ant-|proj-)?[A-Za-z0-9_-]{8,})", "sk-***", text)
    text = re.sub(r"Bearer\s+\S+", "Bearer ***", text)
    return text.strip()[:limit]


def normalized_error(
    category: ErrorCategory,
    message: str,
    *,
    provider_code: str | None,
    certainty: CompletionCertainty = CompletionCertainty.NOT_COMPLETED,
    retry_after: int | None = None,
) -> NormalizedError:
    next_action = {
        ErrorCategory.QUOTA_EXHAUSTED: "Wait for your subscription limit to reset, then resume this task with --quota-restored; or select another configured connection.",
        ErrorCategory.RATE_LIMITED: "The runtime retries with backoff within budget; nothing to do unless it pauses.",
        ErrorCategory.AUTH_REQUIRED: "Log in with the official client (claude auth login / codex login) and resume the task.",
        ErrorCategory.PERMISSION_REQUIRED: "Review the requested permission; adjust the connection's tool policy if appropriate, then resume.",
        ErrorCategory.CONTEXT_LIMIT: "Reduce the task's inputs (split the operation or shorten documents) and resume.",
        ErrorCategory.PROVIDER_UNAVAILABLE: "The runtime retries within budget; check the network or the provider status if it pauses.",
        ErrorCategory.INVALID_OUTPUT: "The runtime retries once with the same inputs; afterwards inspect the rejected output.",
        ErrorCategory.CAPABILITY_MISSING: "Select a connection that provides the required capability or take the task over manually.",
        ErrorCategory.CANCELLED: "Resume or rerun the task deliberately.",
        ErrorCategory.UNKNOWN: "Inspect the diagnostics; resume once the cause is understood.",
    }[category]
    return NormalizedError(
        category=category,
        provider_code=provider_code,
        message=sanitize(message) or category.value,
        automatic_retry_now=category in (ErrorCategory.RATE_LIMITED, ErrorCategory.PROVIDER_UNAVAILABLE),
        retry_after_seconds=retry_after,
        reset_at=None,  # never invented; only set when a provider supplies it reliably
        completion_certainty=certainty,
        next_action=next_action,
        diagnostic_ref=None,
    )


def unknown_usage() -> Usage:
    return Usage(input_tokens=None, output_tokens=None, cost_microunits=None, currency=None, measurement=Measurement.UNKNOWN)


def measured_usage(input_tokens: int | None, output_tokens: int | None, cost_usd: float | None) -> Usage:
    if input_tokens is None and output_tokens is None and cost_usd is None:
        return unknown_usage()
    return Usage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_microunits=int(round(cost_usd * 1_000_000)) if cost_usd is not None else None,
        currency="USD" if cost_usd is not None else None,
        measurement=Measurement.MEASURED,
    )


@dataclass
class CliInvocationLog:
    """Redacted record of what was run, for BackendEvent details."""

    argv: tuple[str, ...]
    cwd: str | None
    exit_code: int | None
    duration_seconds: float
    stderr_excerpt: str
    environment_variable_names: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "argv": [sanitize(a, 200) for a in self.argv],
            "cwd": self.cwd,
            "exit_code": self.exit_code,
            "duration_seconds": round(self.duration_seconds, 3),
            "stderr": sanitize(self.stderr_excerpt, 300),
        }
