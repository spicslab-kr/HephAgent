"""Canary reads across team boundaries are denied (AT-002, AT-003; FR-003, NFR-001)."""

from __future__ import annotations

import pytest

from sdlc.application.artifacts import ArtifactService
from sdlc.application.fixtures import load_fixture
from sdlc.application.handoffs import HandoffService
from sdlc.application.views import TeamView
from sdlc.domain.errors import DeniedScope
from sdlc.domain.models import ArtifactRef, Origin, OwnerTeam, TeamId
from sdlc.domain.teams import TEAM_ORDER
from sdlc.storage import repositories as repo

CANARY = "CANARY-{team}-7f3a"


@pytest.fixture
def loaded(context, cycle_fixture):
    load_fixture(context, cycle_fixture)
    service = ArtifactService(context)
    canaries = {}
    for team in TEAM_ORDER:
        owner = OwnerTeam(team.value)
        canaries[owner] = service.register(
            owner=owner,
            artifact_id=f"{team.value}-private-notes",
            kind="scratch",
            media_type="text/plain",
            relative_path=f"{team.value}/notes.txt",
            data=CANARY.format(team=team.value).encode(),
            origin=Origin.MODEL,
        )
    return {"fixture": {a.artifact_id: a for a in cycle_fixture.artifacts}, "canaries": canaries}


def test_granted_inputs_are_readable_and_journaled(context, loaded):
    design = TeamView(context, TeamId.DESIGN)
    req = loaded["fixture"]["a-requirements"]
    assert "REQ-001" in design.read_text(req.ref, ["d-02"])
    reads = [e for e in repo.list_events(context.db, context.project_id, "view.read")]
    assert reads and reads[-1]["payload"]["via"] == "d-02"


def test_every_team_is_denied_every_other_teams_private_artifact(context, loaded):
    for reader in TEAM_ORDER:
        view = TeamView(context, TeamId(reader.value))
        inbox = [d["delivery_id"] for d in view.list_inbox()]
        for owner, canary in loaded["canaries"].items():
            if owner.value == reader.value:
                assert view.read_text(canary.ref, inbox) == CANARY.format(team=reader.value)
                continue
            with pytest.raises(DeniedScope):
                view.read_text(canary.ref, inbox)
    denials = repo.list_events(context.db, context.project_id, "view.denied")
    assert len(denials) == 6 * 5
    assert all(CANARY.format(team=t.value) not in str(e["payload"]) for e in denials for t in TEAM_ORDER)


def test_provenance_links_do_not_grant_access(context, loaded):
    # Requirements' delivery d-01 grants the participant brief and the plan only.
    requirements = TeamView(context, TeamId.REQUIREMENTS)
    design_doc = loaded["fixture"]["a-design"]
    assert loaded["fixture"]["a-design"].depends_on  # provenance exists ...
    with pytest.raises(DeniedScope):
        requirements.read_text(design_doc.ref, ["d-01"])  # ... but is not a grant


def test_undeclared_delivery_is_not_usable(context, loaded):
    design = TeamView(context, TeamId.DESIGN)
    req = loaded["fixture"]["a-requirements"]
    with pytest.raises(DeniedScope):
        design.read_text(req.ref, [])  # task did not declare d-02
    with pytest.raises(DeniedScope):
        design.read_text(req.ref, ["d-03"])  # another team's delivery id


def test_newer_revision_and_guessed_ids_are_denied(context, loaded):
    service = ArtifactService(context)
    req = loaded["fixture"]["a-requirements"]
    newer = service.register(
        owner=OwnerTeam.REQUIREMENTS, artifact_id="a-requirements", kind="requirements", media_type="text/markdown",
        relative_path="payloads/02-requirements.md", data=b"# Requirements v2 (unpublished)", origin=Origin.MODEL,
    )
    design = TeamView(context, TeamId.DESIGN)
    assert design.read_text(req.ref, ["d-02"]).startswith("# Requirements")
    with pytest.raises(DeniedScope):
        design.read_text(newer.ref, ["d-02"])  # no implicit "latest"
    with pytest.raises(DeniedScope):
        design.read_text(ArtifactRef(artifact_id="a-requirements", revision=1, sha256="a" * 64), ["d-02"])
    with pytest.raises(DeniedScope):
        design.read_text(ArtifactRef(artifact_id="does-not-exist", revision=1, sha256="b" * 64), ["d-02"])


def test_materialize_writes_only_granted_files(context, loaded, tmp_path):
    design = TeamView(context, TeamId.DESIGN)
    refs = [loaded["fixture"]["a-requirements"].ref, loaded["fixture"]["a-planning"].ref]
    written = design.materialize(refs, ["d-02"], tmp_path / "task-inputs")
    assert sorted(p.name for p in written) == ["01-planning.md", "02-requirements.md"]
    with pytest.raises(DeniedScope):
        design.materialize([loaded["fixture"]["a-testing"].ref], ["d-02"], tmp_path / "task-inputs")
    assert not (tmp_path / "task-inputs" / "payloads" / "05-testing.md").exists()


def test_participant_can_inspect_but_agents_cannot(context, loaded):
    # The participant (via ArtifactService) sees everything; a team view does not.
    everything = ArtifactService(context).list()
    assert len(everything) == 7 + 6
    assert len(TeamView(context, TeamId.PLANNING).list_owned()) == 2  # a-planning + canary
    handoffs = HandoffService(context)
    assert [d.delivery_id for d in handoffs.inbox(TeamId.PLANNING)] == ["d-00"]
