"""SQLite connection with the pragmas and migration discipline from handoff docs/06 s2.

* foreign keys on, WAL journal, ``synchronous=FULL``, bounded busy timeout;
* migrations are versioned SQL files with recorded SHA-256 checksums;
* a database newer than this code, or with a changed migration checksum, is refused;
* transactions are explicit ``BEGIN IMMEDIATE`` blocks and must never stay open while
  waiting on a backend, a build or a person.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from importlib import resources
from pathlib import Path

from sdlc.common import now_utc
from sdlc.domain.errors import Conflict, UnsupportedVersion
from sdlc.verification.canonical import sha256_text

MIGRATIONS_PACKAGE = "sdlc.storage.migrations"


def list_migrations() -> list[tuple[int, str, str]]:
    """(version, filename, sql) for every shipped migration, ascending."""
    found: list[tuple[int, str, str]] = []
    for entry in resources.files(MIGRATIONS_PACKAGE).iterdir():
        name = entry.name
        if name.endswith(".sql") and name[:4].isdigit():
            found.append((int(name[:4]), name, entry.read_text(encoding="utf-8")))
    return sorted(found)


class Database:
    def __init__(self, connection: sqlite3.Connection, path: Path | None) -> None:
        self.connection = connection
        self.path = path

    # ------------------------------------------------------------------ lifecycle
    @classmethod
    def open(cls, path: Path | str, *, create: bool = True) -> "Database":
        """Open (and migrate) the database at ``path``; ``":memory:"`` is allowed for tests."""
        memory = str(path) == ":memory:"
        if not memory:
            path = Path(path)
            if not path.exists() and not create:
                raise FileNotFoundError(path)
            path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        if not memory:
            connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = FULL")
        connection.execute("PRAGMA busy_timeout = 5000")
        db = cls(connection, None if memory else path)
        db.migrate()
        return db

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ------------------------------------------------------------------ migrations
    def migrate(self) -> list[int]:
        """Apply pending migrations; refuse unknown versions or changed checksums."""
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS migrations ("
            " version INTEGER PRIMARY KEY, filename TEXT NOT NULL, checksum TEXT NOT NULL, applied_at TEXT NOT NULL)"
        )
        applied = {
            row["version"]: row["checksum"]
            for row in self.connection.execute("SELECT version, checksum FROM migrations")
        }
        shipped = list_migrations()
        known = {v for v, _, _ in shipped}
        newer = sorted(set(applied) - known)
        if newer:
            raise UnsupportedVersion(
                "database was created by a newer version of sdlc; refusing to open",
                unknown_versions=newer,
            )
        applied_now: list[int] = []
        for version, filename, sql in shipped:
            checksum = sha256_text(sql)
            if version in applied:
                if applied[version] != checksum:
                    raise Conflict(
                        "migration checksum differs from the applied version; back up and migrate explicitly",
                        version=version,
                        filename=filename,
                    )
                continue
            with self.transaction():
                self._apply_script(sql)
                self.connection.execute(
                    "INSERT INTO migrations(version, filename, checksum, applied_at) VALUES (?,?,?,?)",
                    (version, filename, checksum, now_utc()),
                )
                if version == 1:
                    self.connection.execute(
                        "INSERT INTO schema_versions(version, applied_at, description) VALUES (?,?,?)",
                        (1, now_utc(), "Initial reference metadata schema"),
                    )
            self.connection.execute(f"PRAGMA user_version = {version}")
            applied_now.append(version)
        return applied_now

    def _apply_script(self, sql: str) -> None:
        # executescript would commit the surrounding transaction, so run statements one by one.
        for statement in _split_statements(sql):
            self.connection.execute(statement)

    # ------------------------------------------------------------------ transactions
    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """``BEGIN IMMEDIATE`` block: serialises writers, rolls back on any exception."""
        if self.connection.in_transaction:
            raise Conflict("nested transactions are not allowed; keep units of work explicit")
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            yield self.connection
        except BaseException:
            self.connection.execute("ROLLBACK")
            raise
        else:
            self.connection.execute("COMMIT")

    # ------------------------------------------------------------------ convenience
    def one(self, sql: str, params: tuple = ()) -> sqlite3.Row | None:
        return self.connection.execute(sql, params).fetchone()

    def all(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        return self.connection.execute(sql, params).fetchall()

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        return self.connection.execute(sql, params)

    def integrity_ok(self) -> bool:
        row = self.one("PRAGMA integrity_check")
        fk = self.all("PRAGMA foreign_key_check")
        return bool(row and row[0] == "ok" and not fk)


def _split_statements(sql: str) -> list[str]:
    """Split a DDL script on ``;`` at statement end, ignoring ``--`` comments."""
    statements: list[str] = []
    buffer: list[str] = []
    for raw in sql.splitlines():
        line = raw.split("--", 1)[0].rstrip()
        if not line.strip():
            continue
        buffer.append(line)
        if line.rstrip().endswith(";"):
            statements.append("\n".join(buffer))
            buffer = []
    if buffer:
        statements.append("\n".join(buffer))
    return [s for s in statements if s.strip(" ;\n")]
