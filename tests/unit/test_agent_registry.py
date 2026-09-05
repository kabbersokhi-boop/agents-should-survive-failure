"""Manifest validation and immutable registration digest checks."""

import pytest

from agents_should_survive_failure.agent_registry import (
    AgentManifestError,
    parse_registration,
    registration_digest,
)


def manifest() -> dict[str, object]:
    return {
        "slug": "operations-investigation",
        "version": "1.0.0",
        "display_name": "Operations Investigation",
        "description": "Investigates bounded operational evidence through governed tools.",
        "input_schema": {"type": "object"},
        "output_schema": {"type": "object"},
        "tools": [{"name": "internal_policy_search", "version": "1"}],
    }


def test_registration_digest_is_stable_and_binds_installation_metadata() -> None:
    registration = parse_registration(
        manifest=manifest(),
        package_name="reference-operations-agent",
        entry_point="example_operations.agent:OperationsAgent",
    )
    changed_package = parse_registration(
        manifest=manifest(),
        package_name="different-package",
        entry_point="example_operations.agent:OperationsAgent",
    )

    assert registration_digest(registration) == registration_digest(registration)
    assert registration_digest(registration) != registration_digest(changed_package)


@pytest.mark.parametrize(
    ("package_name", "entry_point"),
    [("bad package name", "example.agent:Agent"), ("valid-package", "not-an-entry-point")],
)
def test_registration_rejects_unsafe_installation_declarations(
    package_name: str, entry_point: str
) -> None:
    with pytest.raises(AgentManifestError):
        parse_registration(manifest=manifest(), package_name=package_name, entry_point=entry_point)


def test_registration_rejects_invalid_public_manifest() -> None:
    invalid = manifest()
    invalid["slug"] = "INVALID SLUG"

    with pytest.raises(AgentManifestError, match="manifest is invalid"):
        parse_registration(
            manifest=invalid,
            package_name="valid-package",
            entry_point="example.agent:Agent",
        )
