"""JSON Schema (Draft 2020-12) validation of records against the shipped contract.

Two layers are applied on purpose:

1. ``jsonschema`` validates the raw JSON against ``contracts/sdlc.schema.json``, which is
   the machine authority for field names and shapes.
2. The matching pydantic model in :mod:`sdlc.domain.models` is constructed, which adds
   the semantic invariants JSON Schema cannot express.

Unsupported ``schema_version`` values are rejected before either layer runs.
"""

from __future__ import annotations

from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError

from sdlc import SCHEMA_VERSION
from sdlc.domain.errors import InvalidSchema, UnsupportedVersion
from sdlc.domain.models import RECORD_TYPES, Record
from sdlc.verification.canonical import load_json_strict

CONTRACTS_PACKAGE = "sdlc.contracts"
SCHEMA_FILENAME = "sdlc.schema.json"


def contract_path(name: str) -> Path:
    """Filesystem path of a shipped contract file (schema, registry, DDL)."""
    return Path(str(resources.files(CONTRACTS_PACKAGE).joinpath(name)))


@lru_cache(maxsize=1)
def load_schema() -> dict[str, Any]:
    text = contract_path(SCHEMA_FILENAME).read_text(encoding="utf-8")
    schema = load_json_strict(text)
    Draft202012Validator.check_schema(schema)
    return schema


@lru_cache(maxsize=None)
def validator_for(definition: str) -> Draft202012Validator:
    schema = load_schema()
    if definition not in schema["$defs"]:
        raise InvalidSchema(f"unknown record definition {definition!r}", definition=definition)
    wrapper = {"$ref": f"#/$defs/{definition}", "$defs": schema["$defs"]}
    return Draft202012Validator(wrapper, format_checker=FormatChecker())


def definition_names() -> list[str]:
    return sorted(load_schema()["$defs"])


def schema_subset(definition: str) -> dict[str, Any]:
    """A standalone JSON Schema for one definition with only the ``$defs`` it references.

    Providers accept a schema per request; shipping all 33 definitions would exceed
    command-line limits on Windows and waste tokens, so the transitive closure is cut.
    """
    defs = load_schema()["$defs"]
    needed: dict[str, Any] = {}
    stack = [definition]
    while stack:
        name = stack.pop()
        if name in needed:
            continue
        if name not in defs:
            raise InvalidSchema(f"unknown record definition {name!r}", definition=name)
        needed[name] = defs[name]
        for ref in _refs_in(defs[name]):
            stack.append(ref)
    root = dict(needed[definition])
    return {**root, "$defs": {k: v for k, v in needed.items() if k != definition}} if len(needed) > 1 else root


#: Keywords providers' strict structured-output subsets commonly reject. They are dropped
#: from the transport projection; the runtime always re-validates against the full schema.
TRANSPORT_UNSUPPORTED_KEYWORDS = frozenset({"pattern", "format", "minLength", "minItems", "minimum", "maximum", "maxLength", "maxItems"})


def transport_projection(definition: str) -> dict[str, Any]:
    """A provider-safe projection of :func:`schema_subset` (handoff docs/05 s6).

    Structure (properties, required, enums, $ref/$defs, additionalProperties: false) is
    kept; value constraints that strict modes reject are removed. The reply is still
    validated against the full contract by :func:`validate_record`.
    """
    return _strip(schema_subset(definition))


_JSON_TYPES = {str: "string", bool: "boolean", int: "integer", float: "number", list: "array", dict: "object", type(None): "null"}


def _strip(node: Any) -> Any:
    if isinstance(node, dict):
        out = {k: _strip(v) for k, v in node.items() if k not in TRANSPORT_UNSUPPORTED_KEYWORDS}
        # OpenAI's strict validator wants an explicit type next to const/enum (observed 2026-10).
        if "type" not in out:
            if "const" in out:
                out["type"] = _JSON_TYPES.get(type(out["const"]), "string")
            elif "enum" in out and out["enum"]:
                kinds = {_JSON_TYPES.get(type(v), "string") for v in out["enum"]}
                out["type"] = kinds.pop() if len(kinds) == 1 else sorted(kinds)
        return out
    if isinstance(node, list):
        return [_strip(item) for item in node]
    return node


def _refs_in(node: Any) -> list[str]:
    found: list[str] = []
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/$defs/"):
            found.append(ref[len("#/$defs/") :])
        for value in node.values():
            found.extend(_refs_in(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(_refs_in(item))
    return found


def _format_error(error: ValidationError) -> dict[str, Any]:
    path = "$" + "".join(f"/{p}" for p in error.absolute_path)
    return {"path": path, "message": error.message, "validator": error.validator}


def schema_errors(value: Any, definition: str) -> list[dict[str, Any]]:
    """All JSON Schema violations of ``value`` against ``definition`` (empty = valid)."""
    errors = sorted(validator_for(definition).iter_errors(value), key=lambda e: list(e.path))
    return [_format_error(e) for e in errors]


def check_version(value: Any) -> None:
    if isinstance(value, dict) and "schema_version" in value:
        version = value["schema_version"]
        if version != SCHEMA_VERSION:
            raise UnsupportedVersion(
                f"schema_version {version!r} is not supported; expected {SCHEMA_VERSION!r}",
                found=version,
                expected=SCHEMA_VERSION,
            )


def validate_record(value: Any, definition: str) -> Record:
    """Validate raw JSON against the contract and return the typed record.

    Raises :class:`UnsupportedVersion` or :class:`InvalidSchema` with machine-readable
    details; never returns a partially valid object.
    """
    check_version(value)
    errors = schema_errors(value, definition)
    if errors:
        raise InvalidSchema(
            f"{definition} failed JSON Schema validation ({len(errors)} error(s))",
            definition=definition,
            errors=errors[:25],
        )
    model = RECORD_TYPES.get(definition)
    if model is None:
        raise InvalidSchema(
            f"{definition} has no typed model; add it to sdlc.domain.models.RECORD_TYPES",
            definition=definition,
        )
    try:
        return model.model_validate(value)
    except ValueError as exc:  # pydantic.ValidationError is a ValueError subclass
        raise InvalidSchema(
            f"{definition} failed semantic validation", definition=definition, errors=[str(exc)]
        ) from exc


def validate_file(path: Path, definition: str) -> Record:
    return validate_record(load_json_strict(path.read_text(encoding="utf-8-sig")), definition)
