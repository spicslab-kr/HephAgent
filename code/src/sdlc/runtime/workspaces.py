"""Task workspaces for coding work (handoff docs/06 s7, docs/04 s5).

Each coding task gets a private directory under ``<project>/workspaces/<team>/<task>/``
built from the team's immutable baseline snapshot (or empty for a first implementation).
Granted documents are materialised read-only under ``.sdlc-inputs/`` so a coding agent
can read the design without those files becoming part of the candidate. After the job,
``capture`` records a new ``source_snapshot`` artifact plus a ``patch`` artifact and the
list of changed files; the workspace is never the authority, the snapshot is.

Writer discipline: one workspace lease per task; a second writer is refused.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from sdlc.application.artifacts import ArtifactService
from sdlc.application.projects import ProjectContext
from sdlc.application.views import TeamView
from sdlc.artifacts.snapshots import SnapshotManifest, create_snapshot, diff_trees, extract_snapshot, scan_tree
from sdlc.domain.errors import Conflict, MissingInput
from sdlc.domain.models import Artifact, ArtifactRef, Origin, OwnerTeam, Task, TeamId
from sdlc.runtime.leases import Lease, LeaseManager

INPUTS_DIR = ".sdlc-inputs"

#: Whose pinned snapshot a team's workspace starts from. A Testing bench is always built on
#: the *delivered* candidate (its own earlier bench holds a stale copy of the source); every
#: other team continues its own newest candidate (a repair starts from what it shipped).
CONTINUES_OWN_CANDIDATE: dict[str, bool] = {"testing": False}


def pinned_snapshots(context: ProjectContext, task: Task, *, prefer_own: bool) -> list[ArtifactRef]:
    """The task's pinned ``source_snapshot`` inputs, best first.

    Freshness wins: a snapshot granted by a *later* accepted delivery (the corrected
    candidate) beats one forwarded from an earlier package. Within one delivery the
    team preference decides: a repairing team continues its own candidate
    (``prefer_own``), a testing team takes the delivered one.
    """
    from sdlc.storage import repositories as repo

    artifacts = ArtifactService(context)
    view = TeamView(context, task.team_id)
    order = {d.delivery_id: i for i, d in enumerate(repo.accepted_deliveries(context.db, context.project_id, task.team_id.value))}
    ranked = []
    for position, ref in enumerate(task.input_refs):
        artifact = artifacts.get(ref)
        if artifact.kind != "source_snapshot" or artifacts.status(ref) == "superseded":
            continue
        own = artifact.owner_team.value == task.team_id.value
        try:
            via = view.authorize(ref, list(task.input_deliveries)).via_delivery
            recency = order.get(via, -1)
        except Exception:  # noqa: BLE001 - a same-team output is not granted by a delivery
            recency = -1
        # own outputs are not granted through a delivery; when the team repairs its own
        # candidate they rank first, otherwise the newest delivered snapshot wins
        ranked.append(((0 if own and prefer_own else 1, -recency, 0 if own == prefer_own else 1), str(artifact.created_at), ref))
    ranked.sort(key=lambda item: item[1], reverse=True)  # newest artifact first ...
    ranked.sort(key=lambda item: item[0])  # ... then the stable primary order
    return [ref for _, _, ref in ranked]


def remove_tree(path: Path, *, ignore_errors: bool = False) -> None:
    """``shutil.rmtree`` that copes with read-only files (git objects on Windows)."""

    def _retry(func, target, exc_info):  # noqa: ANN001 - shutil callback signature
        import os
        import stat

        try:
            os.chmod(target, stat.S_IWRITE)
            func(target)
        except OSError:
            if not ignore_errors:
                raise

    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=_retry)
    else:  # pragma: no cover - older interpreters
        shutil.rmtree(path, onerror=_retry)


@dataclass
class Workspace:
    task_id: str
    team: TeamId
    root: Path
    baseline_ref: ArtifactRef | None
    baseline_manifest: SnapshotManifest
    lease: Lease
    git_initialized: bool


class WorkspaceManager:
    def __init__(self, context: ProjectContext, owner_id: str) -> None:
        self.ctx = context
        self.artifacts = ArtifactService(context)
        self.leases = LeaseManager(context, owner_id)

    def path_for(self, task: Task) -> Path:
        # Absolute: coding clients receive this as both cwd and ``-C``; a relative path would resolve twice.
        return (self.ctx.workspaces / task.team_id.value / task.task_id).resolve()

    # ------------------------------------------------------------------ prepare
    def prepare(self, task: Task, *, lease_seconds: int = 900, keep_existing: bool = False) -> Workspace:
        """Create (or reuse, if ``keep_existing``) the task workspace from the pinned baseline."""
        root = self.path_for(task)
        lease = self.leases.acquire("workspace", f"{self.ctx.project_id}/{task.team_id.value}/{task.task_id}", lease_seconds)
        try:
            baseline_ref = self._baseline_ref(task)
            if root.exists() and not keep_existing:
                remove_tree(root)
            root.mkdir(parents=True, exist_ok=True)
            manifest = SnapshotManifest()
            if baseline_ref is not None and not keep_existing:
                manifest = extract_snapshot(self.artifacts.read_bytes(baseline_ref), root)
            elif baseline_ref is not None:
                manifest = scan_tree(root)
            self._materialize_inputs(task, root)
            git_ok = self._git_init(root)
        except BaseException:
            # Never leave the workspace lease orphaned: the next attempt (possibly another
            # process) would otherwise be refused until the TTL expires.
            self.leases.release(lease)
            raise
        return Workspace(task.task_id, task.team_id, root, baseline_ref, manifest, lease, git_ok)

    def _baseline_ref(self, task: Task) -> ArtifactRef | None:
        """The candidate to start from: a pinned source_snapshot input, else the latest
        source_snapshot produced by a completed same-team dependency task (bound now and
        journaled, because it did not exist when the task was planned)."""
        from sdlc.runtime.graphs import TaskGraphService
        from sdlc.storage import repositories as repo

        # Newest same-team work first: a snapshot produced by a completed dependency (the
        # bench just built, the code just written) is the tree to continue from; the pinned
        # snapshot (the delivered candidate) is the starting point when nothing newer exists.
        chosen = None
        for ref in TaskGraphService(self.ctx).dependency_outputs(task.task_id):
            if self.artifacts.get(ref).kind == "source_snapshot":
                chosen = ref  # last completed dependency wins
        if chosen is None:
            ranked = pinned_snapshots(self.ctx, task, prefer_own=CONTINUES_OWN_CANDIDATE.get(task.team_id.value, True))
            if ranked:
                return ranked[0]
        if chosen is not None:
            with self.ctx.db.transaction():
                repo.insert_event(self.ctx.db, self.ctx.project_id, self.ctx.ids("event"), "task.inputs_bound",
                                  {"task_id": task.task_id, "source_snapshot": chosen.to_json_dict(), "reason": "dependency output bound at start"},
                                  self.ctx.clock(), team_id=task.team_id.value, task_id=task.task_id)
        return chosen

    def _materialize_inputs(self, task: Task, root: Path) -> list[Path]:
        view = TeamView(self.ctx, task.team_id)
        inputs = root / INPUTS_DIR
        if inputs.exists():
            remove_tree(inputs)
        refs = [r for r in task.input_refs if self.artifacts.get(r).kind != "source_snapshot" and self.artifacts.status(r) != "superseded"]
        written = view.materialize(refs, list(task.input_deliveries), inputs) if refs else []
        (inputs / "README.txt").parent.mkdir(parents=True, exist_ok=True)
        (inputs / "README.txt").write_text(
            "Read-only inputs granted to this task (design, requirements, plan). Do not edit; they are not part of the candidate.\n",
            encoding="utf-8",
        )
        return written

    @staticmethod
    def _git_init(root: Path) -> bool:
        if shutil.which("git") is None:
            return False
        if (root / ".git").exists():
            return True
        try:
            subprocess.run(["git", "init", "-q"], cwd=root, check=True, capture_output=True, timeout=60)
            subprocess.run(["git", "-c", "user.email=sdlc@local", "-c", "user.name=sdlc", "add", "-A"], cwd=root, check=True, capture_output=True, timeout=60)
            subprocess.run(["git", "-c", "user.email=sdlc@local", "-c", "user.name=sdlc", "commit", "-q", "-m", "baseline", "--allow-empty"], cwd=root, check=True, capture_output=True, timeout=60)
            return True
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            return False

    # ------------------------------------------------------------------ capture
    def capture(self, workspace: Workspace, task: Task, *, label: str = "candidate") -> dict[str, Artifact]:
        """Snapshot the tree as a new source_snapshot artifact and record the patch."""
        if not self.leases.is_current(workspace.lease):
            raise Conflict("workspace lease lost; another writer may own this workspace", task_id=task.task_id)
        archive, manifest = create_snapshot(workspace.root)
        before_root = None
        if workspace.baseline_ref is not None:
            before_root = workspace.root.parent / f"{task.task_id}.baseline"
            if before_root.exists():
                remove_tree(before_root)
            extract_snapshot(self.artifacts.read_bytes(workspace.baseline_ref), before_root)
        diff = diff_trees(before_root, workspace.baseline_manifest, workspace.root, manifest)
        if before_root is not None:
            remove_tree(before_root, ignore_errors=True)
        owner = OwnerTeam(task.team_id.value)
        depends = [workspace.baseline_ref] if workspace.baseline_ref else []
        snapshot = self.artifacts.register(
            owner=owner, artifact_id=f"{task.task_id}-{label}-snapshot", kind="source_snapshot", media_type="application/zip",
            relative_path=f"snapshots/{task.task_id}/{label}.zip", data=archive, origin=Origin.TOOL, depends_on=depends, created_by_task=task.task_id,
        )
        patch = self.artifacts.register(
            owner=owner, artifact_id=f"{task.task_id}-{label}-patch", kind="patch", media_type="text/x-diff",
            relative_path=f"snapshots/{task.task_id}/{label}.patch", data=diff.patch.encode("utf-8"), origin=Origin.TOOL, depends_on=[snapshot.ref], created_by_task=task.task_id,
        )
        changes = self.artifacts.register(
            owner=owner, artifact_id=f"{task.task_id}-{label}-changes", kind="change_summary", media_type="application/json",
            relative_path=f"snapshots/{task.task_id}/{label}-changes.json", data=json.dumps({**diff.to_dict(), "files": len(manifest.files), "baseline": workspace.baseline_ref.to_json_dict() if workspace.baseline_ref else None}, indent=2).encode("utf-8"),
            origin=Origin.TOOL, depends_on=[snapshot.ref], created_by_task=task.task_id,
        )
        return {"snapshot": snapshot, "patch": patch, "changes": changes}

    def release(self, workspace: Workspace) -> None:
        self.leases.release(workspace.lease)

    # ------------------------------------------------------------------ take-over
    def export_for_takeover(self, task: Task, destination: Path) -> Path:
        """Copy the task's workspace (without inputs) to a folder the participant can edit."""
        root = self.path_for(task)
        if not root.exists():
            raise MissingInput("the task has no workspace yet", task_id=task.task_id)
        destination = Path(destination)
        if destination.exists():
            raise Conflict("destination already exists; choose an empty folder", path=str(destination))
        shutil.copytree(root, destination, ignore=shutil.ignore_patterns(".git", INPUTS_DIR, "__pycache__"))
        return destination
