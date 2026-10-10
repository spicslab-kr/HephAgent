"""Source and testbench snapshots as archive payloads (handoff docs/03 s4).

A snapshot is a zip archive plus an inner manifest of relative paths and SHA-256 hashes.
On extraction we reject absolute paths, traversal, symlinks, device files, oversized
expansion and paths outside the extraction root. Snapshots are immutable artifacts;
a change produces a new snapshot and a patch artifact describing the difference.
"""

from __future__ import annotations

import difflib
import hashlib
import io
import json
import os
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from sdlc.artifacts.store import safe_relative_path
from sdlc.domain.errors import CorruptArtifact, DeniedScope

MANIFEST_NAME = ".sdlc-snapshot.json"
IGNORED_DIRS = {".git", ".venv", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "node_modules", ".sdlc-inputs"}
MAX_FILE_BYTES = 10 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class FileEntry:
    path: str
    sha256: str
    size: int


@dataclass
class SnapshotManifest:
    files: list[FileEntry] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"schema_version": "1.0", "files": [{"path": f.path, "sha256": f.sha256, "size": f.size} for f in sorted(self.files, key=lambda f: f.path)]}

    @property
    def by_path(self) -> dict[str, FileEntry]:
        return {f.path: f for f in self.files}


def _iter_files(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in IGNORED_DIRS and not Path(dirpath, d).is_symlink())
        for name in sorted(filenames):
            path = Path(dirpath, name)
            if path.is_symlink() or name == MANIFEST_NAME:
                continue
            yield path


def scan_tree(root: Path) -> SnapshotManifest:
    root = Path(root)
    manifest = SnapshotManifest()
    for path in _iter_files(root):
        relative = path.relative_to(root).as_posix()
        safe_relative_path(relative)
        data = path.read_bytes()
        manifest.files.append(FileEntry(relative, hashlib.sha256(data).hexdigest(), len(data)))
    return manifest


def create_snapshot(root: Path, *, max_total_bytes: int = 100 * 1024 * 1024) -> tuple[bytes, SnapshotManifest]:
    """Archive a working tree (deterministic order, fixed timestamps). Returns (zip bytes, manifest)."""
    root = Path(root)
    manifest = scan_tree(root)
    total = sum(f.size for f in manifest.files)
    if total > max_total_bytes:
        raise DeniedScope("snapshot exceeds the configured size limit", total=total, limit=max_total_bytes)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for entry in sorted(manifest.files, key=lambda f: f.path):
            info = zipfile.ZipInfo(entry.path, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, (root / entry.path).read_bytes())
        info = zipfile.ZipInfo(MANIFEST_NAME, date_time=(1980, 1, 1, 0, 0, 0))
        archive.writestr(info, json.dumps(manifest.to_dict(), sort_keys=True, indent=2))
    return buffer.getvalue(), manifest


def read_manifest(archive_bytes: bytes) -> SnapshotManifest:
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        if MANIFEST_NAME not in archive.namelist():
            raise CorruptArtifact("snapshot has no inner manifest")
        raw = json.loads(archive.read(MANIFEST_NAME))
    return SnapshotManifest([FileEntry(f["path"], f["sha256"], f["size"]) for f in raw["files"]])


def extract_snapshot(archive_bytes: bytes, destination: Path, *, max_total_bytes: int = 100 * 1024 * 1024) -> SnapshotManifest:
    """Extract safely; every file is verified against the inner manifest."""
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    manifest = read_manifest(archive_bytes)
    expected = manifest.by_path
    total = 0
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        for info in archive.infolist():
            name = info.filename
            if name == MANIFEST_NAME:
                continue
            if name.endswith("/"):
                continue
            safe_relative_path(name)
            if name not in expected:
                raise CorruptArtifact("archive contains a file missing from its manifest", path=name)
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise DeniedScope("symlinks are not allowed in snapshots", path=name)
            total += info.file_size
            if total > max_total_bytes or info.file_size > MAX_FILE_BYTES:
                raise DeniedScope("snapshot expansion exceeds limits", path=name)
            data = archive.read(name)
            if hashlib.sha256(data).hexdigest() != expected[name].sha256:
                raise CorruptArtifact("snapshot file does not match its manifest hash", path=name)
            target = destination / Path(*PurePosixPath(name).parts)
            if not target.resolve().is_relative_to(destination.resolve()):
                raise DeniedScope("path escapes the extraction root", path=name)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
    return manifest


@dataclass
class TreeDiff:
    added: list[str]
    modified: list[str]
    deleted: list[str]
    patch: str

    @property
    def changed(self) -> list[str]:
        return sorted(self.added + self.modified + self.deleted)

    def to_dict(self) -> dict:
        return {"added": self.added, "modified": self.modified, "deleted": self.deleted}


def diff_trees(before_root: Path | None, before: SnapshotManifest, after_root: Path, after: SnapshotManifest) -> TreeDiff:
    """Unified diff between two scanned trees (text files; binaries listed only)."""
    b, a = before.by_path, after.by_path
    added = sorted(p for p in a if p not in b)
    deleted = sorted(p for p in b if p not in a)
    modified = sorted(p for p in a if p in b and a[p].sha256 != b[p].sha256)
    chunks: list[str] = []
    for path in added + modified + deleted:
        old_text = _read_text(before_root / path) if before_root and path in b else ""
        new_text = _read_text(after_root / path) if path in a else ""
        if old_text is None or new_text is None:
            chunks.append(f"Binary file {path} changed\n")
            continue
        chunks.extend(difflib.unified_diff(old_text.splitlines(keepends=True), new_text.splitlines(keepends=True), fromfile=f"a/{path}", tofile=f"b/{path}"))
    return TreeDiff(added=added, modified=modified, deleted=deleted, patch="".join(chunks))


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, FileNotFoundError):
        return None
