from datetime import UTC, datetime, timedelta

import pytest

from agents_should_survive_failure import auth_cli


def test_parse_expiration_requires_future_timezone_aware_timestamp() -> None:
    future = datetime.now(UTC) + timedelta(days=1)

    assert auth_cli.parse_expiration(None) is None
    assert auth_cli.parse_expiration(future.isoformat()) == future
    with pytest.raises(ValueError, match="timezone"):
        auth_cli.parse_expiration("2026-12-31T00:00:00")
    with pytest.raises(ValueError, match="future"):
        auth_cli.parse_expiration((datetime.now(UTC) - timedelta(seconds=1)).isoformat())


def test_bootstrap_prints_plaintext_once_and_passes_expiry(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    observed: dict[str, object] = {}

    async def bootstrap(
        email: str,
        display_name: str,
        scopes: list[str],
        *,
        expires_at: datetime | None,
    ) -> str:
        observed.update(
            email=email,
            display_name=display_name,
            scopes=scopes,
            expires_at=expires_at,
        )
        return "asf_identifier.plaintext-secret"

    monkeypatch.setattr(auth_cli, "bootstrap", bootstrap)
    auth_cli.bootstrap_main(
        "developer@example.invalid",
        "Developer",
        "runs:read,runs:write",
        "2026-12-31T00:00:00Z",
    )

    assert capsys.readouterr().out == "asf_identifier.plaintext-secret\n"
    assert observed["scopes"] == ["runs:read", "runs:write"]
    assert observed["expires_at"] == datetime(2026, 12, 31, tzinfo=UTC)


def test_revoke_and_disable_commands_only_print_safe_identifiers(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def revoke(key_identifier: str) -> str:
        assert key_identifier == "identifier"
        return "asf_ident...last"

    async def disable(email: str) -> str:
        assert email == "developer@example.invalid"
        return "Local Developer"

    monkeypatch.setattr(auth_cli, "revoke", revoke)
    monkeypatch.setattr(auth_cli, "disable_principal", disable)

    auth_cli.revoke_main("identifier")
    auth_cli.disable_principal_main("developer@example.invalid")

    assert capsys.readouterr().out == (
        "Revoked API key asf_ident...last.\nDisabled principal Local Developer.\n"
    )
