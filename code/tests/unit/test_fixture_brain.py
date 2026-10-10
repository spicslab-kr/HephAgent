from __future__ import annotations

import asyncio

import pytest

from sdlc.adapters.brains.fixture import FixtureBrain, FixtureScript, proposal_payload, quota_exhausted_error
from sdlc.domain.identities import AgentPrincipal
from sdlc.domain.models import ErrorCategory, WorkerProposal
from sdlc.domain.teams import Team
from sdlc.ports.brain import BrainRequest
from sdlc.verification.schemas import validate_record


def _request(task_id="task-1", operation="design.intake") -> BrainRequest:
    return BrainRequest(
        principal=AgentPrincipal("p", "c", Team.DESIGN, "agent-1"),
        task_id=task_id,
        attempt_id="attempt-1",
        operation=operation,
        operation_version=1,
        system_prompt="",
        instructions="",
        documents=(),
        output_schema={},
        output_definition="WorkerProposal",
        max_output_tokens=10,
        timeout_seconds=1,
    )


def test_describe_is_offline_and_labeled():
    brain = FixtureBrain()
    desc = brain.describe()
    assert desc.cost_source == "none" and desc.supports("structured_proposal")
    assert asyncio.run(brain.probe()).live is False


def test_unscripted_task_returns_invalid_output_error():
    brain = FixtureBrain()
    reply = asyncio.run(brain.generate(_request()))
    assert not reply.ok and reply.error.category == ErrorCategory.INVALID_OUTPUT
    assert reply.provider["origin"] == "fixture"


def test_quota_then_success_sequence_and_invocation_count():
    script = FixtureScript.from_raw(
        {"steps": {"task:task-1": [
            {"error": quota_exhausted_error().to_json_dict()},
            {"proposal": proposal_payload("task-1", "recovered")},
        ]}}
    )
    brain = FixtureBrain(script)
    first = asyncio.run(brain.generate(_request()))
    second = asyncio.run(brain.generate(_request()))
    assert first.error.category == ErrorCategory.QUOTA_EXHAUSTED
    assert first.error.reset_at is None  # never invented
    assert second.ok
    proposal = validate_record(second.structured, "WorkerProposal")
    assert isinstance(proposal, WorkerProposal) and proposal.summary == "recovered"
    assert brain.total_invocations == 2


def test_operation_selector_and_raw_text_replay():
    script = FixtureScript.from_raw({"steps": {"operation:design.intake": {"raw_text": "{not json"}}})
    brain = FixtureBrain(script)
    reply = asyncio.run(brain.generate(_request(task_id="other")))
    assert reply.structured is None and reply.text == "{not json"


def test_step_must_have_exactly_one_payload():
    with pytest.raises(ValueError):
        FixtureScript.from_raw({"steps": {"default": {}}})
