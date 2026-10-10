"""Scoped leases with fencing generations (handoff docs/06 s4).

Scopes: ``coordinator`` per project, ``task_attempt`` per attempt, ``workspace`` per
coding workspace, ``connection_permit`` per connection slot. A lease has an owner, an
expiry and a generation that only ever increases, even when the lease is reacquired
after expiry. Every result publication re-checks its generation, so an expired worker
cannot commit a late result. Expiry never proves the old process has stopped; that is
the supervisor's job (see :mod:`sdlc.runtime.scheduler` recovery).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sdlc.application.projects import ProjectContext
from sdlc.domain.errors import Conflict, StaleRevision

SCOPES = ("coordinator", "task_attempt", "workspace", "connection_permit")


@dataclass(frozen=True, slots=True)
class Lease:
    scope_kind: str
    scope_key: str
    owner_id: str
    fencing_generation: int
    expires_at: str


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _format(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


class LeaseManager:
    def __init__(self, context: ProjectContext, owner_id: str) -> None:
        self.ctx = context
        self.owner_id = owner_id

    def _now(self) -> datetime:
        return _parse(self.ctx.clock())

    def acquire(self, scope_kind: str, scope_key: str, ttl_seconds: int) -> Lease:
        """Take the lease if free or expired; the generation always increases."""
        if scope_kind not in SCOPES:
            raise Conflict("unknown lease scope", scope_kind=scope_kind)
        now = self._now()
        expires = _format(now + timedelta(seconds=ttl_seconds))
        with self.ctx.db.transaction():
            row = self.ctx.db.one("SELECT * FROM leases WHERE scope_kind=? AND scope_key=?", (scope_kind, scope_key))
            if row is None:
                generation = 1
                self.ctx.db.execute(
                    "INSERT INTO leases(scope_kind, scope_key, project_id, owner_id, revision, fencing_generation, acquired_at, heartbeat_at, expires_at)"
                    " VALUES (?,?,?,?,1,?,?,?,?)",
                    (scope_kind, scope_key, self.ctx.project_id, self.owner_id, generation, _format(now), _format(now), expires),
                )
            else:
                held_by_other = row["owner_id"] != self.owner_id and _parse(row["expires_at"]) > now
                if held_by_other:
                    raise Conflict("lease is held by another owner", scope_kind=scope_kind, scope_key=scope_key, owner=row["owner_id"], expires_at=row["expires_at"])
                generation = int(row["fencing_generation"]) + 1
                cursor = self.ctx.db.execute(
                    "UPDATE leases SET owner_id=?, revision=revision+1, fencing_generation=?, acquired_at=?, heartbeat_at=?, expires_at=?"
                    " WHERE scope_kind=? AND scope_key=? AND revision=?",
                    (self.owner_id, generation, _format(now), _format(now), expires, scope_kind, scope_key, row["revision"]),
                )
                if cursor.rowcount != 1:
                    raise StaleRevision("lease changed concurrently", scope_kind=scope_kind, scope_key=scope_key)
        return Lease(scope_kind, scope_key, self.owner_id, generation, expires)

    def renew(self, lease: Lease, ttl_seconds: int) -> Lease:
        """Heartbeat through CAS on owner and generation; fails if the lease was taken over."""
        now = self._now()
        expires = _format(now + timedelta(seconds=ttl_seconds))
        with self.ctx.db.transaction():
            cursor = self.ctx.db.execute(
                "UPDATE leases SET revision=revision+1, heartbeat_at=?, expires_at=? WHERE scope_kind=? AND scope_key=? AND owner_id=? AND fencing_generation=?",
                (_format(now), expires, lease.scope_kind, lease.scope_key, lease.owner_id, lease.fencing_generation),
            )
            if cursor.rowcount != 1:
                raise StaleRevision("lease lost: another owner holds a newer generation", scope_key=lease.scope_key)
        return Lease(lease.scope_kind, lease.scope_key, lease.owner_id, lease.fencing_generation, expires)

    def is_current(self, lease: Lease) -> bool:
        row = self.ctx.db.one("SELECT owner_id, fencing_generation, expires_at FROM leases WHERE scope_kind=? AND scope_key=?", (lease.scope_kind, lease.scope_key))
        return bool(row and row["owner_id"] == lease.owner_id and int(row["fencing_generation"]) == lease.fencing_generation and _parse(row["expires_at"]) > self._now())

    def release(self, lease: Lease) -> None:
        """Expire the lease immediately (generation is kept, never reset)."""
        with self.ctx.db.transaction():
            self.ctx.db.execute(
                "UPDATE leases SET revision=revision+1, expires_at=? WHERE scope_kind=? AND scope_key=? AND owner_id=? AND fencing_generation=?",
                (_format(self._now() - timedelta(seconds=1)), lease.scope_kind, lease.scope_key, lease.owner_id, lease.fencing_generation),
            )

    def expired(self, scope_kind: str) -> list[dict]:
        now = self._now()
        rows = self.ctx.db.all("SELECT * FROM leases WHERE scope_kind=? AND project_id=?", (scope_kind, self.ctx.project_id))
        return [dict(r) for r in rows if _parse(r["expires_at"]) <= now]
