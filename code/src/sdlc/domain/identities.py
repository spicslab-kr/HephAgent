"""Agent principal binding (handoff docs/03 section 3).

An agent principal is bound at launch from a registered task. A model-provided
team id can never change it.
"""

from __future__ import annotations

from dataclasses import dataclass

from sdlc.domain.teams import Team


@dataclass(frozen=True, slots=True)
class AgentPrincipal:
    project_id: str
    cycle_id: str
    team_id: Team
    agent_instance_id: str

    def same_team(self, other: "AgentPrincipal") -> bool:
        return (
            self.project_id == other.project_id
            and self.cycle_id == other.cycle_id
            and self.team_id == other.team_id
        )


@dataclass(frozen=True, slots=True)
class SessionOwnershipKey:
    """A native backend session may be resumed only when every field matches (docs/05 s9)."""

    project_id: str
    cycle_id: str
    team_id: Team
    task_id: str
    profile: str
    account_binding_id: str
    workspace_lineage_id: str | None
