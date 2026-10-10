"""Immutable payload storage. Paths are labels; identity is the SHA-256 of the bytes."""

from sdlc.artifacts.store import ContentStore, safe_relative_path

__all__ = ["ContentStore", "safe_relative_path"]
