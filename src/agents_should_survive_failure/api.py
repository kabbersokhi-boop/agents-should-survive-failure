"""Control-plane API application."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated
from uuid import UUID, uuid4

import structlog
from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, Counter, generate_latest
from pydantic import BaseModel
from sqlalchemy import select
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from agents_should_survive_failure.dependencies import (
    DependencySet,
    RuntimeResources,
    check_dependencies,
    create_resources,
)
from agents_should_survive_failure.observability import configure_logging, configure_tracing
from agents_should_survive_failure.persistence.models import (
    EvaluationResult,
    EvaluationRun,
    ModelCall,
    RunStatus,
    Vendor,
    VendorStatus,
    WorkflowEvent,
    WorkflowRun,
)
from agents_should_survive_failure.persistence.repositories import (
    VendorRepository,
    WorkflowRunRepository,
)
from agents_should_survive_failure.persistence.seed import seed_id
from agents_should_survive_failure.persistence.session import Database
from agents_should_survive_failure.settings import Settings, get_settings
from agents_should_survive_failure.workflows.contracts import (
    TASK_QUEUE,
    WORKFLOW_TYPE,
    ApprovalDecisionInput,
    ApprovalDecisionType,
    VendorOnboardingInput,
    WorkflowStatus,
)
from agents_should_survive_failure.workflows.vendor_onboarding import VendorOnboardingWorkflow

REQUESTS = Counter(
    "agents_http_requests_total",
    "HTTP requests processed by the control-plane API.",
    ("method", "path", "status"),
)


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = request.headers.get("x-request-id", str(uuid4()))
        structlog.contextvars.bind_contextvars(request_id=request_id)
        try:
            response = await call_next(request)
            response.headers["x-request-id"] = request_id
            REQUESTS.labels(request.method, request.url.path, str(response.status_code)).inc()
            return response
        finally:
            structlog.contextvars.clear_contextvars()


class HealthResponse(BaseModel):
    """Stable health response contract."""

    status: str


class DependencyHealth(BaseModel):
    status: str
    detail: str | None = None


class ReadinessResponse(BaseModel):
    status: str
    dependencies: dict[str, DependencyHealth]


class VendorCreateRequest(BaseModel):
    external_reference: str
    legal_name: str
    jurisdiction: str
    contact_email: str


class VendorResponse(BaseModel):
    id: UUID
    status: VendorStatus
    risk_score: int | None


class StartOnboardingRequest(BaseModel):
    idempotency_key: str


class WorkflowRunResponse(BaseModel):
    id: UUID
    status: RunStatus
    temporal_workflow_id: str


class WorkflowEventEvidence(BaseModel):
    sequence: int
    event_type: str
    summary: str
    payload: dict[str, object]


class ModelCallEvidence(BaseModel):
    provider: str
    model: str
    correlation_id: str
    status: str
    input_tokens: int | None
    output_tokens: int | None
    latency_ms: int | None
    error_category: str | None
    explanation_summary: str | None


class WorkflowEvidenceResponse(BaseModel):
    workflow_run_id: UUID
    events: list[WorkflowEventEvidence]
    model_calls: list[ModelCallEvidence]


class EvaluationResultReport(BaseModel):
    status: str
    score: float
    metrics: dict[str, object]
    summary: str


class EvaluationReportResponse(BaseModel):
    evaluation_run_id: UUID
    status: str
    configuration: dict[str, object]
    results: list[EvaluationResultReport]


class ApprovalRequestBody(BaseModel):
    decision: ApprovalDecisionType
    rationale: str
    idempotency_key: str
    decided_by_id: UUID = seed_id("user:demo-operator")


async def liveness() -> HealthResponse:
    """Report process liveness without checking external dependencies."""
    return HealthResponse(status="ok")


def get_dependencies(request: Request) -> DependencySet:
    resources: RuntimeResources = request.app.state.resources
    return resources.dependencies


def get_app_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_database(request: Request) -> Database:
    resources: RuntimeResources = request.app.state.resources
    return Database(resources.engine)


async def readiness(
    dependencies: Annotated[DependencySet, Depends(get_dependencies)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> Response:
    checks = await check_dependencies(dependencies, settings.dependency_timeout_seconds)
    ready = all(check.status == "ok" for check in checks.values())
    payload = ReadinessResponse(
        status="ok" if ready else "unavailable",
        dependencies={
            name: DependencyHealth(status=check.status, detail=check.detail)
            for name, check in checks.items()
        },
    )
    return JSONResponse(
        status_code=status.HTTP_200_OK if ready else status.HTTP_503_SERVICE_UNAVAILABLE,
        content=payload.model_dump(),
    )


async def metrics() -> Response:
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


async def create_vendor(
    payload: VendorCreateRequest,
    database: Annotated[Database, Depends(get_database)],
) -> VendorResponse:
    async with database.session() as session:
        vendor = await VendorRepository(session).add(
            Vendor(
                external_reference=payload.external_reference,
                legal_name=payload.legal_name,
                jurisdiction=payload.jurisdiction.upper(),
                contact_email=payload.contact_email,
                status=VendorStatus.SUBMITTED,
            )
        )
        return VendorResponse(id=vendor.id, status=vendor.status, risk_score=vendor.risk_score)


async def start_onboarding(
    vendor_id: UUID,
    payload: StartOnboardingRequest,
    request: Request,
    database: Annotated[Database, Depends(get_database)],
) -> WorkflowRunResponse:
    async with database.session() as session:
        vendors = VendorRepository(session)
        runs = WorkflowRunRepository(session)
        vendor = await vendors.get(vendor_id)
        if vendor is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="vendor not found")
        existing = await runs.get_by_idempotency_key(payload.idempotency_key)
        if existing is not None:
            return WorkflowRunResponse(
                id=existing.id,
                status=existing.status,
                temporal_workflow_id=existing.temporal_workflow_id,
            )
        run = WorkflowRun(
            agent_id=seed_id("agent:vendor-onboarding:v1"),
            vendor_id=vendor.id,
            requested_by_id=seed_id("user:demo-operator"),
            workflow_type=WORKFLOW_TYPE,
            temporal_workflow_id=f"vendor-onboarding-{uuid4()}",
            idempotency_key=payload.idempotency_key,
            status=RunStatus.PENDING,
            input_summary={"vendor_id": str(vendor.id)},
        )
        await runs.add(run)

    resources: RuntimeResources = request.app.state.resources
    await resources.temporal_client.start_workflow(
        VendorOnboardingWorkflow.run,
        VendorOnboardingInput(run_id=str(run.id), vendor_id=str(vendor_id)),
        id=run.temporal_workflow_id,
        task_queue=TASK_QUEUE,
    )
    return WorkflowRunResponse(
        id=run.id, status=run.status, temporal_workflow_id=run.temporal_workflow_id
    )


async def decide_onboarding(
    run_id: UUID,
    payload: ApprovalRequestBody,
    request: Request,
    database: Annotated[Database, Depends(get_database)],
) -> Response:
    async with database.session() as session:
        run = await WorkflowRunRepository(session).get(run_id)
        if run is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="workflow run not found"
            )
        temporal_workflow_id = run.temporal_workflow_id
    resources: RuntimeResources = request.app.state.resources
    handle = resources.temporal_client.get_workflow_handle(temporal_workflow_id)
    await handle.signal(
        VendorOnboardingWorkflow.decide,
        ApprovalDecisionInput(
            decision=payload.decision,
            decided_by_id=str(payload.decided_by_id),
            rationale=payload.rationale,
            idempotency_key=payload.idempotency_key,
        ),
    )
    return Response(status_code=status.HTTP_202_ACCEPTED)


async def onboarding_status(run_id: UUID, request: Request) -> WorkflowStatus:
    resources: RuntimeResources = request.app.state.resources
    async with Database(resources.engine).session() as session:
        run = await WorkflowRunRepository(session).get(run_id)
        if run is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="workflow run not found"
            )
        temporal_workflow_id = run.temporal_workflow_id
    return await resources.temporal_client.get_workflow_handle(temporal_workflow_id).query(
        VendorOnboardingWorkflow.status
    )


async def onboarding_evidence(
    run_id: UUID,
    database: Annotated[Database, Depends(get_database)],
) -> WorkflowEvidenceResponse:
    async with database.session() as session:
        run = await WorkflowRunRepository(session).get(run_id)
        if run is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="workflow run not found"
            )
        events = (
            await session.scalars(
                select(WorkflowEvent)
                .where(WorkflowEvent.workflow_run_id == run_id)
                .order_by(WorkflowEvent.sequence)
            )
        ).all()
        model_calls = (
            await session.scalars(
                select(ModelCall)
                .where(ModelCall.workflow_run_id == run_id)
                .order_by(ModelCall.created_at)
            )
        ).all()
    return WorkflowEvidenceResponse(
        workflow_run_id=run_id,
        events=[
            WorkflowEventEvidence(
                sequence=event.sequence,
                event_type=event.event_type,
                summary=event.summary,
                payload=event.payload,
            )
            for event in events
        ],
        model_calls=[
            ModelCallEvidence(
                provider=model_call.provider,
                model=model_call.model,
                correlation_id=model_call.correlation_id,
                status=model_call.status.value,
                input_tokens=model_call.input_tokens,
                output_tokens=model_call.output_tokens,
                latency_ms=model_call.latency_ms,
                error_category=model_call.error_category,
                explanation_summary=model_call.decision_summary,
            )
            for model_call in model_calls
        ],
    )


async def evaluation_report(
    evaluation_run_id: UUID,
    database: Annotated[Database, Depends(get_database)],
) -> EvaluationReportResponse:
    async with database.session() as session:
        run = await session.get(EvaluationRun, evaluation_run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="evaluation run not found")
        results = (
            await session.scalars(
                select(EvaluationResult)
                .where(EvaluationResult.evaluation_run_id == evaluation_run_id)
                .order_by(EvaluationResult.created_at)
            )
        ).all()
    return EvaluationReportResponse(
        evaluation_run_id=evaluation_run_id,
        status=run.status.value,
        configuration=run.configuration,
        results=[
            EvaluationResultReport(
                status=result.status.value,
                score=float(result.score),
                metrics=result.metrics,
                summary=result.summary,
            )
            for result in results
        ],
    )


async def cancel_onboarding(
    run_id: UUID,
    request: Request,
    database: Annotated[Database, Depends(get_database)],
) -> Response:
    async with database.session() as session:
        run = await WorkflowRunRepository(session).get(run_id)
        if run is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="workflow run not found"
            )
        temporal_workflow_id = run.temporal_workflow_id
    resources: RuntimeResources = request.app.state.resources
    await resources.temporal_client.get_workflow_handle(temporal_workflow_id).signal(
        VendorOnboardingWorkflow.cancel
    )
    return Response(status_code=status.HTTP_202_ACCEPTED)


def create_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or get_settings()
    configure_logging(app_settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        resources = await create_resources(app_settings)
        app.state.resources = resources
        app.state.settings = app_settings
        try:
            yield
        finally:
            await resources.close()

    app = FastAPI(
        title="Agents Should Survive Failure",
        description="Control plane for durable, observable, permission-controlled AI agents.",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.settings = app_settings

    app.add_api_route(
        "/health/live",
        liveness,
        methods=["GET"],
        response_model=HealthResponse,
        tags=["health"],
    )
    app.add_api_route(
        "/health/ready",
        readiness,
        methods=["GET"],
        response_model=ReadinessResponse,
        responses={503: {"model": ReadinessResponse}},
        tags=["health"],
    )
    app.add_api_route("/metrics", metrics, methods=["GET"], include_in_schema=False)
    app.add_api_route("/vendors", create_vendor, methods=["POST"], response_model=VendorResponse)
    app.add_api_route(
        "/vendors/{vendor_id}/onboarding",
        start_onboarding,
        methods=["POST"],
        response_model=WorkflowRunResponse,
    )
    app.add_api_route(
        "/workflow-runs/{run_id}/approval",
        decide_onboarding,
        methods=["POST"],
        status_code=status.HTTP_202_ACCEPTED,
    )
    app.add_api_route(
        "/workflow-runs/{run_id}", onboarding_status, methods=["GET"], response_model=WorkflowStatus
    )
    app.add_api_route(
        "/workflow-runs/{run_id}/evidence",
        onboarding_evidence,
        methods=["GET"],
        response_model=WorkflowEvidenceResponse,
    )
    app.add_api_route(
        "/evaluation-runs/{evaluation_run_id}",
        evaluation_report,
        methods=["GET"],
        response_model=EvaluationReportResponse,
    )
    app.add_api_route(
        "/workflow-runs/{run_id}", cancel_onboarding, methods=["DELETE"], status_code=202
    )
    app.add_middleware(RequestContextMiddleware)

    configure_tracing(app, app_settings)

    return app


app = create_app()
