"""Publish, deliver and receive handoff bundles (handoff docs/06 s5, docs/07 s3).

* ``publish``: seal an exhaustive manifest. The publishing team must own every artifact
  or hold an accepted inbound grant for it (forwarding). The manifest must contain the
  dependency closure of its artifacts. Same bundle id + same bytes is idempotent;
  different bytes under the same id is a conflict.
* ``deliver``: create the immutable envelope whose ``read_grants`` equal the manifest
  exactly, write a ``pending`` receipt and an outbox entry, all in one transaction.
* ``receive``: the outbox worker step. Verifies digest, route and target baseline
  (compare-and-swap against the target team's accepted bundle id), then appends an
  ``accepted`` or ``stale``/``rejected`` receipt event and updates the projection.
  Re-receiving an accepted delivery returns the existing receipt and creates nothing.
* ``accept_baseline``: an engineering decision (participant or policy actor) that makes
  a received bundle the team's current baseline, with CAS on the baseline revision.
  Transport acceptance never changes a baseline by itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from sdlc.application.artifacts import ArtifactService
from sdlc.application.projects import ProjectContext
from sdlc.domain.errors import Conflict, DeniedScope, InvalidSchema, MissingInput, StaleRevision
from sdlc.domain.models import (
    ArtifactRef,
    BundleKind,
    BundleRef,
    Delivery,
    DeliveryReceipt,
    Finding,
    HandoffBundle,
    OwnerTeam,
    ReceiptStatus,
    TeamId,
    TraceLink,
)
from sdlc.domain.teams import Principal, Team, route_allowed
from sdlc.storage import repositories as repo
from sdlc.verification.canonical import digest
from sdlc.verification.fixtures import RefKey


class ActorKind(StrEnum):
    PARTICIPANT = "participant"
    POLICY = "policy"


@dataclass(frozen=True, slots=True)
class Actor:
    """Who makes an engineering decision. A model is never an actor."""

    kind: ActorKind
    actor_id: str
    policy_revision: str | None = None


class HandoffService:
    def __init__(self, context: ProjectContext) -> None:
        self.ctx = context
        self.artifacts = ArtifactService(context)

    # ------------------------------------------------------------------ publish
    def publish(
        self,
        *,
        source: OwnerTeam,
        target: TeamId,
        kind: BundleKind,
        bundle_id: str,
        cycle_id: str,
        baseline_id: str,
        artifact_refs: list[ArtifactRef],
        summary: str,
        input_bundle_refs: list[BundleRef] | None = None,
        trace_links: list[TraceLink] | None = None,
        open_findings: list[Finding] | None = None,
        decision_refs: list[str] | None = None,
        next_actions: list[str] | None = None,
        created_at: str | None = None,
        actor_is_ingress: bool = False,
    ) -> tuple[HandoffBundle, BundleRef]:
        if source == OwnerTeam.PARTICIPANT and not actor_is_ingress:
            raise DeniedScope("only validated ingress may publish as the participant")
        if not route_allowed(kind.value, Principal(source.value), Team(target.value)):
            raise DeniedScope("route not registered for this bundle kind", kind=kind.value, route=f"{source.value}->{target.value}")
        bundle = HandoffBundle(
            schema_version="1.0",
            bundle_id=bundle_id,
            project_id=self.ctx.project_id,
            cycle_id=cycle_id,
            source_team=source,
            target_team=target,
            kind=kind,
            baseline_id=baseline_id,
            input_bundle_refs=input_bundle_refs or [],
            artifact_refs=artifact_refs,
            trace_links=trace_links or [],
            summary=summary,
            open_findings=open_findings or [],
            decision_refs=decision_refs or [],
            next_actions=next_actions or [],
            created_at=created_at or self.ctx.clock(),
        )
        sha256 = digest(bundle)
        existing = repo.get_bundle(self.ctx.db, self.ctx.project_id, bundle_id)
        if existing is not None:
            if existing[1] == sha256:
                return existing[0], BundleRef(bundle_id=bundle_id, sha256=sha256)
            raise Conflict("bundle id already sealed with different content", bundle_id=bundle_id)
        self._check_manifest_authority(bundle)
        for decision_id in bundle.decision_refs:
            if repo.get_decision(self.ctx.db, self.ctx.project_id, decision_id) is None:
                raise MissingInput("decision reference does not exist", decision_id=decision_id)
        with self.ctx.db.transaction():
            if repo.get_bundle(self.ctx.db, self.ctx.project_id, bundle_id) is not None:
                raise Conflict("bundle sealed concurrently", bundle_id=bundle_id)
            repo.insert_bundle(self.ctx.db, bundle, sha256, self.ctx.clock())
            repo.insert_event(
                self.ctx.db,
                self.ctx.project_id,
                self.ctx.ids("event"),
                "bundle.sealed",
                {"bundle_id": bundle_id, "sha256": sha256, "source_team": source.value, "target_team": target.value},
                self.ctx.clock(),
                team_id=None if source == OwnerTeam.PARTICIPANT else source.value,
            )
        return bundle, BundleRef(bundle_id=bundle_id, sha256=sha256)

    def _check_manifest_authority(self, bundle: HandoffBundle) -> None:
        """The publisher owns each artifact or holds an accepted inbound grant; closure is complete."""
        forwarded: set[RefKey] = set()
        if bundle.source_team != OwnerTeam.PARTICIPANT:
            for delivery in repo.accepted_deliveries(self.ctx.db, self.ctx.project_id, bundle.source_team.value):
                forwarded.update(ref.key for ref in delivery.read_grants)
        for parent_ref in bundle.input_bundle_refs:
            parent = repo.get_bundle(self.ctx.db, self.ctx.project_id, parent_ref.bundle_id)
            if parent is None or parent[1] != parent_ref.sha256:
                raise MissingInput("input bundle missing or digest mismatch", bundle_id=parent_ref.bundle_id)
        manifest = {ref.key for ref in bundle.artifact_refs}
        for ref in bundle.artifact_refs:
            artifact = repo.get_artifact_by_ref(self.ctx.db, self.ctx.project_id, ref)
            if artifact is None:
                raise MissingInput("manifest references an unknown artifact revision", ref=ref.to_json_dict())
            if artifact.owner_team != bundle.source_team and ref.key not in forwarded:
                raise DeniedScope(
                    "publisher neither owns nor holds an accepted grant for this artifact",
                    artifact_id=ref.artifact_id,
                    owner=artifact.owner_team.value,
                    publisher=bundle.source_team.value,
                )
            missing = {d.key for d in artifact.depends_on} - manifest
            if missing:
                raise MissingInput(
                    "manifest omits the dependency closure; forward the required inputs explicitly",
                    artifact_id=ref.artifact_id,
                    missing=[list(m) for m in sorted(missing)],
                )

    # ------------------------------------------------------------------ deliver
    def deliver(
        self,
        *,
        bundle_ref: BundleRef,
        expected_target_baseline: str | None,
        delivery_id: str | None = None,
        created_at: str | None = None,
    ) -> Delivery:
        found = repo.get_bundle(self.ctx.db, self.ctx.project_id, bundle_ref.bundle_id)
        if found is None or found[1] != bundle_ref.sha256:
            raise MissingInput("bundle not found or digest mismatch", bundle_ref=bundle_ref.to_json_dict())
        bundle = found[0]
        delivery_id = delivery_id or self.ctx.ids("delivery")
        existing = repo.get_delivery(self.ctx.db, self.ctx.project_id, delivery_id)
        if existing is not None:
            if existing.bundle_ref == bundle_ref and existing.expected_target_baseline == expected_target_baseline:
                return existing
            raise Conflict("delivery id already used for a different envelope", delivery_id=delivery_id)
        if expected_target_baseline is not None:
            if repo.get_bundle(self.ctx.db, self.ctx.project_id, expected_target_baseline) is None:
                raise MissingInput("expected_target_baseline must be a sealed bundle id", bundle_id=expected_target_baseline)
        delivery = Delivery(
            schema_version="1.0",
            delivery_id=delivery_id,
            project_id=self.ctx.project_id,
            bundle_ref=bundle_ref,
            from_team=bundle.source_team,
            to_team=bundle.target_team,
            expected_target_baseline=expected_target_baseline,
            read_grants=list(bundle.artifact_refs),
            created_at=created_at or self.ctx.clock(),
        )
        now = self.ctx.clock()
        receipt = DeliveryReceipt(
            schema_version="1.0",
            receipt_id=self.ctx.ids("receipt"),
            delivery_id=delivery_id,
            project_id=self.ctx.project_id,
            sequence=1,
            status=ReceiptStatus.PENDING,
            reason=None,
            recorded_at=now,
        )
        with self.ctx.db.transaction():
            repo.insert_delivery(self.ctx.db, delivery, now)
            event_id = self.ctx.ids("event")
            repo.insert_event(self.ctx.db, self.ctx.project_id, event_id, "delivery.receipt", receipt.to_json_dict(), now)
            repo.insert_receipt_projection(self.ctx.db, receipt, event_id, now)
            repo.insert_outbox(self.ctx.db, self.ctx.project_id, self.ctx.ids("outbox"), delivery_id, self.ctx.ids("op"), now)
        return delivery

    # ------------------------------------------------------------------ receive
    def receive(self, delivery_id: str) -> DeliveryReceipt:
        """Validate and record receipt. Idempotent: an accepted delivery returns its receipt."""
        delivery = repo.get_delivery(self.ctx.db, self.ctx.project_id, delivery_id)
        if delivery is None:
            raise MissingInput("unknown delivery", delivery_id=delivery_id)
        history = repo.list_receipt_events(self.ctx.db, self.ctx.project_id, delivery_id)
        latest = history[-1] if history else None
        if latest is not None and latest.status != ReceiptStatus.PENDING:
            return latest
        status, reason = self._evaluate(delivery)
        now = self.ctx.clock()
        receipt = DeliveryReceipt(
            schema_version="1.0",
            receipt_id=self.ctx.ids("receipt"),
            delivery_id=delivery_id,
            project_id=self.ctx.project_id,
            sequence=(latest.sequence if latest else 0) + 1,
            status=status,
            reason=reason,
            recorded_at=now,
        )
        with self.ctx.db.transaction():
            projection = repo.get_receipt_projection(self.ctx.db, self.ctx.project_id, delivery_id)
            if projection is None:
                raise Conflict("receipt projection missing", delivery_id=delivery_id)
            if projection["status"] != "pending":
                raise StaleRevision("delivery was settled concurrently", delivery_id=delivery_id)
            event_id = self.ctx.ids("event")
            repo.insert_event(
                self.ctx.db, self.ctx.project_id, event_id, "delivery.receipt", receipt.to_json_dict(), now, team_id=delivery.to_team.value
            )
            repo.cas_receipt_projection(self.ctx.db, receipt, projection["revision"], event_id, now)
            repo.ack_outbox(self.ctx.db, self.ctx.project_id, delivery_id, now)
        return receipt

    def _evaluate(self, delivery: Delivery) -> tuple[ReceiptStatus, str | None]:
        found = repo.get_bundle(self.ctx.db, self.ctx.project_id, delivery.bundle_ref.bundle_id)
        if found is None:
            return ReceiptStatus.REJECTED, "bundle missing"
        bundle, sha256 = found
        if sha256 != delivery.bundle_ref.sha256 or digest(bundle) != sha256:
            return ReceiptStatus.REJECTED, "bundle digest mismatch"
        if (delivery.from_team, delivery.to_team) != (bundle.source_team, bundle.target_team):
            return ReceiptStatus.REJECTED, "delivery route differs from sealed bundle"
        if {r.key for r in delivery.read_grants} != {r.key for r in bundle.artifact_refs}:
            return ReceiptStatus.REJECTED, "read grants differ from the exact manifest"
        if not route_allowed(bundle.kind.value, Principal(bundle.source_team.value), Team(bundle.target_team.value)):
            return ReceiptStatus.REJECTED, "route not registered"
        for ref in bundle.artifact_refs:
            artifact = repo.get_artifact_by_ref(self.ctx.db, self.ctx.project_id, ref)
            if artifact is None or not self.ctx.store.exists(artifact.sha256):
                return ReceiptStatus.REJECTED, f"granted artifact {ref.artifact_id}@{ref.revision} is unavailable"
        current, _ = repo.get_team_baseline(self.ctx.db, self.ctx.project_id, delivery.to_team.value)
        if delivery.expected_target_baseline != current:
            return (
                ReceiptStatus.STALE,
                f"expected target baseline {delivery.expected_target_baseline!r} but the team's accepted baseline is {current!r}; reconcile and redeliver",
            )
        return ReceiptStatus.ACCEPTED, "transport receipt only; engineering acceptance is a separate decision"

    def process_outbox(self) -> list[DeliveryReceipt]:
        """Deliver every pending outbox entry at least once (local transport)."""
        receipts = []
        for entry in repo.pending_outbox(self.ctx.db, self.ctx.project_id):
            receipts.append(self.receive(entry["delivery_id"]))
        return receipts

    # ------------------------------------------------------------------ decisions
    def record_decision(self, actor: Actor, *, scope: str, refs: dict[str, Any], rationale: str, disposition: str, team: TeamId | None = None) -> str:
        decision_id = self.ctx.ids("decision")
        decision = {
            "decision_id": decision_id,
            "actor_kind": actor.kind.value,
            "actor_id": actor.actor_id,
            "policy_revision": actor.policy_revision,
            "scope": scope,
            "refs": refs,
            "disposition": disposition,
            "rationale": rationale,
            "recorded_at": self.ctx.clock(),
        }
        with self.ctx.db.transaction():
            repo.insert_decision(self.ctx.db, self.ctx.project_id, decision_id, actor.actor_id, decision, self.ctx.clock(), team.value if team else None)
            repo.insert_event(self.ctx.db, self.ctx.project_id, self.ctx.ids("event"), "decision.recorded", decision, self.ctx.clock(), team_id=team.value if team else None)
        return decision_id

    def accept_baseline(self, *, team: TeamId, delivery_id: str, actor: Actor, rationale: str, expected_revision: int | None = None) -> str:
        """Make the bundle of an *accepted* delivery the team's current baseline (CAS)."""
        delivery = repo.get_delivery(self.ctx.db, self.ctx.project_id, delivery_id)
        if delivery is None or delivery.to_team != team:
            raise MissingInput("no delivery to this team with that id", delivery_id=delivery_id, team=team.value)
        projection = repo.get_receipt_projection(self.ctx.db, self.ctx.project_id, delivery_id)
        if projection is None or projection["status"] != "accepted":
            raise Conflict("only an accepted (received) delivery can become a baseline", delivery_id=delivery_id, status=projection and projection["status"])
        current, revision = repo.get_team_baseline(self.ctx.db, self.ctx.project_id, team.value)
        if expected_revision is not None and expected_revision != revision:
            raise StaleRevision("team baseline revision changed", expected=expected_revision, found=revision)
        decision_id = self.record_decision(
            actor,
            scope="team_baseline",
            refs={"team": team.value, "delivery_id": delivery_id, "bundle": delivery.bundle_ref.to_json_dict(), "previous_baseline": current},
            rationale=rationale,
            disposition="accept",
            team=team,
        )
        with self.ctx.db.transaction():
            repo.cas_team_baseline(self.ctx.db, self.ctx.project_id, team.value, revision, delivery.bundle_ref.bundle_id, self.ctx.clock())
            repo.insert_event(
                self.ctx.db,
                self.ctx.project_id,
                self.ctx.ids("event"),
                "baseline.accepted",
                {"team": team.value, "bundle_id": delivery.bundle_ref.bundle_id, "decision_id": decision_id, "previous": current},
                self.ctx.clock(),
                team_id=team.value,
            )
        return decision_id

    # ------------------------------------------------------------------ manifest composition
    def compose_manifest(self, team: TeamId, *, kinds: list[str] | None = None, include_inputs: bool = True) -> list[ArtifactRef]:
        """Propose the exact manifest for this team's outgoing handoff.

        Takes the latest non-superseded revision of every artifact the team produced
        (optionally only the given kinds), adds the transitive ``depends_on`` closure, and
        forwards everything the team itself received through accepted deliveries, so a
        downstream team never depends on a summary alone (docs/03 s3, docs/04 s2).
        """
        from sdlc.application.artifacts import ArtifactService

        service = ArtifactService(self.ctx)
        owned = repo.list_artifacts(self.ctx.db, self.ctx.project_id, team.value)
        latest: dict[str, Any] = {}
        for artifact in owned:
            if artifact.kind in ("checkpoint_summary", "rejected_output", "task_graph", "input_assessment", "review"):
                continue
            if kinds and artifact.kind not in kinds:
                continue
            if service.status(artifact.ref) in ("superseded", "stale"):
                continue
            if artifact.artifact_id not in latest or artifact.revision > latest[artifact.artifact_id].revision:
                latest[artifact.artifact_id] = artifact
        # Two different artifact ids at the same path and kind (a reworked document under a
        # new id) are one document slot: the newest wins and the older one is retired, so a
        # downstream team never receives two contradicting versions of the same document.
        by_slot: dict[tuple[str, str], Any] = {}
        for artifact in sorted(latest.values(), key=lambda a: (str(a.created_at), a.artifact_id)):
            slot = (artifact.kind, artifact.relative_path)
            shadowed = by_slot.get(slot)
            if shadowed is not None and shadowed.artifact_id != artifact.artifact_id:
                service.set_status(shadowed.ref, "superseded", reason=f"shadowed by {artifact.artifact_id}@{artifact.revision} at the same path {artifact.relative_path}")
                latest.pop(shadowed.artifact_id, None)
            by_slot[slot] = artifact
        selected: dict[RefKey, ArtifactRef] = {a.ref.key: a.ref for a in latest.values()}
        stack = list(selected.values())
        while stack:
            ref = stack.pop()
            artifact = repo.get_artifact_by_ref(self.ctx.db, self.ctx.project_id, ref)
            if artifact is None:
                raise MissingInput("manifest artifact missing", ref=ref.to_json_dict())
            for dep in artifact.depends_on:
                if dep.key not in selected:
                    selected[dep.key] = dep
                    stack.append(dep)
        if include_inputs:
            for delivery in repo.accepted_deliveries(self.ctx.db, self.ctx.project_id, team.value):
                for ref in delivery.read_grants:
                    selected.setdefault(ref.key, ref)
        return sorted(selected.values(), key=lambda r: (r.artifact_id, r.revision))

    # ------------------------------------------------------------------ queries
    def baseline(self, team: TeamId) -> tuple[str | None, int]:
        return repo.get_team_baseline(self.ctx.db, self.ctx.project_id, team.value)

    def receipt_history(self, delivery_id: str) -> list[DeliveryReceipt]:
        return repo.list_receipt_events(self.ctx.db, self.ctx.project_id, delivery_id)

    def inbox(self, team: TeamId) -> list[Delivery]:
        return repo.accepted_deliveries(self.ctx.db, self.ctx.project_id, team.value)

    def validate_route(self, kind: BundleKind, source: OwnerTeam, target: TeamId) -> None:
        if not route_allowed(kind.value, Principal(source.value), Team(target.value)):
            raise InvalidSchema("route not registered", kind=kind.value, route=f"{source.value}->{target.value}")
