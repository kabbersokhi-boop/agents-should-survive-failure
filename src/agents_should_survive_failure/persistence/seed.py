"""Idempotent development seed data."""

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncEngine

from agents_should_survive_failure.evaluation_scenarios import load_packaged_evaluation_suite
from agents_should_survive_failure.persistence.models import (
    Agent,
    AgentStatus,
    AgentToolGrant,
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
from agents_should_survive_failure.workflows.contracts import GovernedToolName

SEED_NAMESPACE = uuid.UUID("ad2dbd38-ad07-4f39-aedc-2a4894d7767d")


def seed_id(name: str) -> uuid.UUID:
    return uuid.uuid5(SEED_NAMESPACE, name)


def evaluation_case_seed_rows() -> tuple[tuple[type[EvaluationCase], dict[str, object]], ...]:
    suite = load_packaged_evaluation_suite()
    return tuple(
        (
            EvaluationCase,
            {
                "id": seed_id(
                    f"evaluation:{suite.suite_slug}:{suite.suite_version}:{case.slug}:"
                    f"v{case.case_version}"
                ),
                "suite_slug": suite.suite_slug,
                "suite_version": suite.suite_version,
                "schema_version": suite.schema_version,
                "slug": case.slug,
                "version": case.case_version,
                "workflow_type": suite.workflow_type,
                "title": case.title,
                "description": case.description,
                "scenario_type": case.scenario_type.value,
                "input_data": case.input.model_dump(mode="json"),
                "setup": case.setup.model_dump(mode="json"),
                "driver": case.driver.model_dump(mode="json"),
                "expected_outcome": case.expected_outcome.model_dump(mode="json"),
                "evidence_requirements": [item.value for item in case.evidence_requirements],
                "content_sha256": suite.case_content_sha256(case),
                "reviewed_by": suite.reviewed_by,
                "reviewed_at": suite.reviewed_at,
                "enabled": True,
            },
        )
        for case in suite.cases
    )


def seed_rows() -> Sequence[
    tuple[
        type[
            User
            | Agent
            | AgentToolGrant
            | AuthPrincipal
            | ToolDefinition
            | EvaluationCase
            | PolicyDocument
        ],
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
                "package_name": "agents-should-survive-failure",
                "entry_point": (
                    "agents_should_survive_failure.workflows.vendor_onboarding:"
                    "VendorOnboardingWorkflow"
                ),
                "manifest": {},
                "input_schema": {},
                "output_schema": {},
                "compatibility": ">=1.0.0,<2.0.0",
                "integrity_digest": "0" * 64,
                "status": AgentStatus.ACTIVE,
                "configuration": {"model_provider": "deterministic_mock"},
            },
        ),
        (
            Agent,
            {
                "id": seed_id("agent:refund:v1"),
                "name": "refund",
                "version": "1",
                "workflow_type": "refund",
                "package_name": "agents-should-survive-failure",
                "entry_point": "agents_should_survive_failure.workflows.refund:RefundWorkflow",
                "manifest": {},
                "input_schema": {},
                "output_schema": {},
                "compatibility": ">=1.0.0,<2.0.0",
                "integrity_digest": "0" * 64,
                "status": AgentStatus.ACTIVE,
                "configuration": {"model_provider": "deterministic_mock"},
            },
        ),
        (
            ToolDefinition,
            {
                "id": seed_id("tool:vendor-database-query:v1"),
                "name": GovernedToolName.VENDOR_DATABASE_QUERY.value,
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
            AgentToolGrant,
            {
                "id": seed_id("agent-tool-grant:vendor-onboarding:v1:vendor"),
                "agent_id": seed_id("agent:vendor-onboarding:v1"),
                "tool_definition_id": seed_id("tool:vendor-database-query:v1"),
                "policy_version": "1",
                "policy_hash": "d6a183a8ad68d8aafdfced0f5b1d14de51ae91de2c9da1fd99989f3182a64cd0",
            },
        ),
        *(
            (
                ToolDefinition,
                {
                    "id": seed_id(f"tool:{name}:v1"),
                    "name": name,
                    "version": "1",
                    "description": description,
                    "input_schema": schema,
                    "output_schema": output,
                    "permissions": [permission],
                    "risk_class": ToolRiskClass.READ_ONLY,
                    "timeout_seconds": 10,
                    "approval_required": False,
                    "enabled": True,
                },
            )
            for name, description, schema, output, permission in (
                (
                    "order_details",
                    "Read synthetic order details.",
                    {
                        "type": "object",
                        "properties": {"order_id": {"type": "string"}},
                        "required": ["order_id"],
                    },
                    {"type": "object"},
                    "orders:read",
                ),
                (
                    "refund_policy",
                    "Read synthetic refund policy.",
                    {
                        "type": "object",
                        "properties": {"reason": {"type": "string"}},
                        "required": ["reason"],
                    },
                    {"type": "object"},
                    "policy:read",
                ),
            )
        ),
        *(
            (
                AgentToolGrant,
                {
                    "id": seed_id(f"agent-tool-grant:refund:{name}"),
                    "agent_id": seed_id("agent:refund:v1"),
                    "tool_definition_id": seed_id(f"tool:{name}:v1"),
                    "policy_version": "1",
                    "policy_hash": (
                        "d6a183a8ad68d8aafdfced0f5b1d14de51ae91de2c9da1fd99989f3182a64cd0"
                    ),
                },
            )
            for name in ("order_details", "refund_policy")
        ),
        (
            ToolDefinition,
            {
                "id": seed_id("tool:internal-policy-search:v1"),
                "name": GovernedToolName.INTERNAL_POLICY_SEARCH.value,
                "version": "1",
                "description": "Read synthetic internal policy evidence.",
                "input_schema": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}},
                    "required": ["query"],
                    "additionalProperties": False,
                },
                "permissions": ["policy:read"],
                "output_schema": {
                    "type": "object",
                    "properties": {"citations": {"type": "array"}},
                    "required": ["citations"],
                    "additionalProperties": False,
                },
                "risk_class": ToolRiskClass.READ_ONLY,
                "timeout_seconds": 10,
                "approval_required": False,
                "enabled": True,
            },
        ),
        (
            AgentToolGrant,
            {
                "id": seed_id("agent-tool-grant:vendor-onboarding:v1:policy"),
                "agent_id": seed_id("agent:vendor-onboarding:v1"),
                "tool_definition_id": seed_id("tool:internal-policy-search:v1"),
                "policy_version": "1",
                "policy_hash": "d6a183a8ad68d8aafdfced0f5b1d14de51ae91de2c9da1fd99989f3182a64cd0",
            },
        ),
        (
            ToolDefinition,
            {
                "id": seed_id("tool:synthetic-email-send:v1"),
                "name": GovernedToolName.SYNTHETIC_EMAIL_SEND.value,
                "version": "1",
                "description": "Persist a synthetic email after a durable approval decision.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "recipient": {"type": "string"},
                        "subject": {"type": "string"},
                        "body": {"type": "string"},
                    },
                    "required": ["recipient", "subject", "body"],
                    "additionalProperties": False,
                },
                "permissions": ["email:send"],
                "output_schema": {
                    "type": "object",
                    "properties": {"message_id": {"type": "string"}, "status": {"type": "string"}},
                    "required": ["message_id", "status"],
                    "additionalProperties": False,
                },
                "risk_class": ToolRiskClass.REVERSIBLE_WRITE,
                "timeout_seconds": 10,
                "approval_required": True,
                "enabled": True,
            },
        ),
        (
            AgentToolGrant,
            {
                "id": seed_id("agent-tool-grant:vendor-onboarding:v1:email"),
                "agent_id": seed_id("agent:vendor-onboarding:v1"),
                "tool_definition_id": seed_id("tool:synthetic-email-send:v1"),
                "policy_version": "1",
                "policy_hash": "d6a183a8ad68d8aafdfced0f5b1d14de51ae91de2c9da1fd99989f3182a64cd0",
            },
        ),
        *evaluation_case_seed_rows(),
        (
            PolicyDocument,
            {
                "id": seed_id("policy:vendor-approval:0"),
                "title": "Vendor Approval Policy",
                "source_uri": "seed://policy/vendor-approval",
                "chunk_index": 0,
                "content": (
                    "All vendors require an authorized approval after deterministic risk review."
                ),
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
            statement = insert(model).values(**values)
            if model in {Agent, AgentToolGrant, ToolDefinition}:
                await connection.execute(
                    statement.on_conflict_do_nothing(index_elements=[model.id])
                )
                continue
            if model is EvaluationCase:
                case_id = values["id"]
                expected_hash = values["content_sha256"]
                if not isinstance(case_id, uuid.UUID) or not isinstance(expected_hash, str):
                    raise TypeError("evaluation seed identity and digest must be typed")
                await connection.execute(
                    statement.on_conflict_do_nothing(index_elements=[EvaluationCase.id])
                )
                # ``enabled`` is an operator-controlled switch, not reviewed contract content.
                # Reseeding must preserve that operational choice while still rejecting drift in
                # every versioned field bound by ``content_sha256``.
                content_fields = tuple(key for key in values if key not in {"id", "enabled"})
                result = await connection.execute(
                    select(*(getattr(EvaluationCase, key) for key in content_fields)).where(
                        EvaluationCase.id == case_id
                    )
                )
                persisted = result.mappings().one_or_none()
                if persisted is None:
                    raise RuntimeError(
                        "evaluation case seed conflicted with a different persisted identity"
                    )
                mismatched_fields = [key for key in content_fields if persisted[key] != values[key]]
                if mismatched_fields:
                    fields = ", ".join(sorted(mismatched_fields))
                    raise RuntimeError(
                        "reviewed evaluation case content changed without a new suite version and "
                        f"corresponding case version; mismatched fields: {fields}"
                    )
                continue
            update_values = {
                ("metadata" if key == "metadata_" else key): getattr(
                    statement.excluded, "metadata" if key == "metadata_" else key
                )
                for key in values
                if key not in {"id", "created_at", "updated_at"}
            }
            statement = statement.on_conflict_do_update(
                index_elements=[model.id], set_=update_values
            )
            await connection.execute(statement)
