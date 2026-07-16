import pytest

from agents_should_survive_failure.json_schema import (
    JsonSchemaValidationError,
    validate_json_schema,
)

SCHEMA = {
    "type": "object",
    "required": ["incident_id", "approved_follow_up"],
    "properties": {
        "incident_id": {"type": "string"},
        "approved_follow_up": {"type": "boolean"},
    },
    "additionalProperties": False,
}


def test_managed_agent_schema_accepts_contract_value() -> None:
    validate_json_schema({"incident_id": "INC-1", "approved_follow_up": False}, SCHEMA, label="x")


@pytest.mark.parametrize(
    "value",
    [
        {"approved_follow_up": False},
        {"incident_id": "INC-1", "approved_follow_up": "false"},
        {"incident_id": "INC-1", "approved_follow_up": False, "extra": 1},
    ],
)
def test_managed_agent_schema_rejects_invalid_contract_value(value: object) -> None:
    with pytest.raises(JsonSchemaValidationError):
        validate_json_schema(value, SCHEMA, label="x")
