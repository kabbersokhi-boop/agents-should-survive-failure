"""Local operator commands for API-key bootstrap and lifecycle."""

import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agents_should_survive_failure.auth import generate_api_key, validate_scopes
from agents_should_survive_failure.persistence.models import (
    APIKey,
    AuthPrincipal,
    PrincipalStatus,
    PrincipalType,
    User,
    UserStatus,
)
from agents_should_survive_failure.settings import get_settings


async def _bootstrap(email: str, display_name: str, scopes: list[str]) -> str:
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
            generated = generate_api_key()
            session.add(
                APIKey(
                    principal_id=principal.id,
                    key_identifier=generated.key_identifier,
                    key_prefix=generated.key_prefix,
                    last_four=generated.last_four,
                    secret_hash=generated.secret_hash,
                    label="local-bootstrap",
                    scopes=validate_scopes(scopes),
                )
            )
        return generated.plaintext
    finally:
        await engine.dispose()


def bootstrap_main(email: str, display_name: str, scopes_csv: str) -> None:
    scopes = [scope.strip() for scope in scopes_csv.split(",") if scope.strip()]
    if not scopes:
        raise SystemExit("At least one API key scope is required.")
    print(asyncio.run(_bootstrap(email, display_name, scopes)))
