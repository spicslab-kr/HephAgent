"""The pydantic models must mirror contracts/sdlc.schema.json exactly (NFR-009, docs/07)."""

from __future__ import annotations

from enum import StrEnum

import pytest

from sdlc.domain.models import RECORD_TYPES
from sdlc.verification.schemas import definition_names, load_schema


def test_every_definition_has_a_model():
    assert set(definition_names()) == set(RECORD_TYPES)


@pytest.mark.parametrize("definition", definition_names())
def test_fields_and_required_match_schema(definition):
    schema = load_schema()["$defs"][definition]
    model = RECORD_TYPES[definition]
    assert set(schema["properties"]) == set(model.model_fields)
    # Every property is required in the contract; models therefore have no defaults.
    assert set(schema["required"]) == set(model.model_fields)
    assert schema["additionalProperties"] is False
    assert model.model_config.get("extra") == "forbid"


@pytest.mark.parametrize("definition", definition_names())
def test_enum_values_match_schema(definition):
    schema = load_schema()["$defs"][definition]
    model = RECORD_TYPES[definition]
    for name, prop in schema["properties"].items():
        if "enum" not in prop:
            continue
        annotation = model.model_fields[name].annotation
        enums = [a for a in getattr(annotation, "__args__", (annotation,)) if isinstance(a, type) and issubclass(a, StrEnum)]
        assert enums, f"{definition}.{name} should be a StrEnum"
        assert {e.value for e in enums[0]} == set(prop["enum"]), f"{definition}.{name}"
