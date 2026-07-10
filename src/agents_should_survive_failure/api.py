"""Phase 0 API surface."""

from fastapi import FastAPI
from pydantic import BaseModel


class HealthResponse(BaseModel):
    """Stable health response contract."""

    status: str


async def liveness() -> HealthResponse:
    """Report process liveness without checking external dependencies."""
    return HealthResponse(status="ok")


def create_app() -> FastAPI:
    app = FastAPI(
        title="Agents Should Survive Failure",
        description="Control plane for durable, observable, permission-controlled AI agents.",
        version="0.1.0",
    )

    app.add_api_route(
        "/health/live",
        liveness,
        methods=["GET"],
        response_model=HealthResponse,
        tags=["health"],
    )

    return app


app = create_app()
