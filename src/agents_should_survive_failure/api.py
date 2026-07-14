"""Control-plane API application."""

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Annotated, Any
from uuid import UUID, uuid4

import structlog
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from prometheus_client import CONTENT_TYPE_LATEST, Counter, generate_latest
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from temporalio.client import WorkflowUpdateFailedError, WorkflowUpdateRPCTimeoutOrCancelledError

from agents_should_survive_failure.auth import AuthenticatedPrincipal, resolve_api_key
from agents_should_survive_failure.dependencies import (
    DependencySet,
    RuntimeResources,
    check_dependencies,
    create_resources,
)
from agents_should_survive_failure.observability import configure_logging, configure_tracing
from agents_should_survive_failure.persistence.models import (
    Agent,
    ApprovalDecision,
    ApprovalRequest,
    ApprovalStatus,
    EvaluationResult,
    EvaluationRun,
    ModelCall,
    RunStatus,
    ToolInvocation,
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
from agents_should_survive_failure.workflow_starts import (
    RequestFingerprintConflict,
    WorkflowStartCoordinator,
    WorkflowStartUnavailable,
)
from agents_should_survive_failure.workflows.contracts import (
    ApprovalDecisionInput,
    ApprovalDecisionType,
    WorkflowStatus,
)
from agents_should_survive_failure.workflows.vendor_onboarding import VendorOnboardingWorkflow

REQUESTS = Counter(
    "agents_http_requests_total",
    "HTTP requests processed by the control-plane API.",
    ("method", "path", "status"),
)

TERMINAL_RUN_STATUSES = frozenset(
    {RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED, RunStatus.REJECTED}
)
EVENT_STREAM_BATCH_SIZE = 100
EVENT_STREAM_POLL_SECONDS = 0.5


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = request.headers.get("x-request-id", str(uuid4()))
        request.state.request_id = request_id
        content_length = request.headers.get("content-length")
        max_body_bytes = request.app.state.settings.max_request_body_bytes
        if content_length is not None and int(content_length) > max_body_bytes:
            response = JSONResponse(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                content=ApiErrorResponse(
                    code="payload_too_large",
                    message="request payload exceeds the configured limit",
                    request_id=request_id,
                ).model_dump(),
            )
            response.headers["x-request-id"] = request_id
            return response
        structlog.contextvars.bind_contextvars(request_id=request_id)
        try:
            response = await call_next(request)
            response.headers["x-request-id"] = request_id
            route = request.scope.get("route")
            route_path = getattr(route, "path", "unmatched")
            REQUESTS.labels(request.method, route_path, str(response.status_code)).inc()
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


class ApiFieldError(BaseModel):
    field: str
    message: str


class ApiErrorResponse(BaseModel):
    code: str
    message: str
    request_id: str
    field_errors: list[ApiFieldError] | None = None


class StrictRequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class VendorCreateRequest(StrictRequestModel):
    external_reference: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9._:-]+$")
    legal_name: str = Field(min_length=1, max_length=240)
    jurisdiction: str = Field(min_length=2, max_length=2, pattern=r"^[A-Za-z]{2}$")
    contact_email: str = Field(min_length=3, max_length=254, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class VendorResponse(BaseModel):
    id: UUID
    status: VendorStatus
    risk_score: int | None


class StartOnboardingRequest(StrictRequestModel):
    idempotency_key: str = Field(min_length=1, max_length=240, pattern=r"^[A-Za-z0-9._:-]+$")


class WorkflowRunResponse(BaseModel):
    id: UUID
    status: RunStatus
    temporal_workflow_id: str


class WorkflowRunDetailResponse(WorkflowRunResponse):
    workflow_type: str
    vendor_id: UUID | None
    input_summary: dict[str, object]
    result_summary: dict[str, object] | None


class WorkflowRunPage(BaseModel):
    items: list[WorkflowRunDetailResponse]
    limit: int
    offset: int


class AgentResponse(BaseModel):
    id: UUID
    name: str
    version: str
    workflow_type: str
    status: str
    configuration: dict[str, object]


class AgentPage(BaseModel):
    items: list[AgentResponse]
    limit: int
    offset: int


class ApprovalResponse(BaseModel):
    id: UUID
    workflow_run_id: UUID
    request_key: str
    status: str
    summary: str
    version: int


class ToolInvocationResponse(BaseModel):
    id: UUID
    workflow_run_id: UUID
    tool_definition_id: UUID
    status: str
    result_summary: dict[str, object] | None
    error_category: str | None


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


class ModelCallResponse(ModelCallEvidence):
    id: UUID
    workflow_run_id: UUID


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


class ApprovalRequestBody(StrictRequestModel):
    approval_request_id: UUID
    expected_version: int = Field(ge=1)
    decision: ApprovalDecisionType
    rationale: str = Field(min_length=1, max_length=1_000)
    idempotency_key: str = Field(min_length=1, max_length=240, pattern=r"^[A-Za-z0-9._:-]+$")


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


def authentication_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="invalid API key",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_authenticated_principal(
    request: Request,
) -> AuthenticatedPrincipal:
    authorization = request.headers.get("authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise authentication_error()
    database = get_database(request)
    async with database.session() as session:
        principal = await resolve_api_key(session, token)
    if principal is None:
        raise authentication_error()
    return principal


def require_scopes(*scopes: str) -> Callable[..., Any]:
    async def dependency(
        principal: Annotated[AuthenticatedPrincipal, Depends(get_authenticated_principal)],
    ) -> AuthenticatedPrincipal:
        if not principal.allows(*scopes):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="insufficient scope")
        return principal

    return dependency


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


async def metrics(
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> Response:
    if not settings.metrics_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="metrics not found")
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


def _request_id(request: Request) -> str:
    return str(getattr(request.state, "request_id", "unknown"))


async def validation_error(request: Request, error: RequestValidationError) -> JSONResponse:
    field_errors = [
        ApiFieldError(field=".".join(str(part) for part in item["loc"]), message=item["msg"])
        for item in error.errors()
    ]
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content=ApiErrorResponse(
            code="validation_error",
            message="request validation failed",
            request_id=_request_id(request),
            field_errors=field_errors,
        ).model_dump(),
    )


async def http_error(request: Request, error: HTTPException) -> JSONResponse:
    codes = {
        status.HTTP_404_NOT_FOUND: "not_found",
        status.HTTP_409_CONFLICT: "conflict",
        status.HTTP_401_UNAUTHORIZED: "authentication_required",
        status.HTTP_403_FORBIDDEN: "authorization_denied",
        status.HTTP_503_SERVICE_UNAVAILABLE: "dependency_unavailable",
    }
    return JSONResponse(
        status_code=error.status_code,
        content=ApiErrorResponse(
            code=codes.get(error.status_code, "request_failed"),
            message=str(error.detail),
            request_id=_request_id(request),
        ).model_dump(),
    )


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
    principal: Annotated[AuthenticatedPrincipal, Depends(get_authenticated_principal)],
) -> WorkflowRunResponse:
    async with database.session() as session:
        vendors = VendorRepository(session)
        vendor = await vendors.get(vendor_id)
        if vendor is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="vendor not found")
    resources: RuntimeResources = request.app.state.resources
    coordinator = WorkflowStartCoordinator(database, resources.temporal_client)
    try:
        run = await coordinator.create_or_get(
            vendor_id=vendor_id,
            requested_by_id=principal.id,
            agent_id=seed_id("agent:vendor-onboarding:v1"),
            idempotency_key=payload.idempotency_key,
        )
        await coordinator.start(run.id)
    except RequestFingerprintConflict:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="idempotency key was reused for a different onboarding request",
        ) from None
    except WorkflowStartUnavailable as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="workflow start is pending recovery",
            headers={"Retry-After": "1"},
        ) from error
    return WorkflowRunResponse(
        id=run.id, status=run.status, temporal_workflow_id=run.temporal_workflow_id
    )


async def decide_onboarding(
    run_id: UUID,
    payload: ApprovalRequestBody,
    request: Request,
    database: Annotated[Database, Depends(get_database)],
    principal: Annotated[AuthenticatedPrincipal, Depends(get_authenticated_principal)],
) -> Response:
    async with database.session() as session:
        run = await WorkflowRunRepository(session).get(run_id)
        if run is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="workflow run not found"
            )
        approval = await session.scalar(
            select(ApprovalRequest).where(
                ApprovalRequest.id == payload.approval_request_id,
                ApprovalRequest.workflow_run_id == run_id,
            )
        )
        if approval is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="approval request not found"
            )
        existing = await session.scalar(
            select(ApprovalDecision).where(
                ApprovalDecision.approval_request_id == approval.id,
                ApprovalDecision.idempotency_key == payload.idempotency_key,
            )
        )
        decision_status = (
            ApprovalStatus.APPROVED
            if payload.decision is ApprovalDecisionType.APPROVED
            else ApprovalStatus.REJECTED
        )
        if existing is not None:
            if (
                existing.decided_by_id != principal.id
                or existing.decision is not decision_status
                or existing.rationale != payload.rationale
            ):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="idempotency key was reused for a different approval decision",
                )
            return Response(status_code=status.HTTP_202_ACCEPTED)
        if (
            approval.status is not ApprovalStatus.PENDING
            or approval.version != payload.expected_version
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="approval request is no longer pending at the expected version",
            )
        temporal_workflow_id = run.temporal_workflow_id
    resources: RuntimeResources = request.app.state.resources
    handle = resources.temporal_client.get_workflow_handle(temporal_workflow_id)
    decision = ApprovalDecisionInput(
        approval_request_id=str(payload.approval_request_id),
        expected_version=payload.expected_version,
        decision=payload.decision,
        decided_by_id=str(principal.id),
        rationale=payload.rationale,
        idempotency_key=payload.idempotency_key,
    )
    try:
        await handle.execute_update(  # pyright: ignore[reportUnknownMemberType]
            "decide",
            decision,
            id=payload.idempotency_key,
        )
    except WorkflowUpdateFailedError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="approval decision is no longer valid for the workflow state",
        ) from None
    except WorkflowUpdateRPCTimeoutOrCancelledError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="approval decision outcome is unknown; retry with the same idempotency key",
            headers={"Retry-After": "1"},
        ) from None
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


def workflow_run_response(run: WorkflowRun) -> WorkflowRunDetailResponse:
    return WorkflowRunDetailResponse(
        id=run.id,
        status=run.status,
        temporal_workflow_id=run.temporal_workflow_id,
        workflow_type=run.workflow_type,
        vendor_id=run.vendor_id,
        input_summary=run.input_summary,
        result_summary=run.result_summary,
    )


async def list_workflow_runs(
    database: Annotated[Database, Depends(get_database)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=10_000)] = 0,
) -> WorkflowRunPage:
    async with database.session() as session:
        runs = (
            await session.scalars(
                select(WorkflowRun)
                .order_by(WorkflowRun.created_at.desc(), WorkflowRun.id.desc())
                .limit(limit)
                .offset(offset)
            )
        ).all()
    return WorkflowRunPage(
        items=[workflow_run_response(run) for run in runs], limit=limit, offset=offset
    )


async def workflow_run_detail(
    run_id: UUID,
    database: Annotated[Database, Depends(get_database)],
) -> WorkflowRunDetailResponse:
    async with database.session() as session:
        run = await WorkflowRunRepository(session).get(run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="workflow run not found")
    return workflow_run_response(run)


async def list_run_events(
    run_id: UUID,
    database: Annotated[Database, Depends(get_database)],
) -> list[WorkflowEventEvidence]:
    async with database.session() as session:
        if await WorkflowRunRepository(session).get(run_id) is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="workflow run not found"
            )
        events = await WorkflowRunRepository(session).events(run_id)
    return [
        WorkflowEventEvidence(
            sequence=event.sequence,
            event_type=event.event_type,
            summary=event.summary,
            payload=event.payload,
        )
        for event in events
    ]


def event_stream_cursor(request: Request, after_sequence: int) -> int:
    """Use the furthest supplied replay cursor without trusting an invalid header."""
    last_event_id = request.headers.get("last-event-id")
    if last_event_id is None:
        return after_sequence
    try:
        header_cursor = int(last_event_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Last-Event-ID must be a non-negative integer",
        ) from None
    if header_cursor < 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Last-Event-ID must be a non-negative integer",
        )
    return max(after_sequence, header_cursor)


async def stream_run_events(
    run_id: UUID,
    request: Request,
    database: Annotated[Database, Depends(get_database)],
    after_sequence: Annotated[int, Query(ge=0, le=2_147_483_647)] = 0,
) -> StreamingResponse:
    cursor = event_stream_cursor(request, after_sequence)
    async with database.session() as session:
        if await WorkflowRunRepository(session).get(run_id) is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="workflow run not found"
            )

    async def event_source() -> AsyncIterator[str]:
        nonlocal cursor
        while True:
            async with database.session() as session:
                runs = WorkflowRunRepository(session)
                events = await runs.events_after(
                    run_id,
                    after_sequence=cursor,
                    limit=EVENT_STREAM_BATCH_SIZE,
                )
                run = await runs.get(run_id)
            for event in events:
                cursor = event.sequence
                data = {
                    "sequence": event.sequence,
                    "event_type": event.event_type,
                    "summary": event.summary,
                    "payload": event.payload,
                    "occurred_at": event.occurred_at.isoformat(),
                }
                yield f"id: {cursor}\nevent: workflow_event\ndata: {json.dumps(data)}\n\n"
            if run is None or run.status in TERMINAL_RUN_STATUSES:
                return
            if await request.is_disconnected():
                return
            yield ": keepalive\n\n"
            await asyncio.sleep(EVENT_STREAM_POLL_SECONDS)

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def list_agents(
    database: Annotated[Database, Depends(get_database)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=10_000)] = 0,
) -> AgentPage:
    async with database.session() as session:
        agents = (
            await session.scalars(
                select(Agent).order_by(Agent.name, Agent.version).limit(limit).offset(offset)
            )
        ).all()
    return AgentPage(
        items=[
            AgentResponse(
                id=agent.id,
                name=agent.name,
                version=agent.version,
                workflow_type=agent.workflow_type,
                status=agent.status.value,
                configuration=agent.configuration,
            )
            for agent in agents
        ],
        limit=limit,
        offset=offset,
    )


async def list_run_approvals(
    run_id: UUID,
    database: Annotated[Database, Depends(get_database)],
) -> list[ApprovalResponse]:
    async with database.session() as session:
        approvals = (
            await session.scalars(
                select(ApprovalRequest)
                .where(ApprovalRequest.workflow_run_id == run_id)
                .order_by(ApprovalRequest.created_at)
            )
        ).all()
    return [
        ApprovalResponse(
            id=approval.id,
            workflow_run_id=approval.workflow_run_id,
            request_key=approval.request_key,
            status=approval.status.value,
            summary=approval.summary,
            version=approval.version,
        )
        for approval in approvals
    ]


async def list_run_model_calls(
    run_id: UUID,
    database: Annotated[Database, Depends(get_database)],
) -> list[ModelCallResponse]:
    async with database.session() as session:
        calls = (
            await session.scalars(
                select(ModelCall)
                .where(ModelCall.workflow_run_id == run_id)
                .order_by(ModelCall.created_at)
            )
        ).all()
    return [
        ModelCallResponse(
            id=call.id,
            workflow_run_id=call.workflow_run_id,
            provider=call.provider,
            model=call.model,
            correlation_id=call.correlation_id,
            status=call.status.value,
            input_tokens=call.input_tokens,
            output_tokens=call.output_tokens,
            latency_ms=call.latency_ms,
            error_category=call.error_category,
            explanation_summary=call.decision_summary,
        )
        for call in calls
    ]


async def list_run_tool_calls(
    run_id: UUID,
    database: Annotated[Database, Depends(get_database)],
) -> list[ToolInvocationResponse]:
    async with database.session() as session:
        calls = (
            await session.scalars(
                select(ToolInvocation)
                .where(ToolInvocation.workflow_run_id == run_id)
                .order_by(ToolInvocation.created_at)
            )
        ).all()
    return [
        ToolInvocationResponse(
            id=call.id,
            workflow_run_id=call.workflow_run_id,
            tool_definition_id=call.tool_definition_id,
            status=call.status.value,
            result_summary=call.result_summary,
            error_category=call.error_category,
        )
        for call in calls
    ]


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
    app.add_exception_handler(RequestValidationError, validation_error)  # type: ignore[arg-type]
    app.add_exception_handler(HTTPException, http_error)  # type: ignore[arg-type]

    def add_v1_route(
        path: str,
        endpoint: Callable[..., Any],
        *,
        methods: list[str],
        scopes: tuple[str, ...],
        **kwargs: Any,
    ) -> None:
        dependencies = [Depends(require_scopes(*scopes))]
        app.add_api_route(
            f"/api/v1{path}", endpoint, methods=methods, dependencies=dependencies, **kwargs
        )
        app.add_api_route(
            path, endpoint, methods=methods, deprecated=True, dependencies=dependencies, **kwargs
        )

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
    add_v1_route(
        "/vendors",
        create_vendor,
        methods=["POST"],
        scopes=("runs:write",),
        response_model=VendorResponse,
    )
    add_v1_route(
        "/vendors/{vendor_id}/onboarding",
        start_onboarding,
        methods=["POST"],
        scopes=("runs:write",),
        response_model=WorkflowRunResponse,
    )
    add_v1_route(
        "/workflow-runs/{run_id}/approval",
        decide_onboarding,
        methods=["POST"],
        scopes=("approvals:decide",),
        status_code=status.HTTP_202_ACCEPTED,
    )
    app.add_api_route(
        "/workflow-runs/{run_id}",
        onboarding_status,
        methods=["GET"],
        response_model=WorkflowStatus,
        deprecated=True,
        dependencies=[Depends(require_scopes("runs:read"))],
    )
    app.add_api_route(
        "/api/v1/workflow-runs/{run_id}/status",
        onboarding_status,
        methods=["GET"],
        response_model=WorkflowStatus,
        dependencies=[Depends(require_scopes("runs:read"))],
    )
    app.add_api_route(
        "/api/v1/workflow-runs",
        list_workflow_runs,
        methods=["GET"],
        response_model=WorkflowRunPage,
        dependencies=[Depends(require_scopes("runs:read"))],
    )
    app.add_api_route(
        "/api/v1/workflow-runs/{run_id}",
        workflow_run_detail,
        methods=["GET"],
        response_model=WorkflowRunDetailResponse,
        dependencies=[Depends(require_scopes("runs:read"))],
    )
    app.add_api_route(
        "/api/v1/workflow-runs/{run_id}/events",
        list_run_events,
        methods=["GET"],
        response_model=list[WorkflowEventEvidence],
        dependencies=[Depends(require_scopes("runs:read"))],
    )
    app.add_api_route(
        "/api/v1/workflow-runs/{run_id}/events/stream",
        stream_run_events,
        methods=["GET"],
        dependencies=[Depends(require_scopes("runs:read"))],
    )
    app.add_api_route(
        "/api/v1/workflow-runs/{run_id}/approvals",
        list_run_approvals,
        methods=["GET"],
        response_model=list[ApprovalResponse],
        dependencies=[Depends(require_scopes("approvals:read"))],
    )
    app.add_api_route(
        "/api/v1/workflow-runs/{run_id}/model-calls",
        list_run_model_calls,
        methods=["GET"],
        response_model=list[ModelCallResponse],
        dependencies=[Depends(require_scopes("runs:read"))],
    )
    app.add_api_route(
        "/api/v1/workflow-runs/{run_id}/tool-calls",
        list_run_tool_calls,
        methods=["GET"],
        response_model=list[ToolInvocationResponse],
        dependencies=[Depends(require_scopes("runs:read"))],
    )
    app.add_api_route(
        "/api/v1/agents",
        list_agents,
        methods=["GET"],
        response_model=AgentPage,
        dependencies=[Depends(require_scopes("agents:read"))],
    )
    add_v1_route(
        "/workflow-runs/{run_id}/evidence",
        onboarding_evidence,
        methods=["GET"],
        scopes=("runs:read",),
        response_model=WorkflowEvidenceResponse,
    )
    add_v1_route(
        "/evaluation-runs/{evaluation_run_id}",
        evaluation_report,
        methods=["GET"],
        scopes=("evaluations:read",),
        response_model=EvaluationReportResponse,
    )
    add_v1_route(
        "/workflow-runs/{run_id}",
        cancel_onboarding,
        methods=["DELETE"],
        scopes=("runs:write",),
        status_code=202,
    )
    app.add_middleware(RequestContextMiddleware)

    configure_tracing(app, app_settings)

    return app


app = create_app()
