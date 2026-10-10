"""Supplied examples load; mutated examples are rejected (M0 exit evidence, AT-007)."""

from __future__ import annotations

import pytest

from sdlc.domain.errors import InvalidSchema, UnsupportedVersion
from sdlc.domain.models import Delivery, HandoffBundle
from sdlc.verification.canonical import digest
from sdlc.verification.fixtures import FixtureInvalid, check_fixture
from sdlc.verification.schemas import schema_errors, validate_record
from tests.conftest import EXAMPLE_DEFINITIONS


@pytest.mark.parametrize("name,definition", EXAMPLE_DEFINITIONS.items())
def test_example_validates(example_raw, name, definition):
    record = validate_record(example_raw[name], definition)
    assert type(record).__name__ == definition
    # Round trip preserves the exact JSON, so canonical digests are stable.
    assert record.to_json_dict() == example_raw[name]


def test_minimal_cycle_semantics(example_raw):
    fixture = validate_record(example_raw["minimal-cycle"], "CycleFixture")
    counts = check_fixture(fixture)
    assert counts["bundles"] == 7 and counts["deliveries"] == 7 and counts["tasks"] == 1


def test_bundle_digest_matches_delivery_ref(example_raw):
    fixture = validate_record(example_raw["minimal-cycle"], "CycleFixture")
    bundles = {b.bundle_id: b for b in fixture.bundles}
    for delivery in fixture.deliveries:
        assert digest(bundles[delivery.bundle_ref.bundle_id]) == delivery.bundle_ref.sha256


def test_unknown_property_rejected(cycle_raw):
    cycle_raw["unexpected_schema_field"] = True
    with pytest.raises(InvalidSchema) as info:
        validate_record(cycle_raw, "CycleFixture")
    assert info.value.details["errors"][0]["validator"] == "additionalProperties"


def test_unsupported_schema_version_rejected(cycle_raw):
    cycle_raw["schema_version"] = "2.0"
    with pytest.raises(UnsupportedVersion):
        validate_record(cycle_raw, "CycleFixture")


def test_wrong_type_is_rejected_by_schema_layer(cycle_raw):
    cycle_raw["artifacts"][0]["revision"] = "1"  # string, not integer
    assert schema_errors(cycle_raw, "CycleFixture")


def test_altered_payload_hash_rejected(cycle_raw):
    cycle_raw["artifacts"][0]["sha256"] = "0" * 64  # payload bytes no longer match
    fixture = validate_record(cycle_raw, "CycleFixture")
    with pytest.raises(FixtureInvalid):
        check_fixture(fixture)


def test_grants_must_equal_manifest(cycle_raw):
    cycle_raw["deliveries"][1]["read_grants"].pop()  # drop a granted artifact
    fixture = validate_record(cycle_raw, "CycleFixture")
    with pytest.raises(FixtureInvalid, match="grants differ"):
        check_fixture(fixture)


def test_forwarding_requires_accepted_inbound_grant(cycle_raw):
    # Requirements forwards the planning artifact; mark that inbound delivery rejected.
    cycle_raw["receipts"][1]["status"] = "rejected"
    fixture = validate_record(cycle_raw, "CycleFixture")
    with pytest.raises(FixtureInvalid, match="not accepted"):
        check_fixture(fixture)


def test_task_inputs_cannot_exceed_grants(cycle_raw):
    # The design task tries to read the testing report, which no accepted delivery grants it.
    task = cycle_raw["tasks"][0]
    task["input_refs"].append(
        {"artifact_id": "a-testing", "revision": 1, "sha256": cycle_raw["artifacts"][5]["sha256"]}
    )
    fixture = validate_record(cycle_raw, "CycleFixture")
    with pytest.raises(FixtureInvalid, match="exceed accepted delivery grants"):
        check_fixture(fixture)


def test_cross_team_task_dependency_rejected(cycle_raw):
    other = dict(cycle_raw["tasks"][0])
    other.update(task_id="task-testing-001", team_id="testing", operation="testing.plan", input_deliveries=[], input_refs=[])
    cycle_raw["tasks"].append(other)
    cycle_raw["tasks"][0]["depends_on"] = ["task-testing-001"]
    fixture = validate_record(cycle_raw, "CycleFixture")
    with pytest.raises(FixtureInvalid, match="cross-team"):
        check_fixture(fixture)


def test_operation_must_belong_to_task_team(cycle_raw):
    cycle_raw["tasks"][0]["operation"] = "testing.plan"
    with pytest.raises(InvalidSchema):
        validate_record(cycle_raw, "CycleFixture")


def test_bundle_cannot_target_itself(example_raw):
    raw = dict(example_raw["minimal-cycle"]["bundles"][1])
    raw["target_team"] = "planning"
    with pytest.raises(ValueError):
        HandoffBundle.model_validate(raw)


def test_forward_route_must_be_next_stage(cycle_raw):
    cycle_raw["bundles"][1]["target_team"] = "testing"
    cycle_raw["deliveries"][1]["to_team"] = "testing"
    fixture = validate_record(cycle_raw, "CycleFixture")
    with pytest.raises(FixtureInvalid):
        check_fixture(fixture)


def test_stale_delivery_example_expects_a_baseline(example_raw):
    delivery = Delivery.model_validate(example_raw["stale-delivery"])
    assert delivery.expected_target_baseline == "b-01"
