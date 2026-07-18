"""SQLAlchemy mappings for application-owned relational state."""

import enum
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from pgvector.sqlalchemy import HALFVEC  # pyright: ignore[reportMissingTypeStubs]
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utc_now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=utc_now, onupdate=utc_now
    )


class IdMixin:
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)


class UserStatus(enum.StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"


class PrincipalType(enum.StrEnum):
    USER = "user"
    SERVICE = "service"
    AGENT = "agent"


class PrincipalStatus(enum.StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"


class AgentStatus(enum.StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"


class RunStatus(enum.StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING = "waiting"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


class WorkflowStartStatus(enum.StrEnum):
    PENDING = "pending"
    STARTED = "started"
    FAILED = "failed"


class VendorStatus(enum.StrEnum):
    SUBMITTED = "submitted"
    UNDER_REVIEW = "under_review"
    APPROVED = "approved"
    REJECTED = "rejected"


class ApprovalStatus(enum.StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


class InvocationStatus(enum.StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    DENIED = "denied"


class ToolRiskClass(enum.StrEnum):
    READ_ONLY = "read_only"
    REVERSIBLE_WRITE = "reversible_write"
    SENSITIVE_WRITE = "sensitive_write"
    IRREVERSIBLE = "irreversible"


class EvaluationStatus(enum.StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class EvaluationResultStatus(enum.StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"


class FaultPlanStatus(enum.StrEnum):
    ACTIVE = "active"
    EXHAUSTED = "exhausted"
    CLEARED = "cleared"


def _fault_plan_status_values(_: type[FaultPlanStatus]) -> list[str]:
    """Match the lowercase labels created by the fault-plan migration."""

    return [item.value for item in FaultPlanStatus]


class User(IdMixin, TimestampMixin, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[UserStatus] = mapped_column(Enum(UserStatus, name="user_status"), nullable=False)


class AuthPrincipal(IdMixin, TimestampMixin, Base):
    __tablename__ = "auth_principals"
    __table_args__ = (
        CheckConstraint(
            "(principal_type = 'USER' AND user_id IS NOT NULL AND agent_id IS NULL) OR "
            "(principal_type = 'AGENT' AND user_id IS NULL AND agent_id IS NOT NULL) OR "
            "(principal_type = 'SERVICE' AND user_id IS NULL AND agent_id IS NULL)",
            name="ck_auth_principal_identity",
        ),
    )

    principal_type: Mapped[PrincipalType] = mapped_column(
        Enum(PrincipalType, name="principal_type"), nullable=False
    )
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[PrincipalStatus] = mapped_column(
        Enum(PrincipalStatus, name="principal_status"), nullable=False
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), unique=True
    )
    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agents.id", ondelete="RESTRICT"), unique=True
    )


class APIKey(IdMixin, TimestampMixin, Base):
    __tablename__ = "api_keys"
    __table_args__ = (
        UniqueConstraint("key_identifier", name="uq_api_key_identifier"),
        Index("ix_api_keys_principal", "principal_id"),
    )

    principal_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("auth_principals.id", ondelete="RESTRICT"), nullable=False
    )
    key_identifier: Mapped[str] = mapped_column(String(32), nullable=False)
    key_prefix: Mapped[str] = mapped_column(String(20), nullable=False)
    last_four: Mapped[str] = mapped_column(String(4), nullable=False)
    secret_hash: Mapped[str] = mapped_column(Text, nullable=False)
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    scopes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Agent(IdMixin, TimestampMixin, Base):
    __tablename__ = "agents"
    __table_args__ = (UniqueConstraint("name", "version", name="uq_agents_name_version"),)

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    version: Mapped[str] = mapped_column(String(40), nullable=False)
    workflow_type: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    package_name: Mapped[str] = mapped_column(String(120), nullable=False, default="legacy-agent")
    entry_point: Mapped[str] = mapped_column(
        String(240), nullable=False, default="legacy:unavailable"
    )
    manifest: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    input_schema: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    output_schema: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    compatibility: Mapped[str] = mapped_column(String(120), nullable=False, default="legacy")
    integrity_digest: Mapped[str] = mapped_column(String(64), nullable=False, default="0" * 64)
    status: Mapped[AgentStatus] = mapped_column(
        Enum(AgentStatus, name="agent_status"), nullable=False
    )
    configuration: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class Vendor(IdMixin, TimestampMixin, Base):
    __tablename__ = "vendors"
    __table_args__ = (
        CheckConstraint(
            "risk_score IS NULL OR risk_score BETWEEN 0 AND 100", name="ck_vendor_risk"
        ),
        UniqueConstraint("external_reference", name="uq_vendor_external_reference"),
    )

    external_reference: Mapped[str] = mapped_column(String(120), nullable=False)
    legal_name: Mapped[str] = mapped_column(String(240), nullable=False)
    jurisdiction: Mapped[str] = mapped_column(String(2), nullable=False)
    contact_email: Mapped[str] = mapped_column(String(320), nullable=False)
    status: Mapped[VendorStatus] = mapped_column(
        Enum(VendorStatus, name="vendor_status"), nullable=False, index=True
    )
    risk_score: Mapped[int | None] = mapped_column(Integer)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")

    __mapper_args__: dict[str, Any] = {"version_id_col": version}  # noqa: RUF012


class WorkflowRun(IdMixin, TimestampMixin, Base):
    __tablename__ = "workflow_runs"
    __table_args__ = (
        UniqueConstraint(
            "requested_by_id", "idempotency_key", name="uq_workflow_run_principal_idempotency_key"
        ),
        UniqueConstraint("temporal_workflow_id", name="uq_workflow_run_temporal_id"),
        Index("ix_workflow_runs_type_status_created", "workflow_type", "status", "created_at"),
    )

    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id", ondelete="RESTRICT"))
    parent_workflow_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="RESTRICT"), index=True
    )
    root_workflow_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    delegation_depth: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    vendor_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("vendors.id", ondelete="RESTRICT")
    )
    requested_by_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("auth_principals.id", ondelete="RESTRICT")
    )
    workflow_type: Mapped[str] = mapped_column(String(120), nullable=False)
    temporal_workflow_id: Mapped[str] = mapped_column(String(240), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(240), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(
        String(64), nullable=False, default="legacy", server_default="legacy"
    )
    status: Mapped[RunStatus] = mapped_column(Enum(RunStatus, name="run_status"), nullable=False)
    input_summary: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    result_summary: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")

    __mapper_args__: dict[str, Any] = {"version_id_col": version}  # noqa: RUF012


class RunDelegation(IdMixin, TimestampMixin, Base):
    """Immutable parent-to-child authority carve-out for one managed-agent child run."""

    __tablename__ = "run_delegations"
    __table_args__ = (
        UniqueConstraint("child_workflow_run_id", name="uq_run_delegation_child"),
        UniqueConstraint(
            "parent_workflow_run_id", "idempotency_key", name="uq_run_delegation_parent_key"
        ),
        CheckConstraint("delegation_depth >= 1", name="ck_run_delegation_depth"),
    )

    parent_workflow_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    child_workflow_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    root_workflow_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    delegation_depth: Mapped[int] = mapped_column(Integer, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(240), nullable=False)
    budget_limits: Mapped[dict[str, int]] = mapped_column(JSONB, nullable=False)
    allowed_tool_definition_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False)


class WorkflowStartAttempt(IdMixin, TimestampMixin, Base):
    __tablename__ = "workflow_start_attempts"
    __table_args__ = (UniqueConstraint("workflow_run_id", name="uq_workflow_start_attempt_run"),)

    workflow_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[WorkflowStartStatus] = mapped_column(
        Enum(WorkflowStartStatus, name="workflow_start_status"), nullable=False
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    error_category: Mapped[str | None] = mapped_column(String(80))
    attempt_token: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    last_attempted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WorkflowEvent(IdMixin, Base):
    __tablename__ = "workflow_events"
    __table_args__ = (
        UniqueConstraint("workflow_run_id", "sequence", name="uq_workflow_event_sequence"),
        Index("ix_workflow_events_run_occurred", "workflow_run_id", "occurred_at"),
    )

    workflow_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="CASCADE"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(120), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), default=utc_now
    )


class VendorDocument(IdMixin, TimestampMixin, Base):
    __tablename__ = "vendor_documents"
    __table_args__ = (
        UniqueConstraint("vendor_id", "content_sha256", name="uq_vendor_document_content"),
    )

    vendor_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("vendors.id", ondelete="CASCADE"), nullable=False, index=True
    )
    document_type: Mapped[str] = mapped_column(String(80), nullable=False)
    filename: Mapped[str] = mapped_column(String(240), nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_uri: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict
    )


class ApprovedVendor(IdMixin, Base):
    __tablename__ = "approved_vendors"
    __table_args__ = (
        UniqueConstraint("vendor_id", name="uq_approved_vendor_vendor_id"),
        UniqueConstraint("approval_request_id", name="uq_approved_vendor_approval_request_id"),
    )

    vendor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("vendors.id", ondelete="RESTRICT"))
    workflow_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="RESTRICT")
    )
    approval_request_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("approval_requests.id", ondelete="RESTRICT")
    )
    approved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), default=utc_now
    )


class RefundDecision(IdMixin, TimestampMixin, Base):
    __tablename__ = "refund_decisions"
    __table_args__ = (
        UniqueConstraint("workflow_run_id", "idempotency_key", name="uq_refund_decision_key"),
    )

    workflow_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="CASCADE")
    )
    refund_id: Mapped[str] = mapped_column(String(120), nullable=False)
    order_id: Mapped[str] = mapped_column(String(120), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    decision: Mapped[str] = mapped_column(String(40), nullable=False)
    risk_score: Mapped[int] = mapped_column(Integer, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(240), nullable=False)


class RefundProjection(IdMixin, TimestampMixin, Base):
    __tablename__ = "refund_projections"
    __table_args__ = (UniqueConstraint("refund_id", name="uq_refund_projection_refund"),)

    workflow_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="CASCADE")
    )
    refund_id: Mapped[str] = mapped_column(String(120), nullable=False)
    order_id: Mapped[str] = mapped_column(String(120), nullable=False)
    customer_id: Mapped[str] = mapped_column(String(120), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(240), nullable=False, unique=True)


class RefundEmail(IdMixin, TimestampMixin, Base):
    __tablename__ = "refund_emails"
    __table_args__ = (
        UniqueConstraint("workflow_run_id", "idempotency_key", name="uq_refund_email_key"),
    )

    workflow_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="CASCADE")
    )
    customer_id: Mapped[str] = mapped_column(String(120), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(240), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="simulated")


class ApprovalRequest(IdMixin, TimestampMixin, Base):
    __tablename__ = "approval_requests"
    __table_args__ = (
        UniqueConstraint("workflow_run_id", "request_key", name="uq_approval_request_key"),
        Index("ix_approval_requests_status_created", "status", "created_at"),
    )

    workflow_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="CASCADE"), nullable=False
    )
    request_key: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[ApprovalStatus] = mapped_column(
        Enum(ApprovalStatus, name="approval_status"), nullable=False
    )
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")

    __mapper_args__: dict[str, Any] = {"version_id_col": version}  # noqa: RUF012


class ApprovalDecision(IdMixin, Base):
    __tablename__ = "approval_decisions"
    __table_args__ = (
        UniqueConstraint("approval_request_id", "idempotency_key", name="uq_approval_decision_key"),
    )

    approval_request_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("approval_requests.id", ondelete="CASCADE"), nullable=False, index=True
    )
    decided_by_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("auth_principals.id", ondelete="RESTRICT")
    )
    decision: Mapped[ApprovalStatus] = mapped_column(
        Enum(ApprovalStatus, name="approval_decision"), nullable=False
    )
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(240), nullable=False)
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), default=utc_now
    )


class ToolDefinition(IdMixin, TimestampMixin, Base):
    __tablename__ = "tool_definitions"
    __table_args__ = (UniqueConstraint("name", "version", name="uq_tool_definition_version"),)

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    version: Mapped[str] = mapped_column(String(40), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    input_schema: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    output_schema: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    permissions: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    risk_class: Mapped[ToolRiskClass] = mapped_column(
        Enum(ToolRiskClass, name="tool_risk_class"),
        nullable=False,
        default=ToolRiskClass.READ_ONLY,
        server_default="READ_ONLY",
    )
    timeout_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, default=10, server_default="10"
    )
    approval_required: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )


class AgentToolGrant(IdMixin, TimestampMixin, Base):
    """Reviewed immutable capability grant for one registered agent version."""

    __tablename__ = "agent_tool_grants"
    __table_args__ = (
        UniqueConstraint("agent_id", "tool_definition_id", name="uq_agent_tool_grant"),
    )

    agent_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agents.id", ondelete="RESTRICT"), nullable=False
    )
    tool_definition_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tool_definitions.id", ondelete="RESTRICT"), nullable=False
    )
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class RunToolGrantSnapshot(IdMixin, Base):
    """Grant selected at run creation; this is the authorization source during execution."""

    __tablename__ = "run_tool_grant_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "workflow_run_id", "tool_definition_id", name="uq_run_tool_grant_snapshot"
        ),
    )

    workflow_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="CASCADE"), nullable=False
    )
    tool_definition_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tool_definitions.id", ondelete="RESTRICT"), nullable=False
    )
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class ToolRunBinding(IdMixin, TimestampMixin, Base):
    """The immutable tool definition selected for a logical tool name in one run."""

    __tablename__ = "tool_run_bindings"
    __table_args__ = (
        UniqueConstraint("workflow_run_id", "tool_name", name="uq_tool_run_binding_name"),
    )

    workflow_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="CASCADE"), nullable=False
    )
    tool_name: Mapped[str] = mapped_column(String(120), nullable=False)
    tool_definition_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tool_definitions.id", ondelete="RESTRICT"), nullable=False
    )


class ToolInvocation(IdMixin, TimestampMixin, Base):
    __tablename__ = "tool_invocations"
    __table_args__ = (
        UniqueConstraint("workflow_run_id", "idempotency_key", name="uq_tool_invocation_key"),
        Index("ix_tool_invocations_run_status", "workflow_run_id", "status"),
    )

    workflow_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="CASCADE"), nullable=False
    )
    tool_definition_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tool_definitions.id", ondelete="RESTRICT"), nullable=True
    )
    requested_tool_name: Mapped[str] = mapped_column(String(120), nullable=False)
    requested_tool_version: Mapped[str] = mapped_column(String(40), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(240), nullable=False)
    status: Mapped[InvocationStatus] = mapped_column(
        Enum(InvocationStatus, name="invocation_status"), nullable=False
    )
    arguments: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    argument_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, default="legacy")
    correlation_id: Mapped[str] = mapped_column(String(160), nullable=False, default="legacy")
    result_summary: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error_category: Mapped[str | None] = mapped_column(String(80))


class SyntheticEmailMessage(IdMixin, TimestampMixin, Base):
    """Synthetic outbound message created only by the governed email tool."""

    __tablename__ = "synthetic_email_messages"
    __table_args__ = (
        UniqueConstraint("workflow_run_id", "idempotency_key", name="uq_synthetic_email_run_key"),
        Index("ix_synthetic_email_run_created", "workflow_run_id", "created_at"),
    )

    workflow_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="CASCADE"), nullable=False
    )
    idempotency_key: Mapped[str] = mapped_column(String(240), nullable=False)
    recipient: Mapped[str] = mapped_column(String(320), nullable=False)
    subject: Mapped[str] = mapped_column(String(240), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="simulated")


class ModelCall(IdMixin, Base):
    __tablename__ = "model_calls"
    __table_args__ = (Index("ix_model_calls_run_created", "workflow_run_id", "created_at"),)

    workflow_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    model: Mapped[str] = mapped_column(String(160), nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[InvocationStatus] = mapped_column(
        Enum(InvocationStatus, name="model_call_status"), nullable=False
    )
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    error_category: Mapped[str | None] = mapped_column(String(80))
    decision_summary: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), default=utc_now
    )


class PolicyDocument(IdMixin, TimestampMixin, Base):
    __tablename__ = "policy_documents"
    __table_args__ = (
        UniqueConstraint("source_uri", "chunk_index", name="uq_policy_document_chunk"),
        Index(
            "ix_policy_documents_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "halfvec_cosine_ops"},
        ),
    )

    title: Mapped[str] = mapped_column(String(240), nullable=False)
    source_uri: Mapped[str] = mapped_column(Text, nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    embedding_model: Mapped[str] = mapped_column(String(160), nullable=False)
    embedding: Mapped[list[float]] = mapped_column(HALFVEC(2048), nullable=False)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict
    )


class AuditEvent(IdMixin, Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_audit_event_idempotency_key"),
        Index("ix_audit_events_run_occurred", "workflow_run_id", "occurred_at"),
        Index("ix_audit_events_actor_occurred", "actor_id", "occurred_at"),
    )

    workflow_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="SET NULL")
    )
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("auth_principals.id", ondelete="SET NULL")
    )
    action: Mapped[str] = mapped_column(String(120), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(120), nullable=False)
    resource_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    idempotency_key: Mapped[str] = mapped_column(String(240), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), default=utc_now
    )


class FaultInjectionPlan(IdMixin, TimestampMixin, Base):
    """A coordinated, test-only plan consumable exactly once per configured trigger."""

    __tablename__ = "fault_injection_plans"
    __table_args__ = (
        UniqueConstraint("fault_point", "scope_key", name="uq_fault_injection_plan_scope"),
        CheckConstraint("trigger_count >= 1", name="ck_fault_plan_trigger_count"),
        CheckConstraint("remaining_triggers >= 0", name="ck_fault_plan_remaining_triggers"),
        CheckConstraint("delay_ms >= 0", name="ck_fault_plan_delay_ms"),
        Index("ix_fault_injection_plans_active", "status", "fault_point"),
    )

    fault_point: Mapped[str] = mapped_column(String(160), nullable=False)
    scope_key: Mapped[str] = mapped_column(String(240), nullable=False, default="global")
    category: Mapped[str] = mapped_column(String(80), nullable=False)
    trigger_count: Mapped[int] = mapped_column(Integer, nullable=False)
    remaining_triggers: Mapped[int] = mapped_column(Integer, nullable=False)
    delay_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    status: Mapped[FaultPlanStatus] = mapped_column(
        Enum(
            FaultPlanStatus,
            name="fault_plan_status",
            values_callable=_fault_plan_status_values,
        ),
        nullable=False,
    )
    safe_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class FaultInjectionConsumption(IdMixin, Base):
    """Append-only audit evidence for an atomically consumed fault trigger."""

    __tablename__ = "fault_injection_consumptions"
    __table_args__ = (Index("ix_fault_consumption_plan_created", "fault_plan_id", "created_at"),)

    fault_plan_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("fault_injection_plans.id", ondelete="CASCADE"), nullable=False
    )
    fault_point: Mapped[str] = mapped_column(String(160), nullable=False)
    scope_key: Mapped[str] = mapped_column(String(240), nullable=False)
    category: Mapped[str] = mapped_column(String(80), nullable=False)
    remaining_triggers: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), default=utc_now
    )


class RunCheckpoint(IdMixin, TimestampMixin, Base):
    """Latest immutable checkpoint value for a named agent-run checkpoint."""

    __tablename__ = "run_checkpoints"
    __table_args__ = (
        UniqueConstraint("workflow_run_id", "name", name="uq_run_checkpoint_name"),
        CheckConstraint("size_bytes >= 0", name="ck_run_checkpoint_size"),
        CheckConstraint("digest_sha256 ~ '^[0-9a-f]{64}$'", name="ck_run_checkpoint_digest"),
    )

    workflow_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agents.id", ondelete="RESTRICT"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(40), nullable=False)
    value: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    digest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)


class RunArtifact(IdMixin, TimestampMixin, Base):
    """Bounded inline artifact with provenance and cryptographic integrity metadata."""

    __tablename__ = "run_artifacts"
    __table_args__ = (
        UniqueConstraint(
            "workflow_run_id", "name", "digest_sha256", name="uq_run_artifact_name_digest"
        ),
        CheckConstraint("size_bytes >= 0", name="ck_run_artifact_size"),
        CheckConstraint("digest_sha256 ~ '^[0-9a-f]{64}$'", name="ck_run_artifact_digest"),
    )

    workflow_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agents.id", ondelete="RESTRICT"), nullable=False
    )
    parent_artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("run_artifacts.id", ondelete="RESTRICT")
    )
    name: Mapped[str] = mapped_column(String(240), nullable=False)
    content_type: Mapped[str] = mapped_column(String(120), nullable=False)
    digest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)


class RunBudget(IdMixin, TimestampMixin, Base):
    """Pinned limits and durable cumulative usage for one workflow run."""

    __tablename__ = "run_budgets"
    __table_args__ = (UniqueConstraint("workflow_run_id", name="uq_run_budget"),)

    workflow_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="CASCADE"), nullable=False
    )
    limits: Mapped[dict[str, int]] = mapped_column(JSONB, nullable=False)
    consumed: Mapped[dict[str, int]] = mapped_column(JSONB, nullable=False, default=dict)
    exhausted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EvaluationRun(IdMixin, TimestampMixin, Base):
    __tablename__ = "evaluation_runs"
    __table_args__ = (
        UniqueConstraint(
            "requested_by_id",
            "idempotency_key",
            name="uq_evaluation_run_principal_idempotency_key",
        ),
        CheckConstraint(
            "dataset_sha256 ~ '^[0-9a-f]{64}$'", name="ck_evaluation_run_dataset_sha256"
        ),
    )

    requested_by_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("auth_principals.id", ondelete="RESTRICT")
    )
    idempotency_key: Mapped[str] = mapped_column(String(240), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    suite_slug: Mapped[str] = mapped_column(String(160), nullable=False)
    suite_version: Mapped[str] = mapped_column(String(40), nullable=False)
    suite_schema_version: Mapped[str] = mapped_column(String(40), nullable=False)
    dataset_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[EvaluationStatus] = mapped_column(
        Enum(EvaluationStatus, name="evaluation_status"), nullable=False
    )
    configuration: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EvaluationCase(IdMixin, TimestampMixin, Base):
    __tablename__ = "evaluation_cases"
    __table_args__ = (
        UniqueConstraint(
            "suite_slug", "suite_version", "slug", name="uq_evaluation_case_suite_slug"
        ),
        CheckConstraint(
            "content_sha256 ~ '^[0-9a-f]{64}$'", name="ck_evaluation_case_content_sha256"
        ),
    )

    suite_slug: Mapped[str] = mapped_column(String(160), nullable=False)
    suite_version: Mapped[str] = mapped_column(String(40), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(40), nullable=False)
    slug: Mapped[str] = mapped_column(String(160), nullable=False)
    version: Mapped[str] = mapped_column(String(40), nullable=False)
    workflow_type: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    scenario_type: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    input_data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    setup: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    driver: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    expected_outcome: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    evidence_requirements: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    reviewed_by: Mapped[str] = mapped_column(String(160), nullable=False)
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )


class EvaluationResult(IdMixin, Base):
    __tablename__ = "evaluation_results"
    __table_args__ = (
        UniqueConstraint(
            "evaluation_run_id", "evaluation_case_id", name="uq_evaluation_result_case"
        ),
        CheckConstraint("score BETWEEN 0 AND 1", name="ck_evaluation_result_score"),
        CheckConstraint(
            "duration_ms IS NULL OR duration_ms >= 0", name="ck_evaluation_result_duration"
        ),
        CheckConstraint(
            "case_content_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_evaluation_result_case_content_sha256",
        ),
    )

    evaluation_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("evaluation_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    evaluation_case_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("evaluation_cases.id", ondelete="RESTRICT"), nullable=False
    )
    case_slug: Mapped[str] = mapped_column(String(160), nullable=False)
    case_version: Mapped[str] = mapped_column(String(40), nullable=False)
    case_content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    workflow_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="SET NULL")
    )
    status: Mapped[EvaluationResultStatus] = mapped_column(
        Enum(EvaluationResultStatus, name="evaluation_result_status"), nullable=False
    )
    score: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)
    expected_outcome: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    actual_outcome: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    failure_category: Mapped[str | None] = mapped_column(String(120))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    evidence_summary: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), default=utc_now
    )
