"""``sdlc`` command line (handoff docs/07 section 5).

Implemented so far (M0-M1): ``init``, ``doctor``, ``validate``, ``operations``,
``contracts``, ``status``, ``import``, ``fixture load``, ``artifacts list``,
``handoff publish|deliver|receive|inbox``, ``review``.

Every command accepts ``--json`` (before or after the sub-command) and then prints one
stable envelope::

    {"command": "...", "success": true|false, "data": {...}, "errors": [...]}

Progress prose never goes into JSON output. Exit status is non-zero on any error.
Artifact references on the command line are written ``ID@REV:SHA256``; a bare file
name is never accepted as an input.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

import yaml

from sdlc import __version__
from sdlc.application.artifacts import ArtifactService
from sdlc.application.fixtures import load_fixture
from sdlc.application.handoffs import Actor, ActorKind, HandoffService
from sdlc.application.ingress import import_material
from sdlc.application.projects import ProjectContext, create_project, open_project
from sdlc.application.views import TeamView
from sdlc.application.workflow import WorkflowService
from sdlc.application.connections import ConnectionService
from sdlc.application.cycle import CycleRunner, set_project_mode
from sdlc.cli import friendly
from sdlc.adapters.brains.fixture import FixtureScript
from sdlc.adapters.registry import AdapterRegistry
from sdlc.runtime.scheduler import Scheduler
from sdlc.config import ConnectionType, ProjectConfig, Readiness, is_placeholder, load_config
from sdlc.domain.teams import Team as _Team
from sdlc.domain.errors import ServiceError
from sdlc.domain.models import ArtifactRef, BundleKind, BundleRef, DiagramRequest, DiagramType, OwnerTeam, TeamId
from sdlc.domain.teams import TEAM_ORDER, Team
from sdlc.operations import default_registry
from sdlc.verification.canonical import load_json_strict
from sdlc.verification.fixtures import check_fixture
from sdlc.verification.schemas import contract_path, definition_names, validate_record

DEFAULT_HOME = Path(".sdlc")


class CommandError(Exception):
    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(message)
        self.payload = {"code": "command_error", "message": message, "details": details}


# --------------------------------------------------------------------------- M0 commands


def cmd_init(args: argparse.Namespace) -> dict[str, Any]:
    template = Path(args.template) if args.template else _example_config_path()
    context = create_project(Path(args.home), args.name, template, policy=args.policy, force=args.force)
    try:
        return {
            "project_id": args.name,
            "config": str(context.root / "project.yaml"),
            "database": str(context.db.path),
            "fixture_mode": True,
            "policy": args.policy,
            "next": f"sdlc import --project {args.name} --team planning --file brief.md",
        }
    finally:
        context.close()


def cmd_doctor(args: argparse.Namespace) -> dict[str, Any]:
    """Local checks; ``--live`` additionally probes every non-fixture connection (one small request each) and records the result."""
    config = _load_project_config(args)
    if args.live and not getattr(args, "project", None):
        raise CommandError("doctor --live needs --project NAME so the probe results can be recorded")
    report: dict[str, Any] = {"config": str(_config_path(args)), "fixture_mode": config.project.fixture_mode, "connections": {}}
    blocking = 0
    for name, conn in config.connections.items():
        entry: dict[str, Any] = {
            "type": conn.type.value,
            "auth": conn.auth.value,
            "readiness": conn.readiness.value,
            "placeholders": conn.placeholders,
            "billing_source": _billing_source(conn.type),
            "checks": [],
        }
        if conn.type in (ConnectionType.CODEX_CLI, ConnectionType.CLAUDE_CLI):
            found = shutil.which(conn.executable or "")
            entry["checks"].append({"executable": conn.executable, "found": found is not None, "path": found})
            entry["checks"].append({"launcher_profile": conn.launcher_profile, "configured": not is_placeholder(conn.launcher_profile)})
        elif conn.credential_ref:
            var = conn.credential_ref.removeprefix("env:")
            entry["checks"].append({"credential_ref": conn.credential_ref, "variable_present": var in os.environ})
        elif conn.type == ConnectionType.OPENAI_COMPATIBLE:
            entry["checks"].append({"base_url": conn.base_url, "probed": False, "note": "run doctor --live (M4) to probe"})
        if conn.readiness != Readiness.READY:
            blocking += 1
        report["connections"][name] = entry
    report["teams"] = {team.value: dict(config.teams[team].items()) for team in TEAM_ORDER}
    report["isolation"] = {
        "mode": config.runtime.isolation,
        "launcher_profile": config.runtime.launcher_profile,
        "repository_jobs": "blocked (capability_missing) until a launcher profile passes the spike"
        if is_placeholder(config.runtime.launcher_profile)
        else "configured; verify with doctor --live",
    }
    if getattr(args, "project", None):
        context = open_project(Path(args.home), args.project)
        try:
            corrupt = context.store.verify_all()
            report["store"] = {"path": str(context.store.root), "corrupt_objects": corrupt, "database_integrity": context.db.integrity_ok()}
            connections = ConnectionService(context)
            if args.live:
                adapters = AdapterRegistry(context.config, context.root)
                for name, conn in context.config.connections.items():
                    if conn.type == ConnectionType.FIXTURE or conn.placeholders:
                        continue
                    try:
                        result = asyncio.run(adapters.brain(name).probe(live=True))
                    except ServiceError as exc:
                        from sdlc.ports.common import ProbeResult

                        result = ProbeResult(ok=False, live=True, messages=(exc.message,))
                    connections.record_probe(name, result)
                    report["connections"][name]["live_probe"] = {"ok": result.ok, "identity": result.identity, "version": result.version, "messages": list(result.messages)}
            for name in report["connections"]:
                ok, why = connections.dispatchable(name)
                report["connections"][name]["dispatchable"] = ok
                report["connections"][name]["why"] = why
        finally:
            context.close()
    bound = {name for team in config.teams.values() for _, name in team.items()}
    not_ready = [n for n in bound if not report["connections"][n].get("dispatchable", config.connections[n].type == ConnectionType.FIXTURE)]
    report["summary"] = (
        "fixture-only project: no account needed"
        if config.project.fixture_mode
        else (f"bound connections not dispatchable yet: {not_ready}" if not_ready else "all bound connections are dispatchable")
    )
    return report


def cmd_validate(args: argparse.Namespace) -> dict[str, Any]:
    path = Path(args.file)
    definition = args.kind or _guess_definition(path)
    raw = load_json_strict(path.read_text(encoding="utf-8-sig"))
    record = validate_record(raw, definition)
    data: dict[str, Any] = {"file": str(path), "definition": definition, "valid": True}
    if definition == "CycleFixture":
        data["semantics"] = check_fixture(record)  # type: ignore[arg-type]
    return data


def cmd_operations(args: argparse.Namespace) -> dict[str, Any]:
    registry = default_registry()
    ops = registry.for_team(Team(args.team)) if args.team else list(registry)
    return {
        "registry": str(contract_path("operations.json")),
        "count": len(ops),
        "operations": [
            {
                "operation": op.name,
                "version": op.version,
                "team": op.owner_team.value,
                "capabilities": sorted(op.required_capabilities),
                "policy": op.prerequisite_policy,
                "enabled": op.enabled,
            }
            for op in ops
        ],
    }


def cmd_contracts(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "schema": str(contract_path("sdlc.schema.json")),
        "definitions": definition_names(),
        "operations": str(contract_path("operations.json")),
        "ddl": str(contract_path("state.sql")),
    }


# --------------------------------------------------------------------------- M1 commands


def cmd_status(args: argparse.Namespace) -> dict[str, Any]:
    with _project(args) as context:
        handoffs = HandoffService(context)
        scheduler = _scheduler(context, args)
        teams: dict[str, Any] = {}
        for team in TEAM_ORDER:
            baseline, revision = handoffs.baseline(TeamId(team.value))
            view = TeamView(context, TeamId(team.value))
            teams[team.value] = {
                "baseline_bundle_id": baseline,
                "baseline_revision": revision,
                "inbox": view.list_inbox(),
                "owned_artifacts": len(view.list_owned()),
                "tasks": scheduler.describe(TeamId(team.value)),
                "bindings": dict(context.config.teams[team].items()),
            }
        pauses = {k: {"category": v.category, "message": v.message, "reset_at": v.reset_at} for k, v in scheduler.quotas.pauses().items()}
        ready = scheduler.ready()
        billable = [r["task_id"] for r in ready if context.config.connections[r["backend_profile"]].type.value != "fixture"]
        return {
            "project_id": context.project_id,
            "policy": context.config.project.mode,
            "fixture_mode": context.config.project.fixture_mode,
            "teams": teams,
            "quota_pauses": pauses,
            "ready_tasks": [r["task_id"] for r in ready],
            "next_billable_action": f"sdlc stage run would call a paid provider for {billable}" if billable else None,
        }


def cmd_import(args: argparse.Namespace) -> dict[str, Any]:
    with _project(args) as context:
        result = import_material(context, team=TeamId(args.team), path=Path(args.file), purpose=args.purpose)
        return {
            "artifact": _format_ref(result.artifact.ref),
            "extraction_artifact": _format_ref(result.extraction_artifact.ref) if result.extraction_artifact else None,
            "media_type": result.media_type,
            "extraction": result.extraction,
            "needs_input": result.needs_input,
            "warnings": list(result.warnings),
            "delivery_id": result.delivery.delivery_id,
            "receipt": result.receipt.status.value,
            "reason": result.receipt.reason,
            "note": "Original bytes retained; the team reads them only through this delivery's grant.",
        }


def cmd_fixture_load(args: argparse.Namespace) -> dict[str, Any]:
    with _project(args) as context:
        raw = load_json_strict(Path(args.file).read_text(encoding="utf-8-sig"))
        fixture = validate_record(raw, "CycleFixture")
        report = load_fixture(context, fixture)  # type: ignore[arg-type]
        return {"artifacts": report.artifacts, "bundles": report.bundles, "deliveries": report.deliveries, "receipts": report.receipts, "origin": "fixture"}


def cmd_artifacts_list(args: argparse.Namespace) -> dict[str, Any]:
    with _project(args) as context:
        service = ArtifactService(context)
        owner = OwnerTeam(args.owner) if args.owner else None
        items = []
        for artifact in service.list(owner):
            items.append(
                {
                    "ref": _format_ref(artifact.ref),
                    "owner": artifact.owner_team.value,
                    "kind": artifact.kind,
                    "origin": artifact.origin.value,
                    "status": service.status(artifact.ref),
                    "path": artifact.relative_path,
                    "size": artifact.size_bytes,
                }
            )
        return {"count": len(items), "artifacts": items}


def cmd_handoff_publish(args: argparse.Namespace) -> dict[str, Any]:
    """Seal a bundle from a manifest file, or compose it automatically with --auto --target TEAM."""
    with _project(args) as context:
        handoffs = HandoffService(context)
        if args.auto:
            if not args.target:
                raise CommandError("--auto needs --target TEAM")
            refs = handoffs.compose_manifest(TeamId(args.team))
            if not refs:
                raise CommandError("nothing to publish: the team has produced no artifacts yet")
            count = len(repo_list_bundles(context))
            spec = {
                "bundle_id": args.bundle_id or f"b-{args.team}-{count + 1:03d}",
                "cycle_id": "cycle-001",
                "baseline_id": f"baseline-{args.team}-{count + 1:03d}",
                "kind": args.kind,
                "target_team": args.target,
                "summary": args.summary or f"{args.team} handoff to {args.target}: {len(refs)} artifact(s) including forwarded inputs",
                "artifact_refs": [r.to_json_dict() for r in refs],
            }
            if args.dry_run:
                return {"manifest": spec}
        else:
            if not args.manifest:
                raise CommandError("pass --manifest FILE, or --auto --target TEAM")
            spec = load_json_strict(Path(args.manifest).read_text(encoding="utf-8-sig"))
        bundle, ref = handoffs.publish(
            source=OwnerTeam(args.team),
            target=TeamId(spec["target_team"]),
            kind=BundleKind(spec.get("kind", "forward")),
            bundle_id=spec["bundle_id"],
            cycle_id=spec.get("cycle_id", "cycle-001"),
            baseline_id=spec["baseline_id"],
            artifact_refs=[ArtifactRef.model_validate(r) if isinstance(r, dict) else _parse_ref(r) for r in spec["artifact_refs"]],
            summary=spec["summary"],
            input_bundle_refs=[BundleRef.model_validate(r) for r in spec.get("input_bundle_refs", [])],
            decision_refs=spec.get("decision_refs", []),
            next_actions=spec.get("next_actions", []),
        )
        return {
            "bundle": ref.to_json_dict(),
            "route": f"{bundle.source_team.value}->{bundle.target_team.value}",
            "artifacts": [f"{r.artifact_id}@{r.revision}" for r in bundle.artifact_refs],
            "next": f"sdlc handoff deliver --project {context.project_id} --bundle {ref.bundle_id} --no-baseline   (or --expected-target-baseline CURRENT)",
        }


def repo_list_bundles(context: ProjectContext) -> list:
    from sdlc.storage import repositories as repo

    return repo.list_bundles(context.db, context.project_id)


def cmd_handoff_deliver(args: argparse.Namespace) -> dict[str, Any]:
    with _project(args) as context:
        handoffs = HandoffService(context)
        bundle_id, _, sha = args.bundle.partition(":")
        if not sha:
            found = _bundle_digest(context, bundle_id)
            sha = found
        if args.expected_target_baseline is None and not args.no_baseline:
            raise CommandError("pass --expected-target-baseline BUNDLE_ID, or --no-baseline to assert the target has none")
        delivery = handoffs.deliver(bundle_ref=BundleRef(bundle_id=bundle_id, sha256=sha), expected_target_baseline=args.expected_target_baseline)
        receipt = handoffs.receive(delivery.delivery_id) if not args.defer else None
        return {
            "delivery_id": delivery.delivery_id,
            "to_team": delivery.to_team.value,
            "grants": len(delivery.read_grants),
            "receipt": receipt.status.value if receipt else "pending",
            "reason": receipt.reason if receipt else None,
        }


def cmd_handoff_receive(args: argparse.Namespace) -> dict[str, Any]:
    with _project(args) as context:
        handoffs = HandoffService(context)
        receipts = [handoffs.receive(args.delivery)] if args.delivery else handoffs.process_outbox()
        return {"receipts": [r.to_json_dict() for r in receipts]}


def cmd_handoff_inbox(args: argparse.Namespace) -> dict[str, Any]:
    with _project(args) as context:
        view = TeamView(context, TeamId(args.team))
        return {"team": args.team, "inbox": view.list_inbox()}


def cmd_review(args: argparse.Namespace) -> dict[str, Any]:
    """Record a participant decision; ``accept`` makes the delivered bundle the team baseline."""
    with _project(args) as context:
        handoffs = HandoffService(context)
        actor = Actor(ActorKind.PARTICIPANT, args.actor)
        if args.decision == "accept":
            decision_id = handoffs.accept_baseline(
                team=TeamId(args.team),
                delivery_id=args.delivery,
                actor=actor,
                rationale=args.rationale,
                expected_revision=args.expected_revision,
            )
        else:
            decision_id = handoffs.record_decision(
                actor,
                scope="delivery_review",
                refs={"team": args.team, "delivery_id": args.delivery},
                rationale=args.rationale,
                disposition=args.decision,
                team=TeamId(args.team),
            )
        baseline, revision = handoffs.baseline(TeamId(args.team))
        return {"decision_id": decision_id, "disposition": args.decision, "team_baseline": baseline, "baseline_revision": revision}


# --------------------------------------------------------------------------- cycle (continuous mode)


def cmd_cycle_run(args: argparse.Namespace) -> dict[str, Any]:
    """Run stages end to end under policy decisions (project mode must be continuous)."""
    with _project(args) as context:
        runner = CycleRunner(context, _scheduler(context, args))
        objectives = {}
        for item in args.objective or []:
            team, _, text = item.partition("=")
            objectives[team] = text
        report = runner.run(start=TeamId(args.start), end=TeamId(args.end), objectives=objectives, max_rounds=args.max_rounds)
        return report.to_dict()


def cmd_project_mode(args: argparse.Namespace) -> dict[str, Any]:
    with _project(args) as context:
        set_project_mode(context, args.mode)
        return {"project_id": context.project_id, "mode": args.mode}


# --------------------------------------------------------------------------- M4 commands


def cmd_connection_add(args: argparse.Namespace) -> dict[str, Any]:
    """Write a connection (credential references only) into project.yaml."""
    with _project(args) as context:
        conn = ConnectionService(context).add(
            args.name,
            type=ConnectionType(args.type),
            model=args.model,
            executable=args.executable,
            base_url=args.base_url,
            credential_ref=args.credential_ref,
            quota_scope_id=args.quota_scope,
            expected_account_id=args.expected_account,
            replace=args.replace,
        )
        return {
            "connection": args.name,
            "type": conn.type.value,
            "auth": conn.auth.value,
            "readiness": conn.readiness.value,
            "billing_source": _billing_source(conn.type),
            "next": f"sdlc connection bind --project {context.project_id} --role brain --connection {args.name} --all-teams ; then sdlc doctor --project {context.project_id} --live",
        }


def cmd_connection_bind(args: argparse.Namespace) -> dict[str, Any]:
    with _project(args) as context:
        teams = None if args.all_teams else [_Team(t) for t in (args.team or [])]
        if not args.all_teams and not teams:
            raise CommandError("pass --team TEAM (repeatable) or --all-teams")
        config = ConnectionService(context).bind(role=args.role, connection=args.connection, teams=teams)
        return {"role": args.role, "connection": args.connection, "fixture_mode": config.project.fixture_mode, "teams": {t.value: dict(b.items()) for t, b in config.teams.items()}}


def cmd_connection_list(args: argparse.Namespace) -> dict[str, Any]:
    with _project(args) as context:
        return {"connections": ConnectionService(context).list(), "teams": {t.value: dict(b.items()) for t, b in context.config.teams.items()}}


# --------------------------------------------------------------------------- M3 commands


def cmd_stage_propose(args: argparse.Namespace) -> dict[str, Any]:
    """Create the leader's plan_work task for a team; run it with `stage run`, then `stage approve`."""
    with _project(args) as context:
        scheduler = _scheduler(context, args)
        ref = WorkflowService(context, scheduler).propose_stage_plan(TeamId(args.team), objective=args.objective)
        return {"plan_task_id": ref.task_id, "team": ref.team.value, "next": f"sdlc stage run --project {context.project_id} --task {ref.task_id}"}


def cmd_stage_approve(args: argparse.Namespace) -> dict[str, Any]:
    """Admit the task graph a completed plan_work task proposed, under a participant decision."""
    with _project(args) as context:
        scheduler = _scheduler(context, args)
        actor = Actor(ActorKind.PARTICIPANT, args.actor)
        return WorkflowService(context, scheduler).approve_plan(args.plan_task, actor, rationale=args.rationale)


def cmd_diagram_recommend(args: argparse.Namespace) -> dict[str, Any]:
    with _project(args) as context:
        workflow = WorkflowService(context, _scheduler(context, args))
        recs = workflow.recommend_diagrams(TeamId(args.team), _parse_ref(args.source), list(args.delivery or []))
        return {"team": args.team, "recommendations": [{"type": r.diagram_type.value, "question": r.question, "reason": r.reason} for r in recs]}


def cmd_diagram_request(args: argparse.Namespace) -> dict[str, Any]:
    with _project(args) as context:
        workflow = WorkflowService(context, _scheduler(context, args))
        request = DiagramRequest(
            schema_version="1.0",
            project_id=context.project_id,
            team_id=TeamId(args.team),
            requested_by="participant",
            source_refs=[_parse_ref(s) for s in args.source],
            diagram_type=DiagramType(args.type),
            purpose=args.purpose,
            scope_ids=list(args.scope or []),
            preferred_renderer=args.renderer,
        )
        task_id = workflow.request_diagram(request, input_deliveries=list(args.delivery or []))
        return {"task_id": task_id, "next": f"sdlc stage run --project {context.project_id} --task {task_id}"}


def cmd_diagram_status(args: argparse.Namespace) -> dict[str, Any]:
    with _project(args) as context:
        return WorkflowService(context, _scheduler(context, args)).diagram_status(_parse_ref(args.artifact))


def cmd_artifact_revise(args: argparse.Namespace) -> dict[str, Any]:
    """Import a participant-edited copy of a team artifact as its next revision."""
    with _project(args) as context:
        workflow = WorkflowService(context, _scheduler(context, args))
        revised = workflow.revise_artifact(_parse_ref(args.artifact), Path(args.file).read_bytes(), actor_id=args.actor, note=args.note)
        return {"revised": _format_ref(revised.ref), "origin": revised.origin.value, "supersedes": args.artifact, "reconciliation_required": True}


# --------------------------------------------------------------------------- M2 commands


def cmd_stage_plan(args: argparse.Namespace) -> dict[str, Any]:
    """Admit a task-graph proposal for one team from a JSON file (a list of Task records)."""
    with _project(args) as context:
        raw = load_json_strict(Path(args.proposal).read_text(encoding="utf-8-sig"))
        items = raw["tasks"] if isinstance(raw, dict) else raw
        tasks = [validate_record(item, "Task") for item in items]
        admitted = _scheduler(context, args).graphs.admit(TeamId(args.team), tasks)  # type: ignore[arg-type]
        return {"team": admitted.team.value, "task_graph_revision": admitted.task_graph_revision, "tasks": list(admitted.task_ids)}


def cmd_stage_run(args: argparse.Namespace) -> dict[str, Any]:
    """Run ready tasks (or one task). Fixture connections make no billable call."""
    with _project(args) as context:
        scheduler = _scheduler(context, args)
        if args.task:
            scheduler.recover()
            scheduler.ready()  # promotes a task whose retry wait has elapsed
            outcomes = [asyncio.run(scheduler.run_task(args.task))]
        else:
            outcomes = asyncio.run(scheduler.run_ready(TeamId(args.team) if args.team else None))
        return {
            "outcomes": [
                {"task_id": o.task_id, "state": o.state.value, "attempt_id": o.attempt_id, "reason": o.reason, "artifacts": [_format_ref(r) for r in o.artifacts]}
                for o in outcomes
            ],
        }


def cmd_task_list(args: argparse.Namespace) -> dict[str, Any]:
    with _project(args) as context:
        return {"tasks": _scheduler(context, args).describe(TeamId(args.team) if args.team else None)}


def cmd_task_resume(args: argparse.Namespace) -> dict[str, Any]:
    with _project(args) as context:
        return _scheduler(context, args).resume(args.task, dry_run=args.dry_run, quota_restored_by=args.actor if args.quota_restored else None)


def cmd_task_cancel(args: argparse.Namespace) -> dict[str, Any]:
    with _project(args) as context:
        return _scheduler(context, args).cancel(args.task, args.reason)


def cmd_task_rerun(args: argparse.Namespace) -> dict[str, Any]:
    with _project(args) as context:
        return {"successor_task_id": _scheduler(context, args).rerun(args.task)}


def _scheduler(context: ProjectContext, args: argparse.Namespace) -> Scheduler:
    script = FixtureScript.from_file(Path(args.script)) if getattr(args, "script", None) else None
    return Scheduler(context, AdapterRegistry(context.config, context.root, fixture_script=script))


# --------------------------------------------------------------------------- helpers


class _project:
    """Context manager opening the project named by ``--project`` under ``--home``."""

    def __init__(self, args: argparse.Namespace) -> None:
        if not getattr(args, "project", None):
            raise CommandError("pass --project NAME")
        self.context: ProjectContext = open_project(Path(args.home), args.project)

    def __enter__(self) -> ProjectContext:
        return self.context

    def __exit__(self, *exc: object) -> None:
        self.context.close()


def _bundle_digest(context: ProjectContext, bundle_id: str) -> str:
    from sdlc.storage import repositories as repo

    found = repo.get_bundle(context.db, context.project_id, bundle_id)
    if found is None:
        raise CommandError("unknown bundle id", bundle_id=bundle_id)
    return found[1]


def _format_ref(ref: ArtifactRef) -> str:
    return f"{ref.artifact_id}@{ref.revision}:{ref.sha256}"


def _parse_ref(text: str) -> ArtifactRef:
    """``ID@REV:SHA256`` -> ArtifactRef. A bare name is rejected."""
    try:
        ident, rest = text.split("@", 1)
        revision, sha = rest.split(":", 1)
        return ArtifactRef(artifact_id=ident, revision=int(revision), sha256=sha)
    except (ValueError, TypeError) as exc:
        raise CommandError("artifact references must be written ID@REV:SHA256", value=text) from exc


def _example_config_path() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "config" / "project.example.yaml"
        if candidate.exists():
            return candidate
    raise CommandError("config/project.example.yaml not found; pass --template PATH")


def _config_path(args: argparse.Namespace) -> Path:
    if getattr(args, "config", None):
        return Path(args.config)
    if getattr(args, "project", None):
        return Path(args.home) / args.project / "project.yaml"
    raise CommandError("pass --project NAME or --config PATH")


def _load_project_config(args: argparse.Namespace) -> ProjectConfig:
    path = _config_path(args)
    if not path.exists():
        raise CommandError("configuration not found", path=str(path))
    return load_config(path)


def _billing_source(kind: ConnectionType) -> str:
    return {
        ConnectionType.FIXTURE: "none (recorded fixtures)",
        ConnectionType.CODEX_CLI: "native ChatGPT/Codex sign-in, or API key if logged in that way",
        ConnectionType.CLAUDE_CLI: "native Claude account sign-in",
        ConnectionType.OPENAI_RESPONSES: "OpenAI API account (pay per token)",
        ConnectionType.ANTHROPIC_MESSAGES: "Anthropic API account (pay per token)",
        ConnectionType.OPENAI_COMPATIBLE: "local machine or lab server",
        ConnectionType.LOCAL_PROCESS: "none (runs check recipes on this machine)",
    }[kind]


_GUESS = {
    "minimal-cycle": "CycleFixture",
    "quota-task-result": "TaskResult",
    "diagram-request": "DiagramRequest",
    "interrupted-checkpoint": "Checkpoint",
    "stale-delivery": "Delivery",
}


def _guess_definition(path: Path) -> str:
    if path.stem in _GUESS:
        return _GUESS[path.stem]
    raise CommandError("cannot guess the record type; pass --kind", choices=definition_names())


# --------------------------------------------------------------------------- parser


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="print a machine-readable envelope")
    common.add_argument("--home", default=argparse.SUPPRESS, help="directory holding projects (default .sdlc)")

    def subparser(**kw: Any) -> argparse.ArgumentParser:
        return argparse.ArgumentParser(parents=[common], **kw)

    parser = argparse.ArgumentParser(prog="sdlc", description="SDLC Agent Teams command line")
    parser.add_argument("--version", action="version", version=f"sdlc {__version__}")
    parser.add_argument("--json", action="store_true", help="print a machine-readable envelope")
    parser.add_argument("--home", default=str(DEFAULT_HOME), help="directory holding projects (default .sdlc)")
    sub = parser.add_subparsers(dest="command", required=True, parser_class=subparser)
    teams = [t.value for t in TEAM_ORDER]

    # Everyday commands first (sdlc --help lists them in this order).
    friendly.register(sub, subparser, teams)

    p = sub.add_parser("init", help="create a project in fixture mode")
    p.add_argument("name")
    p.add_argument("--policy", choices=["collaborative", "continuous"], default="collaborative")
    p.add_argument("--template", help="alternative example configuration")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("doctor", help="check configuration, connections and stores (local checks)")
    _project_or_config(p)
    p.add_argument("--live", action="store_true", help="probe each non-fixture connection with one small request and record the result")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("validate", help="validate a JSON record against the contract")
    p.add_argument("file")
    p.add_argument("--kind", choices=definition_names())
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("operations", help="list registered team operations")
    p.add_argument("--team", choices=teams)
    p.set_defaults(func=cmd_operations)

    p = sub.add_parser("contracts", help="show where the shipped contracts live")
    p.set_defaults(func=cmd_contracts)

    p = sub.add_parser("status", help="show project status per team")
    p.add_argument("--project", required=True)
    p.add_argument("--script", help=argparse.SUPPRESS)
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("import", help="import a participant document for one team")
    p.add_argument("--project", required=True)
    p.add_argument("--team", required=True, choices=teams)
    p.add_argument("--file", required=True)
    p.add_argument("--purpose", default="participant input")
    p.set_defaults(func=cmd_import)

    p = sub.add_parser("fixture", help="fixture-mode utilities")
    fs = p.add_subparsers(dest="subcommand", required=True, parser_class=subparser)
    q = fs.add_parser("load", help="load a CycleFixture through the real services")
    q.add_argument("--project", required=True)
    q.add_argument("file")
    q.set_defaults(func=cmd_fixture_load)

    p = sub.add_parser("artifacts", help="artifact queries")
    fs = p.add_subparsers(dest="subcommand", required=True, parser_class=subparser)
    q = fs.add_parser("list")
    q.add_argument("--project", required=True)
    q.add_argument("--owner", choices=["participant", *teams])
    q.set_defaults(func=cmd_artifacts_list)

    p = sub.add_parser("handoff", help="publish, deliver and receive handoff bundles")
    fs = p.add_subparsers(dest="subcommand", required=True, parser_class=subparser)
    q = fs.add_parser("publish", help="seal a bundle from a manifest JSON file, or compose it with --auto")
    q.add_argument("--project", required=True)
    q.add_argument("--team", required=True, choices=teams)
    q.add_argument("--manifest")
    q.add_argument("--auto", action="store_true", help="compose the manifest: team outputs + dependency closure + forwarded inputs")
    q.add_argument("--target", choices=teams)
    q.add_argument("--kind", default="forward", choices=["forward", "defect", "change_request", "clarification"])
    q.add_argument("--bundle-id")
    q.add_argument("--summary")
    q.add_argument("--dry-run", action="store_true", help="show the composed manifest without sealing")
    q.set_defaults(func=cmd_handoff_publish)
    q = fs.add_parser("deliver", help="deliver a sealed bundle to its target team")
    q.add_argument("--project", required=True)
    q.add_argument("--bundle", required=True, help="BUNDLE_ID or BUNDLE_ID:SHA256")
    q.add_argument("--expected-target-baseline", help="the target team's current accepted bundle id")
    q.add_argument("--no-baseline", action="store_true", help="assert the target team has no accepted baseline yet")
    q.add_argument("--defer", action="store_true", help="queue in the outbox without receiving")
    q.set_defaults(func=cmd_handoff_deliver)
    q = fs.add_parser("receive", help="process the outbox (or one delivery)")
    q.add_argument("--project", required=True)
    q.add_argument("--delivery")
    q.set_defaults(func=cmd_handoff_receive)
    q = fs.add_parser("inbox", help="accepted deliveries for a team")
    q.add_argument("--project", required=True)
    q.add_argument("--team", required=True, choices=teams)
    q.set_defaults(func=cmd_handoff_inbox)

    p = sub.add_parser("review", help="record a participant decision on a delivered bundle")
    p.add_argument("--project", required=True)
    p.add_argument("--team", required=True, choices=teams)
    p.add_argument("--delivery", required=True)
    p.add_argument("--decision", required=True, choices=["accept", "reject", "changes_requested"])
    p.add_argument("--expected-revision", type=int, help="team baseline revision you reviewed (CAS)")
    p.add_argument("--rationale", default="participant review")
    p.add_argument("--actor", default="participant")
    p.set_defaults(func=cmd_review)

    p = sub.add_parser("stage", help="plan and run a team's task graph")
    fs = p.add_subparsers(dest="subcommand", required=True, parser_class=subparser)
    q = fs.add_parser("plan", help="admit a task-graph proposal (JSON list of Task records)")
    q.add_argument("--project", required=True)
    q.add_argument("--team", required=True, choices=teams)
    q.add_argument("--proposal", required=True)
    q.add_argument("--script", help="fixture script JSON (fixture connections only)")
    q.set_defaults(func=cmd_stage_plan)
    q = fs.add_parser("run", help="run ready tasks")
    q.add_argument("--project", required=True)
    q.add_argument("--team", choices=teams)
    q.add_argument("--task")
    q.add_argument("--script", help="fixture script JSON (fixture connections only)")
    q.set_defaults(func=cmd_stage_run)
    q = fs.add_parser("propose", help="create the leader task that proposes this stage's task graph")
    q.add_argument("--project", required=True)
    q.add_argument("--team", required=True, choices=teams)
    q.add_argument("--objective", required=True)
    q.add_argument("--script", help=argparse.SUPPRESS)
    q.set_defaults(func=cmd_stage_propose)
    q = fs.add_parser("approve", help="admit the proposed task graph (participant decision)")
    q.add_argument("--project", required=True)
    q.add_argument("--plan-task", required=True)
    q.add_argument("--actor", default="participant")
    q.add_argument("--rationale", default="plan approved by participant")
    q.add_argument("--script", help=argparse.SUPPRESS)
    q.set_defaults(func=cmd_stage_approve)

    p = sub.add_parser("cycle", help="run several stages end to end under policy decisions")
    fs = p.add_subparsers(dest="subcommand", required=True, parser_class=subparser)
    q = fs.add_parser("run", help="planning -> ... -> END automatically; stops wherever a person is needed")
    q.add_argument("--project", required=True)
    q.add_argument("--start", default="planning", choices=teams)
    q.add_argument("--end", default="design", choices=teams)
    q.add_argument("--objective", action="append", help="TEAM=text (repeatable); defaults exist per team")
    q.add_argument("--max-rounds", type=int, default=6)
    q.add_argument("--script", help=argparse.SUPPRESS)
    q.set_defaults(func=cmd_cycle_run)

    p = sub.add_parser("project", help="project settings")
    fs = p.add_subparsers(dest="subcommand", required=True, parser_class=subparser)
    q = fs.add_parser("mode", help="collaborative (you decide at each gate) or continuous (policy decides)")
    q.add_argument("--project", required=True)
    q.add_argument("mode", choices=["collaborative", "continuous"])
    q.set_defaults(func=cmd_project_mode)

    p = sub.add_parser("connection", help="add, bind and list LLM/coding connections")
    fs = p.add_subparsers(dest="subcommand", required=True, parser_class=subparser)
    q = fs.add_parser("add", help="add a connection to project.yaml (credential references only)")
    q.add_argument("name")
    q.add_argument("--project", required=True)
    q.add_argument("--type", required=True, choices=[c.value for c in ConnectionType])
    q.add_argument("--model", help="model id or alias, e.g. sonnet / gpt-5 / llama3")
    q.add_argument("--executable", help="CLI executable name or path (claude-cli, codex-cli)")
    q.add_argument("--base-url", help="openai-compatible server URL")
    q.add_argument("--credential-ref", help="env:NAME reference to the API key variable; never the key itself")
    q.add_argument("--quota-scope", help="nonsecret id shared by connections that draw on the same limit")
    q.add_argument("--expected-account", help="account id/email to verify against the client's login status")
    q.add_argument("--replace", action="store_true")
    q.set_defaults(func=cmd_connection_add)
    q = fs.add_parser("bind", help="point a team role (brain/coding/research/execution) at a connection")
    q.add_argument("--project", required=True)
    q.add_argument("--role", required=True, choices=["brain", "coding", "research", "execution"])
    q.add_argument("--connection", required=True)
    q.add_argument("--team", action="append", choices=teams)
    q.add_argument("--all-teams", action="store_true")
    q.set_defaults(func=cmd_connection_bind)
    q = fs.add_parser("list")
    q.add_argument("--project", required=True)
    q.set_defaults(func=cmd_connection_list)

    p = sub.add_parser("diagram", help="recommend, request and inspect diagrams")
    fs = p.add_subparsers(dest="subcommand", required=True, parser_class=subparser)
    q = fs.add_parser("recommend", help="rule-based diagram recommendations from a granted document")
    q.add_argument("--project", required=True)
    q.add_argument("--team", required=True, choices=teams)
    q.add_argument("--source", required=True, help="ID@REV:SHA256")
    q.add_argument("--delivery", action="append", help="accepted delivery id granting the source (repeatable)")
    q.add_argument("--script", help=argparse.SUPPRESS)
    q.set_defaults(func=cmd_diagram_recommend)
    q = fs.add_parser("request", help="create a TEAM.request_diagram task")
    q.add_argument("--project", required=True)
    q.add_argument("--team", required=True, choices=teams)
    q.add_argument("--type", required=True, choices=[d.value for d in DiagramType])
    q.add_argument("--source", required=True, action="append", help="ID@REV:SHA256 (repeatable)")
    q.add_argument("--purpose", required=True)
    q.add_argument("--scope", action="append")
    q.add_argument("--delivery", action="append")
    q.add_argument("--renderer")
    q.add_argument("--script", help=argparse.SUPPRESS)
    q.set_defaults(func=cmd_diagram_request)
    q = fs.add_parser("status", help="syntax check and render status of a diagram_source artifact")
    q.add_argument("--project", required=True)
    q.add_argument("--artifact", required=True, help="ID@REV:SHA256")
    q.add_argument("--script", help=argparse.SUPPRESS)
    q.set_defaults(func=cmd_diagram_status)

    p = sub.add_parser("artifact", help="participant edits of team artifacts")
    fs = p.add_subparsers(dest="subcommand", required=True, parser_class=subparser)
    q = fs.add_parser("revise", help="import an edited copy as the next revision (never overwrites)")
    q.add_argument("--project", required=True)
    q.add_argument("--artifact", required=True, help="ID@REV:SHA256")
    q.add_argument("--file", required=True)
    q.add_argument("--actor", default="participant")
    q.add_argument("--note", default="participant edit")
    q.add_argument("--script", help=argparse.SUPPRESS)
    q.set_defaults(func=cmd_artifact_revise)

    p = sub.add_parser("task", help="inspect, resume, cancel or rerun tasks")
    fs = p.add_subparsers(dest="subcommand", required=True, parser_class=subparser)
    q = fs.add_parser("list")
    q.add_argument("--project", required=True)
    q.add_argument("--team", choices=teams)
    q.set_defaults(func=cmd_task_list)
    q = fs.add_parser("resume", help="return a paused/needs_input task to pending")
    q.add_argument("task")
    q.add_argument("--project", required=True)
    q.add_argument("--dry-run", action="store_true", help="explain what would continue; no state change, no model call")
    q.add_argument("--quota-restored", action="store_true", help="declare that the paused connection's limit has reset")
    q.add_argument("--actor", default="participant")
    q.set_defaults(func=cmd_task_resume)
    q = fs.add_parser("cancel")
    q.add_argument("task")
    q.add_argument("--project", required=True)
    q.add_argument("--reason", default="cancelled by participant")
    q.set_defaults(func=cmd_task_cancel)
    q = fs.add_parser("rerun", help="create a successor task for a terminal task")
    q.add_argument("task")
    q.add_argument("--project", required=True)
    q.set_defaults(func=cmd_task_rerun)
    return parser


def _project_or_config(p: argparse.ArgumentParser) -> None:
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--project", help="project name under --home")
    group.add_argument("--config", help="explicit path to project.yaml")


def main(argv: list[str] | None = None) -> int:
    # Windows consoles often default to a legacy code page (cp949, cp1252) that cannot
    # encode arbitrary artifact text; UTF-8 output keeps --json envelopes parseable.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass
    parser = build_parser()
    args = parser.parse_args(argv)
    errors: list[dict[str, Any]] = []
    data: dict[str, Any] = {}
    try:
        data = args.func(args)
    except (CommandError, friendly.CommandError) as exc:
        errors.append(exc.payload)
    except ServiceError as exc:
        errors.append(exc.to_dict())
    except (OSError, ValueError, KeyError) as exc:
        errors.append({"code": type(exc).__name__, "message": str(exc), "details": {}})
    success = not errors
    command = args.command + (f" {args.subcommand}" if getattr(args, "subcommand", None) else "")
    if args.json:
        print(json.dumps({"command": command, "success": success, "data": data, "errors": errors}, ensure_ascii=False, indent=2, default=str))
    else:
        _render(command, data, errors)
    return 0 if success else 1


def _render(command: str, data: dict[str, Any], errors: list[dict[str, Any]]) -> None:
    if errors:
        for err in errors:
            print(f"error [{err['code']}]: {err['message']}", file=sys.stderr)
            for key, value in err.get("details", {}).items():
                print(f"  {key}: {json.dumps(value, ensure_ascii=False, default=str)[:400]}", file=sys.stderr)
        return
    if command in ("run", "continue") and "stages" in data:
        for stage in data["stages"]:
            print(f"[{stage['status']}] {stage['stage']}")
            for line in stage["what_happened"]:
                print(f"    {line}")
            if stage.get("why_stopped"):
                print(f"    stopped: {stage['why_stopped']}")
        if data.get("resumed"):
            print(f"resumed: {', '.join(data['resumed'])}")
        print(f"\nwhat to do: {data.get('what_to_do', '')}")
        return
    if command == "show" and "stages" in data and "mode" in data:
        print(f"project {data['project']}  ({data['mode']})")
        for stage in data["stages"]:
            line = f"  {stage['stage']:<28} {stage['state']:<18} documents: {stage['documents']:<3} brain: {stage['brain']}"
            print(line)
            for b in stage["blocked"]:
                print(f"      blocked: {b}")
        if data["quota_pauses"]:
            print(f"  paused connections: {', '.join(data['quota_pauses'])}")
        for name, why in data["connections"].items():
            print(f"  {name}: {why}")
        print(f"\nwhat to do: {data['what_to_do']}")
        return
    if command == "operations":
        for op in data["operations"]:
            flag = "enabled" if op["enabled"] else "registered"
            print(f"{op['operation']:<40} v{op['version']}  {flag:<10} {', '.join(op['capabilities'])}")
        print(f"{data['count']} operation(s)")
        return
    print(yaml.safe_dump(data, sort_keys=False, allow_unicode=True, default_flow_style=False).rstrip())


if __name__ == "__main__":
    sys.exit(main())
