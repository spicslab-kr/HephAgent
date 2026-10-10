"""Team-scoped read view (handoff docs/03 s3): the only way an agent sees content.

A :class:`TeamView` can read

* artifacts its own team owns (team-internal reuse), and
* artifacts granted by deliveries that were *accepted* for this team and that the task
  explicitly declares in ``input_deliveries``.

Everything else is denied *before* any bytes are touched: other teams' drafts, the
participant's imports not delivered to this team, provenance links in ``depends_on``,
newer revisions of a granted artifact, and guessed ids. Every denial is journaled so
canary tests can assert it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sdlc.application.artifacts import ArtifactService
from sdlc.application.projects import ProjectContext
from sdlc.artifacts.store import safe_relative_path
from sdlc.domain.errors import DeniedScope
from sdlc.domain.models import Artifact, ArtifactRef, OwnerTeam, TeamId
from sdlc.storage import repositories as repo
from sdlc.verification.fixtures import RefKey


@dataclass(frozen=True, slots=True)
class GrantedInput:
    ref: ArtifactRef
    artifact: Artifact
    via_delivery: str | None  # None = same-team ownership


class TeamView:
    def __init__(self, context: ProjectContext, team: TeamId) -> None:
        self.ctx = context
        self.team = team
        self.artifacts = ArtifactService(context)

    # ------------------------------------------------------------------ grants
    def grants(self, input_deliveries: list[str]) -> dict[RefKey, str]:
        """Exact refs readable through the declared, accepted deliveries of this team."""
        accepted = {d.delivery_id: d for d in repo.accepted_deliveries(self.ctx.db, self.ctx.project_id, self.team.value)}
        result: dict[RefKey, str] = {}
        for delivery_id in input_deliveries:
            delivery = accepted.get(delivery_id)
            if delivery is None:
                self._deny("delivery is not accepted for this team", delivery_id=delivery_id)
            for ref in delivery.read_grants:
                result[ref.key] = delivery_id
        return result

    def authorize(self, ref: ArtifactRef, input_deliveries: list[str]) -> GrantedInput:
        """Return the artifact if (and only if) this team may read exactly ``ref``."""
        artifact = repo.get_artifact_by_ref(self.ctx.db, self.ctx.project_id, ref)
        if artifact is None:
            # Do not reveal whether the id exists at a different revision/digest.
            self._deny("no grant for this exact artifact reference", ref=ref.to_json_dict())
        if artifact.owner_team.value == self.team.value:
            return GrantedInput(ref=ref, artifact=artifact, via_delivery=None)
        granted = self.grants(input_deliveries)
        if ref.key not in granted:
            self._deny("no grant for this exact artifact reference", ref=ref.to_json_dict(), owner=artifact.owner_team.value)
        return GrantedInput(ref=ref, artifact=artifact, via_delivery=granted[ref.key])

    def read_text(self, ref: ArtifactRef, input_deliveries: list[str], encoding: str = "utf-8") -> str:
        return self.read_bytes(ref, input_deliveries).decode(encoding)

    def read_bytes(self, ref: ArtifactRef, input_deliveries: list[str]) -> bytes:
        granted = self.authorize(ref, input_deliveries)
        data = self.artifacts.read_bytes(granted.ref)
        self._journal("view.read", ref=ref.to_json_dict(), via=granted.via_delivery)
        return data

    def materialize(self, refs: list[ArtifactRef], input_deliveries: list[str], destination: Path) -> list[Path]:
        """Copy granted inputs into ``destination`` under their package-relative labels."""
        written: list[Path] = []
        destination = Path(destination)
        for ref in refs:
            granted = self.authorize(ref, input_deliveries)
            relative = safe_relative_path(granted.artifact.relative_path)
            target = destination / Path(*relative.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(self.artifacts.read_bytes(ref))
            written.append(target)
            self._journal("view.materialized", ref=ref.to_json_dict(), path=str(relative), via=granted.via_delivery)
        return written

    def list_owned(self) -> list[Artifact]:
        return repo.list_artifacts(self.ctx.db, self.ctx.project_id, self.team.value)

    def list_inbox(self) -> list[dict]:
        return [
            {"delivery_id": d.delivery_id, "from": d.from_team.value, "bundle": d.bundle_ref.to_json_dict(), "grants": len(d.read_grants)}
            for d in repo.accepted_deliveries(self.ctx.db, self.ctx.project_id, self.team.value)
        ]

    # ------------------------------------------------------------------ journal
    def _deny(self, message: str, **details: object) -> None:
        self._journal("view.denied", message=message, **details)
        raise DeniedScope(message, team=self.team.value, **details)

    def _journal(self, event_type: str, **payload: object) -> None:
        if self.ctx.db.connection.in_transaction:
            return
        with self.ctx.db.transaction():
            repo.insert_event(
                self.ctx.db,
                self.ctx.project_id,
                self.ctx.ids("event"),
                event_type,
                {"team": self.team.value, **payload},
                self.ctx.clock(),
                team_id=self.team.value,
            )


__all__ = ["TeamView", "GrantedInput", "OwnerTeam"]
