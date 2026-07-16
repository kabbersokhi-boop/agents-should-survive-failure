"""Small deterministic JSON-schema subset used for managed-agent contracts.

The public SDK intentionally accepts JSON Schema documents rather than generated
Python models.  The runtime enforces the object/value subset used by registered
agents before executing a task and before persisting a result.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast


class JsonSchemaValidationError(ValueError):
    """A managed-agent task or result does not satisfy its registered schema."""


def validate_json_schema(value: object, schema: Mapping[str, object], *, label: str) -> None:
    """Validate a bounded JSON Schema subset without accepting decorative schemas."""

    expected_type = schema.get("type")
    if expected_type is not None and not _matches_type(value, expected_type):
        raise JsonSchemaValidationError(f"{label} must be {expected_type}")
    if not isinstance(value, Mapping):
        return

    required = schema.get("required", ())
    if not isinstance(required, list):
        raise JsonSchemaValidationError(f"{label} schema has invalid required fields")
    required_items = cast(list[object], required)
    if not all(isinstance(item, str) for item in required_items):
        raise JsonSchemaValidationError(f"{label} schema has invalid required fields")
    for name in cast(list[str], required_items):
        if name not in value:
            raise JsonSchemaValidationError(f"{label} is missing required field {name}")

    properties = schema.get("properties", {})
    if not isinstance(properties, Mapping):
        raise JsonSchemaValidationError(f"{label} schema has invalid properties")
    additional = schema.get("additionalProperties", True)
    typed_properties = cast(Mapping[str, object], properties)
    for name, item in cast(Mapping[str, object], value).items():
        property_schema = typed_properties.get(name)
        if property_schema is None:
            if additional is False:
                raise JsonSchemaValidationError(f"{label} contains undeclared field {name}")
            continue
        if not isinstance(property_schema, Mapping):
            raise JsonSchemaValidationError(f"{label} schema has invalid field {name}")
        validate_json_schema(
            item, cast(Mapping[str, object], property_schema), label=f"{label}.{name}"
        )


def _matches_type(value: object, expected_type: object) -> bool:
    if expected_type == "object":
        return isinstance(value, Mapping)
    if expected_type == "array":
        return isinstance(value, list)
    if expected_type == "string":
        return isinstance(value, str)
    if expected_type == "boolean":
        return isinstance(value, bool)
    if expected_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected_type == "number":
        return isinstance(value, int | float) and not isinstance(value, bool)
    if expected_type in {"null", None}:
        return expected_type is None or value is None
    raise JsonSchemaValidationError(f"unsupported JSON Schema type {expected_type!r}")
