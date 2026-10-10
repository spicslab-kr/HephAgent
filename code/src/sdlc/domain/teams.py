"""The six SDLC teams and the routes that may carry a handoff between them."""

from __future__ import annotations

from enum import StrEnum


class Team(StrEnum):
    """Exactly six content-owning teams. Reusable code never means a shared agent."""

    PLANNING = "planning"
    REQUIREMENTS = "requirements"
    DESIGN = "design"
    IMPLEMENTATION = "implementation"
    TESTING = "testing"
    MAINTENANCE = "maintenance"


class Principal(StrEnum):
    """Who may own content: the six teams plus participant ingress."""

    PARTICIPANT = "participant"
    PLANNING = "planning"
    REQUIREMENTS = "requirements"
    DESIGN = "design"
    IMPLEMENTATION = "implementation"
    TESTING = "testing"
    MAINTENANCE = "maintenance"


TEAM_ORDER: tuple[Team, ...] = (
    Team.PLANNING,
    Team.REQUIREMENTS,
    Team.DESIGN,
    Team.IMPLEMENTATION,
    Team.TESTING,
    Team.MAINTENANCE,
)

#: Forward flow: one immutable delivery per arrow (handoff docs/02, cross-team rules).
FORWARD_ROUTES: frozenset[tuple[Team, Team]] = frozenset(zip(TEAM_ORDER, TEAM_ORDER[1:]))

#: Additional first-release routes (handoff docs/07 section 3), keyed by bundle kind.
#: participant -> any team is manual ingress and is handled separately.
EXTRA_ROUTES: dict[str, frozenset[tuple[Team, Team]]] = {
    # Requirements may be forwarded to Testing for early test planning.
    "forward": frozenset({(Team.REQUIREMENTS, Team.TESTING)}),
    # Testing returns defects to Implementation.
    "defect": frozenset({(Team.TESTING, Team.IMPLEMENTATION)}),
}


def team_index(team: Team) -> int:
    return TEAM_ORDER.index(team)


def route_allowed(kind: str, source: Principal, target: Team) -> bool:
    """Decide whether a bundle of ``kind`` may travel from ``source`` to ``target``.

    - ``manual_input``: participant ingress to any team.
    - ``forward``: the next stage, or the registered Requirements -> Testing route.
    - ``defect``: Testing -> Implementation.
    - ``change_request`` / ``clarification``: a downstream team to any earlier owner, and
      that owner back to the requesting team (the answer). Maintenance may reopen any team.
    - ``baseline``: runtime-recorded baseline bundles use the forward rules.

    Arbitrary broadcast and self-delivery are never allowed.
    """
    if source == Principal.PARTICIPANT:
        return kind == "manual_input"
    src = Team(source.value)
    if src == target:
        return False
    if kind in ("forward", "baseline"):
        return (src, target) in FORWARD_ROUTES or (src, target) in EXTRA_ROUTES["forward"]
    if kind == "defect":
        return (src, target) in EXTRA_ROUTES["defect"]
    if kind in ("change_request", "clarification"):
        return True
    return False
