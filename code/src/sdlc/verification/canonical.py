"""Canonical JSON bytes and SHA-256 digests (handoff docs/07 section 1).

Canonical form: UTF-8, lexicographically sorted object keys, compact separators,
Unicode preserved, no NaN/Infinity, no trailing newline. Hash those exact bytes.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import BaseModel


def canonical_bytes(value: Any) -> bytes:
    """Serialise a JSON-compatible value (or pydantic record) to canonical bytes."""
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def digest(value: Any) -> str:
    """SHA-256 of the canonical bytes of ``value``."""
    return sha256_bytes(canonical_bytes(value))


def load_json_strict(text: str) -> Any:
    """Parse JSON, rejecting duplicate keys and NaN/Infinity constants."""

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    def constant(name: str) -> Any:
        raise ValueError(f"non-JSON numeric constant: {name}")

    return json.loads(text, object_pairs_hook=pairs, parse_constant=constant)


def strip_code_fence(text: str) -> str:
    """Remove a surrounding ```json ... ``` fence that models often add around JSON."""
    stripped = text.strip()
    if stripped.startswith("```"):
        first_newline = stripped.find("\n")
        stripped = stripped[first_newline + 1 :] if first_newline != -1 else ""
        if stripped.rstrip().endswith("```"):
            stripped = stripped.rstrip()[:-3]
    return stripped.strip()
