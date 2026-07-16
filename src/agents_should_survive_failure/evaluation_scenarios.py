"""Versioned, reviewed contracts for real workflow evaluation scenarios."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import datetime
from enum import StrEnum
from functools import lru_cache
from importlib import resources
from pathlib import Path
from types import MappingProxyType
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

from agents_should_survive_failure.workflows.contracts import (
    GovernedToolName,
    WorkflowEventType,
)

ToolArgumentValue = str | int | bool | None


def _encode_canonical_json_special(value: object) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"unsupported canonical JSON value: {type(value).__name__}")


def _canonical_sha256(value: object) -> str:
    """Hash a contract using the locked Python/Pydantic serialization contract.

    This does not claim cross-language canonical JSON compatibility. Reviewed contracts use only
    JSON-safe primitives plus timezone-aware datetimes normalized by Pydantic.
    """

    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
        default=_encode_canonical_json_special,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class StrictContract(BaseModel):
    """Immutable evaluation data that rejects unknown fields."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class ScenarioType(StrEnum):
    LOW_RISK_APPROVED = "low_risk_approved"
    LOW_RISK_REJECTED = "low_risk_rejected"
    HIGH_RISK_APPROVED = "high_risk_approved"
    HIGH_RISK_REJECTED = "high_risk_rejected"
    CANCELLATION_BEFORE_APPROVAL = "cancellation_before_approval"
    CANCELLATION_WAITING_FOR_APPROVAL = "cancellation_waiting_for_approval"
    EARLY_APPROVAL_REJECTED = "early_approval_rejected"
    STALE_APPROVAL_REJECTED = "stale_approval_rejected"
    CONFLICTING_APPROVAL_REJECTED = "conflicting_approval_rejected"
    IDEMPOTENT_DECISION_REPLAY = "idempotent_decision_replay"
    DECISION_IDEMPOTENCY_CONFLICT = "decision_idempotency_conflict"
    MODEL_PROVIDER_FAILURE = "model_provider_failure"
    POLICY_RETRIEVAL_FAILURE = "policy_retrieval_failure"
    TOOL_PERMISSION_DENIED = "tool_permission_denied"
    TOOL_TIMEOUT_RETRY = "tool_timeout_retry"
    WORKER_RESTART_ACTIVE_ACTIVITY = "worker_restart_active_activity"
    WORKER_RESTART_WAITING_APPROVAL = "worker_restart_waiting_approval"
    WORKER_CRASH_AFTER_EFFECT_COMMIT = "worker_crash_after_effect_commit"
    DUPLICATE_PROJECTION_PREVENTED = "duplicate_projection_prevented"
    DUPLICATE_EMAIL_PREVENTED = "duplicate_email_prevented"
    AMBIGUOUS_WORKFLOW_START_RECOVERY = "ambiguous_workflow_start_recovery"
    DATABASE_TRANSIENT_FAILURE = "database_transient_failure"
    MALFORMED_TOOL_INPUT_REJECTED = "malformed_tool_input_rejected"
    UNAUTHORIZED_SENSITIVE_OPERATION = "unauthorized_sensitive_operation"


class ToolInvocationStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    DENIED = "denied"


class ApprovalDecision(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"


class ApprovalTiming(StrEnum):
    BEFORE_REQUEST = "before_request"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    AFTER_DECISION = "after_decision"


class ApprovalActor(StrEnum):
    AUTHORIZED = "authorized"
    UNAUTHORIZED = "unauthorized"


class ApprovalVersionMode(StrEnum):
    CURRENT = "current"
    STALE = "stale"


class IdempotencyKeyMode(StrEnum):
    NEW = "new"
    REUSE_PREVIOUS = "reuse_previous"


class ApprovalAttemptEffect(StrEnum):
    ACCEPTED = "accepted"
    IDEMPOTENT = "idempotent"
    REJECTED_EARLY = "rejected_early"
    REJECTED_STALE = "rejected_stale"
    REJECTED_CONFLICT = "rejected_conflict"
    REJECTED_IDEMPOTENCY_CONFLICT = "rejected_idempotency_conflict"
    FORBIDDEN = "forbidden"


class CancellationPoint(StrEnum):
    NONE = "none"
    BEFORE_APPROVAL = "before_approval"
    WAITING_FOR_APPROVAL = "waiting_for_approval"


class FaultCategory(StrEnum):
    RETRYABLE = "retryable"
    PERMANENT = "permanent"
    PROCESS_TERMINATION = "process_termination"
    AMBIGUOUS_HANDOFF = "ambiguous_handoff"


class ModelProviderMode(StrEnum):
    DETERMINISTIC = "deterministic"
    FAIL_EXPLANATION = "fail_explanation"


class ToolAttemptTiming(StrEnum):
    AFTER_RUN_CREATED = "after_run_created"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    AFTER_TERMINAL = "after_terminal"


class ToolAttemptActor(StrEnum):
    MANAGED_AGENT = "managed_agent"
    UNGRANTED_AGENT = "ungranted_agent"


class ToolAttemptEffect(StrEnum):
    REJECTED_INVALID_INPUT = "rejected_invalid_input"
    REJECTED_PERMISSION = "rejected_permission"


class EvidenceSource(StrEnum):
    WORKFLOW_RUN = "workflow_run"
    WORKFLOW_EVENTS = "workflow_events"
    APPROVALS = "approvals"
    TOOL_INVOCATIONS = "tool_invocations"
    MODEL_CALLS = "model_calls"
    BUSINESS_PROJECTIONS = "business_projections"
    SYNTHETIC_EMAILS = "synthetic_emails"
    AUDIT_EVENTS = "audit_events"
    WORKFLOW_START_ATTEMPTS = "workflow_start_attempts"


class VendorInput(StrictContract):
    external_reference_prefix: str = Field(min_length=3, max_length=80, pattern=r"^[a-z0-9-]+$")
    legal_name: str = Field(min_length=3, max_length=240)
    jurisdiction: str = Field(min_length=2, max_length=2, pattern=r"^[A-Z]{2}$")
    contact_email: str = Field(min_length=3, max_length=254, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class ApprovalAttempt(StrictContract):
    timing: ApprovalTiming
    decision: ApprovalDecision
    actor: ApprovalActor = ApprovalActor.AUTHORIZED
    version_mode: ApprovalVersionMode = ApprovalVersionMode.CURRENT
    idempotency_key_mode: IdempotencyKeyMode = IdempotencyKeyMode.NEW
    expected_effect: ApprovalAttemptEffect

    @model_validator(mode="after")
    def validate_attempt(self) -> ApprovalAttempt:
        effect = self.expected_effect
        if effect is ApprovalAttemptEffect.FORBIDDEN:
            if self.actor is not ApprovalActor.UNAUTHORIZED:
                raise ValueError("forbidden approval attempts must use an unauthorized actor")
            if self.idempotency_key_mode is not IdempotencyKeyMode.NEW:
                raise ValueError("forbidden approval attempts must use a new idempotency key")
        elif self.actor is ApprovalActor.UNAUTHORIZED:
            raise ValueError("unauthorized approval actors must expect a forbidden result")

        if effect is ApprovalAttemptEffect.ACCEPTED:
            if self.timing is not ApprovalTiming.WAITING_FOR_APPROVAL:
                raise ValueError("accepted approval attempts must occur while waiting")
            if self.version_mode is not ApprovalVersionMode.CURRENT:
                raise ValueError("accepted approval attempts must use the current version")
            if self.idempotency_key_mode is not IdempotencyKeyMode.NEW:
                raise ValueError("accepted approval attempts must use a new idempotency key")
        if effect is ApprovalAttemptEffect.REJECTED_EARLY and (
            self.timing is not ApprovalTiming.BEFORE_REQUEST
            or self.version_mode is not ApprovalVersionMode.CURRENT
            or self.idempotency_key_mode is not IdempotencyKeyMode.NEW
        ):
            raise ValueError(
                "early rejection must occur before the request with the current version "
                "and a new key"
            )
        if effect is ApprovalAttemptEffect.REJECTED_STALE:
            if self.timing is not ApprovalTiming.WAITING_FOR_APPROVAL:
                raise ValueError("stale rejection must occur while waiting for approval")
            if self.version_mode is not ApprovalVersionMode.STALE:
                raise ValueError("stale rejection must use a stale approval version")
            if self.idempotency_key_mode is not IdempotencyKeyMode.NEW:
                raise ValueError("stale rejection must use a new idempotency key")
        if effect in {
            ApprovalAttemptEffect.IDEMPOTENT,
            ApprovalAttemptEffect.REJECTED_IDEMPOTENCY_CONFLICT,
        }:
            if self.timing is not ApprovalTiming.AFTER_DECISION:
                raise ValueError("idempotency replays must occur after the accepted decision")
            if self.idempotency_key_mode is not IdempotencyKeyMode.REUSE_PREVIOUS:
                raise ValueError("idempotency replays must reuse the previous key")
            if self.version_mode is not ApprovalVersionMode.CURRENT:
                raise ValueError("idempotency replays must use the current approval version")
        if effect is ApprovalAttemptEffect.REJECTED_CONFLICT:
            if self.timing is not ApprovalTiming.AFTER_DECISION:
                raise ValueError("conflicting decisions must be attempted after acceptance")
            if self.idempotency_key_mode is not IdempotencyKeyMode.NEW:
                raise ValueError("decision conflicts must use a distinct idempotency key")
            if self.version_mode is not ApprovalVersionMode.CURRENT:
                raise ValueError("decision conflicts must use the current approval version")
        return self


class ToolAttempt(StrictContract):
    timing: ToolAttemptTiming
    actor: ToolAttemptActor
    tool_name: GovernedToolName
    arguments: Mapping[str, ToolArgumentValue]
    expected_effect: ToolAttemptEffect
    expected_error_category: str = Field(min_length=3, max_length=120)

    @field_validator("arguments", mode="after")
    @classmethod
    def freeze_arguments(
        cls, value: Mapping[str, ToolArgumentValue]
    ) -> Mapping[str, ToolArgumentValue]:
        return MappingProxyType(dict(value))

    @field_serializer("arguments")
    def serialize_arguments(
        self, value: Mapping[str, ToolArgumentValue]
    ) -> dict[str, ToolArgumentValue]:
        return dict(value)

    @model_validator(mode="after")
    def validate_attempt(self) -> ToolAttempt:
        if self.expected_effect is ToolAttemptEffect.REJECTED_INVALID_INPUT:
            if self.actor is not ToolAttemptActor.MANAGED_AGENT:
                raise ValueError("invalid-input probes must use the managed run agent")
            if self.expected_error_category != "invalid_arguments":
                raise ValueError("invalid-input attempts must expect invalid_arguments")
        if self.expected_effect is ToolAttemptEffect.REJECTED_PERMISSION:
            if self.actor is not ToolAttemptActor.UNGRANTED_AGENT:
                raise ValueError("permission probes must use an ungranted agent")
            if self.expected_error_category != "policy_denied":
                raise ValueError("permission probes must expect policy_denied")
        return self


class FaultPlan(StrictContract):
    fault_point: str = Field(min_length=3, max_length=160, pattern=r"^[a-z0-9_.-]+$")
    category: FaultCategory
    trigger_count: int = Field(default=1, ge=1, le=10)
    consume_once: bool = True
    retryable: bool
    delay_ms: int = Field(default=0, ge=0, le=60_000)
    expected_retry_count_min: int = Field(default=0, ge=0, le=20)

    @model_validator(mode="after")
    def validate_retry_contract(self) -> FaultPlan:
        if (
            self.category
            in {
                FaultCategory.RETRYABLE,
                FaultCategory.PROCESS_TERMINATION,
                FaultCategory.AMBIGUOUS_HANDOFF,
            }
            and not self.retryable
        ):
            raise ValueError("retryable recovery faults must set retryable=true")
        if self.category is FaultCategory.PERMANENT and self.retryable:
            raise ValueError("permanent faults must set retryable=false")
        if self.category is FaultCategory.PERMANENT and self.expected_retry_count_min != 0:
            raise ValueError("permanent faults cannot require retries")
        if not self.consume_once:
            raise ValueError("reviewed Phase B faults must use consume-once semantics")
        return self


class ScenarioSetup(StrictContract):
    model_provider_mode: ModelProviderMode = ModelProviderMode.DETERMINISTIC
    omitted_tool_grants: tuple[GovernedToolName, ...] = ()
    faults: tuple[FaultPlan, ...] = ()

    @model_validator(mode="after")
    def validate_setup(self) -> ScenarioSetup:
        if len(self.omitted_tool_grants) != len(set(self.omitted_tool_grants)):
            raise ValueError("omitted tool grants must be unique")
        fault_points = [fault.fault_point for fault in self.faults]
        if len(fault_points) != len(set(fault_points)):
            raise ValueError("fault points must be unique within a case")
        return self


class ScenarioDriver(StrictContract):
    final_decision: ApprovalDecision | None = None
    cancellation_point: CancellationPoint = CancellationPoint.NONE
    approval_attempts: tuple[ApprovalAttempt, ...] = ()
    tool_attempts: tuple[ToolAttempt, ...] = ()

    @model_validator(mode="after")
    def validate_terminal_driver(self) -> ScenarioDriver:
        if (
            self.cancellation_point is not CancellationPoint.NONE
            and self.final_decision is not None
        ):
            raise ValueError("cancelled scenarios cannot also declare a final decision")
        accepted_indices = [
            index
            for index, attempt in enumerate(self.approval_attempts)
            if attempt.expected_effect is ApprovalAttemptEffect.ACCEPTED
        ]
        if self.final_decision is None:
            if accepted_indices:
                raise ValueError("scenarios without a final decision cannot accept a decision")
        elif (
            len(accepted_indices) != 1
            or self.approval_attempts[accepted_indices[0]].decision is not self.final_decision
        ):
            raise ValueError("final decisions require exactly one matching accepted attempt")

        accepted_index = accepted_indices[0] if accepted_indices else None
        accepted_attempt = (
            self.approval_attempts[accepted_index] if accepted_index is not None else None
        )
        post_decision_effects = {
            ApprovalAttemptEffect.IDEMPOTENT,
            ApprovalAttemptEffect.REJECTED_CONFLICT,
            ApprovalAttemptEffect.REJECTED_IDEMPOTENCY_CONFLICT,
        }
        for index, attempt in enumerate(self.approval_attempts):
            if attempt.idempotency_key_mode is IdempotencyKeyMode.REUSE_PREVIOUS and index == 0:
                raise ValueError("the first approval attempt cannot reuse an idempotency key")
            if attempt.expected_effect in post_decision_effects and (
                accepted_index is None or index <= accepted_index
            ):
                raise ValueError(
                    "post-decision approval attempts must follow the accepted decision"
                )
            if (
                accepted_index is not None
                and index > accepted_index
                and attempt.expected_effect not in post_decision_effects
            ):
                raise ValueError("only replay or conflict checks may follow an accepted decision")

            if attempt.expected_effect is ApprovalAttemptEffect.IDEMPOTENT:
                if accepted_attempt is None or attempt.decision is not accepted_attempt.decision:
                    raise ValueError("idempotent replay must repeat the accepted decision")
                if (
                    self.approval_attempts[index - 1].expected_effect
                    is not ApprovalAttemptEffect.ACCEPTED
                ):
                    raise ValueError(
                        "idempotent replay must immediately follow the accepted decision"
                    )
            if attempt.expected_effect in {
                ApprovalAttemptEffect.REJECTED_CONFLICT,
                ApprovalAttemptEffect.REJECTED_IDEMPOTENCY_CONFLICT,
            }:
                if accepted_attempt is None or attempt.decision is accepted_attempt.decision:
                    raise ValueError("conflict checks must disagree with the accepted decision")
                if (
                    attempt.expected_effect is ApprovalAttemptEffect.REJECTED_IDEMPOTENCY_CONFLICT
                    and self.approval_attempts[index - 1].expected_effect
                    is not ApprovalAttemptEffect.ACCEPTED
                ):
                    raise ValueError(
                        "idempotency conflict must immediately follow the accepted decision"
                    )
        return self


class DuplicatePreventionExpectation(StrictContract):
    approval_decisions_max: int = Field(default=1, ge=0, le=1)
    approved_vendor_rows_max: int = Field(default=1, ge=0, le=1)
    synthetic_email_rows_max: int = Field(default=1, ge=0, le=1)
    workflow_event_sequences_unique: bool = True


class ToolInvocationExpectation(StrictContract):
    tool_name: GovernedToolName
    minimum_count: int = Field(ge=0, le=20)
    maximum_count: int = Field(ge=0, le=20)
    required_statuses: tuple[ToolInvocationStatus, ...] = ()
    required_error_categories: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_counts_and_evidence(self) -> ToolInvocationExpectation:
        if self.maximum_count < self.minimum_count:
            raise ValueError("tool invocation maximum_count cannot be below minimum_count")
        if self.minimum_count == 0 and self.maximum_count == 0:
            if self.required_statuses or self.required_error_categories:
                raise ValueError("absent tools cannot require statuses or error categories")
        elif not self.required_statuses:
            raise ValueError("expected tool invocations must declare at least one status")
        if len(self.required_statuses) != len(set(self.required_statuses)):
            raise ValueError("required tool statuses must be unique")
        if len(self.required_error_categories) != len(set(self.required_error_categories)):
            raise ValueError("required tool error categories must be unique")
        return self


class ExpectedOutcome(StrictContract):
    risk_score: int | None = Field(default=None, ge=0, le=100)
    run_status: Literal["succeeded", "rejected", "cancelled", "failed", "waiting"]
    vendor_status: Literal["submitted", "under_review", "approved", "rejected"]
    approval_request_count: int = Field(ge=0, le=1)
    approval_decision_count: int = Field(ge=0, le=1)
    approval_status: Literal["absent", "pending", "approved", "rejected", "cancelled"]
    approved_vendor_count: int = Field(ge=0, le=1)
    synthetic_email_count: int = Field(ge=0, le=1)
    model_call_status: Literal["succeeded", "failed", "absent", "any"]
    failure_category: str | None = Field(default=None, max_length=120)
    activity_retry_count_min: int = Field(default=0, ge=0, le=20)
    workflow_start_attempt_count_min: int = Field(default=1, ge=1, le=20)
    tool_invocations: tuple[ToolInvocationExpectation, ...] = Field(min_length=3, max_length=3)
    expected_event_types: tuple[WorkflowEventType, ...] = Field(min_length=1)
    duplicate_prevention: DuplicatePreventionExpectation = Field(
        default_factory=DuplicatePreventionExpectation
    )

    @model_validator(mode="after")
    def validate_business_effects(self) -> ExpectedOutcome:
        tool_names = [item.tool_name for item in self.tool_invocations]
        if set(tool_names) != set(GovernedToolName) or len(tool_names) != len(set(tool_names)):
            raise ValueError("tool expectations must contain each governed tool exactly once")
        if len(self.expected_event_types) != len(set(self.expected_event_types)):
            raise ValueError("expected workflow event types must be unique")

        if self.approval_status == "absent":
            if self.approval_request_count != 0 or self.approval_decision_count != 0:
                raise ValueError("absent approvals require zero request and decision rows")
        else:
            if self.approval_request_count != 1:
                raise ValueError("persisted approval statuses require one request row")
            expected_decisions = 1 if self.approval_status in {"approved", "rejected"} else 0
            if self.approval_decision_count != expected_decisions:
                raise ValueError("approval decision count does not match approval status")

        if self.vendor_status == "approved":
            if self.run_status != "succeeded" or self.approval_status != "approved":
                raise ValueError("approved vendors require a succeeded run and approved decision")
            if self.approved_vendor_count != 1 or self.synthetic_email_count != 1:
                raise ValueError("approved outcomes require exactly one projection and email")
        elif self.approved_vendor_count != 0 or self.synthetic_email_count != 0:
            raise ValueError("non-approved outcomes cannot expect projection or email rows")

        if self.vendor_status == "rejected" and (
            self.run_status != "rejected" or self.approval_status != "rejected"
        ):
            raise ValueError("rejected vendors require a rejected run and decision")
        if self.run_status == "succeeded" and self.vendor_status != "approved":
            raise ValueError("succeeded runs require an approved vendor")
        if self.run_status == "rejected" and self.vendor_status != "rejected":
            raise ValueError("rejected runs require a rejected vendor")
        if self.run_status == "waiting" and (
            self.vendor_status != "under_review" or self.approval_status != "pending"
        ):
            raise ValueError("waiting runs require an under-review vendor and pending approval")
        if self.run_status == "cancelled" and self.vendor_status != "under_review":
            raise ValueError("current workflow cancellation leaves the vendor under review")
        if self.run_status == "cancelled" and self.approval_status != "cancelled":
            raise ValueError("current workflow cancellation must cancel its approval request")
        if self.run_status == "failed" and self.failure_category is None:
            raise ValueError("failed outcomes require a failure_category")
        if self.run_status != "failed" and self.failure_category is not None:
            raise ValueError("non-failed outcomes cannot declare a failure_category")

        final_event_by_status = {
            "succeeded": WorkflowEventType.APPROVAL_DECIDED,
            "rejected": WorkflowEventType.APPROVAL_DECIDED,
            "cancelled": WorkflowEventType.REVIEW_CANCELLED,
            "failed": WorkflowEventType.REVIEW_FAILED,
            "waiting": WorkflowEventType.APPROVAL_REQUESTED,
        }
        if self.expected_event_types[-1] is not final_event_by_status[self.run_status]:
            raise ValueError("expected workflow events must end at the declared run state")
        return self


class EvaluationCaseDefinition(StrictContract):
    slug: str = Field(min_length=3, max_length=160, pattern=r"^[a-z0-9-]+$")
    case_version: str = Field(min_length=1, max_length=40)
    scenario_type: ScenarioType
    title: str = Field(min_length=3, max_length=200)
    description: str = Field(min_length=10, max_length=1_000)
    input: VendorInput
    setup: ScenarioSetup = Field(default_factory=ScenarioSetup)
    driver: ScenarioDriver
    expected_outcome: ExpectedOutcome
    evidence_requirements: tuple[EvidenceSource, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_case_semantics(self) -> EvaluationCaseDefinition:
        expected = self.expected_outcome
        driver = self.driver

        if len(self.evidence_requirements) != len(set(self.evidence_requirements)):
            raise ValueError("evidence requirements must be unique")
        required_evidence = set(EvidenceSource)
        missing_evidence = required_evidence - set(self.evidence_requirements)
        if missing_evidence:
            missing_values = ", ".join(sorted(item.value for item in missing_evidence))
            raise ValueError(f"evaluation case is missing evidence sources: {missing_values}")

        if (
            expected.run_status == "succeeded"
            and driver.final_decision is not ApprovalDecision.APPROVED
        ):
            raise ValueError("succeeded scenarios require a final approved decision")
        if (
            expected.run_status == "rejected"
            and driver.final_decision is not ApprovalDecision.REJECTED
        ):
            raise ValueError("rejected scenarios require a final rejected decision")
        if expected.run_status in {"failed", "waiting", "cancelled"} and driver.final_decision:
            raise ValueError(
                "failed, waiting, and cancelled scenarios cannot declare a final decision"
            )
        if expected.run_status == "cancelled":
            if driver.cancellation_point is CancellationPoint.NONE:
                raise ValueError("cancelled outcomes require a cancellation point")
        elif driver.cancellation_point is not CancellationPoint.NONE:
            raise ValueError("only cancelled outcomes may declare a cancellation point")

        expected_retry_min = max(
            (fault.expected_retry_count_min for fault in self.setup.faults), default=0
        )
        if expected.activity_retry_count_min < expected_retry_min:
            raise ValueError("expected activity retries cannot be below the fault-plan minimum")

        if self.scenario_type is ScenarioType.MODEL_PROVIDER_FAILURE:
            if self.setup.model_provider_mode is not ModelProviderMode.FAIL_EXPLANATION:
                raise ValueError("model-provider failure requires the failing provider profile")
            if expected.model_call_status != "failed":
                raise ValueError("model-provider failure must expect a failed model call")
        if (
            self.scenario_type is ScenarioType.TOOL_PERMISSION_DENIED
            and GovernedToolName.VENDOR_DATABASE_QUERY not in self.setup.omitted_tool_grants
        ):
            raise ValueError("tool-permission denial must omit the required vendor lookup grant")
        if self.scenario_type is ScenarioType.MALFORMED_TOOL_INPUT_REJECTED and not any(
            attempt.expected_effect is ToolAttemptEffect.REJECTED_INVALID_INPUT
            for attempt in driver.tool_attempts
        ):
            raise ValueError("malformed-input coverage requires an invalid tool probe")

        required_approval_effects = {
            ScenarioType.EARLY_APPROVAL_REJECTED: ApprovalAttemptEffect.REJECTED_EARLY,
            ScenarioType.STALE_APPROVAL_REJECTED: ApprovalAttemptEffect.REJECTED_STALE,
            ScenarioType.CONFLICTING_APPROVAL_REJECTED: ApprovalAttemptEffect.REJECTED_CONFLICT,
            ScenarioType.IDEMPOTENT_DECISION_REPLAY: ApprovalAttemptEffect.IDEMPOTENT,
            ScenarioType.DECISION_IDEMPOTENCY_CONFLICT: (
                ApprovalAttemptEffect.REJECTED_IDEMPOTENCY_CONFLICT
            ),
            ScenarioType.UNAUTHORIZED_SENSITIVE_OPERATION: ApprovalAttemptEffect.FORBIDDEN,
        }
        required_effect = required_approval_effects.get(self.scenario_type)
        if required_effect is not None and not any(
            attempt.expected_effect is required_effect for attempt in driver.approval_attempts
        ):
            raise ValueError(
                f"{self.scenario_type.value} requires approval effect {required_effect.value}"
            )

        cancellation_points = {
            ScenarioType.CANCELLATION_BEFORE_APPROVAL: CancellationPoint.BEFORE_APPROVAL,
            ScenarioType.CANCELLATION_WAITING_FOR_APPROVAL: (
                CancellationPoint.WAITING_FOR_APPROVAL
            ),
        }
        expected_cancellation = cancellation_points.get(self.scenario_type)
        if (
            expected_cancellation is not None
            and driver.cancellation_point is not expected_cancellation
        ):
            raise ValueError(
                f"{self.scenario_type.value} requires cancellation point "
                f"{expected_cancellation.value}"
            )

        fault_driven_scenarios = {
            ScenarioType.POLICY_RETRIEVAL_FAILURE,
            ScenarioType.TOOL_TIMEOUT_RETRY,
            ScenarioType.WORKER_RESTART_ACTIVE_ACTIVITY,
            ScenarioType.WORKER_RESTART_WAITING_APPROVAL,
            ScenarioType.WORKER_CRASH_AFTER_EFFECT_COMMIT,
            ScenarioType.DUPLICATE_PROJECTION_PREVENTED,
            ScenarioType.DUPLICATE_EMAIL_PREVENTED,
            ScenarioType.AMBIGUOUS_WORKFLOW_START_RECOVERY,
            ScenarioType.DATABASE_TRANSIENT_FAILURE,
        }
        if self.scenario_type in fault_driven_scenarios and not self.setup.faults:
            raise ValueError(f"{self.scenario_type.value} requires an explicit fault plan")
        return self

    def content_sha256(self) -> str:
        return _canonical_sha256(self.model_dump(mode="python"))


class EvaluationSuiteDefinition(StrictContract):
    schema_version: Literal["1"]
    suite_slug: str = Field(min_length=3, max_length=160, pattern=r"^[a-z0-9-]+$")
    suite_version: str = Field(min_length=1, max_length=40)
    workflow_type: Literal["vendor_onboarding"]
    description: str = Field(min_length=20, max_length=1_000)
    reviewed_by: str = Field(min_length=3, max_length=160)
    reviewed_at: datetime
    cases: tuple[EvaluationCaseDefinition, ...] = Field(min_length=20)

    @model_validator(mode="after")
    def validate_case_catalog(self) -> EvaluationSuiteDefinition:
        if self.reviewed_at.utcoffset() is None:
            raise ValueError("reviewed_at must include a timezone offset")
        slugs = [case.slug for case in self.cases]
        if len(slugs) != len(set(slugs)):
            raise ValueError("evaluation case slugs must be unique within a suite")
        scenario_types = [case.scenario_type for case in self.cases]
        missing = set(ScenarioType) - set(scenario_types)
        if missing:
            missing_values = ", ".join(sorted(item.value for item in missing))
            raise ValueError(f"evaluation suite is missing required scenarios: {missing_values}")
        if len(scenario_types) != len(set(scenario_types)):
            raise ValueError("reviewed suite v1 must contain one case per scenario type")
        return self

    def content_sha256(self) -> str:
        return _canonical_sha256(self.model_dump(mode="python"))

    def case_content_sha256(self, case: EvaluationCaseDefinition) -> str:
        """Bind a case digest to the reviewed suite metadata that gives it meaning."""

        payload = {
            "schema_version": self.schema_version,
            "suite_slug": self.suite_slug,
            "suite_version": self.suite_version,
            "workflow_type": self.workflow_type,
            "description": self.description,
            "reviewed_by": self.reviewed_by,
            "reviewed_at": self.reviewed_at.isoformat(),
            "case": case.model_dump(mode="python"),
        }
        return _canonical_sha256(payload)


def load_evaluation_suite(path: Path) -> EvaluationSuiteDefinition:
    """Load and strictly validate one evaluation suite JSON document."""

    return EvaluationSuiteDefinition.model_validate_json(path.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def load_packaged_evaluation_suite() -> EvaluationSuiteDefinition:
    """Load the reviewed suite shipped as package data."""

    dataset = resources.files("agents_should_survive_failure.evaluation_datasets").joinpath(
        "vendor_onboarding.v1.json"
    )
    return EvaluationSuiteDefinition.model_validate_json(dataset.read_text(encoding="utf-8"))


def validate_packaged_evaluation_suite() -> tuple[int, str]:
    """Return the reviewed case count and stable dataset digest after validation."""

    suite = load_packaged_evaluation_suite()
    return len(suite.cases), suite.content_sha256()
