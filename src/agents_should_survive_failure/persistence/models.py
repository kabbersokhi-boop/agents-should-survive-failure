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


class VendorStatus(enum.StrEnum):
    SUBMITTED = "submitted"
    UNDER_REVIEW = "under_review"
    APPROVED = "approved"
    REJECTED = "rejected"


class ApprovalStatus(enum.StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    INFORMATION_REQUESTED = "information_requested"
    CANCELLED = "cancelled"


class InvocationStatus(enum.StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    DENIED = "denied"


class EvaluationStatus(enum.StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class EvaluationResultStatus(enum.StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"


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
        UniqueConstraint("idempotency_key", name="uq_workflow_run_idempotency_key"),
        UniqueConstraint("temporal_workflow_id", name="uq_workflow_run_temporal_id"),
        Index("ix_workflow_runs_type_status_created", "workflow_type", "status", "created_at"),
    )

    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id", ondelete="RESTRICT"))
    vendor_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("vendors.id", ondelete="RESTRICT")
    )
    requested_by_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    workflow_type: Mapped[str] = mapped_column(String(120), nullable=False)
    temporal_workflow_id: Mapped[str] = mapped_column(String(240), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(240), nullable=False)
    status: Mapped[RunStatus] = mapped_column(Enum(RunStatus, name="run_status"), nullable=False)
    input_summary: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    result_summary: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")

    __mapper_args__: dict[str, Any] = {"version_id_col": version}  # noqa: RUF012


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
    decided_by_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
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
    permissions: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
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
    tool_definition_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tool_definitions.id", ondelete="RESTRICT"), nullable=False
    )
    idempotency_key: Mapped[str] = mapped_column(String(240), nullable=False)
    status: Mapped[InvocationStatus] = mapped_column(
        Enum(InvocationStatus, name="invocation_status"), nullable=False
    )
    arguments: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    result_summary: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error_category: Mapped[str | None] = mapped_column(String(80))


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
    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    action: Mapped[str] = mapped_column(String(120), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(120), nullable=False)
    resource_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    idempotency_key: Mapped[str] = mapped_column(String(240), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), default=utc_now
    )


class EvaluationRun(IdMixin, TimestampMixin, Base):
    __tablename__ = "evaluation_runs"
    __table_args__ = (UniqueConstraint("idempotency_key", name="uq_evaluation_run_key"),)

    requested_by_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    idempotency_key: Mapped[str] = mapped_column(String(240), nullable=False)
    status: Mapped[EvaluationStatus] = mapped_column(
        Enum(EvaluationStatus, name="evaluation_status"), nullable=False
    )
    configuration: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EvaluationCase(IdMixin, TimestampMixin, Base):
    __tablename__ = "evaluation_cases"
    __table_args__ = (UniqueConstraint("slug", "version", name="uq_evaluation_case_version"),)

    slug: Mapped[str] = mapped_column(String(160), nullable=False)
    version: Mapped[str] = mapped_column(String(40), nullable=False)
    workflow_type: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    input_data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    expected_outcome: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
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
    )

    evaluation_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("evaluation_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    evaluation_case_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("evaluation_cases.id", ondelete="RESTRICT"), nullable=False
    )
    workflow_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="SET NULL")
    )
    status: Mapped[EvaluationResultStatus] = mapped_column(
        Enum(EvaluationResultStatus, name="evaluation_result_status"), nullable=False
    )
    score: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), default=utc_now
    )
