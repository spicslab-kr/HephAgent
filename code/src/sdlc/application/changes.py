"""Change packages and stale propagation (handoff docs/02 FR-015, FR-019; docs/03 s88).

When Maintenance's impact analysis names the earliest affected owner, the runtime:

1. seals the Maintenance package (change spec, impact analysis, triage, reproduction,
   rework package and the forwarded request memo) as a ``change_request`` bundle and
   delivers it to that team (artifact-only, a journaled decision; never a private chat);
2. marks the current outputs of the owner and of every team downstream of it ``stale``:
   the evidence is not erased, it merely no longer applies to the next baseline
   (releases and their manifests stay historical and are never marked).

A stale document is still readable and exportable; planning and handoff composition
skip it, so the reopened teams produce fresh revisions on the new inputs.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sdlc.application.artifacts import ArtifactService
from sdlc.application.handoffs import Actor, HandoffService
from sdlc.application.projects import ProjectContext
from sdlc.domain.models import BundleKind, OwnerTeam, TeamId
from sdlc.domain.teams import TEAM_ORDER
from sdlc.storage import repositories as repo

NEVER_STALE_KINDS = frozenset({"release_manifest", "release_summary", "rejected_output", "checkpoint_summary"})
CHANGE_PACKAGE_KINDS = ["change_spec", "impact_analysis", "triage_note", "reproduction_report", "rework_package"]


@dataclass
class ReopenResult:
    owner: str
    reopened_teams: list[str]
    stale_artifacts: dict[str, int] = field(default_factory=dict)
    delivery_id: str | None = None
    bundle_id: str | None = None
    decision_id: str | None = None


class ChangeService:
    def __init__(self, context: ProjectContext, actor: Actor) -> None:
        self.ctx = context
        self.actor = actor
        self.artifacts = ArtifactService(context)
        self.handoffs = HandoffService(context)

    def deliver_change_package(self, owner: TeamId, summary: str) -> ReopenResult:
        order = [t.value for t in TEAM_ORDER]
        if owner == TeamId.MAINTENANCE:
            raise ValueError("maintenance cannot route a change to itself")
        refs = self.handoffs.compose_manifest(TeamId.MAINTENANCE, kinds=CHANGE_PACKAGE_KINDS)
        count = len(repo.list_bundles(self.ctx.db, self.ctx.project_id))
        current, _ = self.handoffs.baseline(owner)
        _, bundle_ref = self.handoffs.publish(
            source=OwnerTeam.MAINTENANCE, target=owner, kind=BundleKind.CHANGE_REQUEST, bundle_id=f"b-change-{count + 1:03d}", cycle_id="cycle-001",
            baseline_id=f"baseline-change-{count + 1:03d}", artifact_refs=refs, summary=summary,
        )
        delivery = self.handoffs.deliver(bundle_ref=bundle_ref, expected_target_baseline=current)
        receipt = self.handoffs.receive(delivery.delivery_id)
        if receipt.status.value != "accepted":
            raise RuntimeError(f"change delivery {delivery.delivery_id} was {receipt.status.value}: {receipt.reason}")
        decision = self.handoffs.record_decision(self.actor, scope="change_package", refs={"delivery_id": delivery.delivery_id, "bundle": bundle_ref.to_json_dict(), "owner": owner.value},
                                                 rationale=f"change routed to the earliest affected owner ({owner.value}); dependent outputs downstream are stale", disposition="accepted", team=owner)
        result = self.mark_stale(owner, reason=f"change package {bundle_ref.bundle_id} reopens {owner.value}")
        result.delivery_id, result.bundle_id, result.decision_id = delivery.delivery_id, bundle_ref.bundle_id, decision
        return result

    def mark_stale(self, owner: TeamId, *, reason: str) -> ReopenResult:
        """Stale every current output of ``owner`` and of the teams after it up to Testing."""
        order = [t.value for t in TEAM_ORDER]
        reopened = order[order.index(owner.value): order.index("testing") + 1]
        result = ReopenResult(owner=owner.value, reopened_teams=reopened)
        for team in reopened:
            n = 0
            for artifact in self.artifacts.list(OwnerTeam(team)):
                if artifact.kind in NEVER_STALE_KINDS or self.artifacts.status(artifact.ref) in ("superseded", "stale"):
                    continue
                self.artifacts.set_status(artifact.ref, "stale", reason=reason)
                n += 1
            result.stale_artifacts[team] = n
            with self.ctx.db.transaction():
                repo.insert_event(self.ctx.db, self.ctx.project_id, self.ctx.ids("event"), "baseline.invalidated",
                                  {"team": team, "reason": reason, "stale_artifacts": n, "note": "evidence preserved; applicability to the next baseline withdrawn"}, self.ctx.clock(), team_id=team)
        return result
