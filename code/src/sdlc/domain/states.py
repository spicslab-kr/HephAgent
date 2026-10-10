"""Task state machine (handoff docs/06 section 3 and diagrams/03_task_states.mmd).

Only the runtime changes the state of a task, and only along these edges.
"""

from __future__ import annotations

from enum import StrEnum

from sdlc.domain.errors import InvalidTransition


class TaskState(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    RETRY_WAIT = "retry_wait"
    PAUSED = "paused"
    NEEDS_INPUT = "needs_input"
    INTERRUPTED = "interrupted"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_STATES: frozenset[TaskState] = frozenset(
    {TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELLED}
)

ALLOWED_TRANSITIONS: dict[TaskState, frozenset[TaskState]] = {
    TaskState.PENDING: frozenset({TaskState.RUNNING, TaskState.NEEDS_INPUT, TaskState.CANCELLED}),
    TaskState.RUNNING: frozenset(
        {
            TaskState.COMPLETED,
            TaskState.RETRY_WAIT,
            TaskState.PAUSED,
            TaskState.NEEDS_INPUT,
            TaskState.INTERRUPTED,
            TaskState.FAILED,
            TaskState.CANCELLED,
        }
    ),
    TaskState.RETRY_WAIT: frozenset({TaskState.PENDING, TaskState.PAUSED, TaskState.CANCELLED}),
    TaskState.PAUSED: frozenset({TaskState.PENDING, TaskState.NEEDS_INPUT, TaskState.CANCELLED}),
    TaskState.NEEDS_INPUT: frozenset({TaskState.PENDING, TaskState.CANCELLED}),
    TaskState.INTERRUPTED: frozenset(
        {
            TaskState.COMPLETED,
            TaskState.PENDING,
            TaskState.PAUSED,
            TaskState.NEEDS_INPUT,
            TaskState.FAILED,
            TaskState.CANCELLED,
        }
    ),
    TaskState.COMPLETED: frozenset(),
    TaskState.FAILED: frozenset(),
    TaskState.CANCELLED: frozenset(),
}


def can_transition(current: TaskState, nxt: TaskState) -> bool:
    return nxt in ALLOWED_TRANSITIONS[current]


def assert_transition(current: TaskState, nxt: TaskState) -> None:
    if not can_transition(current, nxt):
        raise InvalidTransition(
            f"task state may not move from {current} to {nxt}",
            current=str(current),
            next=str(nxt),
        )


#: Result outcomes a TaskResult may carry. ``interrupted`` is runtime-only.
RESULT_OUTCOMES: frozenset[str] = frozenset(
    {"completed", "needs_input", "paused", "failed", "cancelled"}
)
