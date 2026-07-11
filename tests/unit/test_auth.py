from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from agents_should_survive_failure.auth import (
    AuthenticatedPrincipal,
    generate_api_key,
    key_is_active,
    parse_api_key,
    validate_scopes,
    verify_secret,
)


def test_generated_api_key_stores_only_a_verifier() -> None:
    key = generate_api_key()
    parsed = parse_api_key(key.plaintext)

    assert parsed is not None
    assert parsed[0] == key.key_identifier
    assert verify_secret(parsed[1], key.secret_hash)
    assert key.plaintext not in key.secret_hash
    assert key.last_four == parsed[1][-4:]


def test_api_key_verifier_rejects_tampering_and_malformed_values() -> None:
    key = generate_api_key()
    parsed = parse_api_key(key.plaintext)
    assert parsed is not None

    assert not verify_secret(f"{parsed[1]}x", key.secret_hash)
    assert parse_api_key("not-an-api-key") is None
    assert parse_api_key("asf_identifier.") is None


def test_scope_validation_and_admin_authorization() -> None:
    assert validate_scopes(["runs:read", "runs:read"]) == ["runs:read"]
    with pytest.raises(ValueError, match="Unsupported"):
        validate_scopes(["runs:root"])

    principal = AuthenticatedPrincipal(uuid4(), uuid4(), frozenset({"admin"}))
    assert principal.allows("approvals:decide")


def test_expired_and_revoked_keys_are_inactive() -> None:
    assert not key_is_active(expires_at=datetime.now(UTC) - timedelta(seconds=1), revoked_at=None)
    assert not key_is_active(expires_at=None, revoked_at=datetime.now(UTC))
    assert key_is_active(expires_at=datetime.now(UTC) + timedelta(days=1), revoked_at=None)
