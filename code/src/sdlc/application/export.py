"""Baseline export and verification (handoff docs/02 FR-021, docs/08 AT-019).

``export_release`` writes exactly the artifacts a release manifest pins, plus their
``depends_on`` closure and the Testing team's readable report, into a folder, together
with ``EXPORT_MANIFEST.json`` (ids, revisions, SHA-256, owner, kind, origin, lifecycle
status, evidence labels). Nothing outside that closure is written: no drafts of other
work, no configuration, no credentials, no provider sessions (the runtime never stores
those anyway). ``verify_export`` recomputes every hash and reports tampering or loss.
Bytes round-trip exactly, so Korean and English documents survive untouched.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from sdlc.application.artifacts import ArtifactService
from sdlc.application.projects import ProjectContext
from sdlc.application.release import ReleaseService
from sdlc.artifacts.store import safe_relative_path
from sdlc.domain.models import Artifact, ArtifactRef, OwnerTeam
from sdlc.storage import repositories as repo

MANIFEST_NAME = "EXPORT_MANIFEST.json"


@dataclass
class ExportResult:
    folder: Path
    release_id: str
    files: list[str] = field(default_factory=list)
    excluded: int = 0


@dataclass
class VerifyResult:
    ok: bool
    checked: int
    missing: list[str] = field(default_factory=list)
    tampered: list[str] = field(default_factory=list)
    extra: list[str] = field(default_factory=list)


def _closure(context: ProjectContext, roots: list[ArtifactRef]) -> list[Artifact]:
    artifacts = ArtifactService(context)
    seen: dict[tuple, Artifact] = {}
    stack = list(roots)
    while stack:
        ref = stack.pop()
        if ref.key in seen:
            continue
        artifact = artifacts.get(ref)
        seen[ref.key] = artifact
        stack.extend(artifact.depends_on)
    return sorted(seen.values(), key=lambda a: (a.owner_team.value, a.relative_path, a.revision))


def export_release(context: ProjectContext, destination: Path, release_id: str | None = None) -> ExportResult:
    releases = ReleaseService(context)
    manifest_artifact, manifest = releases.manifest(release_id)
    artifacts = ArtifactService(context)
    roots = [manifest_artifact.ref] + [ArtifactRef.model_validate(manifest[k]) for k in ("source_ref", "requirements_ref", "design_ref", "runbook_ref", "rollback_ref")]
    roots += [ArtifactRef.model_validate(r) for r in manifest["bench_refs"] + manifest["execution_refs"]]
    selected = _closure(context, roots)
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    entries = []
    result = ExportResult(folder=destination, release_id=manifest["release_id"])
    for artifact in selected:
        relative = safe_relative_path(artifact.relative_path)
        target = destination / artifact.owner_team.value / Path(*relative.parts)
        if target.exists():  # two revisions at one path: keep both, the later one suffixed
            target = target.with_name(f"{target.stem}@{artifact.revision}{target.suffix}")
        target.parent.mkdir(parents=True, exist_ok=True)
        data = artifacts.read_bytes(artifact.ref)
        target.write_bytes(data)
        labels = []
        if artifact.origin.value != "model" and artifact.origin.value != "participant":
            labels.append(f"origin:{artifact.origin.value}")
        if artifact.origin.value == "fixture":
            labels.append("fixture-evidence: cannot satisfy release gates")
        if context.config.runtime.isolation != "strict" and artifact.kind in ("execution_record", "source_snapshot", "test_bench", "check_report", "execution_report"):
            labels.append("nonconforming_isolation")
        entries.append({
            "path": str(target.relative_to(destination)).replace("\\", "/"), "artifact_id": artifact.artifact_id, "revision": artifact.revision, "sha256": artifact.sha256,
            "bytes": len(data), "owner": artifact.owner_team.value, "kind": artifact.kind, "media_type": artifact.media_type, "origin": artifact.origin.value,
            "status": artifacts.status(artifact.ref), "created_by_task": artifact.created_by_task, "labels": labels,
        })
        result.files.append(entries[-1]["path"])
    total = len(repo.list_artifacts(context.db, context.project_id))
    result.excluded = total - len(selected)
    export_manifest = {
        "schema_version": "1.0", "project_id": context.project_id, "release_id": manifest["release_id"], "exported_at": context.clock(),
        "release_manifest": manifest_artifact.ref.to_json_dict(), "artifact_count": len(entries), "excluded_count": result.excluded,
        "note": "exact selected revisions only; drafts outside the release closure, configuration, credentials and provider sessions are never exported",
        "artifacts": entries,
    }
    (destination / MANIFEST_NAME).write_text(json.dumps(export_manifest, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    with context.db.transaction():
        repo.insert_event(context.db, context.project_id, context.ids("event"), "baseline.exported",
                          {"release_id": manifest["release_id"], "folder": str(destination), "artifacts": len(entries)}, context.clock(), team_id="testing")
    return result


def verify_export(folder: Path) -> VerifyResult:
    folder = Path(folder)
    manifest_path = folder / MANIFEST_NAME
    if not manifest_path.exists():
        return VerifyResult(ok=False, checked=0, missing=[MANIFEST_NAME])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    result = VerifyResult(ok=True, checked=0)
    listed = set()
    for entry in manifest["artifacts"]:
        path = folder / Path(*entry["path"].split("/"))
        listed.add(entry["path"])
        if not path.exists():
            result.missing.append(entry["path"])
            continue
        result.checked += 1
        if hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
            result.tampered.append(entry["path"])
    for path in folder.rglob("*"):
        if path.is_file() and path.name != MANIFEST_NAME:
            rel = str(path.relative_to(folder)).replace("\\", "/")
            if rel not in listed:
                result.extra.append(rel)
    result.ok = not (result.missing or result.tampered)
    return result


__all__ = ["ExportResult", "VerifyResult", "export_release", "verify_export", "OwnerTeam"]
