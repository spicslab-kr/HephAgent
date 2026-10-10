from __future__ import annotations

import pytest

from sdlc.domain.errors import InvalidTransition
from sdlc.domain.states import ALLOWED_TRANSITIONS, TERMINAL_STATES, TaskState, assert_transition, can_transition
from sdlc.domain.teams import TEAM_ORDER, Principal, Team, route_allowed


def test_six_teams_in_order():
    assert [t.value for t in TEAM_ORDER] == [
        "planning", "requirements", "design", "implementation", "testing", "maintenance",
    ]


def test_terminal_states_have_no_exits():
    for state in TERMINAL_STATES:
        assert ALLOWED_TRANSITIONS[state] == frozenset()


@pytest.mark.parametrize(
    "current,nxt",
    [
        (TaskState.PENDING, TaskState.RUNNING),
        (TaskState.RUNNING, TaskState.PAUSED),
        (TaskState.PAUSED, TaskState.PENDING),
        (TaskState.RUNNING, TaskState.INTERRUPTED),
        (TaskState.INTERRUPTED, TaskState.COMPLETED),
        (TaskState.RETRY_WAIT, TaskState.PENDING),
    ],
)
def test_legal_transitions(current, nxt):
    assert can_transition(current, nxt)
    assert_transition(current, nxt)


@pytest.mark.parametrize(
    "current,nxt",
    [
        (TaskState.PENDING, TaskState.COMPLETED),  # must run first
        (TaskState.COMPLETED, TaskState.RUNNING),  # terminal
        (TaskState.PAUSED, TaskState.RUNNING),  # must go through pending
        (TaskState.NEEDS_INPUT, TaskState.PAUSED),
    ],
)
def test_illegal_transitions(current, nxt):
    with pytest.raises(InvalidTransition):
        assert_transition(current, nxt)


def test_forward_routes():
    assert route_allowed("forward", Principal.PLANNING, Team.REQUIREMENTS)
    assert route_allowed("forward", Principal.REQUIREMENTS, Team.TESTING)  # early test planning
    assert not route_allowed("forward", Principal.PLANNING, Team.DESIGN)
    assert not route_allowed("forward", Principal.TESTING, Team.PLANNING)


def test_feedback_routes():
    assert route_allowed("defect", Principal.TESTING, Team.IMPLEMENTATION)
    assert not route_allowed("defect", Principal.DESIGN, Team.IMPLEMENTATION)
    assert route_allowed("change_request", Principal.MAINTENANCE, Team.REQUIREMENTS)
    assert route_allowed("clarification", Principal.REQUIREMENTS, Team.DESIGN)  # the answer


def test_participant_only_manual_input():
    assert route_allowed("manual_input", Principal.PARTICIPANT, Team.PLANNING)
    assert not route_allowed("forward", Principal.PARTICIPANT, Team.PLANNING)
    assert not route_allowed("manual_input", Principal.PLANNING, Team.REQUIREMENTS)


def test_no_self_delivery():
    for team in TEAM_ORDER:
        assert not route_allowed("change_request", Principal(team.value), team)
