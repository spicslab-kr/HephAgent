"""Publication, delivery, receipt and baseline semantics (AT-003, AT-007, AT-013 slices)."""

from __future__ import annotations

import pytest

from sdlc.application.artifacts import ArtifactService
from sdlc.application.fixtures import load_fixture
from sdlc.application.handoffs import Actor, ActorKind, HandoffService
from sdlc.domain.errors import Conflict, DeniedScope, MissingInput, StaleRevision
from sdlc.domain.models import ArtifactRef, BundleKind, BundleRef, Origin, OwnerTeam, ReceiptStatus, TeamId
from sdlc.storage import repositories as repo
from sdlc.verification.schemas import validate_record

PARTICIPANT = Actor(ActorKind.PARTICIPANT, "instructor")


def _register(ctx, owner: OwnerTeam, artifact_id: str, text: str, depends_on=None, ingress=False):
    return ArtifactService(ctx).register(
        owner=owner,
        artifact_id=artifact_id,
        kind="document",
        media_type="text/markdown",
        relative_path=f"{owner.value}/{artifact_id}.md",
        data=text.encode("utf-8"),
        origin=Origin.FIXTURE,
        depends_on=depends_on or [],
        actor_is_ingress=ingress,
    )


def test_fixture_loads_and_receipts_are_accepted(context, cycle_fixture):
    report = load_fixture(context, cycle_fixture)
    assert report.artifacts == 7 and set(report.receipts.values()) == {"accepted"}
    handoffs = HandoffService(context)
    assert [d.delivery_id for d in handoffs.inbox(TeamId.DESIGN)] == ["d-02"]
    # Loading again is idempotent: same bundles, same deliveries, no new receipts.
    again = load_fixture(context, cycle_fixture)
    assert again.receipts == report.receipts
    assert len(handoffs.receipt_history("d-02")) == 2  # pending + accepted, once


def test_publish_requires_ownership_or_accepted_grant(context):
    plan = _register(context, OwnerTeam.PLANNING, "plan", "# plan")
    handoffs = HandoffService(context)
    with pytest.raises(DeniedScope, match="neither owns"):
        handoffs.publish(
            source=OwnerTeam.REQUIREMENTS,
            target=TeamId.DESIGN,
            kind=BundleKind.FORWARD,
            bundle_id="b-x",
            cycle_id="c",
            baseline_id="base",
            artifact_refs=[plan.ref],
            summary="forwarding something I was never given",
        )


def test_publish_requires_dependency_closure(context):
    plan = _register(context, OwnerTeam.PLANNING, "plan", "# plan")
    plan2 = _register(context, OwnerTeam.PLANNING, "plan-detail", "# detail", depends_on=[plan.ref])
    handoffs = HandoffService(context)
    with pytest.raises(MissingInput, match="dependency closure"):
        handoffs.publish(
            source=OwnerTeam.PLANNING, target=TeamId.REQUIREMENTS, kind=BundleKind.FORWARD,
            bundle_id="b-1", cycle_id="c", baseline_id="base", artifact_refs=[plan2.ref], summary="s",
        )
    bundle, ref = handoffs.publish(
        source=OwnerTeam.PLANNING, target=TeamId.REQUIREMENTS, kind=BundleKind.FORWARD,
        bundle_id="b-1", cycle_id="c", baseline_id="base", artifact_refs=[plan.ref, plan2.ref], summary="s",
    )
    assert len(bundle.artifact_refs) == 2
    # Same id, same content: idempotent. Same id, different content: conflict.
    assert handoffs.publish(
        source=OwnerTeam.PLANNING, target=TeamId.REQUIREMENTS, kind=BundleKind.FORWARD,
        bundle_id="b-1", cycle_id="c", baseline_id="base", artifact_refs=[plan.ref, plan2.ref], summary="s",
    )[1] == ref
    with pytest.raises(Conflict):
        handoffs.publish(
            source=OwnerTeam.PLANNING, target=TeamId.REQUIREMENTS, kind=BundleKind.FORWARD,
            bundle_id="b-1", cycle_id="c", baseline_id="base", artifact_refs=[plan.ref], summary="different",
        )


def test_publish_rejects_unregistered_route_and_participant_impersonation(context):
    plan = _register(context, OwnerTeam.PLANNING, "plan", "# plan")
    handoffs = HandoffService(context)
    with pytest.raises(DeniedScope, match="route"):
        handoffs.publish(
            source=OwnerTeam.PLANNING, target=TeamId.TESTING, kind=BundleKind.FORWARD,
            bundle_id="b-skip", cycle_id="c", baseline_id="base", artifact_refs=[plan.ref], summary="skip stages",
        )
    with pytest.raises(DeniedScope, match="ingress"):
        handoffs.publish(
            source=OwnerTeam.PARTICIPANT, target=TeamId.PLANNING, kind=BundleKind.MANUAL_INPUT,
            bundle_id="b-fake", cycle_id="c", baseline_id="base", artifact_refs=[plan.ref], summary="pretend to be the user",
        )
    with pytest.raises(DeniedScope):
        _register(context, OwnerTeam.PARTICIPANT, "fake-brief", "not via ingress")


def test_deliver_receive_and_baseline_cas(context):
    plan = _register(context, OwnerTeam.PLANNING, "plan", "# plan")
    handoffs = HandoffService(context)
    _, ref = handoffs.publish(
        source=OwnerTeam.PLANNING, target=TeamId.REQUIREMENTS, kind=BundleKind.FORWARD,
        bundle_id="b-1", cycle_id="c", baseline_id="base-1", artifact_refs=[plan.ref], summary="first",
    )
    delivery = handoffs.deliver(bundle_ref=ref, expected_target_baseline=None)
    assert delivery.read_grants == [plan.ref]
    receipt = handoffs.receive(delivery.delivery_id)
    assert receipt.status == ReceiptStatus.ACCEPTED and receipt.sequence == 2
    assert handoffs.receive(delivery.delivery_id) == receipt  # idempotent, no new event
    assert handoffs.baseline(TeamId.REQUIREMENTS) == (None, 1)  # transport != engineering acceptance

    decision = handoffs.accept_baseline(team=TeamId.REQUIREMENTS, delivery_id=delivery.delivery_id, actor=PARTICIPANT, rationale="reviewed")
    assert handoffs.baseline(TeamId.REQUIREMENTS) == ("b-1", 2)
    assert repo.get_decision(context.db, context.project_id, decision)["decision"]["actor_kind"] == "participant"

    # A revision delivered against the old (null) baseline is stale, not last-writer-wins.
    plan2 = _register(context, OwnerTeam.PLANNING, "plan", "# plan v2")
    _, ref2 = handoffs.publish(
        source=OwnerTeam.PLANNING, target=TeamId.REQUIREMENTS, kind=BundleKind.FORWARD,
        bundle_id="b-2", cycle_id="c", baseline_id="base-2", artifact_refs=[plan2.ref], summary="second",
    )
    stale = handoffs.receive(handoffs.deliver(bundle_ref=ref2, expected_target_baseline=None).delivery_id)
    assert stale.status == ReceiptStatus.STALE and "b-1" in stale.reason
    assert handoffs.baseline(TeamId.REQUIREMENTS) == ("b-1", 2)
    ok = handoffs.receive(handoffs.deliver(bundle_ref=ref2, expected_target_baseline="b-1").delivery_id)
    assert ok.status == ReceiptStatus.ACCEPTED
    with pytest.raises(StaleRevision):
        handoffs.accept_baseline(team=TeamId.REQUIREMENTS, delivery_id=ok.delivery_id, actor=PARTICIPANT, rationale="x", expected_revision=1)
    handoffs.accept_baseline(team=TeamId.REQUIREMENTS, delivery_id=ok.delivery_id, actor=PARTICIPANT, rationale="x", expected_revision=2)
    assert handoffs.baseline(TeamId.REQUIREMENTS) == ("b-2", 3)


def test_only_accepted_delivery_can_become_baseline(context):
    plan = _register(context, OwnerTeam.PLANNING, "plan", "# plan")
    handoffs = HandoffService(context)
    _, ref = handoffs.publish(
        source=OwnerTeam.PLANNING, target=TeamId.REQUIREMENTS, kind=BundleKind.FORWARD,
        bundle_id="b-1", cycle_id="c", baseline_id="base", artifact_refs=[plan.ref], summary="s",
    )
    delivery = handoffs.deliver(bundle_ref=ref, expected_target_baseline=None)
    with pytest.raises(Conflict):
        handoffs.accept_baseline(team=TeamId.REQUIREMENTS, delivery_id=delivery.delivery_id, actor=PARTICIPANT, rationale="too early")


def test_receive_rejects_tampered_bundle(context):
    plan = _register(context, OwnerTeam.PLANNING, "plan", "# plan")
    handoffs = HandoffService(context)
    _, ref = handoffs.publish(
        source=OwnerTeam.PLANNING, target=TeamId.REQUIREMENTS, kind=BundleKind.FORWARD,
        bundle_id="b-1", cycle_id="c", baseline_id="base", artifact_refs=[plan.ref], summary="s",
    )
    with pytest.raises(MissingInput):
        handoffs.deliver(bundle_ref=BundleRef(bundle_id="b-1", sha256="0" * 64), expected_target_baseline=None)
    delivery = handoffs.deliver(bundle_ref=ref, expected_target_baseline=None)
    context.db.execute("UPDATE bundles SET sha256=? WHERE bundle_id='b-1'", ("f" * 64,))
    assert handoffs.receive(delivery.delivery_id).status == ReceiptStatus.REJECTED


def test_outbox_processes_deferred_deliveries(context):
    plan = _register(context, OwnerTeam.PLANNING, "plan", "# plan")
    handoffs = HandoffService(context)
    _, ref = handoffs.publish(
        source=OwnerTeam.PLANNING, target=TeamId.REQUIREMENTS, kind=BundleKind.FORWARD,
        bundle_id="b-1", cycle_id="c", baseline_id="base", artifact_refs=[plan.ref], summary="s",
    )
    delivery = handoffs.deliver(bundle_ref=ref, expected_target_baseline=None)
    assert repo.pending_outbox(context.db, context.project_id)[0]["delivery_id"] == delivery.delivery_id
    receipts = handoffs.process_outbox()
    assert [r.status for r in receipts] == [ReceiptStatus.ACCEPTED]
    assert repo.pending_outbox(context.db, context.project_id) == []
    assert handoffs.process_outbox() == []


def test_artifact_revisions_are_append_only(context):
    service = ArtifactService(context)
    first = _register(context, OwnerTeam.DESIGN, "design", "v1")
    second = _register(context, OwnerTeam.DESIGN, "design", "v2")
    assert (first.revision, second.revision) == (1, 2)
    assert service.read_bytes(first.ref) == b"v1" and service.read_bytes(second.ref) == b"v2"
    assert service.status(first.ref) == "draft"
    service.set_status(first.ref, "superseded", reason="revision 2 published")
    assert service.status(first.ref) == "superseded"
    with pytest.raises(MissingInput):
        service.get(ArtifactRef(artifact_id="design", revision=1, sha256=second.sha256))  # wrong digest
