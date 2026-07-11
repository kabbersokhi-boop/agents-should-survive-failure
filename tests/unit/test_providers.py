import pytest

from agents_should_survive_failure.providers import DeterministicModelProvider, ModelRequest


@pytest.mark.asyncio
async def test_deterministic_provider_returns_bounded_explanation() -> None:
    response = await DeterministicModelProvider().explain(
        ModelRequest(correlation_id="test", prompt="Vendor is low risk")
    )

    assert response.provider == "deterministic_mock"
    assert response.input_tokens == 4
    assert "reasoning" not in response.summary.lower()
