"""Local operator commands for API-key bootstrap and lifecycle."""

import asyncio
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agents_should_survive_failure.auth import generate_api_key, validate_scopes
from agents_should_survive_failure.persistence.models import (
    APIKey,
    AuditEvent,
    AuthPrincipal,
    PrincipalStatus,
    PrincipalType,
    User,
    UserStatus,
)
from agents_should_survive_failure.settings import get_settings


def parse_expiration(value: str | None) -> datetime | None:
    """Accept an explicit future ISO-8601 expiry without silently assuming a timezone."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("API key expiry must be an ISO-8601 timestamp") from error
    if parsed.tzinfo is None:
        raise ValueError("API key expiry must include a timezone")
    expires_at = parsed.astimezone(UTC)
    if expires_at <= datetime.now(UTC):
        raise ValueError("API key expiry must be in the future")
    return expires_at


async def bootstrap(
    email: str,
    display_name: str,
    scopes: list[str],
    *,
    expires_at: datetime | None = None,
) -> str:
    engine = create_async_engine(get_settings().database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions.begin() as session:
            user = await session.scalar(select(User).where(User.email == email))
            if user is None:
                user = User(email=email, display_name=display_name, status=UserStatus.ACTIVE)
                session.add(user)
                await session.flush()
            principal = await session.scalar(
                select(AuthPrincipal).where(AuthPrincipal.user_id == user.id)
            )
            if principal is None:
                principal = AuthPrincipal(
                    id=user.id,
                    principal_type=PrincipalType.USER,
                    display_name=user.display_name,
                    status=PrincipalStatus.ACTIVE,
                    user_id=user.id,
                )
                session.add(principal)
                await session.flush()
            generated = generate_api_key()
            key = APIKey(
                principal_id=principal.id,
                key_identifier=generated.key_identifier,
                key_prefix=generated.key_prefix,
                last_four=generated.last_four,
                secret_hash=generated.secret_hash,
                label="local-bootstrap",
                scopes=validate_scopes(scopes),
                expires_at=expires_at,
            )
            session.add(key)
            await session.flush()
            session.add(
                AuditEvent(
                    actor_id=principal.id,
                    action="api_key.create",
                    resource_type="api_key",
                    resource_id=key.id,
                    idempotency_key=f"api-key-create:{key.id}",
                    summary="A local operator created an API key.",
                    evidence={
                        "key_prefix": key.key_prefix,
                        "scopes": key.scopes,
                        "expires_at": key.expires_at.isoformat() if key.expires_at else None,
                    },
                )
            )
        return generated.plaintext
    finally:
        await engine.dispose()


async def revoke(key_identifier: str) -> str:
    engine = create_async_engine(get_settings().database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions.begin() as session:
            key = await session.scalar(
                select(APIKey).where(APIKey.key_identifier == key_identifier)
            )
            if key is None:
                raise ValueError("API key identifier was not found")
            if key.revoked_at is None:
                key.revoked_at = datetime.now(UTC)
                session.add(
                    AuditEvent(
                        actor_id=key.principal_id,
                        action="api_key.revoke",
                        resource_type="api_key",
                        resource_id=key.id,
                        idempotency_key=f"api-key-revoke:{key.id}",
                        summary="A local operator revoked an API key.",
                        evidence={"key_prefix": key.key_prefix},
                    )
                )
            return f"{key.key_prefix}...{key.last_four}"
    finally:
        await engine.dispose()


async def disable_principal(email: str) -> str:
    engine = create_async_engine(get_settings().database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions.begin() as session:
            user = await session.scalar(select(User).where(User.email == email))
            if user is None:
                raise ValueError("user was not found")
            principal = await session.scalar(
                select(AuthPrincipal).where(AuthPrincipal.user_id == user.id)
            )
            if principal is None:
                raise ValueError("user has no API principal")
            if principal.status is not PrincipalStatus.DISABLED:
                user.status = UserStatus.DISABLED
                principal.status = PrincipalStatus.DISABLED
                session.add(
                    AuditEvent(
                        action="principal.disable",
                        resource_type="auth_principal",
                        resource_id=principal.id,
                        idempotency_key=f"principal-disable:{principal.id}",
                        summary="A local operator disabled an API principal.",
                        evidence={"principal_type": principal.principal_type.value},
                    )
                )
            return principal.display_name
    finally:
        await engine.dispose()


def bootstrap_main(
    email: str,
    display_name: str,
    scopes_csv: str,
    expires_at: str | None = None,
) -> None:
    scopes = [scope.strip() for scope in scopes_csv.split(",") if scope.strip()]
    if not scopes:
        raise SystemExit("At least one API key scope is required.")
    plaintext = asyncio.run(
        bootstrap(email, display_name, scopes, expires_at=parse_expiration(expires_at))
    )
    print(plaintext)


def revoke_main(key_identifier: str) -> None:
    print(f"Revoked API key {asyncio.run(revoke(key_identifier))}.")


def disable_principal_main(email: str) -> None:
    print(f"Disabled principal {asyncio.run(disable_principal(email))}.")
