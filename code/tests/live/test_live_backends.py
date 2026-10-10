"""Opt-in live smoke tests. They cost real usage and need logged-in clients.

Run with:  SDLC_LIVE=1 pytest tests/live -q
Skipped (not passed) otherwise. A skip is never compatibility evidence.
"""

from __future__ import annotations

import asyncio
import os

import pytest

from sdlc.adapters.brains.claude_cli import ClaudeCliBrain
from sdlc.adapters.brains.codex_cli import CodexCliBrain
from sdlc.adapters.cli_common import find_executable
from sdlc.domain.identities import AgentPrincipal
from sdlc.domain.teams import Team
from sdlc.ports.brain import BrainRequest, ContextDocument
from sdlc.verification.schemas import validate_record

pytestmark = pytest.mark.skipif(os.environ.get("SDLC_LIVE") != "1", reason="set SDLC_LIVE=1 to run live backend smokes (costs usage)")


def _request(task_id: str) -> BrainRequest:
    return BrainRequest(
        AgentPrincipal("live", "c", Team.PLANNING, "a"), task_id, "a1", "planning.frame_problem", 1,
        "You are the planning team worker. Answer with one WorkerProposal JSON object.",
        "Frame the problem as one problem_frame Markdown candidate (docs/PROBLEM.md) with headings Problem and Scope.",
        (ContextDocument("brief", 1, "0" * 64, "brief.md", "text/markdown", "A library member reserves one copy; two active reservations per copy are forbidden."),),
        {"$ref": "#/$defs/WorkerProposal"}, "WorkerProposal", 2000, 180,
    )


@pytest.mark.skipif(find_executable("claude") is None, reason="claude CLI not installed")
def test_claude_cli_live_proposal():
    brain = ClaudeCliBrain(model="sonnet")
    probe = asyncio.run(brain.probe(live=True))
    assert probe.ok, probe
    reply = asyncio.run(brain.generate(_request("live-claude")))
    assert reply.ok, reply.error
    proposal = validate_record(reply.structured, "WorkerProposal")
    assert proposal.task_id == "live-claude" and reply.usage.measurement.value == "measured"


@pytest.mark.skipif(find_executable("codex") is None, reason="codex CLI not installed")
def test_codex_cli_live_proposal():
    brain = CodexCliBrain()
    probe = asyncio.run(brain.probe(live=True))
    assert probe.ok, probe
    reply = asyncio.run(brain.generate(_request("live-codex")))
    assert reply.ok, reply.error
    proposal = validate_record(reply.structured, "WorkerProposal")
    assert proposal.task_id == "live-codex"
