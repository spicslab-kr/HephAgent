from __future__ import annotations

import pytest

from sdlc.artifacts.store import ContentStore, safe_relative_path
from sdlc.domain.errors import Conflict, CorruptArtifact, DeniedScope, UnsupportedVersion
from sdlc.storage.database import Database, list_migrations
from sdlc.verification.canonical import sha256_bytes


def test_put_is_content_addressed_and_idempotent(tmp_path):
    store = ContentStore(tmp_path)
    sha, size = store.put(b"hello")
    assert sha == sha256_bytes(b"hello") and size == 5
    assert store.put(b"hello") == (sha, size)
    assert store.get(sha) == b"hello"
    assert not list((tmp_path / "staging").iterdir())  # no leftover temp files


def test_corruption_is_detected_on_read_and_on_republish(tmp_path):
    store = ContentStore(tmp_path)
    sha, _ = store.put(b"payload")
    store.object_path(sha).write_bytes(b"tampered")
    with pytest.raises(CorruptArtifact):
        store.get(sha)
    with pytest.raises(CorruptArtifact):
        store.put(b"payload")
    assert store.verify_all() == [sha]


def test_missing_object(tmp_path):
    store = ContentStore(tmp_path)
    with pytest.raises(CorruptArtifact):
        store.get("0" * 64)
    with pytest.raises(DeniedScope):
        store.object_path("not-a-digest")


@pytest.mark.parametrize("bad", ["/abs", "a/../b", "..", "C:/x", "a\\b", "./a", "a//b", "a\x08b", "", "."])
def test_unsafe_relative_paths_rejected(bad):
    with pytest.raises(DeniedScope):
        safe_relative_path(bad)


def test_safe_relative_path_accepts_nested_unicode():
    assert str(safe_relative_path("docs/명세서/REQUIREMENTS.md")) == "docs/명세서/REQUIREMENTS.md"


def test_database_migrates_once_and_reopens(tmp_path):
    path = tmp_path / "state.sqlite3"
    db = Database.open(path)
    tables = {r[0] for r in db.all("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"projects", "tasks", "attempts", "artifacts", "bundles", "deliveries", "events", "leases", "migrations"} <= tables
    assert db.one("PRAGMA foreign_keys")[0] == 1
    assert db.one("PRAGMA journal_mode")[0] == "wal"
    assert db.integrity_ok()
    db.close()
    db = Database.open(path)
    assert db.all("SELECT version FROM migrations") and db.migrate() == []
    db.close()


def test_database_refuses_newer_version(tmp_path):
    path = tmp_path / "state.sqlite3"
    db = Database.open(path)
    db.execute("INSERT INTO migrations(version, filename, checksum, applied_at) VALUES (99,'x','y','z')")
    db.close()
    with pytest.raises(UnsupportedVersion):
        Database.open(path)


def test_database_refuses_changed_checksum(tmp_path):
    path = tmp_path / "state.sqlite3"
    db = Database.open(path)
    db.execute("UPDATE migrations SET checksum='deadbeef' WHERE version=1")
    db.close()
    with pytest.raises(Conflict):
        Database.open(path)


def test_transaction_rolls_back_on_error():
    db = Database.open(":memory:")
    with pytest.raises(RuntimeError):
        with db.transaction():
            db.execute("INSERT INTO projects(project_id,name,revision,created_at,updated_at,configuration_json) VALUES ('p','p',1,'t','t','{}')")
            raise RuntimeError("boom")
    assert db.one("SELECT count(*) FROM projects")[0] == 0
    with pytest.raises(Conflict):
        with db.transaction():
            with db.transaction():
                pass


def test_migrations_are_versioned_sql_files():
    shipped = list_migrations()
    assert [v for v, _, _ in shipped] == [1]
    assert "CREATE TABLE schema_versions" in shipped[0][2]
    assert "PRAGMA" not in shipped[0][2]
