"""Idempotent development seed data."""

import uuid
from collections.abc import Sequence

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncEngine

from agents_should_survive_failure.persistence.models import (
    Agent,
    AgentStatus,
    AuthPrincipal,
    EvaluationCase,
    PolicyDocument,
    PrincipalStatus,
    PrincipalType,
    ToolDefinition,
    ToolRiskClass,
    User,
    UserStatus,
)

SEED_NAMESPACE = uuid.UUID("ad2dbd38-ad07-4f39-aedc-2a4894d7767d")


def seed_id(name: str) -> uuid.UUID:
    return uuid.uuid5(SEED_NAMESPACE, name)


def seed_rows() -> Sequence[
    tuple[
        type[User | Agent | AuthPrincipal | ToolDefinition | EvaluationCase | PolicyDocument],
        dict[str, object],
    ]
]:
    return (
        (
            User,
            {
                "id": seed_id("user:demo-operator"),
                "email": "operator@example.invalid",
                "display_name": "Demo Operator",
                "status": UserStatus.ACTIVE,
            },
        ),
        (
            AuthPrincipal,
            {
                "id": seed_id("user:demo-operator"),
                "principal_type": PrincipalType.USER,
                "display_name": "Demo Operator",
                "status": PrincipalStatus.ACTIVE,
                "user_id": seed_id("user:demo-operator"),
                "agent_id": None,
            },
        ),
        (
            Agent,
            {
                "id": seed_id("agent:vendor-onboarding:v1"),
                "name": "vendor-onboarding",
                "version": "1",
                "workflow_type": "vendor_onboarding",
                "status": AgentStatus.ACTIVE,
                "configuration": {
                    "model_provider": "deterministic_mock",
                    "tool_permissions": ["vendors:read"],
                },
            },
        ),
        (
            ToolDefinition,
            {
                "id": seed_id("tool:vendor-database-query:v1"),
                "name": "vendor_database_query",
                "version": "1",
                "description": "Read-only lookup of synthetic vendor records.",
                "input_schema": {
                    "type": "object",
                    "properties": {"external_reference": {"type": "string"}},
                    "required": ["external_reference"],
                    "additionalProperties": False,
                },
                "permissions": ["vendors:read"],
                "output_schema": {
                    "type": "object",
                    "properties": {
                        "found": {"type": "boolean"},
                        "vendor_id": {"type": "string"},
                        "status": {"type": "string"},
                    },
                    "required": ["found"],
                    "additionalProperties": False,
                },
                "risk_class": ToolRiskClass.READ_ONLY,
                "timeout_seconds": 10,
                "approval_required": False,
                "enabled": True,
            },
        ),
        (
            EvaluationCase,
            {
                "id": seed_id("evaluation:complete-low-risk-vendor:v1"),
                "slug": "complete-low-risk-vendor",
                "version": "1",
                "workflow_type": "vendor_onboarding",
                "input_data": {
                    "legal_name": "Example Components Ltd",
                    "jurisdiction": "US",
                },
                "expected_outcome": {"requires_approval": True, "risk_band": "low"},
                "enabled": True,
            },
        ),
        (
            PolicyDocument,
            {
                "id": seed_id("policy:vendor-approval:0"),
                "title": "Vendor Approval Policy",
                "source_uri": "seed://policy/vendor-approval",
                "chunk_index": 0,
                "content": "All vendors require a human approval after deterministic risk review.",
                "content_sha256": "c" * 64,
                "embedding_model": "deterministic-embedding-2048d-v1",
                "embedding": [1.0] + [0.0] * 2047,
                "metadata_": {"category": "vendor_onboarding"},
            },
        ),
    )


async def seed_database(engine: AsyncEngine) -> None:
    async with engine.begin() as connection:
        for model, values in seed_rows():
            statement = insert(model).values(**values).on_conflict_do_nothing()
            await connection.execute(statement)
