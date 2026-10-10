"""Load a ``CycleFixture`` through the real services (FR-022, fixture-mode development).

Every artifact, bundle and delivery in the fixture passes the same authorization,
digest and route checks as live work. Loaded records keep ``origin: fixture``, so
nothing loaded this way can satisfy an engineering gate.

A fixture written for one project id can be loaded into a project with another id:
the records are re-stamped with the open project's id and bundle digests are
recomputed, because the project id is part of the sealed bytes. Loading the same
fixture twice is idempotent.
"""

from __future__ import annotations

from dataclasses import dataclass

from sdlc.application.handoffs import HandoffService
from sdlc.application.projects import ProjectContext
from sdlc.domain.errors import Conflict
from sdlc.domain.models import BundleRef, CycleFixture, DeliveryReceipt, Origin, OwnerTeam, ReceiptStatus
from sdlc.storage import repositories as repo
from sdlc.verification.fixtures import check_fixture


@dataclass(frozen=True, slots=True)
class LoadReport:
    artifacts: int
    bundles: int
    deliveries: int
    receipts: dict[str, str]
    remapped_project_id: str | None


def load_fixture(context: ProjectContext, fixture: CycleFixture) -> LoadReport:
    check_fixture(fixture)
    remapped = fixture.project_id if fixture.project_id != context.project_id else None
    handoffs = HandoffService(context)

    # Artifacts in dependency order (the fixture checker already proved the graph is acyclic).
    pending = {a.ref.key: a for a in fixture.artifacts}
    done: set = set()
    while pending:
        progressed = False
        for key, artifact in list(pending.items()):
            if not all(d.key in done for d in artifact.depends_on):
                continue
            if repo.get_artifact_by_ref(context.db, context.project_id, artifact.ref) is None:
                payload = fixture.payloads[artifact.relative_path].encode("utf-8")
                stored = handoffs.artifacts.register(
                    owner=artifact.owner_team,
                    artifact_id=artifact.artifact_id,
                    kind=artifact.kind,
                    media_type=artifact.media_type,
                    relative_path=artifact.relative_path,
                    data=payload,
                    origin=Origin.FIXTURE,
                    depends_on=list(artifact.depends_on),
                    created_by_task=artifact.created_by_task,
                    created_at=artifact.created_at,
                    actor_is_ingress=artifact.owner_team == OwnerTeam.PARTICIPANT,
                )
                if stored.ref != artifact.ref:
                    raise Conflict(
                        "fixture artifact reproduced with a different reference (an unrelated revision already uses this id)",
                        artifact_id=artifact.artifact_id,
                        expected=artifact.ref.to_json_dict(),
                        stored=stored.ref.to_json_dict(),
                    )
            done.add(key)
            del pending[key]
            progressed = True
        if not progressed:
            raise Conflict("fixture artifacts could not be ordered")

    # Bundles and deliveries in the fixture's listed order: each bundle may forward only
    # what an earlier accepted delivery granted, exactly as in live operation.
    deliveries_by_bundle = {d.bundle_ref.bundle_id: d for d in fixture.deliveries}
    sealed: dict[str, BundleRef] = {}
    receipts: dict[str, str] = {}
    for bundle in fixture.bundles:
        _, bundle_ref = handoffs.publish(
            source=bundle.source_team,
            target=bundle.target_team,
            kind=bundle.kind,
            bundle_id=bundle.bundle_id,
            cycle_id=bundle.cycle_id,
            baseline_id=bundle.baseline_id,
            artifact_refs=list(bundle.artifact_refs),
            summary=bundle.summary,
            input_bundle_refs=[sealed[r.bundle_id] for r in bundle.input_bundle_refs],
            trace_links=list(bundle.trace_links),
            open_findings=list(bundle.open_findings),
            decision_refs=list(bundle.decision_refs),
            next_actions=list(bundle.next_actions),
            created_at=bundle.created_at,
            actor_is_ingress=bundle.source_team == OwnerTeam.PARTICIPANT,
        )
        sealed[bundle.bundle_id] = bundle_ref
        delivery = deliveries_by_bundle.get(bundle.bundle_id)
        if delivery is None:
            continue
        if remapped is None and bundle_ref != delivery.bundle_ref:
            raise Conflict("fixture bundle digest differs after sealing", bundle_id=bundle.bundle_id)
        handoffs.deliver(
            bundle_ref=bundle_ref,
            expected_target_baseline=delivery.expected_target_baseline,
            delivery_id=delivery.delivery_id,
            created_at=delivery.created_at,
        )
        receipt: DeliveryReceipt = handoffs.receive(delivery.delivery_id)
        receipts[delivery.delivery_id] = receipt.status.value
        if receipt.status != ReceiptStatus.ACCEPTED:
            raise Conflict("fixture delivery was not accepted", delivery_id=delivery.delivery_id, reason=receipt.reason)
    return LoadReport(
        artifacts=len(fixture.artifacts),
        bundles=len(fixture.bundles),
        deliveries=len(fixture.deliveries),
        receipts=receipts,
        remapped_project_id=remapped,
    )
