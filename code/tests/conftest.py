from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from sdlc.application.projects import ProjectContext, open_in_memory
from sdlc.common import FixedClock, SequentialIds
from sdlc.config import load_config
from sdlc.domain.models import CycleFixture
from sdlc.verification.schemas import validate_record

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"

EXAMPLE_DEFINITIONS = {
    "minimal-cycle": "CycleFixture",
    "quota-task-result": "TaskResult",
    "diagram-request": "DiagramRequest",
    "interrupted-checkpoint": "Checkpoint",
    "stale-delivery": "Delivery",
}


@pytest.fixture(scope="session")
def example_raw() -> dict[str, dict]:
    return {
        name: json.loads((EXAMPLES / f"{name}.json").read_text(encoding="utf-8-sig"))
        for name in EXAMPLE_DEFINITIONS
    }


@pytest.fixture
def cycle_raw(example_raw) -> dict:
    """A fresh deep copy of minimal-cycle.json for mutation tests."""
    return copy.deepcopy(example_raw["minimal-cycle"])


@pytest.fixture
def cycle_fixture(example_raw) -> CycleFixture:
    return validate_record(example_raw["minimal-cycle"], "CycleFixture")  # type: ignore[return-value]


@pytest.fixture
def example_config_path() -> Path:
    return ROOT / "config" / "project.example.yaml"


@pytest.fixture
def context(tmp_path, example_config_path) -> ProjectContext:
    """In-memory SQLite plus a real content store, deterministic clock and ids."""
    config = load_config(example_config_path)
    ctx = open_in_memory(config, tmp_path / "project", clock=FixedClock(), ids=SequentialIds())
    yield ctx
    ctx.close()
