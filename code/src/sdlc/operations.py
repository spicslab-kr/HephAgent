"""Operation registry: the only names a team may run (handoff docs/03 s5, docs/07 s8).

The registry is seeded from ``contracts/operations.json``. Each entry declares the
owning team, proposal schema, parameter schema, required capabilities, prerequisite
policy name and write scope. Workers cannot invent operation names. A registry entry
is *not* a working capability: the prerequisite policy must be implemented by the
team module before the runtime will dispatch it (see :attr:`Operation.enabled`).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from functools import lru_cache
from typing import Any

from jsonschema import Draft202012Validator

from sdlc.domain.errors import DeniedScope, InvalidSchema
from sdlc.domain.teams import Team
from sdlc.verification.canonical import load_json_strict
from sdlc.verification.schemas import contract_path, load_schema

REGISTRY_FILENAME = "operations.json"

#: Capabilities an adapter may declare; unknown capability names fail closed.
KNOWN_CAPABILITIES: frozenset[str] = frozenset(
    {
        "structured_proposal",
        "scoped_repository",
        "isolated_execution",
        "diagram_source_generation",
        # Research may be satisfied by a retrieval backend OR explicitly imported evidence.
        "research_or_imported_evidence",
        "native_resume",
        "streaming",
        "interrupt",
        "usage_events",
        "permission_mediation",
    }
)


@dataclass(frozen=True, slots=True)
class Operation:
    name: str
    version: int
    owner_team: Team
    proposal_schema: str
    parameter_schema: dict[str, Any]
    required_capabilities: frozenset[str]
    prerequisite_policy: str
    write_scope: str
    cross_team_reads: str
    domain_specification: str
    #: Set when a team module registers the prerequisite policy (M3+). Until then the
    #: runtime refuses to dispatch the operation rather than skipping the check.
    enabled: bool = field(default=False, compare=False)

    @property
    def short_name(self) -> str:
        return self.name.split(".", 1)[1]

    @property
    def key(self) -> tuple[str, int]:
        return (self.name, self.version)

    def validate_parameters(self, parameters: dict[str, Any]) -> None:
        validator = Draft202012Validator(
            {**self.parameter_schema, "$defs": load_schema()["$defs"]}
        )
        errors = sorted(validator.iter_errors(parameters), key=lambda e: list(e.path))
        if errors:
            raise InvalidSchema(
                f"parameters for {self.name} are invalid",
                operation=self.name,
                errors=[{"path": "$" + "".join(f"/{p}" for p in e.absolute_path), "message": e.message} for e in errors],
            )


class OperationRegistry:
    def __init__(self, operations: list[Operation]) -> None:
        self._by_key: dict[tuple[str, int], Operation] = {}
        for op in operations:
            if op.key in self._by_key:
                raise InvalidSchema("duplicate operation/version", operation=op.name, version=op.version)
            self._by_key[op.key] = op

    @classmethod
    def from_contract(cls) -> "OperationRegistry":
        raw = load_json_strict(contract_path(REGISTRY_FILENAME).read_text(encoding="utf-8"))
        return cls.from_raw(raw)

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> "OperationRegistry":
        defs = load_schema()["$defs"]
        operations: list[Operation] = []
        for item in raw["operations"]:
            name, owner = item["operation"], Team(item["owner_team"])
            if not name.startswith(owner.value + "."):
                raise InvalidSchema("operation prefix does not match owner", operation=name)
            if item["cross_team_reads"] != "accepted_delivery_grants_only":
                raise InvalidSchema("operation expands cross-team access", operation=name)
            proposal = item["proposal_schema"]
            if not proposal.startswith("#/$defs/") or proposal[len("#/$defs/") :] not in defs:
                raise InvalidSchema("unknown proposal schema", operation=name, schema=proposal)
            caps = frozenset(item["required_capabilities"])
            unknown = caps - KNOWN_CAPABILITIES
            if unknown:
                raise InvalidSchema("unknown capability", operation=name, capabilities=sorted(unknown))
            Draft202012Validator.check_schema({**item["parameter_schema"], "$defs": defs})
            operations.append(
                Operation(
                    name=name,
                    version=int(item["version"]),
                    owner_team=owner,
                    proposal_schema=proposal,
                    parameter_schema=item["parameter_schema"],
                    required_capabilities=caps,
                    prerequisite_policy=item["prerequisite_policy"],
                    write_scope=item["write_scope"],
                    cross_team_reads=item["cross_team_reads"],
                    domain_specification=item["domain_specification"],
                )
            )
        return cls(operations)

    def enable(self, name: str, version: int) -> Operation:
        """Mark an operation dispatchable once its prerequisite policy is implemented."""
        op = self.get(name, version)
        enabled = replace(op, enabled=True)
        self._by_key[op.key] = enabled
        return enabled

    def __len__(self) -> int:
        return len(self._by_key)

    def __iter__(self):
        return iter(sorted(self._by_key.values(), key=lambda o: o.key))

    def get(self, name: str, version: int) -> Operation:
        op = self._by_key.get((name, version))
        if op is None:
            raise InvalidSchema("operation not registered", operation=name, version=version)
        return op

    def for_team(self, team: Team) -> list[Operation]:
        return [op for op in self if op.owner_team == team]

    def resolve_for_task(self, team: Team, name: str, version: int, parameters: dict[str, Any]) -> Operation:
        """Look up an operation for a task and confirm team ownership plus parameter shape."""
        op = self.get(name, version)
        if op.owner_team != team:
            raise DeniedScope(
                "task team may not run another team's operation",
                team=team.value,
                operation=name,
                owner=op.owner_team.value,
            )
        op.validate_parameters(parameters)
        return op


@lru_cache(maxsize=1)
def default_registry() -> OperationRegistry:
    return OperationRegistry.from_contract()
