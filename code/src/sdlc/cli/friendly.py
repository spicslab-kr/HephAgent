"""The everyday commands: setup, new, run, continue, show, export, brain.

These wrap the fine-grained commands in :mod:`sdlc.cli.main` (which stay available as
the advanced surface). Names are chosen for a person who has a project description
and wants the six teams to work on it, not for someone who knows the runtime.

    sdlc setup                                  install/check the Claude and Codex clients
    sdlc new aes --brief brief.md               create a project from a description
    sdlc run aes [--until design] [--step] [--ask]
    sdlc continue aes [--quota-restored]
    sdlc show aes [--stage design]
    sdlc export aes --to out/
    sdlc brain aes claude|codex|fixture [--stage design]
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from sdlc.application.artifacts import ArtifactService
from sdlc.application.connections import ConnectionService
from sdlc.application.cycle import IMPLEMENTED_STAGES, CycleRunner, set_project_mode
from sdlc.application.handoffs import Actor, ActorKind, HandoffService
from sdlc.application.ingress import import_material
from sdlc.application.projects import ProjectContext, create_project, open_project
from sdlc.artifacts.store import safe_relative_path
from sdlc.config import ConnectionType
from sdlc.domain.errors import Conflict, MissingInput
from sdlc.domain.models import OwnerTeam, TeamId
from sdlc.domain.teams import TEAM_ORDER, Team
from sdlc.ports.common import ProbeResult

STAGES = [t.value for t in TEAM_ORDER]
STAGE_TITLES = {
    "planning": "Planning (계획)",
    "requirements": "Requirements (요구분석)",
    "design": "Design (설계)",
    "implementation": "Implementation (구현)",
    "testing": "Testing (테스트)",
    "maintenance": "Maintenance (유지보수)",
}
#: ChatKHU (Kyung Hee University generative-AI platform) exposes an OpenAI-compatible gateway.
CHATKHU_GATEWAY = "https://factchat-cloud.mindlogic.ai/v1/gateway"
CHATKHU_DEFAULT_MODEL = "claude-sonnet-5"
CHATKHU_KEY_VARIABLE = "CHATKHU_API_KEY"

NOISE_KINDS = {"rejected_output", "checkpoint_summary", "task_graph"}


class CommandError(Exception):
    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(message)
        self.payload = {"code": "command_error", "message": message, "details": details}


# --------------------------------------------------------------------------- setup


def cmd_setup(args: argparse.Namespace) -> dict[str, Any]:
    """Run the platform install script (Node, Claude Code CLI, Codex CLI, login check)."""
    root = _repo_root()
    if sys.platform.startswith("win"):
        script = root / "scripts" / "install-backends.ps1"
        argv = ["powershell", "-ExecutionPolicy", "Bypass", "-File", str(script)]
        if args.skip_install:
            argv.append("-SkipInstall")
    else:
        script = root / "scripts" / "install-backends.sh"
        argv = ["sh", str(script)] + (["--skip-install"] if args.skip_install else [])
    if not script.exists():
        raise CommandError("install script not found", path=str(script))
    proc = subprocess.run(argv, cwd=root, text=True, capture_output=True, encoding="utf-8", errors="replace")
    print(proc.stdout)
    return {"script": str(script), "exit_code": proc.returncode, "claude": shutil.which("claude") is not None, "codex": shutil.which("codex") is not None}


# --------------------------------------------------------------------------- new


def cmd_new(args: argparse.Namespace) -> dict[str, Any]:
    """Create a project from a brief: init, connect clients, import the brief, accept it."""
    home = Path(args.home)
    brief = Path(args.brief)
    if not brief.is_file():
        raise CommandError("brief file not found", path=str(brief))
    template = _example_config_path()
    context = create_project(home, args.name, template, policy="continuous" if args.mode == "auto" else "collaborative", force=False)
    try:
        steps: list[str] = []
        connections = ConnectionService(context)
        for name, ctype, scope in (("claude-native", ConnectionType.CLAUDE_CLI, "claude-subscription"), ("codex-native", ConnectionType.CODEX_CLI, "codex-subscription")):
            connections.add(name, type=ctype, model=args.claude_model if ctype == ConnectionType.CLAUDE_CLI else None, quota_scope_id=scope, replace=True)
        brain = {"claude": "claude-native", "codex": "codex-native", "fixture": "fixture"}[args.brain]
        connections.bind(role="brain", connection=brain)
        steps.append(f"brain for every stage: {args.brain}")
        coder = {"claude": "claude-native", "codex": "codex-native", "fixture": "fixture"}[args.coder or ("codex" if args.brain != "fixture" else "fixture")]
        connections.bind(role="coding", connection=coder, teams=[Team("design"), Team("implementation"), Team("testing")])
        connections.add("local-runner", type=ConnectionType.LOCAL_PROCESS, quota_scope_id="local-machine", replace=True)
        connections.bind(role="execution", connection="local-runner", teams=[Team("implementation"), Team("testing"), Team("maintenance")])
        steps.append(f"coding backend for design/implementation/testing: {coder}; check runner: local-runner (development isolation)")
        probes = _probe_live(context, sorted({c for c in (brain, coder) if c != "fixture"}))
        for name, result in probes.items():
            steps.append(f"{name}: {'ready' if result.ok else 'NOT ready: ' + '; '.join(result.messages)}")
        result = import_material(context, team=TeamId.PLANNING, path=brief, purpose=args.purpose or f"project brief for {args.name}")
        steps.append(f"brief imported ({result.extraction}{'; ' + result.needs_input if result.needs_input else ''})")
        HandoffService(context).accept_baseline(team=TeamId.PLANNING, delivery_id=result.delivery.delivery_id, actor=Actor(ActorKind.PARTICIPANT, args.actor), rationale="brief accepted as the planning starting point")
        steps.append("brief accepted as the Planning starting point")
        not_ready = [n for n, r in probes.items() if not r.ok]
        return {
            "project": args.name,
            "folder": str(context.root),
            "mode": "auto" if args.mode == "auto" else "ask",
            "brain": args.brain,
            "steps": steps,
            "warnings": [f"{n} is not usable yet; run `sdlc setup` and log in, then `sdlc show {args.name}`" for n in not_ready],
            "next": f"sdlc run {args.name}" + ("" if args.mode == "auto" else " --ask"),
        }
    finally:
        context.close()


def _probe_live(context: ProjectContext, names: list[str]) -> dict[str, ProbeResult]:
    import asyncio

    from sdlc.adapters.registry import AdapterRegistry

    adapters = AdapterRegistry(context.config, context.root)
    service = ConnectionService(context)
    results: dict[str, ProbeResult] = {}
    for name in names:
        try:
            result = asyncio.run(adapters.brain(name).probe(live=True))
        except Exception as exc:  # noqa: BLE001 - surfaced as a probe failure
            result = ProbeResult(ok=False, live=True, messages=(str(exc),))
        service.record_probe(name, result)
        results[name] = result
    return results


# --------------------------------------------------------------------------- run / continue


def cmd_run(args: argparse.Namespace) -> dict[str, Any]:
    """Run stages. Automatic by default (policy decisions); --ask puts you at every gate."""
    with _project(args) as context:
        if args.ask and context.config.project.mode != "collaborative":
            set_project_mode(context, "collaborative")
        if args.auto and context.config.project.mode != "continuous":
            set_project_mode(context, "continuous")
        if context.config.project.mode == "collaborative" and not args.ask:
            raise CommandError(f"project {context.project_id} is in ask mode: run `sdlc run {context.project_id} --ask`, or make it automatic with `--auto`")
        gate = _console_gate if args.ask else None
        runner = CycleRunner(context, _scheduler(context, args), gate=gate, participant_id=args.actor)
        start = TeamId(args.stage) if args.stage else runner.next_stage()
        if start is None:
            return {"project": context.project_id, "done": True, "message": "every available stage is finished; see: sdlc show " + context.project_id}
        end = TeamId(args.until) if args.until else (start if args.step or args.stage else TeamId(IMPLEMENTED_STAGES[-1]))
        objectives = {k: v for k, _, v in (o.partition("=") for o in (args.objective or []))}
        if args.again and not args.stage:
            raise CommandError("--again needs --stage: name the stage to redo")
        report = runner.run(start=start, end=end, objectives=objectives, replan=bool(args.again), defect_loops=max(0, args.defect_loops), change_loops=max(0, args.change_loops))
        return _friendly_report(context, report)


def cmd_continue(args: argparse.Namespace) -> dict[str, Any]:
    """Resume paused/needs_input tasks, then run again from the first unfinished stage."""
    with _project(args) as context:
        scheduler = _scheduler(context, args)
        resumed: list[str] = []
        for row in scheduler.graphs.list_tasks(None):
            if row["state"] in ("paused", "needs_input"):
                plan = scheduler.resume(row["task_id"], quota_restored_by=args.actor if args.quota_restored else None)
                if "state_after" in plan:
                    resumed.append(row["task_id"])
                elif "blocked" in plan:
                    return {"project": context.project_id, "resumed": resumed, "blocked": plan["blocked"], "hint": "pass --quota-restored once your limit has reset"}
        gate = _console_gate if context.config.project.mode == "collaborative" else None
        runner = CycleRunner(context, scheduler, gate=gate, participant_id=args.actor)
        start = runner.next_stage()
        if start is None:
            return {"project": context.project_id, "resumed": resumed, "done": True}
        end = TeamId(args.until) if args.until else TeamId(IMPLEMENTED_STAGES[-1])
        report = runner.run(start=start, end=end)
        out = _friendly_report(context, report)
        out["resumed"] = resumed
        return out


def _console_gate(kind: str, summary: dict[str, Any]) -> bool:
    if not sys.stdin.isatty():
        raise Conflict("--ask needs an interactive terminal (no prompt available); use automatic mode instead")
    print()
    if kind == "approve_plan":
        print(f"The {summary['team']} leader proposes these tasks:")
        for task in summary["tasks"]:
            deps = f" (after {', '.join(task['depends_on'])})" if task["depends_on"] else ""
            print(f"  - {task['task_id']}: {task['operation']}{deps}")
            if task.get("objective"):
                print(f"      {task['objective']}")
        question = "Approve this plan and let the workers run? [y/N] "
    else:
        print(f"Hand the {summary['from']} package to {summary['to']} ({len(summary['artifacts'])} artifacts):")
        for item in summary["artifacts"]:
            print(f"  - {item}")
        question = f"Accept it as {summary['to']}'s starting point? [y/N] "
    answer = input(question).strip().lower()
    return answer in ("y", "yes")


def _friendly_report(context: ProjectContext, report) -> dict[str, Any]:
    out: dict[str, Any] = {"project": context.project_id, "stages": [], "stopped": report.stopped}
    for stage in report.stages:
        out["stages"].append({"stage": stage.team, "status": stage.status, "what_happened": stage.steps, "why_stopped": stage.reason})
    if report.stopped:
        out["what_to_do"] = report.next_action
    else:
        out["what_to_do"] = f"read the results: sdlc show {context.project_id}  /  sdlc export {context.project_id} --to out"
    return out


# --------------------------------------------------------------------------- show / export / brain


def cmd_show(args: argparse.Namespace) -> dict[str, Any]:
    with _project(args) as context:
        scheduler = _scheduler(context, args)
        handoffs = HandoffService(context)
        artifacts = ArtifactService(context)
        connections = ConnectionService(context)
        if args.stage:
            team = TeamId(args.stage)
            docs = [a for a in artifacts.list(OwnerTeam(team.value)) if a.kind not in NOISE_KINDS and artifacts.status(a.ref) not in ("superseded", "stale")]
            return {
                "project": context.project_id,
                "stage": STAGE_TITLES[team.value],
                "baseline": handoffs.baseline(team)[0],
                "documents": [{"kind": a.kind, "file": a.relative_path, "status": artifacts.status(a.ref), "origin": a.origin.value, "ref": f"{a.artifact_id}@{a.revision}:{a.sha256}"} for a in docs],
                "tasks": scheduler.describe(team),
                "read_them": f"sdlc export {context.project_id} --to out --stage {team.value}",
            }
        stages = []
        for team in TEAM_ORDER:
            tid = TeamId(team.value)
            tasks = scheduler.describe(tid)
            workers = [t for t in tasks if not t["task_id"].startswith(f"{team.value}-plan-")]
            if team.value not in IMPLEMENTED_STAGES:
                state = "not available yet"
            elif workers and all(t["state"] == "completed" for t in workers):
                state = "finished"
            elif any(t["state"] in ("needs_input", "paused") for t in tasks):
                state = "blocked"
            elif tasks:
                state = "in progress"
            elif handoffs.baseline(tid)[0] is not None:
                state = "ready to start"
            else:
                state = "waiting for input"
            blocked = [f"{t['task_id']}: {t.get('cause', t['state'])}" for t in tasks if t["state"] in ("needs_input", "paused")]
            docs = [a.kind for a in artifacts.list(OwnerTeam(team.value)) if a.kind not in NOISE_KINDS and artifacts.status(a.ref) not in ("superseded", "stale")]
            stages.append({"stage": STAGE_TITLES[team.value], "state": state, "documents": len(docs), "blocked": blocked, "brain": context.config.teams[team].brain})
        pauses = scheduler.quotas.pauses()
        mode = "auto (policy decides at gates)" if context.config.project.mode == "continuous" else "ask (you decide at gates)"
        next_action = _next_action(context, stages, pauses)
        return {"project": context.project_id, "mode": mode, "stages": stages, "quota_pauses": sorted(pauses), "connections": {n: v["dispatchable"][1] for n, v in connections.list().items() if n != "fixture"}, "what_to_do": next_action}


def _next_action(context: ProjectContext, stages: list[dict[str, Any]], pauses: dict) -> str:
    p = context.project_id
    if pauses:
        return f"a connection is paused ({', '.join(sorted(pauses))}); when your limit has reset: sdlc continue {p} --quota-restored"
    for s in stages:
        if s["state"] == "blocked":
            stage = s["stage"].split(" ")[0].lower()
            return f"{s['stage']} is blocked; look at the cause with: sdlc show {p} --stage {stage}; then: sdlc continue {p}"
    mode_flag = "" if context.config.project.mode == "continuous" else " --ask"
    for index, s in enumerate(stages):
        if s["state"] in ("ready to start", "in progress"):
            return f"next: sdlc run {p}{mode_flag}"
        if s["state"] == "waiting for input" and index > 0 and stages[index - 1]["state"] == "finished":
            return f"{stages[index - 1]['stage']} is finished and its package has not been handed on yet; review it (sdlc export {p} --to out) and then: sdlc run {p}{mode_flag}"
    if all(s["state"] in ("finished", "not available yet") for s in stages):
        return f"all available stages are finished; read them: sdlc export {p} --to out"
    return f"import a brief with: sdlc new {p} --brief FILE"


def cmd_export(args: argparse.Namespace) -> dict[str, Any]:
    """Copy each stage's current documents into <to>/<stage>/<relative path>; with --release, export
    exactly one release's closure with EXPORT_MANIFEST.json (hashes, labels) for later verification."""
    with _project(args) as context:
        if args.release is not None:
            from sdlc.application.export import export_release

            result = export_release(context, Path(args.to), None if args.release == "" else args.release)
            return {"project": context.project_id, "release_id": result.release_id, "folder": str(result.folder), "files": len(result.files), "excluded_artifacts": result.excluded,
                    "verify": f"sdlc verify-export {result.folder}"}
        artifacts = ArtifactService(context)
        dest = Path(args.to)
        written: list[str] = []
        owners = [OwnerTeam(args.stage)] if args.stage else [OwnerTeam.PARTICIPANT, *[OwnerTeam(t.value) for t in TEAM_ORDER]]
        for owner in owners:
            latest: dict[str, Any] = {}
            for a in artifacts.list(owner):
                if a.kind in NOISE_KINDS or artifacts.status(a.ref) in ("superseded", "stale"):
                    continue
                if a.artifact_id not in latest or a.revision > latest[a.artifact_id].revision:
                    latest[a.artifact_id] = a
            for a in latest.values():
                relative = safe_relative_path(a.relative_path)
                target = dest / owner.value / Path(*relative.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(artifacts.read_bytes(a.ref))
                written.append(str(target))
        out = {"project": context.project_id, "folder": str(dest), "files": len(written), "written": written}
        if getattr(args, "html", False):
            from sdlc.application.overview import write_overview

            page = write_overview(context, _scheduler(context, args), dest / "index.html", exported_root=dest)
            out["overview"] = str(page)
            out["what_to_do"] = f"open {page} in a browser"
        return out


def cmd_brain(args: argparse.Namespace) -> dict[str, Any]:
    """Choose the model for every stage or one stage: claude, codex, local (an OpenAI-compatible server) or fixture."""
    with _project(args) as context:
        connection = {"claude": "claude-native", "codex": "codex-native", "fixture": "fixture", "local": "local-model", "chatkhu": "chatkhu"}[args.which]
        if args.which in ("local", "chatkhu"):
            try:
                import openai  # noqa: F401
            except ImportError as exc:
                raise CommandError(f"the {args.which} profile needs the OpenAI-compatible client: pip install -e .[local]") from exc
        if args.which == "local":
            if not args.base_url or not args.model:
                raise CommandError("local needs --base-url (e.g. http://localhost:11434/v1 for Ollama) and --model (the model name the server lists)")
            ConnectionService(context).add(connection, type=ConnectionType.OPENAI_COMPATIBLE, base_url=args.base_url, model=args.model, quota_scope_id="local-model", replace=True)
        if args.which == "chatkhu":
            # Kyung Hee University ChatKHU API Gateway: OpenAI dialect. The key stays in the
            # CHATKHU_API_KEY environment variable; the project records only the reference.
            import os

            ConnectionService(context).add(connection, type=ConnectionType.OPENAI_COMPATIBLE, base_url=args.base_url or CHATKHU_GATEWAY, model=args.model or CHATKHU_DEFAULT_MODEL,
                                           credential_ref=f"env:{CHATKHU_KEY_VARIABLE}", quota_scope_id="chatkhu", replace=True)
            if not os.environ.get(CHATKHU_KEY_VARIABLE):
                raise CommandError(f"set {CHATKHU_KEY_VARIABLE} in this terminal first (ChatKHU > API Gateway > create a key; it is shown once), then rerun this command. The key is never written to the project.")
        if connection not in context.config.connections:
            raise CommandError(f"{connection} is not configured in this project; run: sdlc new ... or sdlc connection add")
        teams = [Team(args.stage)] if args.stage else None
        config = ConnectionService(context).bind(role="brain", connection=connection, teams=teams)
        probes = _probe_live(context, [connection]) if connection != "fixture" else {}
        return {
            "project": context.project_id,
            "brain": {t.value: b.brain for t, b in config.teams.items()},
            "check": {n: ("ready" if r.ok else "NOT ready: " + "; ".join(r.messages)) for n, r in probes.items()},
        }



# --------------------------------------------------------------------------- isolation / take-over


def cmd_answer(args: argparse.Namespace) -> dict[str, Any]:
    """Give a stage's team a decision memo (markdown, pdf, docx): the answer to a worker's questions."""
    with _project(args) as context:
        path = Path(args.file)
        if not path.exists():
            raise CommandError("memo file not found", path=str(path))
        result = import_material(context, team=TeamId(args.stage), path=path, purpose=args.purpose or f"participant decision for {args.stage}")
        return {
            "project": context.project_id,
            "stage": args.stage,
            "memo": str(path),
            "delivered_as": result.delivery.delivery_id,
            "extraction": result.extraction,
            "warnings": list(result.warnings),
            "what_to_do": f"redo the stage on the new inputs: sdlc run {context.project_id} --stage {args.stage} --again"
            f"   (or, if only one task was waiting and the memo is enough: sdlc task resume TASK --project {context.project_id})",
        }


def cmd_request(args: argparse.Namespace) -> dict[str, Any]:
    """Hand Maintenance a change request: a bug report, a changed requirement, an observation (md/pdf/docx)."""
    with _project(args) as context:
        path = Path(args.file)
        if not path.exists():
            raise CommandError("request file not found", path=str(path))
        result = import_material(context, team=TeamId.MAINTENANCE, path=path, purpose=args.purpose or "participant change request")
        return {
            "project": context.project_id,
            "request": str(path),
            "delivered_as": result.delivery.delivery_id,
            "extraction": result.extraction,
            "warnings": list(result.warnings),
            "what_to_do": f"run the maintenance stage: sdlc run {context.project_id} --stage maintenance   (it triages, analyses impact, routes the change to the owning team, re-runs the cycle once and verifies closure)",
        }


def cmd_release(args: argparse.Namespace) -> dict[str, Any]:
    """Record a release from the current test evidence, or explain exactly why the gate refuses."""
    from sdlc.application.release import ReleaseService

    with _project(args) as context:
        service = ReleaseService(context)
        gate = service.gate()
        if not gate.allowed:
            return {"project": context.project_id, "released": False, "why_not": gate.reasons, "verdicts": gate.counts,
                    "what_to_do": "fix the inputs upstream (sdlc answer / sdlc request), then rerun testing: sdlc run " + context.project_id + " --stage testing --again"}
        artifact, gate = service.create(Actor(ActorKind.PARTICIPANT, args.actor), rationale=f"participant {args.actor} recorded the release")
        return {"project": context.project_id, "released": True, "release_id": artifact.artifact_id.removesuffix("-manifest"), "conformance": gate.conformance,
                "verdicts": gate.counts, "note": "nonconforming: produced under development isolation; the compliant lifecycle gate is not satisfied" if gate.conformance != "conforming" else "conforming",
                "what_to_do": f"export it: sdlc export {context.project_id} --release --to out/release"}


def cmd_verify_export(args: argparse.Namespace) -> dict[str, Any]:
    """Recompute every hash in an exported folder; report missing, tampered and extra files."""
    from sdlc.application.export import verify_export

    result = verify_export(Path(args.folder))
    return {"folder": args.folder, "ok": result.ok, "checked": result.checked, "missing": result.missing, "tampered": result.tampered, "extra": result.extra}


def cmd_isolation(args: argparse.Namespace) -> dict[str, Any]:
    """strict: executions run inside a probed launcher profile (coding still refused); development: everything allowed, labeled nonconforming; probe: run the canary test."""
    from sdlc.runtime.launcher import probe as run_probe, profile_from_config, usable_profile

    with _project(args) as context:
        service = ConnectionService(context)
        if args.mode == "probe":
            profile = profile_from_config(context, args.launcher)
            if profile is None:
                raise CommandError("no launcher profile to probe; configure one first: sdlc isolation NAME strict --launcher docker --image python:3.12")
            outcome = run_probe(context, profile)
            return {"project": context.project_id, "profile": profile.describe(), "passed": outcome.passed, "checks": outcome.checks, "reasons": outcome.reasons,
                    "what_to_do": "executions now run conforming under strict isolation" if outcome.passed else "fix the engine or image, then probe again; until then strict mode refuses executions"}
        raw = service._raw()
        raw["runtime"]["isolation"] = args.mode
        if args.launcher:
            if not args.image:
                raise CommandError("--launcher needs --image (e.g. python:3.12)")
            raw.setdefault("launchers", {})[args.launcher] = {"type": "docker", "image": args.image, "python": args.python or "python3", "executable": "docker", "network": False}
            raw["runtime"]["launcher_profile"] = args.launcher
        service._write(raw)
        profile, why = usable_profile(context)
        note = (
            "development: coding and check runs use the client's own sandbox inside the task workspace; every result is labeled nonconforming and cannot pass the compliant lifecycle gate"
            if args.mode == "development"
            else ("strict: executions run inside the probed launcher profile (conforming); coding is still refused under strict isolation" if profile
                  else f"strict: executions are refused until a launcher profile passes its probe ({why}); coding is refused under strict isolation")
        )
        out = {"project": context.project_id, "isolation": args.mode, "launcher": context.config.runtime.launcher_profile, "note": note}
        if args.mode == "strict" and args.launcher:
            out["what_to_do"] = f"sdlc isolation {context.project_id} probe"
        return out


def cmd_takeover(args: argparse.Namespace) -> dict[str, Any]:
    """Copy a coding task's workspace to a folder you can edit; the task is paused for you."""
    from sdlc.runtime.workspaces import WorkspaceManager

    with _project(args) as context:
        scheduler = _scheduler(context, args)
        task = scheduler.graphs.load_task(args.task)
        row = scheduler.graphs.task_row(args.task)
        if row["state"] not in ("needs_input", "paused", "completed", "failed", "cancelled"):
            raise CommandError(f"task {args.task} is {row['state']}; stop it first (sdlc task cancel) or wait until it is not running")
        manager = WorkspaceManager(context, "participant")
        destination = manager.export_for_takeover(task, Path(args.to))
        return {"task": args.task, "folder": str(destination), "next": f"edit the files, then: sdlc return-work {context.project_id} {args.task} --from {destination}"}


def cmd_return_work(args: argparse.Namespace) -> dict[str, Any]:
    """Import your edited folder as the task's candidate snapshot (participant provenance)."""
    from sdlc.artifacts.snapshots import create_snapshot, diff_trees, scan_tree
    from sdlc.domain.models import Origin

    with _project(args) as context:
        scheduler = _scheduler(context, args)
        task = scheduler.graphs.load_task(args.task)
        source = Path(getattr(args, "from_dir"))
        if not source.is_dir():
            raise CommandError("folder not found", path=str(source))
        archive, manifest = create_snapshot(source)
        artifacts = ArtifactService(context)
        baseline = next((r for r in task.input_refs if artifacts.get(r).kind == "source_snapshot"), None)
        snapshot = artifacts.register(owner=OwnerTeam(task.team_id.value), artifact_id=f"{args.task}-participant-snapshot", kind="source_snapshot", media_type="application/zip",
                                      relative_path=f"snapshots/{args.task}/participant.zip", data=archive, origin=Origin.PARTICIPANT, depends_on=[baseline] if baseline else [], created_by_task=args.task)
        diff = diff_trees(None, scan_tree(source), source, manifest) if baseline is None else None
        return {"task": args.task, "snapshot": f"{snapshot.artifact_id}@{snapshot.revision}", "files": len(manifest.files), "origin": "participant",
                "next": f"sdlc run {context.project_id} --stage implementation to continue with your candidate, or sdlc show {context.project_id} --stage implementation"}

# --------------------------------------------------------------------------- helpers


class _project:
    def __init__(self, args: argparse.Namespace) -> None:
        name = getattr(args, "name", None) or getattr(args, "project", None)
        if not name:
            raise CommandError("which project? pass its name")
        try:
            self.context = open_project(Path(args.home), name)
        except MissingInput as exc:
            raise CommandError(f"no project named {name!r} under {args.home}; create one with: sdlc new {name} --brief FILE") from exc

    def __enter__(self) -> ProjectContext:
        return self.context

    def __exit__(self, *exc: object) -> None:
        self.context.close()


def _scheduler(context: ProjectContext, args: argparse.Namespace):
    from sdlc.adapters.brains.fixture import FixtureScript
    from sdlc.adapters.registry import AdapterRegistry
    from sdlc.runtime.scheduler import Scheduler

    script = FixtureScript.from_file(Path(args.script)) if getattr(args, "script", None) else None
    return Scheduler(context, AdapterRegistry(context.config, context.root, fixture_script=script))


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "scripts" / "install-backends.ps1").exists():
            return parent
    return Path.cwd()


def _example_config_path() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "config" / "project.example.yaml"
        if candidate.exists():
            return candidate
    raise CommandError("config/project.example.yaml not found")


def register(sub: Any, subparser: Any, teams: list[str]) -> None:
    """Attach the friendly commands to the main parser."""
    p = sub.add_parser("setup", help="install and check the Claude Code and Codex clients")
    p.add_argument("--skip-install", action="store_true", help="only check versions and login")
    p.set_defaults(func=cmd_setup)

    p = sub.add_parser("new", help="create a project from a brief (Markdown, PDF or DOCX)")
    p.add_argument("name")
    p.add_argument("--brief", required=True, help="your project description file")
    p.add_argument("--brain", choices=["claude", "codex", "fixture"], default="claude", help="which model works for the teams (default claude)")
    p.add_argument("--coder", choices=["claude", "codex", "fixture"], help="which client edits code (default codex, the preferred coding backend)")
    p.add_argument("--mode", choices=["auto", "ask"], default="auto", help="auto: policy decides at gates; ask: you approve each plan and package")
    p.add_argument("--claude-model", default="sonnet")
    p.add_argument("--purpose")
    p.add_argument("--actor", default="participant")
    p.set_defaults(func=cmd_new)

    p = sub.add_parser("run", help="run the stages (planning -> requirements -> design); stops wherever a person is needed")
    p.add_argument("name")
    p.add_argument("--until", choices=teams, help="stop after this stage")
    p.add_argument("--step", action="store_true", help="run only the next stage")
    p.add_argument("--stage", choices=teams, help="run exactly this stage")
    p.add_argument("--again", action="store_true", help="with --stage: redo that stage (its unfinished tasks are cancelled, the leader plans again on the current inputs)")
    p.add_argument("--defect-loops", type=int, default=1, help="after Testing finds defects: how many times to send them back, repair and re-test automatically (default 1; 0 = stop and ask)")
    p.add_argument("--change-loops", type=int, default=1, help="after Maintenance routes a change: how many times to re-run the owning team through Testing and closure automatically (default 1; 0 = stop and ask)")
    p.add_argument("--ask", action="store_true", help="you approve each plan and each package (switches the project to ask mode)")
    p.add_argument("--auto", action="store_true", help="switch the project to automatic mode")
    p.add_argument("--objective", action="append", help="STAGE=text, what you want that stage to focus on (repeatable)")
    p.add_argument("--actor", default="participant")
    p.add_argument("--script", help=argparse.SUPPRESS)
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("continue", help="resume after fixing what stopped the run")
    p.add_argument("name")
    p.add_argument("--until", choices=teams)
    p.add_argument("--quota-restored", action="store_true", help="declare that a paused connection's limit has reset")
    p.add_argument("--actor", default="participant")
    p.add_argument("--script", help=argparse.SUPPRESS)
    p.set_defaults(func=cmd_continue)

    p = sub.add_parser("show", help="what is finished, what is blocked, what to do next")
    p.add_argument("name")
    p.add_argument("--stage", choices=teams, help="list one stage's documents and tasks")
    p.add_argument("--script", help=argparse.SUPPRESS)
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("export", help="copy every stage's documents into a folder you can read")
    p.add_argument("name")
    p.add_argument("--to", required=True, help="destination folder")
    p.add_argument("--stage", choices=teams)
    p.add_argument("--release", nargs="?", const="", help="export one release's exact closure with a verifiable manifest (latest release, or RELEASE_ID)")
    p.add_argument("--html", action="store_true", help="also write index.html: a self-contained overview of stages, documents, tasks and releases")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("request", help="hand Maintenance a change request (bug report, changed requirement, observation); then: sdlc run NAME --stage maintenance")
    p.add_argument("name")
    p.add_argument("file", help="markdown, pdf or docx")
    p.add_argument("--purpose")
    p.set_defaults(func=cmd_request)

    p = sub.add_parser("release", help="record a release from the current test evidence (refused with reasons when the evidence does not allow it)")
    p.add_argument("name")
    p.set_defaults(func=cmd_release)

    p = sub.add_parser("verify-export", help="check an exported release folder against its EXPORT_MANIFEST.json")
    p.add_argument("folder")
    p.set_defaults(func=cmd_verify_export)

    p = sub.add_parser("answer", help="hand a stage's team your decision memo (answers a worker's blocking questions); then rerun with --again")
    p.add_argument("name")
    p.add_argument("file", help="markdown, pdf or docx with your decision")
    p.add_argument("--stage", required=True, choices=teams, help="the stage whose worker asked")
    p.add_argument("--purpose")
    p.set_defaults(func=cmd_answer)

    p = sub.add_parser("isolation", help="strict (refuse repository work until a launcher exists) or development (allow, labeled nonconforming)")
    p.add_argument("name")
    p.add_argument("mode", choices=["strict", "development", "probe"], help="strict / development, or probe the configured launcher with the canary test")
    p.add_argument("--launcher", help="name a container launcher profile to configure (docker)")
    p.add_argument("--image", help="container image for the launcher, e.g. python:3.12")
    p.add_argument("--python", help="interpreter name inside the image (default python3)")
    p.set_defaults(func=cmd_isolation)

    p = sub.add_parser("takeover", help="copy a coding task's workspace to a folder you edit yourself")
    p.add_argument("name")
    p.add_argument("task")
    p.add_argument("--to", required=True)
    p.add_argument("--script", help=argparse.SUPPRESS)
    p.set_defaults(func=cmd_takeover)

    p = sub.add_parser("return-work", help="import your edited folder as the task's candidate")
    p.add_argument("name")
    p.add_argument("task")
    p.add_argument("--from", dest="from_dir", required=True)
    p.add_argument("--script", help=argparse.SUPPRESS)
    p.set_defaults(func=cmd_return_work)

    p = sub.add_parser("brain", help="choose claude, codex, chatkhu (KHU gateway), local (OpenAI-compatible server) or fixture for all stages or one stage")
    p.add_argument("name")
    p.add_argument("which", choices=["claude", "codex", "chatkhu", "local", "fixture"])
    p.add_argument("--stage", choices=teams)
    p.add_argument("--base-url", help="local: the server OpenAI-compatible base URL, e.g. http://localhost:11434/v1 (chatkhu: preset)")
    p.add_argument("--model", help="local/chatkhu: the model name the server serves (chatkhu default claude-sonnet-5)")
    p.set_defaults(func=cmd_brain)
