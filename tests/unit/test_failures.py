"""Regression tests for Temporal retry classification and sanitized diagnostics."""

from sqlalchemy.exc import OperationalError
from temporalio.exceptions import ApplicationError

from agents_should_survive_failure.failures import (
    FailureCategory,
    PlatformFailure,
    classify_failure,
    temporal_failure,
)


def test_known_dependency_failures_are_retryable() -> None:
    assert (
        classify_failure(TimeoutError("provider response contained api-key=secret")).retryable
        is True
    )
    assert classify_failure(
        OperationalError("select 1", {}, RuntimeError("database unavailable"))
    ).retryable


def test_permanent_failures_are_not_retried() -> None:
    failure = classify_failure(PermissionError("not allowed"))

    assert failure.category is FailureCategory.AUTHORIZATION_DENIED
    assert failure.retryable is False


def test_unknown_defects_fail_safely_without_leaking_exception_content() -> None:
    error = RuntimeError("NVIDIA_API_KEY=not-for-evidence")
    failure = classify_failure(error)
    temporal = temporal_failure(error)

    assert failure.category is FailureCategory.UNEXPECTED
    assert failure.retryable is False
    assert isinstance(temporal, ApplicationError)
    assert str(temporal) != str(error)
    assert "not-for-evidence" not in str(temporal)


def test_explicit_platform_failure_keeps_declared_semantics() -> None:
    original = PlatformFailure(FailureCategory.AMBIGUOUS_HANDOFF, retryable=True)
    temporal = temporal_failure(original)

    assert classify_failure(original) is original
    assert temporal.non_retryable is False
