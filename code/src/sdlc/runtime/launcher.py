"""Isolation launcher profiles (handoff docs/03 s80, docs/06 s9).

Strict isolation needs an operating-system or container boundary that exposes only the
task workspace to the process under test: no host files, no credentials, no network.
This module implements the first profile, ``docker``: every execution command is wrapped
as ``docker run --rm --network none -v WORKSPACE:/work -w /work IMAGE ...`` with an empty
environment. A profile is usable only after its **canary probe** passed on this machine
and was recorded in the project journal (``launcher.probed``):

1. a file inside the workspace must be readable (the mount works);
2. a canary file placed *outside* the workspace, and the project home, must be unreadable;
3. an outbound connection must fail (network off);
4. the host environment must not leak (no ``*_API_KEY``-style variables inside).

Coding clients (Codex, Claude Code) are *not* launched through a profile yet: they would
need an image carrying the client and its login. Under strict isolation coding work is
therefore still refused with ``capability_missing``; executions (checks, benches) run
conforming. The release gate reports which part of the evidence is conforming.
"""

from __future__ import annotations

import json
import shlex
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sdlc.application.projects import ProjectContext
from sdlc.storage import repositories as repo

CONTAINER_WORKDIR = "/work"
CANARY_TEXT = "SDLC-CANARY-SECRET-DO-NOT-READ"
LEAK_VARIABLE = "SDLC_CANARY_API_KEY"

_PROBE_SCRIPT = r"""
import os, socket, sys, json
out = {"workspace_readable": False, "canary_readable": False, "home_readable": False, "network": "unknown", "env_leak": False, "cwd": os.getcwd()}
try:
    out["workspace_readable"] = open(os.path.join(os.getcwd(), "probe-ok.txt"), encoding="utf-8").read().strip() == "ok"
except OSError:
    pass
for key, path in (("canary_readable", sys.argv[1]), ("home_readable", sys.argv[2])):
    try:
        open(path, encoding="utf-8").read()
        out[key] = True
    except OSError:
        out[key] = False
try:
    s = socket.create_connection(("1.1.1.1", 53), timeout=3)
    s.close()
    out["network"] = "reachable"
except OSError:
    out["network"] = "blocked"
out["env_leak"] = any(k.endswith("_API_KEY") for k in os.environ)
print(json.dumps(out))
"""


def host_command(executable: str) -> list[str]:
    """How to start ``executable`` on this host: a ``.cmd``/``.bat`` shim on Windows needs ``cmd /c``."""
    found = shutil.which(executable) or executable
    if found.lower().endswith((".cmd", ".bat")):
        return ["cmd", "/c", found]
    return [found]


@dataclass(frozen=True, slots=True)
class LauncherProfile:
    name: str
    type: str  # docker
    image: str
    python: str = "python3"  # interpreter name inside the image, substituted for {python}
    executable: str = "docker"

    def wrap(self, argv: list[str], workspace: Path) -> list[str]:
        """The host command that runs ``argv`` inside the boundary with only ``workspace`` visible."""
        if self.type != "docker":
            raise ValueError(f"unsupported launcher type {self.type!r}")
        inner = [self.python if a == "{python}" else a for a in argv]
        return [*host_command(self.executable), "run", "--rm", "--network", "none", "--env", "LANG=C.UTF-8", "-v", f"{Path(workspace).resolve()}:{CONTAINER_WORKDIR}", "-w", CONTAINER_WORKDIR, self.image, *inner]

    def describe(self) -> dict[str, Any]:
        return {"name": self.name, "type": self.type, "image": self.image, "python": self.python, "executable": self.executable}


@dataclass
class ProbeOutcome:
    profile: str
    passed: bool
    checks: dict[str, Any] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)
    command: list[str] = field(default_factory=list)

    def to_json_dict(self) -> dict[str, Any]:
        return {"profile": self.profile, "passed": self.passed, "checks": self.checks, "reasons": self.reasons, "command": self.command}


def profile_from_config(context: ProjectContext, name: str | None = None) -> LauncherProfile | None:
    """The launcher profile named by ``runtime.launcher_profile`` (or ``name``), if configured."""
    raw = context.config.model_dump(mode="json")
    launchers = raw.get("launchers") or {}
    wanted = name or raw["runtime"].get("launcher_profile")
    entry = launchers.get(wanted) if wanted else None
    if not entry:
        return None
    return LauncherProfile(name=wanted, type=entry["type"], image=entry["image"], python=entry.get("python", "python3"), executable=entry.get("executable", "docker"))


def probe(context: ProjectContext, profile: LauncherProfile, *, runner=None, timeout: int = 120) -> ProbeOutcome:
    """Run the canary probe through the profile and journal the outcome. ``runner`` (tests)
    replaces ``subprocess.run``; the real run needs the container engine on PATH."""
    outcome = ProbeOutcome(profile=profile.name, passed=False)
    if shutil.which(profile.executable) is None and runner is None:
        outcome.reasons.append(f"{profile.executable!r} is not installed or not on PATH (install Docker Desktop / Docker Engine and retry)")
        _journal(context, outcome)
        return outcome
    with tempfile.TemporaryDirectory(prefix="sdlc-probe-ws-") as ws, tempfile.TemporaryDirectory(prefix="sdlc-probe-outside-") as outside:
        workspace = Path(ws)
        (workspace / "probe-ok.txt").write_text("ok", encoding="utf-8")
        (workspace / "probe.py").write_text(_PROBE_SCRIPT, encoding="utf-8")
        canary = Path(outside) / "canary.txt"
        canary.write_text(CANARY_TEXT, encoding="utf-8")
        home_file = context.root / "project.yaml"
        argv = profile.wrap(["{python}", "probe.py", str(canary), str(home_file)], workspace)
        outcome.command = argv
        env = {LEAK_VARIABLE: "leak-test"}
        try:
            run = runner or (lambda a, **kw: subprocess.run(a, capture_output=True, text=True, timeout=timeout, env={**_minimal_env(), **env}, shell=False))
            proc = run(argv)
        except (OSError, subprocess.TimeoutExpired) as exc:
            outcome.reasons.append(f"probe process failed: {exc}")
            _journal(context, outcome)
            return outcome
        text = (proc.stdout or "").strip().splitlines()
        try:
            checks = json.loads(text[-1]) if text else {}
        except ValueError:
            checks = {}
        outcome.checks = {**checks, "exit_code": proc.returncode}
        if proc.returncode != 0 or not checks:
            outcome.reasons.append(f"probe did not run inside the profile (exit {proc.returncode}): {(proc.stderr or '')[:300]}")
        else:
            if not checks.get("workspace_readable"):
                outcome.reasons.append("the workspace mount is not readable inside the boundary")
            if checks.get("canary_readable"):
                outcome.reasons.append("a file outside the workspace was readable: the boundary does not isolate the host filesystem")
            if checks.get("home_readable"):
                outcome.reasons.append("the project home was readable inside the boundary")
            if checks.get("network") != "blocked":
                outcome.reasons.append(f"network is {checks.get('network')}; strict execution requires it off")
            if checks.get("env_leak"):
                outcome.reasons.append("host environment variables leaked into the boundary")
        outcome.passed = not outcome.reasons
    _journal(context, outcome)
    return outcome


def _minimal_env() -> dict[str, str]:
    import os

    return {k: v for k, v in os.environ.items() if k in ("PATH", "SYSTEMROOT", "SystemRoot", "HOME", "USERPROFILE", "TEMP", "TMP", "COMSPEC", "PATHEXT", "DOCKER_HOST")}


def _journal(context: ProjectContext, outcome: ProbeOutcome) -> None:
    with context.db.transaction():
        repo.insert_event(context.db, context.project_id, context.ids("event"), "launcher.probed", outcome.to_json_dict(), context.clock())


def last_probe(context: ProjectContext, profile_name: str) -> ProbeOutcome | None:
    """The latest journaled probe for the profile on this project, if any."""
    for row in context.db.all("SELECT payload_json FROM events WHERE project_id=? AND event_type='launcher.probed' ORDER BY sequence_number DESC", (context.project_id,)):
        payload = json.loads(row["payload_json"])
        if payload.get("profile") == profile_name:
            return ProbeOutcome(profile=profile_name, passed=bool(payload.get("passed")), checks=payload.get("checks", {}), reasons=payload.get("reasons", []), command=payload.get("command", []))
    return None


def usable_profile(context: ProjectContext) -> tuple[LauncherProfile | None, str]:
    """(profile, reason): the configured profile when its last probe passed, else why not."""
    profile = profile_from_config(context)
    if profile is None:
        return None, "no launcher profile configured (sdlc isolation NAME strict --launcher docker --image python:3.12)"
    last = last_probe(context, profile.name)
    if last is None:
        return None, f"launcher profile {profile.name!r} has not been probed on this machine (sdlc isolation probe NAME)"
    if not last.passed:
        return None, f"launcher profile {profile.name!r} failed its last probe: " + "; ".join(last.reasons)
    return profile, "ok"


def shell_join(argv: list[str]) -> str:
    return " ".join(shlex.quote(a) for a in argv)
