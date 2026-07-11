"""Serializable contracts shared by the API, activities, and Temporal workflow."""

from dataclasses import dataclass
from enum import StrEnum

WORKFLOW_TYPE = "vendor_onboarding"
TASK_QUEUE = "vendor-onboarding"


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
