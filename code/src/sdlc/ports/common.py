"""Shared adapter vocabulary: capability declarations, descriptions and probes."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol, runtime_checkable


class Support(StrEnum):
    """Each capability is declared individually; a vendor claim alone is ``unknown``."""

    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class CapabilityStatus:
    name: str
    support: Support
    evidence: str = ""


@dataclass(frozen=True, slots=True)
class AdapterDescription:
    """Reported by ``describe()`` without contacting any provider."""

    adapter_id: str
    adapter_version: str
    connection_type: str
    auth_mode: str
    #: Where usage is billed: "none", "native_subscription", "api_account", "local".
    cost_source: str
    capabilities: tuple[CapabilityStatus, ...] = ()
    limits: dict[str, int | str] = field(default_factory=dict)

    def supports(self, capability: str) -> bool:
        return any(c.name == capability and c.support == Support.SUPPORTED for c in self.capabilities)

    def unknown(self, capability: str) -> bool:
        return not any(c.name == capability for c in self.capabilities) or any(
            c.name == capability and c.support == Support.UNKNOWN for c in self.capabilities
        )


@dataclass(frozen=True, slots=True)
class ProbeResult:
    ok: bool
    live: bool
    messages: tuple[str, ...] = ()
    #: "verified", "identity_unverified" or "mismatch" (docs/05 s3). Never "verified" without evidence.
    identity: str = "identity_unverified"
    version: str | None = None


@runtime_checkable
class Adapter(Protocol):
    def describe(self) -> AdapterDescription: ...

    async def probe(self, live: bool = False) -> ProbeResult:
        """Check installation/configuration. Only ``live=True`` may contact a provider."""
        ...
