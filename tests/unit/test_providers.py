import pytest

from agents_should_survive_failure.providers import (
    DeterministicModelProvider,
    ModelRequest,
    NVIDIAModelProvider,
)


@pytest.mark.asyncio
async def test_deterministic_provider_returns_bounded_explanation() -> None:
    response = await DeterministicModelProvider().explain(
        ModelRequest(correlation_id="test", prompt="Vendor is low risk")
    )

    assert response.provider == "deterministic_mock"
    assert response.input_tokens == 4
    assert "reasoning" not in response.summary.lower()


def test_nvidia_provider_requires_explicit_credential() -> None:
    with pytest.raises(ValueError, match="NVIDIA_API_KEY"):
        NVIDIAModelProvider(api_key=None, base_url="https://example.invalid/v1", model="demo")
