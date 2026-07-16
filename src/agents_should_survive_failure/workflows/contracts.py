"""Serializable contracts shared by the API, activities, and Temporal workflow."""

from dataclasses import dataclass
from enum import StrEnum

WORKFLOW_TYPE = "vendor_onboarding"
TASK_QUEUE = "vendor-onboarding"


class WorkflowEventType(StrEnum):
    """Persisted workflow event identifiers owned by the production runtime."""

    REVIEW_STARTED = "review.started"
    RISK_ASSESSED = "risk.assessed"
    RISK_POLICY_CONTEXT = "risk.policy_context"
    APPROVAL_REQUESTED = "approval.requested"
    APPROVAL_DECIDED = "approval.decided"
    REVIEW_CANCELLED = "review.cancelled"
    REVIEW_FAILED = "review.failed"


class GovernedToolName(StrEnum):
    """Registered governed-tool identifiers owned by the production runtime."""

    VENDOR_DATABASE_QUERY = "vendor_database_query"
    INTERNAL_POLICY_SEARCH = "internal_policy_search"
    SYNTHETIC_EMAIL_SEND = "synthetic_email_send"


class ApprovalDecisionType(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"


@dataclass(frozen=True)
class VendorOnboardingInput:
    run_id: str
    vendor_id: str


@dataclass(frozen=True)
class RiskAssessment:
    score: int
    summary: str


@dataclass(frozen=True)
class ApprovalDecisionInput:
    approval_request_id: str
    expected_version: int
    decision: ApprovalDecisionType
    decided_by_id: str
    rationale: str
    idempotency_key: str


@dataclass(frozen=True)
class WorkflowStatus:
    phase: str
    approval_request_id: str | None
    decision: ApprovalDecisionType | None
