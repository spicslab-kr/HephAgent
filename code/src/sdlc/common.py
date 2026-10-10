"""Small shared helpers: UTC timestamps and identifier generation.

Services take a ``Clock`` and an ``IdFactory`` so tests can be deterministic.
"""

from __future__ import annotations

import secrets
from collections.abc import Callable
from datetime import UTC, datetime

Clock = Callable[[], str]
IdFactory = Callable[[str], str]


def now_utc() -> str:
    """RFC 3339 UTC timestamp with millisecond precision, e.g. 2026-10-09T01:02:03.456Z."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def new_id(prefix: str) -> str:
    """Opaque stable identifier: ``<prefix>-<12 hex chars>``."""
    return f"{prefix}-{secrets.token_hex(6)}"


class SequentialIds:
    """Deterministic id factory for tests and fixtures: ``<prefix>-0001``, ``-0002`` ..."""

    def __init__(self) -> None:
        self._counts: dict[str, int] = {}

    def __call__(self, prefix: str) -> str:
        self._counts[prefix] = self._counts.get(prefix, 0) + 1
        return f"{prefix}-{self._counts[prefix]:04d}"


class FixedClock:
    """Clock that returns a constant or an advancing sequence of timestamps."""

    def __init__(self, start: str = "2026-10-09T00:00:00Z") -> None:
        self.value = start

    def __call__(self) -> str:
        return self.value
