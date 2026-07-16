"""Typed, sanitized failure contracts used at Temporal activity boundaries."""

from __future__ import annotations

from enum import StrEnum

from sqlalchemy.exc import OperationalError
from temporalio.exceptions import ApplicationError


class FailureCategory(StrEnum):
    """Operator-safe categories for failures at external-effect boundaries."""

    AUTHORIZATION_DENIED = "authorization_denied"
    INVALID_INPUT = "invalid_input"
    MISSING_GRANT = "missing_immutable_grant"
    TOOL_UNSUPPORTED = "tool_unsupported"
    TOOL_VERSION_MISMATCH = "tool_version_mismatch"
    IDENTITY_MISMATCH = "vendor_identity_mismatch"
    STALE_APPROVAL = "stale_approval_version"
    CONFLICTING_APPROVAL = "conflicting_approval_decision"
    IDEMPOTENCY_CONFLICT = "idempotency_key_conflict"
    INVALID_MANIFEST = "invalid_manifest"
    BUDGET_EXHAUSTED = "budget_exhausted"
    TIMEOUT = "timeout"
    DATABASE_UNAVAILABLE = "database_unavailable"
    MCP_UNAVAILABLE = "mcp_transport_unavailable"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    TOOL_UNAVAILABLE = "tool_handler_unavailable"
    RATE_LIMITED = "rate_limited"
    WORKER_TERMINATED = "worker_terminated"
    AMBIGUOUS_HANDOFF = "ambiguous_handoff"
    UNEXPECTED = "unexpected_internal_error"


class PlatformFailure(Exception):
    """A safe activity-boundary failure with explicit retry semantics."""

    def __init__(
        self, category: FailureCategory, *, retryable: bool, detail: str | None = None
    ) -> None:
        super().__init__(detail or category.value)
        self.category = category
        self.retryable = retryable

    @property
    def safe_message(self) -> str:
        """Return a diagnostic that cannot contain raw dependency payloads or secrets."""

        return self.category.value


def classify_failure(error: BaseException) -> PlatformFailure:
    """Classify known dependency failures; fail unknown defects safely and visibly."""

    if isinstance(error, PlatformFailure):
        return error
    if isinstance(error, TimeoutError):
        return PlatformFailure(FailureCategory.TIMEOUT, retryable=True)
    if isinstance(error, OperationalError):
        return PlatformFailure(FailureCategory.DATABASE_UNAVAILABLE, retryable=True)
    if isinstance(error, PermissionError):
        return PlatformFailure(FailureCategory.AUTHORIZATION_DENIED, retryable=False)
    if isinstance(error, ValueError):
        return PlatformFailure(FailureCategory.INVALID_INPUT, retryable=False)
    return PlatformFailure(FailureCategory.UNEXPECTED, retryable=False)


def temporal_failure(error: BaseException) -> ApplicationError:
    """Convert a classified error to a Temporal failure without exposing raw exception text."""

    failure = classify_failure(error)
    return ApplicationError(
        f"required governed tool failed: {failure.safe_message}",
        type=failure.category.value,
        non_retryable=not failure.retryable,
    )
