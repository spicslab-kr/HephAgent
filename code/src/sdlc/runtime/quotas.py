"""Quota-scope circuit breaker (handoff docs/05 s10, docs/06 s6).

When any attempt fails with ``quota_exhausted`` or ``auth_required`` the affected
``quota_scope_id`` is paused: no further requests go to any connection sharing that
scope until a participant explicitly declares the connection restored. The runtime
never infers that availability has returned, and never rotates to another account.

The pause is durable: it is the latest ``quota.paused`` / ``quota.restored`` event for
the scope, so it survives a process restart.
"""

from __future__ import annotations

from dataclasses import dataclass

from sdlc.application.projects import ProjectContext
from sdlc.domain.models import NormalizedError
from sdlc.storage import repositories as repo


@dataclass(frozen=True, slots=True)
class QuotaPause:
    quota_scope_id: str
    category: str
    message: str
    reset_at: str | None
    recorded_at: str
    task_id: str | None


class QuotaRegistry:
    def __init__(self, context: ProjectContext) -> None:
        self.ctx = context

    def pauses(self) -> dict[str, QuotaPause]:
        """Scopes currently paused: latest event per scope decides."""
        state: dict[str, QuotaPause] = {}
        for event in repo.list_events(self.ctx.db, self.ctx.project_id):
            if event["event_type"] == "quota.paused":
                p = event["payload"]
                state[p["quota_scope_id"]] = QuotaPause(
                    quota_scope_id=p["quota_scope_id"],
                    category=p["category"],
                    message=p["message"],
                    reset_at=p.get("reset_at"),
                    recorded_at=event["occurred_at"],
                    task_id=event["task_id"],
                )
            elif event["event_type"] == "quota.restored":
                state.pop(event["payload"]["quota_scope_id"], None)
        return state

    def is_paused(self, quota_scope_id: str) -> QuotaPause | None:
        return self.pauses().get(quota_scope_id)

    def pause(self, quota_scope_id: str, error: NormalizedError, task_id: str | None) -> None:
        """Called inside the caller's transaction."""
        repo.insert_event(
            self.ctx.db,
            self.ctx.project_id,
            self.ctx.ids("event"),
            "quota.paused",
            {
                "quota_scope_id": quota_scope_id,
                "category": error.category.value,
                "message": error.message,
                "reset_at": error.reset_at,  # only if the provider supplied it; never invented
                "provider_code": error.provider_code,
            },
            self.ctx.clock(),
            task_id=task_id,
        )

    def restore(self, quota_scope_id: str, actor_id: str, note: str) -> None:
        """A participant declares the connection usable again (an explicit decision)."""
        with self.ctx.db.transaction():
            repo.insert_event(
                self.ctx.db,
                self.ctx.project_id,
                self.ctx.ids("event"),
                "quota.restored",
                {"quota_scope_id": quota_scope_id, "actor_id": actor_id, "note": note},
                self.ctx.clock(),
            )
