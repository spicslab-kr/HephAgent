"""Local process runner: the trusted collector for registered recipes (handoff docs/06 s8).

Runs an argument array inside a workspace with an explicit, allow-listed environment
(no provider credentials, no inherited API keys), a deadline, and captured output. It
produces the ``ExecutionRecord`` itself; a model never does. Under development isolation
there is no container, so network and filesystem are not restricted beyond the working
directory; the record says so (``fixture_mode`` false, collector ``local-runner-dev``).
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
import time
from pathlib import Path

from sdlc.common import now_utc
from sdlc.domain.models import CaseObservation, CaseStatus, ExecutionRecord, ExecutionStatus
from sdlc.ports.common import AdapterDescription, CapabilityStatus, ProbeResult, Support
from sdlc.ports.execution import ExecutionRequest

COLLECTOR_ID = "local-runner-dev"
SAFE_BASE_ENV = ("PATH", "SYSTEMROOT", "SystemRoot", "TEMP", "TMP", "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "COMSPEC", "PATHEXT", "LANG", "LC_ALL", "PYTHONIOENCODING")


class LocalProcessRunner:
    def __init__(self, *, artifacts_writer=None, launcher=None) -> None:
        self._write = artifacts_writer  # callable(name, bytes) -> ArtifactRef | None
        #: An isolation launcher profile (runtime.launcher.LauncherProfile): when set, every
        #: command runs inside its container boundary with only the workspace mounted.
        self.launcher = launcher

    def describe(self) -> AdapterDescription:
        return AdapterDescription(
            adapter_id=COLLECTOR_ID, adapter_version="1", connection_type="local-process", auth_mode="none", cost_source="local",
            capabilities=(
                CapabilityStatus("isolated_execution", Support.UNSUPPORTED, "no container; development isolation only"),
                CapabilityStatus("process_execution", Support.SUPPORTED, "argv, deadline, allow-listed env, captured output"),
            ),
            limits={"isolation": "development", "network": "not restricted"},
        )

    async def probe(self, live: bool = False) -> ProbeResult:
        return ProbeResult(ok=True, live=False, messages=("local process runner (development isolation)",), identity="verified")

    @staticmethod
    def _confine_pytest(argv: list[str], workdir: Path) -> list[str]:
        """Keep pytest inside the candidate: without an explicit config it walks *up* from the
        workspace and adopts the first ``pyproject.toml``/``pytest.ini`` it meets (the host
        project's when the workspace lives inside a repository), silently changing options
        such as ``-q``. A candidate that ships its own config keeps it."""
        if "pytest" not in argv or any(a == "-c" or a.startswith("--rootdir") for a in argv):
            return argv
        if any((workdir / name).exists() for name in ("pytest.ini", "pyproject.toml", "tox.ini", "setup.cfg")):
            return argv + ["--rootdir", str(workdir)]
        neutral = Path(tempfile.gettempdir()) / "sdlc-runner" / "pytest.ini"
        neutral.parent.mkdir(parents=True, exist_ok=True)
        if not neutral.exists():
            neutral.write_text("[pytest]\n", encoding="utf-8")
        return argv + ["--rootdir", str(workdir), "-c", str(neutral)]

    @staticmethod
    def _strip_host_config(argv: list[str]) -> list[str]:
        """Inside a container the neutral pytest.ini written on the host does not exist, and there
        is no host project to walk into anyway: drop ``-c <host path>`` and pin ``--rootdir /work``."""
        out: list[str] = []
        skip = False
        for i, a in enumerate(argv):
            if skip:
                skip = False
                continue
            if a == "-c" and i + 1 < len(argv) and "sdlc-runner" in argv[i + 1]:
                skip = True
                continue
            if a == "--rootdir" and i + 1 < len(argv):
                out += ["--rootdir", "/work"]
                skip = True
                continue
            out.append(a)
        return out

    async def execute(self, request: ExecutionRequest) -> ExecutionRecord:
        env = {k: v for k, v in os.environ.items() if k in SAFE_BASE_ENV or k in request.environment_allowlist}
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        started = now_utc()
        t0 = time.monotonic()
        status = ExecutionStatus.PASSED
        exit_code: int | None = None
        stdout = stderr = ""
        argv = self._confine_pytest(list(request.argv), Path(request.working_directory))
        cwd = str(request.working_directory)
        if self.launcher is not None:
            # inside the boundary the workspace is /work: host-only paths must not be passed in
            argv = self.launcher.wrap(self._strip_host_config(argv), Path(request.working_directory))
            env = {k: v for k, v in env.items() if k in ("PATH", "SYSTEMROOT", "SystemRoot", "HOME", "USERPROFILE", "TEMP", "TMP", "COMSPEC", "PATHEXT", "DOCKER_HOST")}
            cwd = None
        try:
            proc = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=request.timeout_seconds, shell=False)
            exit_code, stdout, stderr = proc.returncode, proc.stdout, proc.stderr
        except FileNotFoundError as exc:
            status, stderr = ExecutionStatus.SETUP_FAILED, f"executable not found: {exc}"
        except subprocess.TimeoutExpired as exc:
            status = ExecutionStatus.TIMED_OUT
            stdout = (exc.stdout.decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else exc.stdout) or ""
            stderr = (exc.stderr.decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else exc.stderr) or ""
        observations = parse_pytest_output(stdout)
        if status == ExecutionStatus.PASSED:
            if exit_code not in (0, None):
                status = ExecutionStatus.ASSERTION_FAILED if observations or "failed" in stdout.lower() else ExecutionStatus.SETUP_FAILED
            elif not observations and "collected 0 items" in stdout:
                status = ExecutionStatus.NOT_RUN  # zero tests collected is not a pass
        stdout_ref = self._write(f"{request.execution_id}-stdout.txt", stdout.encode("utf-8")) if self._write else None
        stderr_ref = self._write(f"{request.execution_id}-stderr.txt", stderr.encode("utf-8")) if self._write else None
        return ExecutionRecord(
            schema_version="1.0",
            execution_id=request.execution_id,
            project_id=request.principal.project_id,
            team_id=request.principal.team_id.value,  # type: ignore[arg-type]
            task_id=request.task_id,
            attempt_id=request.attempt_id,
            source_ref=request.source_ref,
            suite_ref=request.suite_ref,
            environment_ref=request.environment_ref,
            recipe_id=request.recipe_id,
            argv=argv,
            working_directory=str(request.working_directory),
            environment_variable_names=sorted(env) if self.launcher is None else [],  # nothing of the host environment enters the boundary
            started_at=started,
            ended_at=now_utc(),
            status=status,
            exit_code=exit_code,
            stdout_ref=stdout_ref,
            stderr_ref=stderr_ref,
            report_refs=[],
            case_observations=observations,
            collector_id=COLLECTOR_ID,
            fixture_mode=False,
        )


#: One ``pytest -v`` result line: ``path::node[params] STATUS``; parameters may hold any text.
_PYTEST_LINE = re.compile(r"^(?P<node>\S+::.+?)\s+(?P<status>PASSED|FAILED|ERROR|SKIPPED|XFAIL|XPASS)\b", re.M)


def parse_pytest_output(stdout: str) -> list[CaseObservation]:
    """Map pytest -v lines to case observations (node id -> status)."""
    mapping = {"PASSED": CaseStatus.PASSED, "FAILED": CaseStatus.FAILED, "ERROR": CaseStatus.ERROR, "SKIPPED": CaseStatus.SKIPPED, "XFAIL": CaseStatus.SKIPPED, "XPASS": CaseStatus.PASSED}
    out = []
    for match in _PYTEST_LINE.finditer(stdout):
        node = match.group("node").strip()
        case_id = re.sub(r"[^A-Za-z0-9_.:-]", "-", node)[:128]
        out.append(CaseObservation(case_id=case_id, status=mapping[match.group("status")], message=node))
    return out
