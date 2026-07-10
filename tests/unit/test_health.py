import pytest
from httpx import ASGITransport, AsyncClient

from agents_should_survive_failure.api import create_app


@pytest.mark.asyncio
async def test_liveness_contract() -> None:
    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
