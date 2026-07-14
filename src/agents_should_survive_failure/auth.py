"""API-key generation, verification, and authorization contracts."""

import base64
import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agents_should_survive_failure.persistence.models import (
    APIKey,
    AuthPrincipal,
    PrincipalStatus,
    PrincipalType,
)

API_KEY_SCOPES = frozenset(
    {
        "admin",
        "agents:read",
        "agents:write",
        "approvals:decide",
        "approvals:read",
        "evaluations:execute",
        "evaluations:read",
        "runs:read",
        "runs:write",
        "tools:invoke",
    }
)
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1


@dataclass(frozen=True)
class GeneratedAPIKey:
    plaintext: str
    key_identifier: str
    key_prefix: str
    last_four: str
    secret_hash: str


@dataclass(frozen=True)
class AuthenticatedPrincipal:
    id: UUID
    key_id: UUID
    scopes: frozenset[str]
    principal_type: PrincipalType = PrincipalType.USER

    def allows(self, *required_scopes: str) -> bool:
        return "admin" in self.scopes or set(required_scopes).issubset(self.scopes)


def validate_scopes(scopes: list[str]) -> list[str]:
    normalized = sorted(set(scopes))
    unknown = set(normalized).difference(API_KEY_SCOPES)
    if unknown:
        raise ValueError(f"Unsupported API key scopes: {', '.join(sorted(unknown))}")
    return normalized


def generate_api_key() -> GeneratedAPIKey:
    identifier = secrets.token_urlsafe(9).replace("-", "").replace("_", "")[:12]
    secret = secrets.token_urlsafe(32)
    plaintext = f"asf_{identifier}.{secret}"
    return GeneratedAPIKey(
        plaintext=plaintext,
        key_identifier=identifier,
        key_prefix=f"asf_{identifier[:6]}",
        last_four=secret[-4:],
        secret_hash=hash_secret(secret),
    )


def parse_api_key(value: str) -> tuple[str, str] | None:
    prefix, separator, secret = value.partition(".")
    if separator != "." or not prefix.startswith("asf_") or not secret:
        return None
    identifier = prefix.removeprefix("asf_")
    if not identifier or len(identifier) > 32:
        return None
    return identifier, secret


def hash_secret(secret: str) -> str:
    salt = secrets.token_bytes(16)
    derived = hashlib.scrypt(
        secret.encode("utf-8"), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P
    )
    return "$".join(
        (
            "scrypt",
            str(_SCRYPT_N),
            str(_SCRYPT_R),
            str(_SCRYPT_P),
            base64.urlsafe_b64encode(salt).decode("ascii"),
            base64.urlsafe_b64encode(derived).decode("ascii"),
        )
    )


def verify_secret(secret: str, encoded: str) -> bool:
    try:
        algorithm, n, r, p, salt, expected = encoded.split("$")
        if algorithm != "scrypt":
            return False
        derived = hashlib.scrypt(
            secret.encode("utf-8"),
            salt=base64.urlsafe_b64decode(salt.encode("ascii")),
            n=int(n),
            r=int(r),
            p=int(p),
        )
        return hmac.compare_digest(derived, base64.urlsafe_b64decode(expected.encode("ascii")))
    except (ValueError, UnicodeError):
        return False


def key_is_active(*, expires_at: datetime | None, revoked_at: datetime | None) -> bool:
    return revoked_at is None and (expires_at is None or expires_at > datetime.now(UTC))


async def resolve_api_key(session: AsyncSession, raw_value: str) -> AuthenticatedPrincipal | None:
    parsed = parse_api_key(raw_value)
    if parsed is None:
        return None
    identifier, secret = parsed
    key = await session.scalar(select(APIKey).where(APIKey.key_identifier == identifier))
    if key is None or not key_is_active(expires_at=key.expires_at, revoked_at=key.revoked_at):
        return None
    if not verify_secret(secret, key.secret_hash):
        return None
    principal = await session.get(AuthPrincipal, key.principal_id)
    if principal is None or principal.status is not PrincipalStatus.ACTIVE:
        return None
    try:
        scopes = frozenset(validate_scopes(key.scopes))
    except ValueError:
        return None
    key.last_used_at = datetime.now(UTC)
    return AuthenticatedPrincipal(
        id=principal.id,
        key_id=key.id,
        scopes=scopes,
        principal_type=principal.principal_type,
    )
