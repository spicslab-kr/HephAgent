"""Typed service findings (handoff docs/07 section 4).

These are runtime/service errors, deliberately distinct from provider
``NormalizedError`` categories. Never map a stale delivery to ``provider_unavailable``.
"""

from __future__ import annotations


class ServiceError(Exception):
    """Base class; ``code`` is the stable machine-readable finding name."""

    code = "service_error"

    def __init__(self, message: str, **details: object) -> None:
        super().__init__(message)
        self.message = message
        self.details = details

    def to_dict(self) -> dict[str, object]:
        return {"code": self.code, "message": self.message, "details": self.details}


class InvalidSchema(ServiceError):
    code = "invalid_schema"


class MissingInput(ServiceError):
    code = "missing_input"


class DeniedScope(ServiceError):
    code = "denied_scope"


class StaleRevision(ServiceError):
    code = "stale_revision"


class Conflict(ServiceError):
    code = "conflict"


class UnsupportedVersion(ServiceError):
    code = "unsupported_version"


class CorruptArtifact(ServiceError):
    code = "corrupt_artifact"


class InvalidTransition(ServiceError):
    code = "invalid_transition"


class CapabilityMissing(ServiceError):
    """An operation needs a capability the selected connection cannot provide."""

    code = "capability_missing"
