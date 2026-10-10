"""Artifact registration and lifecycle projections (handoff docs/03 s4, docs/07 s2).

Registration order: bytes become durable in the content store first, then one SQLite
transaction records the immutable metadata, the initial ``draft`` status and the
journal event. Revision numbers advance only within an artifact id. Nothing is ever
edited in place.
"""

from __future__ import annotations

from sdlc.application.projects import ProjectContext
from sdlc.artifacts.store import safe_relative_path
from sdlc.domain.errors import Conflict, CorruptArtifact, DeniedScope, MissingInput
from sdlc.domain.models import Artifact, ArtifactRef, Origin, OwnerTeam
from sdlc.storage import repositories as repo

ARTIFACT_STATUSES = ("draft", "reviewable", "accepted", "superseded", "stale")


class ArtifactService:
    def __init__(self, context: ProjectContext) -> None:
        self.ctx = context

    def register(
        self,
        *,
        owner: OwnerTeam,
        artifact_id: str,
        kind: str,
        media_type: str,
        relative_path: str,
        data: bytes,
        origin: Origin,
        depends_on: list[ArtifactRef] | None = None,
        created_by_task: str | None = None,
        created_at: str | None = None,
        actor_is_ingress: bool = False,
    ) -> Artifact:
        """Store ``data`` and record a new revision owned by ``owner``.

        ``owner == participant`` is legal only through validated ingress
        (``actor_is_ingress=True``); a team can never impersonate the participant.
        """
        if owner == OwnerTeam.PARTICIPANT and not actor_is_ingress:
            raise DeniedScope("participant ownership is only legal through validated ingress")
        safe_relative_path(relative_path)
        depends_on = depends_on or []
        for ref in depends_on:
            if repo.get_artifact_by_ref(self.ctx.db, self.ctx.project_id, ref) is None:
                raise MissingInput("depends_on references an unknown artifact revision", ref=ref.to_json_dict())
        sha256, size = self.ctx.store.put(data)
        now = created_at or self.ctx.clock()
        with self.ctx.db.transaction():
            revision = repo.next_artifact_revision(self.ctx.db, self.ctx.project_id, artifact_id)
            artifact = Artifact(
                schema_version="1.0",
                project_id=self.ctx.project_id,
                artifact_id=artifact_id,
                owner_team=owner,
                revision=revision,
                kind=kind,
                media_type=media_type,
                relative_path=relative_path,
                sha256=sha256,
                size_bytes=size,
                origin=origin,
                created_by_task=created_by_task,
                created_at=now,
                depends_on=depends_on,
            )
            event_id = self.ctx.ids("event")
            repo.insert_event(
                self.ctx.db,
                self.ctx.project_id,
                event_id,
                "artifact.registered",
                artifact.to_json_dict(),
                self.ctx.clock(),
                team_id=None if owner == OwnerTeam.PARTICIPANT else owner.value,
            )
            repo.insert_artifact(self.ctx.db, artifact, storage_key=sha256, status_event_id=event_id, now=self.ctx.clock())
        return artifact

    def get(self, ref: ArtifactRef) -> Artifact:
        artifact = repo.get_artifact_by_ref(self.ctx.db, self.ctx.project_id, ref)
        if artifact is None:
            raise MissingInput("no artifact matches this exact reference", ref=ref.to_json_dict())
        return artifact

    def read_bytes(self, ref: ArtifactRef) -> bytes:
        """Verified bytes for an exact reference. Callers must authorise first (see views)."""
        artifact = self.get(ref)
        data = self.ctx.store.get(artifact.sha256)
        if len(data) != artifact.size_bytes:
            raise CorruptArtifact("stored size differs from metadata", ref=ref.to_json_dict())
        return data

    def status(self, ref: ArtifactRef) -> str:
        self.get(ref)
        status, _ = repo.get_artifact_status(self.ctx.db, self.ctx.project_id, ref.artifact_id, ref.revision)
        return status

    def set_status(self, ref: ArtifactRef, status: str, *, reason: str) -> None:
        """Move the lifecycle projection; the payload and metadata never change."""
        if status not in ARTIFACT_STATUSES:
            raise Conflict("unknown artifact status", status=status)
        artifact = self.get(ref)
        with self.ctx.db.transaction():
            current, revision = repo.get_artifact_status(self.ctx.db, self.ctx.project_id, ref.artifact_id, ref.revision)
            event_id = self.ctx.ids("event")
            repo.insert_event(
                self.ctx.db,
                self.ctx.project_id,
                event_id,
                "artifact.status",
                {"ref": ref.to_json_dict(), "from": current, "to": status, "reason": reason},
                self.ctx.clock(),
                team_id=None if artifact.owner_team == OwnerTeam.PARTICIPANT else artifact.owner_team.value,
            )
            repo.cas_artifact_status(self.ctx.db, self.ctx.project_id, ref.artifact_id, ref.revision, revision, status, event_id, self.ctx.clock())

    def list(self, owner: OwnerTeam | None = None) -> list[Artifact]:
        return repo.list_artifacts(self.ctx.db, self.ctx.project_id, owner.value if owner else None)
