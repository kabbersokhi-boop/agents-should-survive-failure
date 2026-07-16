"""Immutable registration of trusted, operator-installed managed-agent manifests."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

from agents_should_survive_failure_sdk import AgentMetadata
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agents_should_survive_failure.persistence.models import (
    Agent,
    AgentStatus,
    AgentToolGrant,
    ToolDefinition,
)

MANAGED_AGENT_WORKFLOW_TYPE = "managed_agent"
_ENTRY_POINT = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*:[A-Za-z_][A-Za-z0-9_]*$")
_PACKAGE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,119}$")


class AgentManifestError(ValueError):
    """A supplied public SDK manifest or installation declaration is invalid."""


class AgentRegistrationConflict(ValueError):
    """An immutable package version was re-registered with different content."""


class MissingDeclaredTool(ValueError):
    """A manifest requires a governed tool version that has not been registered."""


@dataclass(frozen=True)
class AgentRegistration:
    """Validated operator-installation metadata for one immutable agent version."""

    metadata: AgentMetadata
    package_name: str
    entry_point: str


def parse_registration(
    *, manifest: dict[str, Any], package_name: str, entry_point: str
) -> AgentRegistration:
    """Validate only data contracts; package code is never imported during registration."""

    if not _PACKAGE_NAME.fullmatch(package_name):
        raise AgentManifestError("package name is invalid")
    if not _ENTRY_POINT.fullmatch(entry_point):
        raise AgentManifestError("entry point must be a module path and symbol")
    try:
        metadata = AgentMetadata.model_validate(manifest)
    except ValidationError as error:
        raise AgentManifestError("managed agent manifest is invalid") from error
    return AgentRegistration(metadata=metadata, package_name=package_name, entry_point=entry_point)


def registration_digest(registration: AgentRegistration) -> str:
    """Return a stable digest of every immutable persisted registration field."""

    payload = {
        "entry_point": registration.entry_point,
        "manifest": registration.metadata.model_dump(mode="json"),
        "package_name": registration.package_name,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
            "utf-8"
        )
    ).hexdigest()


async def register_agent(session: AsyncSession, *, registration: AgentRegistration) -> Agent:
    """Insert an immutable agent version and its immutable tool grants idempotently."""

    metadata = registration.metadata
    digest = registration_digest(registration)
    existing = await session.scalar(
        select(Agent).where(Agent.name == metadata.slug, Agent.version == metadata.version)
    )
    if existing is not None:
        if existing.configuration.get("registration_sha256") != digest:
            raise AgentRegistrationConflict(
                "agent identity and version already have different content"
            )
        return existing

    required_tools = {(tool.name, tool.version) for tool in metadata.tools}
    tools = (
        await session.scalars(
            select(ToolDefinition)
            .where(ToolDefinition.enabled.is_(True))
            .order_by(ToolDefinition.name)
        )
    ).all()
    tool_by_identity = {(tool.name, tool.version): tool for tool in tools}
    missing = required_tools - set(tool_by_identity)
    if missing:
        names = ", ".join(f"{name}@{version}" for name, version in sorted(missing))
        raise MissingDeclaredTool(f"declared governed tools are unavailable: {names}")

    agent = Agent(
        name=metadata.slug,
        version=metadata.version,
        workflow_type=MANAGED_AGENT_WORKFLOW_TYPE,
        package_name=registration.package_name,
        entry_point=registration.entry_point,
        manifest=metadata.model_dump(mode="json"),
        input_schema=dict(metadata.input_schema),
        output_schema=dict(metadata.output_schema),
        compatibility=metadata.compatibility,
        integrity_digest=digest,
        status=AgentStatus.ACTIVE,
        configuration={
            "compatibility": metadata.compatibility,
            "entry_point": registration.entry_point,
            "manifest": metadata.model_dump(mode="json"),
            "package_name": registration.package_name,
            "registration_sha256": digest,
        },
    )
    session.add(agent)
    await session.flush()
    for tool_name, tool_version in required_tools:
        tool = tool_by_identity[(tool_name, tool_version)]
        session.add(
            AgentToolGrant(
                agent_id=agent.id,
                tool_definition_id=tool.id,
                policy_version=metadata.version,
                policy_hash=digest,
            )
        )
    await session.flush()
    return agent
