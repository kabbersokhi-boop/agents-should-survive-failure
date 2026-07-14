import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from agents_should_survive_failure import auth_cli
from agents_should_survive_failure.auth import parse_api_key
from agents_should_survive_failure.persistence.models import (
    APIKey,
    AuditEvent,
    AuthPrincipal,
    PrincipalStatus,
    User,
)


@pytest_asyncio.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    database_engine = create_async_engine(
        os.getenv(
            "INTEGRATION_DATABASE_URL",
            "postgresql+asyncpg://agents:local-development-only@127.0.0.1:5432/agents",
        )
    )
    try:
        yield database_engine
    finally:
        await database_engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_local_operator_key_lifecycle_is_persisted(
    engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        auth_cli,
        "get_settings",
        lambda: SimpleNamespace(database_url=engine.url.render_as_string(hide_password=False)),
    )
    email = f"lifecycle-{uuid4()}@example.invalid"
    expires_at = datetime.now(UTC) + timedelta(days=1)

    plaintext = await auth_cli.bootstrap(
        email,
        "Lifecycle Test Principal",
        ["runs:read"],
        expires_at=expires_at,
    )
    parsed = parse_api_key(plaintext)
    assert parsed is not None
    identifier, _ = parsed

    safe_identifier = await auth_cli.revoke(identifier)
    assert safe_identifier.startswith("asf_")
    assert safe_identifier.endswith(plaintext[-4:])
    assert await auth_cli.disable_principal(email) == "Lifecycle Test Principal"

    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        key = await session.scalar(select(APIKey).where(APIKey.key_identifier == identifier))
        user = await session.scalar(select(User).where(User.email == email))
        assert key is not None and key.revoked_at is not None
        assert key.expires_at == expires_at
        assert user is not None
        principal = await session.scalar(
            select(AuthPrincipal).where(AuthPrincipal.user_id == user.id)
        )
        assert principal is not None and principal.status is PrincipalStatus.DISABLED
        audit_actions = list(
            await session.scalars(
                select(AuditEvent.action)
                .where(AuditEvent.resource_id.in_([key.id, principal.id]))
                .order_by(AuditEvent.action)
            )
        )
        assert audit_actions == ["api_key.create", "api_key.revoke", "principal.disable"]
