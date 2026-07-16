"""Durable checkpoints, artifacts, and deterministic budget accounting for managed runs."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agents_should_survive_failure.failures import FailureCategory, PlatformFailure
from agents_should_survive_failure.persistence.models import (
    RunArtifact,
    RunBudget,
    RunCheckpoint,
    WorkflowRun,
)

_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,159}$")
_CONTENT_TYPE = re.compile(r"^[A-Za-z0-9!#$&^_.+-]+/[A-Za-z0-9!#$&^_.+-]+$")


class RuntimeStateConflict(ValueError):
    """An idempotency key or checkpoint name was reused with different content."""


class RuntimeStateValidationError(ValueError):
    """A bounded runtime-state payload is malformed or unsafe."""


def _json_payload(value: dict[str, Any], *, maximum_bytes: int) -> tuple[bytes, str]:
    try:
        encoded = json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise RuntimeStateValidationError("runtime state must be JSON serializable") from error
    if len(encoded) > maximum_bytes:
        raise RuntimeStateValidationError("runtime state exceeds the configured size limit")
    return encoded, hashlib.sha256(encoded).hexdigest()


async def save_checkpoint(
    session: AsyncSession,
    *,
    workflow_run_id: uuid.UUID,
    agent_id: uuid.UUID,
    name: str,
    schema_version: str,
    value: dict[str, Any],
    maximum_bytes: int,
) -> RunCheckpoint:
    """Idempotently save a validated checkpoint bound to the run-pinned agent version."""

    if not _NAME.fullmatch(name) or not schema_version or len(schema_version) > 40:
        raise RuntimeStateValidationError("checkpoint name or schema version is invalid")
    encoded, digest = _json_payload(value, maximum_bytes=maximum_bytes)
    run = await session.get(WorkflowRun, workflow_run_id)
    if run is None or run.agent_id != agent_id:
        raise RuntimeStateValidationError("checkpoint agent does not match the pinned run agent")
    checkpoint = await session.scalar(
        select(RunCheckpoint)
        .where(RunCheckpoint.workflow_run_id == workflow_run_id, RunCheckpoint.name == name)
        .with_for_update()
    )
    if checkpoint is not None:
        if (
            checkpoint.agent_id != agent_id
            or checkpoint.schema_version != schema_version
            or checkpoint.digest_sha256 != digest
        ):
            raise RuntimeStateConflict("checkpoint name is already bound to different content")
        return checkpoint
    checkpoint = RunCheckpoint(
        workflow_run_id=workflow_run_id,
        agent_id=agent_id,
        name=name,
        schema_version=schema_version,
        value=value,
        digest_sha256=digest,
        size_bytes=len(encoded),
    )
    session.add(checkpoint)
    await session.flush()
    return checkpoint


async def load_checkpoint(
    session: AsyncSession, *, workflow_run_id: uuid.UUID, name: str
) -> RunCheckpoint | None:
    """Load the latest immutable named checkpoint for one durable run."""

    return await session.scalar(
        select(RunCheckpoint).where(
            RunCheckpoint.workflow_run_id == workflow_run_id, RunCheckpoint.name == name
        )
    )


async def create_artifact(
    session: AsyncSession,
    *,
    workflow_run_id: uuid.UUID,
    agent_id: uuid.UUID,
    name: str,
    content_type: str,
    content: bytes,
    maximum_bytes: int,
    parent_artifact_id: uuid.UUID | None = None,
) -> RunArtifact:
    """Store an idempotent bounded inline artifact without allowing filesystem paths."""

    if not _NAME.fullmatch(name) or "/" in name or "\\" in name:
        raise RuntimeStateValidationError("artifact name is invalid")
    if not _CONTENT_TYPE.fullmatch(content_type):
        raise RuntimeStateValidationError("artifact content type is invalid")
    if len(content) > maximum_bytes:
        raise RuntimeStateValidationError("artifact exceeds the configured size limit")
    run = await session.get(WorkflowRun, workflow_run_id)
    if run is None or run.agent_id != agent_id:
        raise RuntimeStateValidationError("artifact agent does not match the pinned run agent")
    if parent_artifact_id is not None:
        parent = await session.get(RunArtifact, parent_artifact_id)
        if parent is None or parent.workflow_run_id != workflow_run_id:
            raise RuntimeStateValidationError("artifact parent does not belong to this run")
    digest = hashlib.sha256(content).hexdigest()
    artifact = await session.scalar(
        select(RunArtifact)
        .where(
            RunArtifact.workflow_run_id == workflow_run_id,
            RunArtifact.name == name,
            RunArtifact.digest_sha256 == digest,
        )
        .with_for_update()
    )
    if artifact is not None:
        return artifact
    artifact = RunArtifact(
        workflow_run_id=workflow_run_id,
        agent_id=agent_id,
        parent_artifact_id=parent_artifact_id,
        name=name,
        content_type=content_type,
        digest_sha256=digest,
        size_bytes=len(content),
        content=content,
    )
    session.add(artifact)
    await session.flush()
    return artifact


async def initialize_budget(
    session: AsyncSession, *, workflow_run_id: uuid.UUID, limits: dict[str, int]
) -> RunBudget:
    """Persist a run-pinned budget once; changing it after start is a conflict."""

    if any(not key or value < 0 for key, value in limits.items()):
        raise RuntimeStateValidationError("budget limits must be non-negative named integers")
    budget = await session.scalar(
        select(RunBudget).where(RunBudget.workflow_run_id == workflow_run_id).with_for_update()
    )
    if budget is not None:
        if budget.limits != limits:
            raise RuntimeStateConflict("run budget is already pinned to different limits")
        return budget
    budget = RunBudget(workflow_run_id=workflow_run_id, limits=limits, consumed={})
    session.add(budget)
    await session.flush()
    return budget


async def consume_budget(
    session: AsyncSession, *, workflow_run_id: uuid.UUID, amount: dict[str, int]
) -> RunBudget:
    """Atomically apply positive usage and fail before a configured limit is exceeded."""

    if any(not key or value < 0 for key, value in amount.items()):
        raise RuntimeStateValidationError("budget usage must be non-negative named integers")
    budget = await session.scalar(
        select(RunBudget).where(RunBudget.workflow_run_id == workflow_run_id).with_for_update()
    )
    if budget is None:
        raise RuntimeStateValidationError("run budget has not been initialized")
    updated = dict(budget.consumed)
    for key, increment in amount.items():
        next_value = updated.get(key, 0) + increment
        if next_value > budget.limits.get(key, 0):
            budget.exhausted_at = datetime.now(UTC)
            raise PlatformFailure(FailureCategory.BUDGET_EXHAUSTED, retryable=False)
        updated[key] = next_value
    budget.consumed = updated
    return budget
