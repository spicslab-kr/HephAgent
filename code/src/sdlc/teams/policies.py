"""Operation policies: prerequisites before dispatch, output checks after.

Registering a policy for an operation is what enables it in the registry (handoff
docs/07 s8: "implement these policies before enabling an operation"). Policies see
only the task, its team-scoped view and the proposal; never the database.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from sdlc.application.views import TeamView
from sdlc.domain.models import ArtifactCandidate, Finding, Severity, Task, WorkerProposal
from sdlc.operations import OperationRegistry
from sdlc.verification.outputs import check_candidates


def finding(code: str, message: str, severity: Severity = Severity.BLOCKING) -> Finding:
    return Finding(finding_id=f"finding-{code}", code=code, severity=severity, message=message, related_refs=[])


class OperationPolicy(Protocol):
    name: str

    def check(self, task: Task, view: TeamView) -> list[Finding]:
        """Prerequisites; blocking findings send the task to needs_input with zero calls."""
        ...

    def check_outputs(self, task: Task, proposal: WorkerProposal, view: TeamView | None = None) -> list[Finding]:
        """Output obligations; blocking findings make the reply invalid_output."""
        ...

    def required_kinds(self, task: Task) -> tuple[str, ...]:
        """Artifact kinds the worker is told to produce (exact ``kind`` strings)."""
        ...


@dataclass(frozen=True, slots=True)
class BasePolicy:
    """Default policy: no prerequisites beyond generic candidate checks."""

    name: str

    def check(self, task: Task, view: TeamView) -> list[Finding]:
        return []

    def check_outputs(self, task: Task, proposal: WorkerProposal, view: TeamView | None = None) -> list[Finding]:
        return check_candidates(list(proposal.artifact_candidates))

    def required_kinds(self, task: Task) -> tuple[str, ...]:
        return ()

    def normalize(self, task: Task, proposal: WorkerProposal, view: TeamView | None = None) -> tuple[WorkerProposal, list[Finding]]:
        """Deterministic normalisation before output checks (e.g. fixing a copied hash to the pinned value)."""
        return proposal, []

    def context_notes(self, task: Task, view: TeamView) -> list[str]:
        """Facts the worker must respect, computed from granted inputs (e.g. the valid requirement ids)."""
        return []


class PolicyRegistry:
    def __init__(self) -> None:
        self._policies: dict[str, OperationPolicy] = {}

    def register(self, registry: OperationRegistry, operation: str, version: int, policy: OperationPolicy) -> None:
        op = registry.get(operation, version)
        if op.prerequisite_policy != policy.name:
            raise ValueError(f"policy {policy.name!r} does not match registry entry {op.prerequisite_policy!r}")
        self._policies[op.prerequisite_policy] = policy
        registry.enable(operation, version)

    def get(self, policy_name: str) -> OperationPolicy | None:
        return self._policies.get(policy_name)

    def names(self) -> list[str]:
        return sorted(self._policies)


def candidates_of_kind(proposal: WorkerProposal, kind: str) -> list[ArtifactCandidate]:
    return [c for c in proposal.artifact_candidates if c.kind == kind]
