"""Content-addressed, no-overwrite payload store (handoff docs/06 section 5).

Publication order is fixed so that a crash never leaves a half-written object:

1. write the bytes to a temporary file on the same volume, flush and fsync;
2. compute the digest from the bytes actually written;
3. move the file to ``objects/<aa>/<sha256>`` without overwriting; if an object with that
   digest already exists, the existing bytes are re-hashed and must match, otherwise the
   store reports corruption and stops.

Only after the file is durable does the caller register metadata in SQLite. A crash in
between leaves an unreferenced object, which is harmless; a garbage collector may remove
such files after a retention window (not implemented in M1).
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path, PurePosixPath

from sdlc.domain.errors import CorruptArtifact, DeniedScope
from sdlc.verification.canonical import sha256_bytes

_RELATIVE_PATH = re.compile(r"^(?!/)(?!.*\\)(?!.*(?:^|/)\.\.(?:/|$))(?![A-Za-z]:).+$")


def safe_relative_path(value: str) -> PurePosixPath:
    """Accept only package-relative POSIX paths; reject absolute, drive, backslash, traversal."""
    if (
        not _RELATIVE_PATH.match(value)
        or value.startswith("./")
        or "//" in value
        or any(ord(ch) < 32 or ch == "\x7f" for ch in value)
    ):
        raise DeniedScope("unsafe relative path", path=value)
    parts = PurePosixPath(value).parts
    if not parts or any(part in ("", ".", "..") for part in parts):
        raise DeniedScope("unsafe relative path", path=value)
    return PurePosixPath(value)


class ContentStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.objects = self.root / "objects"
        self.staging = self.root / "staging"
        self.objects.mkdir(parents=True, exist_ok=True)
        self.staging.mkdir(parents=True, exist_ok=True)

    def object_path(self, sha256: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{64}", sha256):
            raise DeniedScope("invalid digest", sha256=sha256)
        return self.objects / sha256[:2] / sha256

    def exists(self, sha256: str) -> bool:
        return self.object_path(sha256).is_file()

    def put(self, data: bytes) -> tuple[str, int]:
        """Durably store ``data``; return ``(sha256, size)``. Idempotent for identical bytes."""
        fd, temp_name = tempfile.mkstemp(prefix="put-", dir=self.staging)
        temp = Path(temp_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            sha256 = sha256_bytes(temp.read_bytes())
            destination = self.object_path(sha256)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                existing = destination.read_bytes()
                if sha256_bytes(existing) != sha256:
                    raise CorruptArtifact(
                        "stored object does not match its digest; stopping", sha256=sha256, path=str(destination)
                    )
                temp.unlink(missing_ok=True)
                return sha256, len(data)
            try:
                # Atomic no-overwrite rename: fails if another writer published first.
                os.link(temp, destination) if hasattr(os, "link") and os.name != "nt" else temp.replace(destination)
            except FileExistsError:
                pass
            finally:
                temp.unlink(missing_ok=True)
            _fsync_dir(destination.parent)
            return sha256, len(data)
        except BaseException:
            temp.unlink(missing_ok=True)
            raise

    def get(self, sha256: str) -> bytes:
        """Read and verify. A digest mismatch is corruption, never silently returned."""
        path = self.object_path(sha256)
        if not path.is_file():
            raise CorruptArtifact("object missing from store", sha256=sha256)
        data = path.read_bytes()
        if sha256_bytes(data) != sha256:
            raise CorruptArtifact("object bytes do not match digest", sha256=sha256, path=str(path))
        return data

    def verify_all(self) -> list[str]:
        """Return digests of corrupt objects (used by doctor and tests)."""
        bad: list[str] = []
        for path in self.objects.glob("*/*"):
            if path.is_file() and sha256_bytes(path.read_bytes()) != path.name:
                bad.append(path.name)
        return bad


def _fsync_dir(directory: Path) -> None:
    if os.name == "nt":
        return  # directory fsync is not supported on Windows
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
