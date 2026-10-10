from __future__ import annotations

import copy

import pytest
import yaml

from sdlc.config import ConnectionType, ProjectConfig, Readiness, load_config
from sdlc.domain.errors import DeniedScope, InvalidSchema
from sdlc.domain.teams import TEAM_ORDER, Team
from sdlc.operations import OperationRegistry, default_registry


def test_registry_loads_all_operations():
    registry = default_registry()
    assert len(registry) == 69
    for team in TEAM_ORDER:
        names = {op.short_name for op in registry.for_team(team)}
        assert {"intake", "plan_work", "review", "prepare_handoff", "request_diagram"} <= names


def test_registry_operations_start_disabled():
    assert all(not op.enabled for op in default_registry())


def test_registry_rejects_cross_team_resolution():
    registry = default_registry()
    with pytest.raises(DeniedScope):
        registry.resolve_for_task(Team.TESTING, "design.define_contracts", 1, {})


def test_registry_validates_parameters():
    registry = default_registry()
    good = {"objective": "x", "scope_ids": [], "requested_artifact_kinds": [], "diagram_request": None}
    registry.resolve_for_task(Team.DESIGN, "design.define_contracts", 1, good)
    with pytest.raises(InvalidSchema):
        registry.resolve_for_task(Team.DESIGN, "design.define_contracts", 1, {"objective": "x"})


def test_registry_rejects_expanded_reads():
    raw = {
        "operations": [
            {
                "operation": "design.sneaky",
                "version": 1,
                "owner_team": "design",
                "proposal_schema": "#/$defs/WorkerProposal",
                "parameter_schema": {"type": "object"},
                "required_capabilities": [],
                "prerequisite_policy": "x",
                "cross_team_reads": "everything",
                "write_scope": "owned_task_workspace",
                "domain_specification": "x",
            }
        ]
    }
    with pytest.raises(InvalidSchema):
        OperationRegistry.from_raw(raw)


def test_registry_rejects_unknown_capability():
    raw = {
        "operations": [
            {
                "operation": "design.x",
                "version": 1,
                "owner_team": "design",
                "proposal_schema": "#/$defs/WorkerProposal",
                "parameter_schema": {"type": "object"},
                "required_capabilities": ["root_shell"],
                "prerequisite_policy": "x",
                "cross_team_reads": "accepted_delivery_grants_only",
                "write_scope": "owned_task_workspace",
                "domain_specification": "x",
            }
        ]
    }
    with pytest.raises(InvalidSchema):
        OperationRegistry.from_raw(raw)


def test_example_config_loads(example_config_path):
    config = load_config(example_config_path)
    assert config.project.fixture_mode is True
    assert config.connections["fixture"].readiness == Readiness.READY
    assert config.connections["codex-native"].readiness == Readiness.UNCONFIGURED
    assert "model" in config.connections["codex-native"].placeholders
    assert config.connection_for(Team.DESIGN, "coding").type == ConnectionType.FIXTURE


def _raw(example_config_path) -> dict:
    return yaml.safe_load(example_config_path.read_text(encoding="utf-8"))


def test_config_rejects_secret_values(example_config_path):
    raw = _raw(example_config_path)
    raw["connections"]["openai-api"]["credential_ref"] = "sk-not-a-reference"
    with pytest.raises(ValueError, match="never a secret value"):
        ProjectConfig.model_validate(raw)


def test_config_rejects_missing_team(example_config_path):
    raw = _raw(example_config_path)
    del raw["teams"]["maintenance"]
    with pytest.raises(ValueError, match="missing"):
        ProjectConfig.model_validate(raw)


def test_config_rejects_unknown_connection_binding(example_config_path):
    raw = _raw(example_config_path)
    raw["teams"]["design"]["coding"] = "nope"
    with pytest.raises(ValueError, match="unknown connection"):
        ProjectConfig.model_validate(raw)


def test_config_brain_cannot_serve_coding_role(example_config_path):
    raw = _raw(example_config_path)
    raw["project"]["fixture_mode"] = False
    raw["teams"]["implementation"]["coding"] = "openai-api"
    with pytest.raises(ValueError, match="cannot serve role 'coding'"):
        ProjectConfig.model_validate(raw)


def test_fixture_mode_forbids_live_bindings(example_config_path):
    raw = _raw(example_config_path)
    raw["teams"]["planning"]["brain"] = "openai-api"
    with pytest.raises(ValueError, match="fixture_mode"):
        ProjectConfig.model_validate(raw)


def test_config_rejects_provider_fallback(example_config_path):
    raw = _raw(example_config_path)
    raw["runtime"]["automatic_provider_fallback"] = True
    with pytest.raises(ValueError):
        ProjectConfig.model_validate(raw)


def test_config_rejects_unknown_keys(example_config_path):
    raw = copy.deepcopy(_raw(example_config_path))
    raw["runtime"]["surprise"] = 1
    with pytest.raises(ValueError):
        ProjectConfig.model_validate(raw)
